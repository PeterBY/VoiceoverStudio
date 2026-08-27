"""ffmpeg mixing graphs (port of the proven dub_episode.sh / run_variants_51.sh).

Surround sources: duck ONLY the center channel (dialogue) under the narrator,
music/effects untouched. Stereo/mono sources: duck both channels. The stereo
downmix uses normalized pan coefficients + alimiter — a naive sum clips and makes
players cut audio dead the moment the voiceover starts (hard-won gotcha).
"""
from . import ffbin

DUCK = "sidechaincompress=threshold=0.03:ratio={r}:attack=5:release=300"
DOWNMIX = ("pan=stereo|FL=0.4142*FL+0.2929*FC+0.2929*BL|"
           "FR=0.4142*FR+0.2929*FC+0.2929*BR,alimiter=limit=0.95")


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
    duckf = DUCK.format(r=ratio)
    g = [f"[0:a:{aidx}]aresample=48000,aformat=channel_layouts=5.1,"
         f"channelsplit=channel_layout=5.1[FL][FR][FC][LFE][BL][BR]"]
    if duck:
        g.append("[1:a]aresample=48000,aformat=channel_layouts=mono,apad,asplit=2[sck][plc]")
        g.append(f"[FC][sck]{duckf}[FCd]")
        fc_in = "[FCd]"
    else:
        g.append("[1:a]aresample=48000,aformat=channel_layouts=mono,apad[plc]")
        fc_in = "[FC]"
    g.append(f"{fc_in}[plc]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.95[FCn]")
    g.append("[FL][FR][FCn][LFE][BL][BR]join=inputs=6:channel_layout=5.1[plmix]")
    if out_format == "original":
        return ";".join(g), "[plmix]", "448k"
    g.append(f"[plmix]{DOWNMIX}[plst]")
    return ";".join(g), "[plst]", "256k"


def _graph_stereo(aidx, duck, ratio):
    duckf = DUCK.format(r=ratio)
    g = [f"[0:a:{aidx}]aresample=48000,aformat=channel_layouts=stereo,"
         f"channelsplit=channel_layout=stereo[L][R]"]
    if duck:
        g.append("[1:a]aresample=48000,aformat=channel_layouts=mono,apad,asplit=4[sl][sr][p1][p2]")
        g.append(f"[L][sl]{duckf}[Ld]")
        g.append(f"[R][sr]{duckf}[Rd]")
        left, right = "[Ld]", "[Rd]"
    else:
        g.append("[1:a]aresample=48000,aformat=channel_layouts=mono,apad,asplit=2[p1][p2]")
        left, right = "[L]", "[R]"
    g.append(f"{left}[p1]amix=inputs=2:duration=first:normalize=0[Lm]")
    g.append(f"{right}[p2]amix=inputs=2:duration=first:normalize=0[Rm]")
    g.append("[Lm][Rm]join=inputs=2:channel_layout=stereo,alimiter=limit=0.95[plst]")
    return ";".join(g), "[plst]", "256k"


def build_dub_track(src, audio_type_index, dub_wav, out_mka, *, channels,
                    duck=True, duck_ratio=2.0, out_format="stereo", cancel=None):
    """Mix the narrator WAV over the chosen original track -> AC3 in .mka.

    The track is built to a FILE first; muxing is a separate copy step — producing
    it inline with -c:v copy silently truncated audio in the legacy pipeline.
    """
    if layout_kind(channels) == "surround":
        graph, out_lbl, br = _graph_surround(audio_type_index, duck, duck_ratio, out_format)
    else:
        graph, out_lbl, br = _graph_stereo(audio_type_index, duck, duck_ratio)
    ffbin.run(["-y", "-v", "error", "-i", str(src), "-i", str(dub_wav),
               "-filter_complex", graph, "-map", out_lbl,
               "-c:a", "ac3", "-b:a", br, str(out_mka)],
              cancel=cancel)
