"""PCM math: clip decode/trim, timecode placement, loudness gap plan, WAV IO.

Straight port of the proven build_dub.py (numpy float32 mono @48k). Placement is
shift-only by default (max_speed=1.0): a clip never overlaps the next cue's start;
drift resets at pauses.

Leveling is a gap plan: one target (`gap_db`) — how far the narrator sits above
the scene bed under speech — reached by attenuation only. Per cue, gap = V - S
(narrator clip level minus scene level): a shortfall ducks the bed, an excess
cuts the narrator. Depths ride to the mixer as a bed-gain envelope; the bed
itself is multiplied here in numpy (variable per-cue depth can't be a
sidechaingate constant, and ffmpeg-side gain math is where the EOF races lived).
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
MASK_SR = 8000  # duck envelope rate: a slow envelope doesn't need 48k (file is 6x smaller)

MAX_VO_CUT_DB = 12.0   # narrator never cut deeper than this below its natural level
# duck depth is deliberately unclamped: the gap knob itself is the limit
LEVEL_FLOOR_DB = -55.0  # frames below this are silence for level measurement


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


def write_wav_mono(path, data, sr=SR):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((np.clip(data, -1.0, 1.0) * 32767).astype(np.int16).tobytes())


def _active_level(seg, sr, frame_ms=50):
    """Speech-ish level of a segment: 70th percentile of the active 50 ms RMS
    frames (within 25 dB of the loudest, above the silence floor), in dBFS.
    None if the segment is silent/too short."""
    fl = int(frame_ms / 1000 * sr)
    if len(seg) < fl:
        return None
    nfr = len(seg) // fl
    fr = seg[:nfr * fl].reshape(nfr, fl)
    db = 20.0 * np.log10(np.sqrt(np.mean(fr * fr, axis=1)) + 1e-9)
    act = db[(db > float(db.max()) - 25) & (db > LEVEL_FLOOR_DB)]
    return float(np.percentile(act, 70)) if act.size else None


def clip_level(mp3, cancel=None):
    """Active level of a TTS clip (dBFS), cached in a sidecar .lvl beside the mp3
    (the mp3 path is content-keyed, so the sidecar never goes stale)."""
    lvl = Path(str(mp3) + ".lvl")
    try:
        return float(lvl.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    v = _active_level(decode_clip(mp3, cancel=cancel), SR)
    v = -30.0 if v is None else v  # a silent clip is pathological; assume a typical level
    try:
        lvl.write_text(f"{v:.2f}", encoding="utf-8")
    except OSError:
        pass
    return v


def _scene_groups(cues):
    """Indexes grouped into scenes: consecutive cues with gaps < 2 s."""
    groups, cur = [], [0]
    for i in range(1, len(cues)):
        if cues[i].start - cues[i - 1].end < 2.0:
            cur.append(i)
        else:
            groups.append(cur)
            cur = [i]
    groups.append(cur)
    return groups


def compute_gap_plan(cues, ref_wav, cache_dir, voice, *, gap_db=8.0,
                     csv_path=None, progress=None, cancel=None):
    """Per-cue narrator gains + bed duck depths for one target: the narrator sits
    `gap_db` above the scene bed under speech, by attenuation only.

      gap = V - S;  gap < target -> duck the bed by the shortfall (unclamped)
                    gap > target -> cut the narrator by the excess (<= MAX_VO_CUT_DB)

    S: active level of the reference channel in the cue window (center for 5.1,
    mono downmix for stereo); V: active level of the cue's TTS clip (this is the
    synthesis pass — placement then hits the clip cache). A silent reference
    reads as the floor, so the narrator drops to the max cut over near-silence.
    The measured gap is median-3 smoothed within scenes (cue gaps < 2 s) before
    the split, so single-line jitter doesn't wobble the mix.

    Returns ({num: linear narrator gain}, {num: duck_db}, stats).
    """
    y, sr = read_wav_mono(ref_wav)
    PAD = 0.4
    spoken = [c for c in cues if tts_text(c.text)]
    if not spoken:
        return {}, {}, {"cues": 0}
    S, raw = [], []
    for i, c in enumerate(spoken):
        if cancel is not None and cancel.is_set():
            raise ffbin.CancelledError("cancelled")
        a = max(0, int((c.start - PAD) * sr))
        b = min(len(y), int((c.end + PAD) * sr))
        s = _active_level(y[a:b], sr)
        v = clip_level(ensure_clip(cache_dir, tts_text(c.text), voice), cancel=cancel)
        S.append(s)
        raw.append(v - (s if s is not None else LEVEL_FLOOR_DB))
        if progress:
            progress(i + 1, len(spoken), f"line {i + 1}/{len(spoken)}")
    sm = list(raw)
    for g in _scene_groups(spoken):
        for j in range(len(g)):
            sm[g[j]] = float(np.median([raw[g[m]] for m in range(max(0, j - 1), min(len(g), j + 2))]))
    gains, ducks = {}, {}
    vo_cuts, duck_dbs = [], []
    for i, c in enumerate(spoken):
        vo_cut = min(max(sm[i] - gap_db, 0.0), MAX_VO_CUT_DB)
        duck = max(gap_db - sm[i], 0.0)
        gains[c.num] = 10.0 ** (-vo_cut / 20.0)
        ducks[c.num] = round(duck, 2)
        vo_cuts.append(vo_cut)
        duck_dbs.append(duck)
    if csv_path:
        with open(csv_path, "w", newline="") as f:
            wr = csv.writer(f)
            wr.writerow(["num", "start", "end", "S_db", "V_db", "gap_db", "gap_sm_db",
                         "vo_cut_db", "duck_db", "note"])
            for i, c in enumerate(spoken):
                note = "silent ref" if S[i] is None else ""
                if vo_cuts[i] >= MAX_VO_CUT_DB:
                    note = (note + " clamped").strip()
                wr.writerow([c.num, round(c.start, 2), round(c.end, 2),
                             "" if S[i] is None else round(S[i], 1),
                             round(raw[i] + (S[i] if S[i] is not None else LEVEL_FLOOR_DB), 1),
                             round(raw[i], 1), round(sm[i], 1),
                             round(vo_cuts[i], 2), round(duck_dbs[i], 2), note])
    stats = {"gap_target_db": gap_db,
             "median_gap_db": round(float(np.median(sm)), 1),
             "vo_cut_mean_db": round(float(np.mean(vo_cuts)), 2),
             "vo_cut_max_db": round(float(np.max(vo_cuts)), 2),
             "duck_mean_db": round(float(np.mean(duck_dbs)), 2),
             "duck_max_db": round(float(np.max(duck_dbs)), 2),
             "silent_ref": sum(1 for s in S if s is None)}
    return gains, ducks, stats


def compute_level_gains(cues, ref_wav, k=0.6, csv_path=None):
    """0.1.0 'Legacy' leveling, kept verbatim for A/B listening: per-cue narrator
    gain = k * (scene level - episode median), clamped [-8, +4] dB, median-3
    smoothed within scenes. Relative by design: a quiet scene lowers the narrator
    only as far as it sits under the episode median (the gap plan replaced this)."""
    GMIN, GMAX, PAD = -8.0, 4.0, 0.4
    y, sr = read_wav_mono(ref_wav)
    lseg = []
    for c in cues:
        a = max(0, int((c.start - PAD) * sr))
        b = min(len(y), int((c.end + PAD) * sr))
        lseg.append(_active_level(y[a:b], sr))
    valid = [x for x in lseg if x is not None]
    lref = float(np.median(valid)) if valid else 0.0
    raw = [0.0 if x is None else max(GMIN, min(GMAX, k * (x - lref))) for x in lseg]
    sm = list(raw)
    for g in _scene_groups(cues):
        for j in range(len(g)):
            sm[g[j]] = float(np.median([raw[g[m]] for m in range(max(0, j - 1), min(len(g), j + 2))]))
    if csv_path:
        with open(csv_path, "w", newline="") as f:
            wr = csv.writer(f)
            wr.writerow(["num", "start", "end", "L_seg_db", "gain_raw_db", "gain_smoothed_db", "note"])
            for i, c in enumerate(cues):
                note = "silent ref" if lseg[i] is None else ""
                if raw[i] in (GMIN, GMAX):
                    note = (note + " clamped").strip()
                wr.writerow([c.num, round(c.start, 2), round(c.end, 2),
                             "" if lseg[i] is None else round(lseg[i], 1),
                             round(raw[i], 2), round(sm[i], 2), note])
    stats = {"L_ref_db": round(lref, 1),
             "gain_min_db": round(min(sm), 2) if sm else 0,
             "gain_max_db": round(max(sm), 2) if sm else 0,
             "silent_ref": sum(1 for x in lseg if x is None)}
    return {cues[i].num: 10.0 ** (sm[i] / 20.0) for i in range(len(cues))}, stats


def _gain_envelope(items, total_s, ramp_in=0.15, ramp_out=0.3):
    """Bed-gain envelope @MASK_SR: 1.0 outside narrator speech, 10^(-duck/20)
    under it, linear ramps at the edges; overlaps keep the deeper duck. Built
    from ACTUAL clip placements, not cue times — shifted clips duck where the
    voice really is."""
    n = int(total_s * MASK_SR)
    env = np.ones(n, dtype=np.float32)
    ri, ro = int(ramp_in * MASK_SR), int(ramp_out * MASK_SR)
    for a, b, duck_db in items:
        g = 10.0 ** (-float(duck_db) / 20.0)
        i0, i1 = int(a * MASK_SR), min(n, int(b * MASK_SR))
        if g >= 0.999 or i1 <= i0:
            continue
        j0, j1 = max(0, i0 - ri), min(n, i1 + ro)
        cand = np.full(j1 - j0, g, dtype=np.float32)
        if i0 > j0:
            cand[:i0 - j0] = np.linspace(1.0, g, i0 - j0, endpoint=False)
        if j1 > i1:
            cand[i1 - j0:] = np.linspace(g, 1.0, j1 - i1, endpoint=False)
        env[j0:j1] = np.minimum(env[j0:j1], cand)
    return env


def apply_envelope(bed_wav, env_wav, out_wav, cancel=None):
    """Multiply the bed PCM by the duck envelope (env @MASK_SR, upsampled x6),
    chunked so a movie-length bed never sits in RAM. Past the envelope's end the
    gain is 1.0 (tail untouched). Pure numpy on purpose: per-cue depth needs a
    variable gain, which sidechaingate can't do and amultiply must not do."""
    env, esr = read_wav_mono(env_wav)
    rep = SR // int(esr)
    with wave.open(str(bed_wav), "rb") as wi, wave.open(str(out_wav), "wb") as wo:
        ch = wi.getnchannels()
        wo.setnchannels(ch)
        wo.setsampwidth(2)
        wo.setframerate(wi.getframerate())
        BLK = SR * 30  # frames per chunk; multiple of `rep` keeps env indexing aligned
        pos = 0
        while True:
            if cancel is not None and cancel.is_set():
                raise ffbin.CancelledError("cancelled")
            raw = wi.readframes(BLK)
            if not raw:
                break
            x = np.frombuffer(raw, dtype=np.int16).astype(np.float32).reshape(-1, ch)
            e0, need = pos // rep, -(-len(x) // rep)
            eseg = env[e0:e0 + need]
            if len(eseg) < need:
                eseg = np.concatenate([eseg, np.ones(need - len(eseg), dtype=np.float32)])
            gain = np.repeat(eseg, rep)[:len(x)]
            wo.writeframes(np.clip(x * gain[:, None], -32768, 32767).astype(np.int16).tobytes())
            pos += len(x)


def build_track(cues, cache_dir, voice, total_s, *, gains=None, ducks=None,
                max_speed=1.0, progress=None, cancel=None):
    """Synthesize every cue and place it on a mono master track.

    gains: {num: linear multiplier} or None. ducks: {num: duck_db} or None.
    Returns (master float32, bed-gain envelope float32 @MASK_SR, stats dict).
    """
    master = np.zeros(int(total_s * SR) + SR, dtype=np.float32)
    speech = []  # (start_s, end_s, duck_db) of each placed clip, for the envelope
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
        speech.append((start, start + dur, ducks.get(c.num, 0.0) if ducks else 0.0))
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
    return master, _gain_envelope(speech, total_s), stats
