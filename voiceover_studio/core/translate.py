"""Subtitle translation via an OpenAI-compatible API.

Supports both `/v1/responses` (preferred) and `/v1/chat/completions`; `style='auto'`
tries responses first and falls back to chat once (remembered for the session).
Two-pass: an episode brief (synopsis/characters/glossary distilled from the whole
text) rides along in every request; batches are aligned to scene pauses and carry
a rolling context of preceding translations plus a lookahead of upcoming source
lines. Incremental cache, coverage check with per-line retry.
"""
import json
import re
import time
from pathlib import Path

import httpx

from ..config import DEFAULT_PROMPT, LANG_NAMES

# Wire protocol lives outside the user-editable prompt template so the format
# contract survives any prompt customization in Settings.
PROTOCOL = """\
Input is a JSON object:
- "context": preceding dialogue already translated (id, source, translation) — continuity only.
- "upcoming": the source lines that follow this chunk — for understanding only.
- "translate": {id: line} — the lines to translate.
Reply with ONE strict JSON object mapping every id from "translate" to its translation,
e.g. {"12": "...", "13": "..."}. No other ids, no other text, no code fences, no comments."""

BRIEF_PROMPT = """\
You prepare a translation brief for dubbing subtitles into {target_language}.
From the subtitle text below (may be sampled), write a compact brief, under 350 words, plain text:
SYNOPSIS: 2-3 sentences.
CHARACTERS: name — gender, role, key relationships.
ADDRESS: who speaks to whom formally/informally (as relevant for {target_language}).
GLOSSARY: recurring names/terms/places with the {target_language} rendering to use consistently.
TONE: genre, register, style notes.
Reply with the brief only."""

SCENE_GAP = 2.0    # s of silence between cues treated as a scene boundary (matches audio leveling)
BRIEF_CHARS = 24000  # max source chars sent for the brief; longer texts are line-sampled


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
                 batch_size=40, context_lines=20, lookahead_lines=10, timeout=180):
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
        self.lookahead = lookahead_lines
        self.brief = ""     # episode brief text; set by the caller (see job.run_job)
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

    def _sys(self):
        parts = [self.prompt, PROTOCOL]
        if self.brief.strip():
            parts.append("Episode brief — established facts, follow them:\n" + self.brief.strip())
        return "\n\n".join(parts)

    def _post(self, path, payload):
        r = self.client.post(f"{self.base}{path}", json=payload)
        if r.status_code in (404, 405):
            raise LookupError(path)  # endpoint not supported -> style fallback
        r.raise_for_status()
        return r.json()

    def _ask_responses(self, user_text, system):
        data = self._post("/v1/responses", {
            "model": self.model,
            "instructions": system,
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

    def _ask_chat(self, user_text, system):
        data = self._post("/v1/chat/completions", {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user_text},
            ],
            "temperature": 0.3,
        })
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError):
            raise TranslateError(f"malformed chat reply: {str(data)[:300]}")

    def _ask(self, user_text, system=None, retries=3):
        system = system or self._sys()
        last = None
        for attempt in range(retries):
            try:
                if self.style in ("auto", "responses"):
                    try:
                        return self._ask_responses(user_text, system)
                    except LookupError:
                        self.style = "chat"  # remembered: server has no /v1/responses
                return self._ask_chat(user_text, system)
            except (httpx.HTTPError, TranslateError) as e:
                last = e
                if attempt < retries - 1:
                    time.sleep(2 * (attempt + 1))
        raise TranslateError(f"translation API failed after {retries} tries: {last}")

    # -- brief --------------------------------------------------------------

    def build_brief(self, cues, cancel=None):
        """One-shot episode brief from the full source text (line-sampled if huge).
        Raises TranslateError on failure — the caller decides to go on without it."""
        if cancel is not None and cancel.is_set():
            raise TranslateError("cancelled")
        lines = [c.text.replace("\n", " ") for c in cues if c.text]
        text = "\n".join(lines)
        if len(text) > BRIEF_CHARS:
            step = len(text) // BRIEF_CHARS + 1
            text = "\n".join(lines[::step])
        self.resolve_model()
        system = BRIEF_PROMPT.format(
            target_language=LANG_NAMES.get(self.target_lang, self.target_lang))
        return self._ask(text, system=system, retries=1).strip()

    # -- translation --------------------------------------------------------

    def _batches(self, todo):
        """Pack cues into batches ≤ batch_size, preferring cuts at scene boundaries
        (pause ≥ SCENE_GAP) so a batch holds coherent dialogue."""
        scenes, cur = [], []
        for prev, c in zip([None] + todo, todo):
            if cur and c.start - prev.end >= SCENE_GAP:
                scenes.append(cur)
                cur = []
            cur.append(c)
        if cur:
            scenes.append(cur)
        batches, cur = [], []
        for sc in scenes:
            while len(sc) > self.batch_size:  # giant scene: hard split
                if cur:
                    batches.append(cur)
                    cur = []
                batches.append(sc[:self.batch_size])
                sc = sc[self.batch_size:]
            if cur and len(cur) + len(sc) > self.batch_size:
                batches.append(cur)
                cur = []
            cur.extend(sc)
        if cur:
            batches.append(cur)
        return batches

    def _context(self, first, done, by_num):
        """Last context_lines translated lines preceding cue `first`."""
        ctx = []
        for n in sorted(k for k in done if k < first.num)[-self.context_lines:]:
            src = by_num.get(n)
            ctx.append((n, src.text.replace("\n", " ") if src else "", done[n]))
        return ctx

    def _upcoming(self, last, ordered, pos, n=None):
        """Source lines that follow cue `last` (lookahead, read-only)."""
        i = pos[last.num] + 1
        return [c.text.replace("\n", " ") for c in ordered[i: i + (n or self.lookahead)]]

    def _payload(self, batch, context, upcoming):
        obj = {}
        if context:
            obj["context"] = [{"id": n, "source": s, "translation": t} for n, s, t in context]
        if upcoming:
            obj["upcoming"] = upcoming
        obj["translate"] = {str(c.num): c.text.replace("\n", " ") for c in batch}
        return json.dumps(obj, ensure_ascii=False)

    def translate_cues(self, cues, cache_path=None, progress=None, cancel=None):
        """cues: cleaned source cues. Returns {num(int): text}. Incremental cache on disk."""
        self.resolve_model()
        done = {}
        cache = Path(cache_path) if cache_path else None
        if cache and cache.is_file():
            done = {int(k): v for k, v in json.loads(cache.read_text(encoding="utf-8")).items()}
        ordered = [c for c in cues if c.text]
        pos = {c.num: i for i, c in enumerate(ordered)}
        by_num = {c.num: c for c in cues}
        todo = [c for c in ordered if c.num not in done]
        total = len(todo)
        n_done = 0
        for batch in self._batches(todo):
            if cancel is not None and cancel.is_set():
                raise TranslateError("cancelled")
            payload = self._payload(batch, self._context(batch[0], done, by_num),
                                    self._upcoming(batch[-1], ordered, pos))
            reply = self._ask(payload)
            try:
                obj = _extract_json(reply)
            except ValueError:
                reply = self._ask(payload + '\nReminder: reply with ONLY the JSON object.')
                obj = _extract_json(reply)  # second failure raises to caller
            for k, v in obj.items():
                k = re.sub(r"\D", "", str(k))
                if k and int(k) in by_num and isinstance(v, str):
                    done[int(k)] = v.strip()
            n_done += len(batch)
            if cache:
                cache.write_text(json.dumps({str(k): v for k, v in sorted(done.items())},
                                            ensure_ascii=False, indent=1), encoding="utf-8")
            if progress:
                progress(min(n_done, total), total, f"translated {min(n_done, total)}/{total}")
        # coverage: retry stragglers one by one, each with its local context
        missing = [c for c in ordered if c.num not in done]
        for c in missing[:60]:
            if cancel is not None and cancel.is_set():
                raise TranslateError("cancelled")
            i = pos[c.num]
            ctx = [(x.num, x.text.replace("\n", " "), done[x.num])
                   for x in ordered[max(0, i - 4): i] if x.num in done]
            try:
                obj = _extract_json(self._ask(self._payload([c], ctx,
                                                            self._upcoming(c, ordered, pos, 3))))
                v = obj.get(str(c.num)) or next(iter(obj.values()), None)
                if isinstance(v, str):
                    done[c.num] = v.strip()
            except (TranslateError, ValueError):
                pass
        if cache:
            cache.write_text(json.dumps({str(k): v for k, v in sorted(done.items())},
                                        ensure_ascii=False, indent=1), encoding="utf-8")
        return done
