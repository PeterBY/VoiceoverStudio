"""ffmpeg mixing graphs (port of the proven dub_episode.sh / run_variants_51.sh).

Surround sources: duck ONLY the center channel (dialogue) under the narrator,
music/effects untouched. Stereo/mono sources: duck both channels. The stereo
downmix uses normalized pan coefficients + alimiter — a naive sum clips and makes
players cut audio dead the moment the voiceover starts (hard-won gotcha).

Ducking is envelope-driven: the placement stage writes a speech mask (0/1 wav,
built from where clips actually landed); the graph inverts it into a gate key and
sidechaingate's the original: floor = 1/ratio, so depth is deterministic (ratio 2
= -6 dB under speech), no pumping — keying a sidechain compressor off the quiet
edge-tts waveform gave a barely audible ~2 dB. sidechaingate, NOT amultiply: an
amultiply of unequal-length inputs hangs the ffmpeg 9 graph scheduler at EOF on
some shapes (flaky, seen on surround->stereo), while the sidechain* shape has
survived whole movies.
"""
from . import ffbin

DOWNMIX = ("pan=stereo|FL=0.4142*FL+0.2929*FC+0.2929*BL|"
           "FR=0.4142*FR+0.2929*FC+0.2929*BR,alimiter=limit=0.95")

# key BELOW threshold (i.e. speech, after inversion) -> gain drops to `range`
DUCKGATE = "sidechaingate=threshold=0.5:range={g:.4f}:attack=10:release=60:detection=peak"

# apad BEFORE aeval: past the mask's end the padding (0) inverts to key=1 -> gate
# open -> tail untouched
DUCKKEY = "[2:a]aresample=48000,aformat=channel_layouts=mono,apad,aeval=1-val(0)"


def _gate(ratio):
    return DUCKGATE.format(g=1.0 / max(1.0, float(ratio)))


def layout_kind(channels):
    return "surround" if channels >= 6 else "stereo"


def extract_ref(src, audio_type_index, channels, out_wav, cancel=None):
    """Loudness reference for level-tracking: center channel (5.1) or mono downmix."""
    if channels >= 6:
        af = "aformat=channel_layouts=5.1,pan=mono|c0=FC"
    elif channels == 2:
        af = "pan=mono|c0=0.5*c0+0.5*c1"
    else:
        af = "aformat=channel_layouts=mono"
    ffbin.run(["-y", "-v", "error", "-i", str(src), "-map", f"0:a:{audio_type_index}",
               "-af", af, "-ar", "48000", "-ac", "1", "-c:a", "pcm_s16le", str(out_wav)],
              cancel=cancel)


def _graph_surround(aidx, duck, ratio, out_format):
    g = [f"[0:a:{aidx}]aresample=48000,aformat=channel_layouts=5.1,"
         f"channelsplit=channel_layout=5.1[FL][FR][FC][LFE][BL][BR]",
         "[1:a]aresample=48000,aformat=channel_layouts=mono,apad[plc]"]
    if duck:
        g.append(f"{DUCKKEY}[key]")
        g.append(f"[FC][key]{_gate(ratio)}[FCd]")
        fc_in = "[FCd]"
    else:
        fc_in = "[FC]"
    g.append(f"{fc_in}[plc]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.95[FCn]")
    g.append("[FL][FR][FCn][LFE][BL][BR]join=inputs=6:channel_layout=5.1[plmix]")
    if out_format == "original":
        return ";".join(g), "[plmix]", "448k"
    g.append(f"[plmix]{DOWNMIX}[plst]")
    return ";".join(g), "[plst]", "256k"


def _graph_stereo(aidx, duck, ratio):
    g = [f"[0:a:{aidx}]aresample=48000,aformat=channel_layouts=stereo,"
         f"channelsplit=channel_layout=stereo[L][R]",
         "[1:a]aresample=48000,aformat=channel_layouts=mono,apad,asplit=2[p1][p2]"]
    if duck:
        g.append(f"{DUCKKEY},asplit=2[kl][kr]")
        g.append(f"[L][kl]{_gate(ratio)}[Ld]")
        g.append(f"[R][kr]{_gate(ratio)}[Rd]")
        left, right = "[Ld]", "[Rd]"
    else:
        left, right = "[L]", "[R]"
    g.append(f"{left}[p1]amix=inputs=2:duration=first:normalize=0[Lm]")
    g.append(f"{right}[p2]amix=inputs=2:duration=first:normalize=0[Rm]")
    g.append("[Lm][Rm]join=inputs=2:channel_layout=stereo,alimiter=limit=0.95[plst]")
    return ";".join(g), "[plst]", "256k"


def build_dub_track(src, audio_type_index, dub_wav, out_mka, *, channels,
                    duck=True, duck_ratio=2.0, duck_mask=None, out_format="stereo",
                    cancel=None):
    """Mix the narrator WAV over the chosen original track -> AC3 in .mka.

    duck_mask: speech-mask wav from the placement stage (required when duck=True).
    The track is built to a FILE first; muxing is a separate copy step — producing
    it inline with -c:v copy silently truncated audio in the legacy pipeline.
    """
    if duck and duck_mask is None:
        raise ValueError("duck=True needs a duck_mask")
    if layout_kind(channels) == "surround":
        graph, out_lbl, br = _graph_surround(audio_type_index, duck, duck_ratio, out_format)
    else:
        graph, out_lbl, br = _graph_stereo(audio_type_index, duck, duck_ratio)
    cmd = ["-y", "-v", "error", "-i", str(src), "-i", str(dub_wav)]
    if duck:
        cmd += ["-i", str(duck_mask)]
    ffbin.run([*cmd, "-filter_complex", graph, "-map", out_lbl,
               "-c:a", "ac3", "-b:a", br, str(out_mka)],
              cancel=cancel)
