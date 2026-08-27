"""Subtitle translation via an OpenAI-compatible API.

Supports both `/v1/responses` (preferred) and `/v1/chat/completions`; `style='auto'`
tries responses first and falls back to chat once (remembered for the session).
Batched with rolling context, incremental cache, coverage check with per-line retry.
"""
import json
import re
import time
from pathlib import Path

import httpx

from ..config import DEFAULT_PROMPT, LANG_NAMES


class TranslateError(RuntimeError):
    pass


def _extract_json(text):
    """Parse a JSON object out of a model reply (tolerates code fences / prose around)."""
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("no JSON object in reply")
    return json.loads(m.group(0))


class Translator:
    def __init__(self, api_url, api_key="", model="", style="auto",
                 prompt_template=None, target_lang="pl",
                 batch_size=40, context_lines=6, timeout=180):
        if not api_url:
            raise TranslateError("translation API URL is not configured")
        self.base = api_url.rstrip("/")
        if self.base.endswith("/v1"):
            self.base = self.base[:-3]
        self.key = api_key
        self.model = model
        self.style = style  # auto | responses | chat
        self.target_lang = target_lang
        self.batch_size = batch_size
        self.context_lines = context_lines
        self.prompt = (prompt_template or DEFAULT_PROMPT).format(
            target_language=LANG_NAMES.get(target_lang, target_lang)
        )
        self.client = httpx.Client(
            timeout=timeout,
            headers={"Authorization": f"Bearer {self.key}"} if self.key else {},
        )

    # -- HTTP ---------------------------------------------------------------

    def resolve_model(self):
        if self.model:
            return self.model
        r = self.client.get(f"{self.base}/v1/models")
        r.raise_for_status()
        data = r.json().get("data", [])
        if not data:
            raise TranslateError("API lists no models; set api_model explicitly")
        self.model = data[0]["id"]
        return self.model

    def _post(self, path, payload):
        r = self.client.post(f"{self.base}{path}", json=payload)
        if r.status_code in (404, 405):
            raise LookupError(path)  # endpoint not supported -> style fallback
        r.raise_for_status()
        return r.json()

    def _ask_responses(self, user_text):
        data = self._post("/v1/responses", {
            "model": self.model,
            "instructions": self.prompt,
            "input": user_text,
            "temperature": 0.3,
        })
        if isinstance(data.get("output_text"), str) and data["output_text"]:
            return data["output_text"]
        parts = []
        for item in data.get("output", []):
            for c in item.get("content", []) or []:
                if c.get("type") in ("output_text", "text") and c.get("text"):
                    parts.append(c["text"])
        if not parts:
            raise TranslateError(f"empty /v1/responses reply: {str(data)[:300]}")
        return "".join(parts)

    def _ask_chat(self, user_text):
        data = self._post("/v1/chat/completions", {
            "model": self.model,
            "messages": [
                {"role": "system", "content": self.prompt},
                {"role": "user", "content": user_text},
            ],
            "temperature": 0.3,
        })
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError):
            raise TranslateError(f"malformed chat reply: {str(data)[:300]}")

    def _ask(self, user_text, retries=3):
        last = None
        for attempt in range(retries):
            try:
                if self.style in ("auto", "responses"):
                    try:
                        return self._ask_responses(user_text)
                    except LookupError:
                        self.style = "chat"  # remembered: server has no /v1/responses
                return self._ask_chat(user_text)
            except (httpx.HTTPError, TranslateError) as e:
                last = e
                time.sleep(2 * (attempt + 1))
        raise TranslateError(f"translation API failed after {retries} tries: {last}")

    # -- translation --------------------------------------------------------

    def _payload(self, batch, context):
        return json.dumps({
            "context": [{"id": n, "source": s, "translation": t} for n, s, t in context],
            "translate": {str(c.num): c.text.replace("\n", " ") for c in batch},
        }, ensure_ascii=False)

    def translate_cues(self, cues, cache_path=None, progress=None, cancel=None):
        """cues: cleaned source cues. Returns {num(int): text}. Incremental cache on disk."""
        self.resolve_model()
        done = {}
        cache = Path(cache_path) if cache_path else None
        if cache and cache.is_file():
            done = {int(k): v for k, v in json.loads(cache.read_text(encoding="utf-8")).items()}
        todo = [c for c in cues if c.text and c.num not in done]
        total = len(todo)
        n_done = 0
        by_num = {c.num: c for c in cues}
        while todo:
            if cancel is not None and cancel.is_set():
                raise TranslateError("cancelled")
            batch = todo[: self.batch_size]
            todo = todo[self.batch_size:]
            ctx = []
            for n in sorted(k for k in done if k < batch[0].num)[-self.context_lines:]:
                src = by_num.get(n)
                ctx.append((n, src.text.replace("\n", " ") if src else "", done[n]))
            reply = self._ask(self._payload(batch, ctx))
            try:
                obj = _extract_json(reply)
            except ValueError:
                reply = self._ask(self._payload(batch, ctx) +
                                  '\nReminder: reply with ONLY the JSON object.')
                obj = _extract_json(reply)  # second failure raises to caller
            got = 0
            for k, v in obj.items():
                k = re.sub(r"\D", "", str(k))
                if k and int(k) in by_num and isinstance(v, str):
                    done[int(k)] = v.strip()
                    got += 1
            n_done += len(batch)
            if cache:
                cache.write_text(json.dumps({str(k): v for k, v in sorted(done.items())},
                                            ensure_ascii=False, indent=1), encoding="utf-8")
            if progress:
                progress(min(n_done, total), total, f"translated {min(n_done, total)}/{total}")
        # coverage: retry stragglers one by one
        missing = [c for c in cues if c.text and c.num not in done]
        for c in missing[:60]:
            if cancel is not None and cancel.is_set():
                raise TranslateError("cancelled")
            try:
                obj = _extract_json(self._ask(self._payload([c], [])))
                v = obj.get(str(c.num)) or next(iter(obj.values()), None)
                if isinstance(v, str):
                    done[c.num] = v.strip()
            except (TranslateError, ValueError):
                pass
        if cache:
            cache.write_text(json.dumps({str(k): v for k, v in sorted(done.items())},
                                        ensure_ascii=False, indent=1), encoding="utf-8")
        return done
