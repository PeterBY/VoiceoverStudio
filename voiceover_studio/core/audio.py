"""PCM math: clip decode/trim, timecode placement, TTS level-tracking, WAV IO.

Straight port of the proven build_dub.py (numpy float32 mono @48k). Placement is
shift-only by default (max_speed=1.0): a clip never overlaps the next cue's start;
drift resets at pauses. Level-tracking per tts-level-tracking-spec.md.
"""
import csv
import wave
from pathlib import Path

import numpy as np

from . import ffbin
from .srt import tts_text
from .tts import ensure_clip

SR = 48000
GAP = 0.04  # min silence between shifted clips (s)


def trim_silence(x, thr=0.008, pad=0.03):
    """Trim leading/trailing near-silence (edge-tts pads clips -> chronic drift otherwise)."""
    if x.size == 0:
        return x
    idx = np.where(np.abs(x) > thr)[0]
    if idx.size == 0:
        return x
    p = int(pad * SR)
    return x[max(0, idx[0] - p): min(len(x), idx[-1] + p)]


def decode_clip(path, atempo=None, cancel=None):
    filt = ["-filter:a", f"atempo={atempo:.4f}"] if atempo and atempo > 1.001 else []
    raw = ffbin.run(["-v", "error", "-i", str(path), *filt,
                     "-ar", str(SR), "-ac", "1", "-f", "s16le", "-"],
                    cancel=cancel, capture=True)
    return trim_silence(np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0)


def read_wav_mono(path):
    with wave.open(str(path), "rb") as w:
        sr, n, ch = w.getframerate(), w.getnframes(), w.getnchannels()
        raw = w.readframes(n)
    a = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    return (a.reshape(-1, ch).mean(1) if ch > 1 else a), sr


def write_wav_mono(path, data):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((np.clip(data, -1.0, 1.0) * 32767).astype(np.int16).tobytes())


def compute_level_gains(cues, ref_wav, k=0.6, csv_path=None):
    """Per-cue linear gain so the narrator tracks the original scene loudness,
    measured on the reference channel (center for 5.1, mono downmix for stereo)."""
    GMIN, GMAX, PAD, FRAME_MS, FLOOR = -8.0, 4.0, 0.4, 50, -55.0
    y, sr = read_wav_mono(ref_wav)
    fl = int(FRAME_MS / 1000 * sr)
    lseg = []
    for c in cues:
        a = max(0, int((c.start - PAD) * sr))
        b = min(len(y), int((c.end + PAD) * sr))
        seg = y[a:b]
        if len(seg) < fl:
            lseg.append(None)
            continue
        nfr = len(seg) // fl
        fr = seg[:nfr * fl].reshape(nfr, fl)
        db = 20.0 * np.log10(np.sqrt(np.mean(fr * fr, axis=1)) + 1e-9)
        mx = float(db.max())
        act = db[(db > mx - 25) & (db > FLOOR)]
        lseg.append(float(np.percentile(act, 70)) if act.size else None)
    valid = [x for x in lseg if x is not None]
    lref = float(np.median(valid)) if valid else 0.0
    raw = [0.0 if x is None else max(GMIN, min(GMAX, k * (x - lref))) for x in lseg]
    # median-3 smoothing within a "scene" (consecutive cues with <2s gaps)
    sm = list(raw)
    groups, cur = [], [0]
    for i in range(1, len(cues)):
        if cues[i].start - cues[i - 1].end < 2.0:
            cur.append(i)
        else:
            groups.append(cur)
            cur = [i]
    groups.append(cur)
    for g in groups:
        for j in range(len(g)):
            sm[g[j]] = float(np.median([raw[g[m]] for m in range(max(0, j - 1), min(len(g), j + 2))]))
    if csv_path:
        with open(csv_path, "w", newline="") as f:
            wr = csv.writer(f)
            wr.writerow(["idx", "start", "end", "L_seg_db", "valid", "gain_raw_db", "gain_smoothed_db", "note"])
            for i, c in enumerate(cues):
                note = "silent ref" if lseg[i] is None else ""
                if raw[i] in (GMIN, GMAX):
                    note = (note + " clamped").strip()
                wr.writerow([i, round(c.start, 2), round(c.end, 2),
                             "" if lseg[i] is None else round(lseg[i], 1), lseg[i] is not None,
                             round(raw[i], 2), round(sm[i], 2), note])
    stats = {"L_ref_db": round(lref, 1),
             "gain_min_db": round(min(sm), 2) if sm else 0,
             "gain_max_db": round(max(sm), 2) if sm else 0,
             "silent_ref": sum(1 for x in lseg if x is None)}
    return {cues[i].num: 10.0 ** (sm[i] / 20.0) for i in range(len(cues))}, stats


def build_track(cues, cache_dir, voice, total_s, *, gains=None, max_speed=1.0,
                progress=None, cancel=None):
    """Synthesize every cue and place it on a mono master track.

    gains: {num: linear multiplier} or None. Returns (master float32, stats dict).
    """
    master = np.zeros(int(total_s * SR) + SR, dtype=np.float32)
    cursor, placed, shifted, sped, maxshift = 0.0, 0, 0, 0, 0.0
    for i, c in enumerate(cues):
        if cancel is not None and cancel.is_set():
            raise ffbin.CancelledError("cancelled")
        txt = tts_text(c.text)
        if not txt:
            continue
        mp3 = ensure_clip(cache_dir, txt, voice)
        clip = decode_clip(mp3, cancel=cancel)
        dur = len(clip) / SR
        start = max(c.start, cursor)
        nxt = cues[i + 1].start if i + 1 < len(cues) else 1e9
        slot = nxt - start
        speed = 1.0
        if max_speed > 1.001 and dur > slot > 0.4:
            speed = min(dur / slot, max_speed)
            if speed > 1.001:
                clip = decode_clip(mp3, atempo=speed, cancel=cancel)
                dur = len(clip) / SR
        if gains:
            clip = clip * gains.get(c.num, 1.0)
        s0 = int(start * SR)
        s1 = s0 + len(clip)
        if s1 > len(master):
            master = np.concatenate([master, np.zeros(s1 - len(master) + 1, dtype=np.float32)])
        master[s0:s1] += clip
        cursor = start + dur + GAP
        placed += 1
        maxshift = max(maxshift, start - c.start)
        if start - c.start > 0.3:
            shifted += 1
        if speed > 1.05:
            sped += 1
        if progress:
            progress(i + 1, len(cues), f"line {i + 1}/{len(cues)}")
    if progress:
        progress(len(cues), len(cues), "synthesis done")
    master = master[:int(total_s * SR)]
    peak = float(np.max(np.abs(master))) if master.size else 0.0
    if peak > 1.0:
        master *= 0.98 / peak
    stats = {"cues": len(cues), "placed": placed, "shifted": shifted,
             "sped_up": sped, "max_shift_s": round(maxshift, 1)}
    return master, stats
