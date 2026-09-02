"""App settings: the per-user settings.json, overridden by env / project .env.

Env overrides (dev): AI_APIURL, AI_APIKEY, AI_MODEL, AI_STYLE (auto|responses|chat).
"""
import json
import math
import os
from pathlib import Path

if os.name == "nt":
    CONFIG_DIR = Path(os.environ.get("APPDATA", Path.home())) / "VoiceoverStudio"
else:
    CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "voiceover-studio"
SETTINGS_PATH = CONFIG_DIR / "settings.json"

# Style rules only — the input/output format contract (PROTOCOL) is fixed in core.translate
# and always appended, so edits here cannot break the wire format.
DEFAULT_PROMPT = """\
You are a professional audiovisual translator. Translate subtitle dialogue into {target_language} \
for a single-narrator voiceover (lektor) that is read over the quieted original audio.

Rules:
- Keep translations COMPACT: the spoken line must fit the subtitle's time slot. Prefer short, natural wording. There is no speed-up: an overlong line desynchronizes the dub.
- Collapse repetitions: "Go! Go! Go!" -> one word; a name shouted twice -> once (the original stays audible underneath).
- Natural colloquial dialogue; keep the register and tone of each line; keep forms of address consistent across lines (use the provided context).
- Follow the episode brief when given: character genders, forms of address, glossary renderings.
- Keep proper names as-is unless there is a customary localized form.
- If a line is pure noise/sound description, return "" for it.
"""

DEFAULTS = {
    # translation API (OpenAI-compatible)
    "api_url": "",
    "api_key": "",
    "api_model": "",          # empty -> auto-pick first from /v1/models
    "api_style": "auto",      # auto | responses | chat
    "prompt_template": DEFAULT_PROMPT,
    "batch_size": 40,
    "context_lines": 20,
    "lookahead_lines": 10,
    # work/cache dirs: "" = beside each source; a path moves them there (e.g. off a network drive)
    "work_dir": "",
    "cleanup_work": True,     # delete a file's work dir after a verified success
    # dubbing defaults
    "target_lang": "pl",
    "voice": "pl-PL-ZofiaNeural",
    "dub_format": "stereo",   # stereo | original
    "duck": True,
    "duck_db": 6.0,           # constant bed duck depth (fixed/off level modes)
    "level_mode": "gap",      # gap | fixed | off | legacy (0.1.0 algorithm)
    "gap_db": 8.0,            # gap mode: narrator sits this far above the scene bed
    "legacy_ratio": 2.0,      # legacy mode: sidechaincompress ratio
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
    cfg, loaded = dict(DEFAULTS), {}
    if SETTINGS_PATH.is_file():
        try:
            loaded = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
            cfg.update(loaded)
        except (OSError, json.JSONDecodeError):
            pass
    if cfg.get("context_lines") in (6, "6"):
        cfg["context_lines"] = DEFAULTS["context_lines"]  # pre-brief default; never UI-exposed
    # 0.2.x migration: ratio-based duck + median level tracking became the gap model
    # (the old algorithm survives as level_mode "legacy")
    if "duck_ratio" in loaded:
        try:
            if "duck_db" not in loaded:
                cfg["duck_db"] = round(20 * math.log10(max(1.0, float(loaded["duck_ratio"]))), 1)
            if "legacy_ratio" not in loaded:
                cfg["legacy_ratio"] = float(loaded["duck_ratio"])
        except (TypeError, ValueError):
            pass
    if cfg.get("level_mode") == "track":
        cfg["level_mode"] = "legacy"
    if cfg.get("level_mode") not in ("gap", "fixed", "off", "legacy"):
        cfg["level_mode"] = "gap"
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

# ISO 639-2/T codes where they diverge from the /B codes in LANG3 (files use either)
LANG3_T = {"de": "deu", "fr": "fra", "cs": "ces", "sk": "slk", "nl": "nld", "zh": "zho"}


def same_lang(tag, target_lang):
    """True if a stream language tag (2/3-letter, possibly BCP-47 like 'pt-BR')
    names `target_lang` (2-letter). 'und'/empty tags never match."""
    t = (tag or "").strip().lower().split("-")[0]
    tl = (target_lang or "").strip().lower()
    if not t or t == "und" or not tl:
        return False
    return t in (tl, LANG3.get(tl, ""), LANG3_T.get(tl, ""))
