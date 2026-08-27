"""App settings: the per-user settings.json, overridden by env / project .env.

Env overrides (dev): AI_APIURL, AI_APIKEY, AI_MODEL, AI_STYLE (auto|responses|chat).
"""
import json
import os
from pathlib import Path

if os.name == "nt":
    CONFIG_DIR = Path(os.environ.get("APPDATA", Path.home())) / "VoiceoverStudio"
else:
    CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "voiceover-studio"
SETTINGS_PATH = CONFIG_DIR / "settings.json"

DEFAULT_PROMPT = """\
You are a professional audiovisual translator. Translate subtitle dialogue into {target_language} \
for a single-narrator voiceover (lektor) that is read over the quieted original audio.

Rules:
- Keep translations COMPACT: the spoken line must fit the subtitle's time slot. Prefer short, natural wording. There is no speed-up: an overlong line desynchronizes the dub.
- Collapse repetitions: "Go! Go! Go!" -> one word; a name shouted twice -> once (the original stays audible underneath).
- Natural colloquial dialogue; keep the register and tone of each line; keep forms of address consistent across lines (use the provided context).
- Keep proper names as-is unless there is a customary localized form.
- If a line is pure noise/sound description, return "" for it.
- Reply with a STRICT JSON object mapping line ids to translations, e.g. {{"12": "...", "13": "..."}}. No other text, no code fences, no comments.
- Translate EVERY line given in "translate". Do not re-output the context lines.
"""

DEFAULTS = {
    # translation API (OpenAI-compatible)
    "api_url": "",
    "api_key": "",
    "api_model": "",          # empty -> auto-pick first from /v1/models
    "api_style": "auto",      # auto | responses | chat
    "prompt_template": DEFAULT_PROMPT,
    "batch_size": 40,
    "context_lines": 6,
    # dubbing defaults
    "target_lang": "pl",
    "voice": "pl-PL-ZofiaNeural",
    "dub_format": "stereo",   # stereo | original
    "duck": True,
    "duck_ratio": 1.5,   # user-picked on the test sample (2.0 ducked the original too hard)
    "level_mode": "track",    # track | fixed | off
    "level_k": 0.6,
    "fixed_gain_db": 0.0,
    "max_speed": 1.0,         # locked: no speed-up
}

_ENV_MAP = {
    "AI_APIURL": "api_url",
    "AI_APIKEY": "api_key",
    "AI_MODEL": "api_model",
    "AI_STYLE": "api_style",
}


def load_dotenv(path=".env"):
    """Minimal .env loader (KEY=VALUE lines) -> os.environ (no overwrite of real env)."""
    p = Path(path)
    if not p.is_file():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def load_settings():
    cfg = dict(DEFAULTS)
    if SETTINGS_PATH.is_file():
        try:
            cfg.update(json.loads(SETTINGS_PATH.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            pass
    for env_key, cfg_key in _ENV_MAP.items():
        if os.environ.get(env_key):
            cfg[cfg_key] = os.environ[env_key]
    return cfg


def save_settings(cfg):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    keep = {k: v for k, v in cfg.items() if k in DEFAULTS}
    SETTINGS_PATH.write_text(json.dumps(keep, ensure_ascii=False, indent=2), encoding="utf-8")


LANG_NAMES = {
    "pl": "Polish", "en": "English", "de": "German", "ru": "Russian", "uk": "Ukrainian",
    "es": "Spanish", "fr": "French", "it": "Italian", "pt": "Portuguese", "cs": "Czech",
    "sk": "Slovak", "hu": "Hungarian", "tr": "Turkish", "ja": "Japanese", "ko": "Korean",
    "zh": "Chinese", "nl": "Dutch", "sv": "Swedish", "no": "Norwegian", "da": "Danish",
}

LANG3 = {
    "pl": "pol", "en": "eng", "de": "ger", "ru": "rus", "uk": "ukr", "es": "spa",
    "fr": "fre", "it": "ita", "pt": "por", "cs": "cze", "sk": "slo", "hu": "hun",
    "tr": "tur", "ja": "jpn", "ko": "kor", "zh": "chi", "nl": "dut", "sv": "swe",
    "no": "nor", "da": "dan",
}
