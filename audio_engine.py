"""Etapa de mezcla de audio (solo audio): voz + BGM con ducking + whooshes, y loudnorm de dos pasadas (Módulo 5a)."""

import json
import math
import os
import re
from pathlib import Path
from typing import Any, Optional
from rich.console import Console

from config import (
    AUDIO_CHANNELS,
    AUDIO_SAMPLE_RATE,
    BGM_VOLUME,
    DUCK_ATTACK_MS,
    DUCK_RATIO,
    DUCK_RELEASE_MS,
    DUCK_THRESHOLD,
    LOUDNORM_I,
    LOUDNORM_LRA,
    LOUDNORM_TP,
    SAMPLES_PER_FRAME,
    SFX_VOLUME,
    get_ffmpeg_binary,
)
from pipeline_utils import (
    CacheManifest,
    PipelineError,
    RunStats,
    fingerprint,
    run_cmd,
    scene_frames,
    scene_start_frames,
)

console = Console()

BGM_FADE_IN_SEC = 1.0
BGM_FADE_OUT_SEC = 1.0
LOUDNORM_TARGET = f"I={LOUDNORM_I}:TP={LOUDNORM_TP}:LRA={LOUDNORM_LRA}"
_FMT = f"aformat=sample_fmts=fltp:sample_rates={AUDIO_SAMPLE_RATE}:channel_layouts=stereo"
_LOUDNORM_KEYS = ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset")


def parse_loudnorm_json(stderr: str) -> Optional[dict[str, float]]:
    """Extrae el bloque JSON de la pasada 1 de loudnorm desde stderr de ffmpeg.

    Devuelve None si no hay bloque, falta alguna clave o algún valor no es finito (p. ej. "-inf" en silencio).
    """
    if not stderr:
        return None
    blocks = re.findall(r"\{[^{}]*\}", stderr)
    for block in reversed(blocks):
        if "input_i" not in block:
            continue
        try:
            raw = json.loads(block)
            values = {k: float(raw[k]) for k in _LOUDNORM_KEYS}
        except (ValueError, KeyError, TypeError):
            continue
        if all(math.isfinite(v) for v in values.values()):
            return values
    return None


def transition_offsets_samples(scenes: list[dict[str, Any]]) -> list[int]:
    """Muestra de inicio de cada escena desde la 2.ª (donde suena el whoosh); sale de los fotogramas, sin deriva."""
    return [start * SAMPLES_PER_FRAME for start in scene_start_frames(scenes)[1:]]


def build_mix_filtergraph(total_samples: int, offsets_samples: list[int], with_sfx: bool) -> str:
    """Grafo de mezcla. Entradas: 0 = voz (wav concatenado), 1 = BGM, 2 = whoosh (solo si with_sfx)."""
    total_sec = total_samples / AUDIO_SAMPLE_RATE
    fade_out_start = max(total_sec - BGM_FADE_OUT_SEC, 0.5)
    parts = [
        f"[0:a]{_FMT},asplit=2[vmain][vsc]",
        # BGM en bucle, recortada al largo exacto, con fundidos, y comprimida con la voz como cadena lateral
        f"[1:a]{_FMT},aloop=loop=-1:size=2e+09,atrim=end_sample={total_samples},asetpts=PTS-STARTPTS,"
        f"volume={BGM_VOLUME},afade=t=in:ss=0:d={BGM_FADE_IN_SEC},"
        f"afade=t=out:st={fade_out_start:.3f}:d={BGM_FADE_OUT_SEC}[bgm]",
        f"[bgm][vsc]sidechaincompress=threshold={DUCK_THRESHOLD}:ratio={DUCK_RATIO}:"
        f"attack={DUCK_ATTACK_MS}:release={DUCK_RELEASE_MS}[bgmd]",
    ]
    tail = f"apad=whole_len={total_samples},atrim=end_sample={total_samples}"
    if with_sfx and offsets_samples:
        n = len(offsets_samples)
        parts.append(f"[2:a]{_FMT},volume={SFX_VOLUME},asplit={n}" + "".join(f"[w{i}]" for i in range(n)))
        for i, off in enumerate(offsets_samples):
            parts.append(f"[w{i}]adelay={off}S|{off}S[dw{i}]")  # 'S' = muestras: sin redondeo a ms
        parts.append("".join(f"[dw{i}]" for i in range(n)) + f"amix=inputs={n}:dropout_transition=0:normalize=0[sfx]")
        parts.append(f"[vmain][bgmd][sfx]amix=inputs=3:duration=first:dropout_transition=0:normalize=0,{tail}[aout]")
    else:
        parts.append(f"[vmain][bgmd]amix=inputs=2:duration=first:dropout_transition=0:normalize=0,{tail}[aout]")
    return ";".join(parts)


def wav_sample_count(path: Path) -> int:
    """Muestras por canal de un wav PCM o float (lee la cabecera RIFF; el módulo wave no admite float)."""
    with open(path, "rb") as f:
        header = f.read(12)
        if header[:4] != b"RIFF" or header[8:12] != b"WAVE":
            raise PipelineError(f"{path.name} no es un archivo WAV válido.")
        frame_bytes = 0
        while True:
            chunk_header = f.read(8)
            if len(chunk_header) < 8:
                raise PipelineError(f"{path.name}: WAV sin chunk 'data'.")
            chunk_id, size = chunk_header[:4], int.from_bytes(chunk_header[4:], "little")
            if chunk_id == b"fmt ":
                fmt = f.read(size)
                channels = int.from_bytes(fmt[2:4], "little")
                bits = int.from_bytes(fmt[14:16], "little")
                frame_bytes = channels * bits // 8
            elif chunk_id == b"data":
                if not frame_bytes:
                    raise PipelineError(f"{path.name}: WAV con 'data' antes de 'fmt '.")
                if size in (0, 0xFFFFFFFF):  # tamaño no finalizado (salida en streaming)
                    size = path.stat().st_size - f.tell()
                return size // frame_bytes
            else:
                f.seek(size + (size & 1), 1)


def _loudnorm_two_pass(mix_raw: Path, mix_final: Path, total_samples: int, ffmpeg_bin: str) -> None:
    """loudnorm de dos pasadas (medición + corrección lineal); si no se puede medir, una sola pasada con aviso."""
    pass1 = run_cmd(
        [
            ffmpeg_bin, "-hide_banner", "-nostats", "-y", "-i", str(mix_raw),
            "-af", f"loudnorm={LOUDNORM_TARGET}:print_format=json", "-f", "null", "-",
        ],
        "loudnorm (pasada 1: medición)",
    )
    measured = parse_loudnorm_json(pass1.stderr)

    finish = f"aresample={AUDIO_SAMPLE_RATE},apad=whole_len={total_samples},atrim=end_sample={total_samples}"
    if measured is None:
        console.print(
            "[bold yellow]⚠ No se pudo leer la medición de loudnorm (pasada 1); se usa loudnorm de una sola pasada "
            "(menos preciso).[/bold yellow]"
        )
        af = f"loudnorm={LOUDNORM_TARGET},{finish}"
    else:
        console.print(
            f"  [dim]Medición: {measured['input_i']:.1f} LUFS, true peak {measured['input_tp']:.1f} dBTP, "
            f"LRA {measured['input_lra']:.1f} → objetivo {LOUDNORM_I} LUFS[/dim]"
        )
        af = (
            f"loudnorm={LOUDNORM_TARGET}:measured_I={measured['input_i']:.2f}:measured_TP={measured['input_tp']:.2f}:"
            f"measured_LRA={measured['input_lra']:.2f}:measured_thresh={measured['input_thresh']:.2f}:"
            f"offset={measured['target_offset']:.2f}:linear=true,{finish}"
        )

    part = mix_final.with_name(mix_final.stem + ".part.wav")
    run_cmd(
        [
            ffmpeg_bin, "-hide_banner", "-nostats", "-y", "-i", str(mix_raw), "-af", af,
            "-c:a", "pcm_s16le", "-ar", str(AUDIO_SAMPLE_RATE), "-ac", str(AUDIO_CHANNELS), str(part),
        ],
        "loudnorm (pasada 2: normalización)",
    )
    os.replace(part, mix_final)


def mix_audio(
    timing_metadata: dict[str, Any],
    workspace_dir: Path,
    bgm_path: Path,
    whoosh_path: Path,
    manifest: CacheManifest,
    force: bool = False,
    stats: Optional[RunStats] = None,
) -> Path:
    """Genera audio/mix_final.wav (voz + BGM con ducking + whooshes, normalizado a -14 LUFS). Caché por huella."""
    scenes = timing_metadata.get("scenes", [])
    audio_dir = workspace_dir / "audio"
    voice_wavs = [audio_dir / sc.get("wav_file", f"scene_{sc['scene_id']:02d}.wav") for sc in scenes]
    total_frames = sum(scene_frames(sc) for sc in scenes)
    total_samples = total_frames * SAMPLES_PER_FRAME
    offsets = transition_offsets_samples(scenes)
    with_sfx = bool(offsets) and whoosh_path.exists()
    graph = build_mix_filtergraph(total_samples, offsets, with_sfx)

    mix_final = audio_dir / "mix_final.wav"
    fp = fingerprint(
        kind="mix",
        voices=voice_wavs,
        bgm=bgm_path,
        sfx=whoosh_path if with_sfx else None,
        sfx_volume=SFX_VOLUME,
        bgm_volume=BGM_VOLUME,
        duck=[DUCK_THRESHOLD, DUCK_RATIO, DUCK_ATTACK_MS, DUCK_RELEASE_MS],
        whoosh_offsets_samples=offsets if with_sfx else [],
        total_samples=total_samples,
        sample_rate=AUDIO_SAMPLE_RATE,
        channels=AUDIO_CHANNELS,
        loudnorm=LOUDNORM_TARGET,
        graph=graph,
    )
    if not force and manifest.is_fresh(mix_final, fp) and mix_final.stat().st_size > 1000:
        console.print(f"[green]✓ Mezcla de audio en caché:[/] {mix_final}")
        return mix_final

    missing = [p.name for p in voice_wavs if not p.is_file()]
    if missing:
        raise PipelineError(f"Faltan los wav de locución: {', '.join(missing)}. Ejecuta 'generate' de nuevo.")

    ffmpeg_bin = get_ffmpeg_binary()
    console.print(
        f"[cyan]Mezclando audio: voz + BGM con ducking + {len(offsets) if with_sfx else 0} whooshes, "
        f"loudnorm de dos pasadas a {LOUDNORM_I} LUFS...[/cyan]"
    )

    # 1. Voz: concatenación exacta por muestras de los wav por escena
    concat_list = workspace_dir / "audio_concat.txt"
    concat_list.write_text("".join(f"file '{p.as_posix()}'\n" for p in voice_wavs), encoding="utf-8")
    full_voice = workspace_dir / "full_voice.wav"
    run_cmd(
        [ffmpeg_bin, "-hide_banner", "-nostats", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_list),
         "-c:a", "pcm_s16le", str(full_voice)],
        "Concatenación de audio de locución",
    )
    voice_samples = wav_sample_count(full_voice)
    if voice_samples != total_samples:
        raise PipelineError(
            f"La voz concatenada tiene {voice_samples} muestras y se esperaban {total_samples} "
            f"({total_frames} fotogramas)."
        )

    # 2. Mezcla cruda (float: sin recortes antes de normalizar)
    mix_raw = audio_dir / "mix_raw.wav"
    cmd = [ffmpeg_bin, "-hide_banner", "-nostats", "-y", "-i", str(full_voice), "-i", str(bgm_path)]
    if with_sfx:
        cmd += ["-i", str(whoosh_path)]
    cmd += [
        "-filter_complex", graph, "-map", "[aout]",
        "-c:a", "pcm_f32le", "-ar", str(AUDIO_SAMPLE_RATE), "-ac", str(AUDIO_CHANNELS), str(mix_raw),
    ]
    run_cmd(cmd, "Mezcla de audio (voz, BGM con ducking, whooshes)")
    raw_samples = wav_sample_count(mix_raw)
    if raw_samples != total_samples:
        raise PipelineError(f"La mezcla cruda tiene {raw_samples} muestras y se esperaban {total_samples}.")

    # 3. loudnorm de dos pasadas
    _loudnorm_two_pass(mix_raw, mix_final, total_samples, ffmpeg_bin)
    manifest.record(mix_final, fp)
    if stats is not None:
        stats.mix = True
    console.print(f"[bold green]✓ Mezcla de audio lista:[/] {mix_final}")
    return mix_final
