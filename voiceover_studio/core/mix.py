"""ffmpeg mixing graphs (port of the proven dub_episode.sh / run_variants_51.sh).

Surround sources: duck ONLY the center channel (dialogue) under the narrator,
music/effects untouched. Stereo/mono sources: duck both channels. The stereo
downmix uses normalized pan coefficients + alimiter — a naive sum clips and makes
players cut audio dead the moment the voiceover starts (hard-won gotcha).

Ducking itself happens BEFORE the graph: the placement stage writes a bed-gain
envelope (per-cue depth from the gap plan, built from where clips actually
landed) and audio.apply_envelope multiplies the extracted bed (FC for 5.1, the
stereo pair otherwise) by it in numpy. The graph only sums the pre-ducked bed
with the narrator — no gain math inside ffmpeg. History: a sidechain compressor
keyed off the quiet edge-tts waveform gave a barely audible ~2 dB and pumped;
sidechaingate had a deterministic depth but a constant one (no per-cue tracking);
amultiply of unequal-length inputs hangs the ffmpeg 9 graph scheduler at EOF.
Do not move the gain back into the graph.
"""
from . import ffbin

DOWNMIX = ("pan=stereo|FL=0.4142*FL+0.2929*FC+0.2929*BL|"
           "FR=0.4142*FR+0.2929*FC+0.2929*BR,alimiter=limit=0.95")


def layout_kind(channels):
    return "surround" if channels >= 6 else "stereo"


def extract_ref(src, audio_type_index, channels, out_wav, cancel=None):
    """Loudness reference for the gap plan: center channel (5.1) or mono downmix."""
    if channels >= 6:
        af = "aformat=channel_layouts=5.1,pan=mono|c0=FC"
    elif channels == 2:
        af = "pan=mono|c0=0.5*c0+0.5*c1"
    else:
        af = "aformat=channel_layouts=mono"
    ffbin.run(["-y", "-v", "error", "-i", str(src), "-map", f"0:a:{audio_type_index}",
               "-af", af, "-ar", "48000", "-ac", "1", "-c:a", "pcm_s16le", str(out_wav)],
              cancel=cancel)


def extract_bed(src, audio_type_index, channels, out_wav, cancel=None):
    """The channels the duck envelope applies to, as PCM: center (5.1) or the
    full stereo pair."""
    if channels >= 6:
        af, ac = "aformat=channel_layouts=5.1,pan=mono|c0=FC", 1
    else:
        af, ac = "aformat=channel_layouts=stereo", 2
    ffbin.run(["-y", "-v", "error", "-i", str(src), "-map", f"0:a:{audio_type_index}",
               "-af", af, "-ar", "48000", "-ac", str(ac), "-c:a", "pcm_s16le", str(out_wav)],
              cancel=cancel)


def _graph_surround(aidx, ducked, out_format):
    """ducked: the pre-ducked FC arrives as input 2; the source's own FC is not
    split out at all. Otherwise FC comes straight from the source."""
    if ducked:
        g = [f"[0:a:{aidx}]aresample=48000,aformat=channel_layouts=5.1,"
             f"channelsplit=channel_layout=5.1:channels=FL+FR+LFE+BL+BR[FL][FR][LFE][BL][BR]",
             "[2:a]aresample=48000,aformat=channel_layouts=mono[FC]"]
    else:
        g = [f"[0:a:{aidx}]aresample=48000,aformat=channel_layouts=5.1,"
             f"channelsplit=channel_layout=5.1[FL][FR][FC][LFE][BL][BR]"]
    g.append("[1:a]aresample=48000,aformat=channel_layouts=mono,apad[plc]")
    g.append("[FC][plc]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.95[FCn]")
    g.append("[FL][FR][FCn][LFE][BL][BR]join=inputs=6:channel_layout=5.1[plmix]")
    if out_format == "original":
        return ";".join(g), "[plmix]", "448k"
    g.append(f"[plmix]{DOWNMIX}[plst]")
    return ";".join(g), "[plst]", "256k"


def _graph_stereo(aidx, ducked):
    """ducked: input 0 IS the pre-ducked stereo bed file (the source track isn't
    an input at all). Otherwise input 0 is the source."""
    src_lbl = "[0:a]" if ducked else f"[0:a:{aidx}]"
    g = [f"{src_lbl}aresample=48000,aformat=channel_layouts=stereo,"
         f"channelsplit=channel_layout=stereo[L][R]",
         "[1:a]aresample=48000,aformat=channel_layouts=mono,apad,asplit=2[p1][p2]"]
    g.append("[L][p1]amix=inputs=2:duration=first:normalize=0[Lm]")
    g.append("[R][p2]amix=inputs=2:duration=first:normalize=0[Rm]")
    g.append("[Lm][Rm]join=inputs=2:channel_layout=stereo,alimiter=limit=0.95[plst]")
    return ";".join(g), "[plst]", "256k"


def build_dub_track(src, audio_type_index, dub_wav, out_mka, *, channels,
                    bed_wav=None, out_format="stereo", cancel=None):
    """Mix the narrator WAV over the chosen original track -> AC3 in .mka.

    bed_wav: pre-ducked bed from audio.apply_envelope (FC mono for surround, the
    stereo pair for stereo); None = no ducking.
    The track is built to a FILE first; muxing is a separate copy step — producing
    it inline with -c:v copy silently truncated audio in the legacy pipeline.
    """
    if layout_kind(channels) == "surround":
        graph, out_lbl, br = _graph_surround(audio_type_index, bed_wav is not None, out_format)
        inputs = [str(src), str(dub_wav)] + ([str(bed_wav)] if bed_wav else [])
    else:
        graph, out_lbl, br = _graph_stereo(audio_type_index, bed_wav is not None)
        inputs = [str(bed_wav) if bed_wav else str(src), str(dub_wav)]
    cmd = ["-y", "-v", "error"]
    for i in inputs:
        cmd += ["-i", i]
    ffbin.run([*cmd, "-filter_complex", graph, "-map", out_lbl,
               "-c:a", "ac3", "-b:a", br, str(out_mka)],
              cancel=cancel)
