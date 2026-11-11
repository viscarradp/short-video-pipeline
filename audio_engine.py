"""Etapa de mezcla de audio (solo audio): voz + BGM con ducking + whooshes, y loudnorm de dos pasadas (Módulo 5a)."""

import hashlib
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
    BGM_GAIN_LIMITS_DB,
    BGM_RELATIVE_DB,
    BGM_VOLUME,
    DUCK_ATTACK_MS,
    DUCK_RATIO,
    DUCK_RELEASE_MS,
    DUCK_THRESHOLD,
    LOUDNORM_I,
    LOUDNORM_LRA,
    LOUDNORM_TP,
    MUSIC_DIR,
    MUSIC_EXTENSIONS,
    SAMPLES_PER_FRAME,
    SFX_VOLUME,
    get_ffmpeg_binary,
)
from pipeline_utils import (
    CacheManifest,
    PipelineError,
    RunStats,
    file_sha256,
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


SFX_SINGLE_GRAPH_MAX = 40  # hasta tantos whooshes, un único amix (el grafo de siempre: no invalida mezclas en caché)
SFX_BATCH_SIZE = 20  # con más cortes (videos de 100+ escenas), whooshes por grupo que luego se suman en un segundo amix


def transition_offsets_samples(scenes: list[dict[str, Any]]) -> list[int]:
    """Muestra de inicio de cada escena desde la 2.ª (donde suena el whoosh); sale de los fotogramas, sin deriva."""
    return [start * SAMPLES_PER_FRAME for start in scene_start_frames(scenes)[1:]]


def build_mix_filtergraph(
    total_samples: int, offsets_samples: list[int], with_sfx: bool, bgm_gain: float = BGM_VOLUME
) -> str:
    """Grafo de mezcla. Entradas: 0 = voz (wav concatenado), 1 = BGM, 2 = whoosh (solo si with_sfx).

    `bgm_gain` es la ganancia LINEAL de la BGM (antes del ducking), calculada por `resolve_bgm_gain`.
    """
    total_sec = total_samples / AUDIO_SAMPLE_RATE
    fade_out_start = max(total_sec - BGM_FADE_OUT_SEC, 0.5)
    parts = [
        f"[0:a]{_FMT},asplit=2[vmain][vsc]",
        # BGM en bucle, recortada al largo exacto, con fundidos, y comprimida con la voz como cadena lateral
        f"[1:a]{_FMT},aloop=loop=-1:size=2e+09,atrim=end_sample={total_samples},asetpts=PTS-STARTPTS,"
        f"volume={bgm_gain:.4f},afade=t=in:ss=0:d={BGM_FADE_IN_SEC},"
        f"afade=t=out:st={fade_out_start:.3f}:d={BGM_FADE_OUT_SEC}[bgm]",
        f"[bgm][vsc]sidechaincompress=threshold={DUCK_THRESHOLD}:ratio={DUCK_RATIO}:"
        f"attack={DUCK_ATTACK_MS}:release={DUCK_RELEASE_MS}[bgmd]",
    ]
    tail = f"apad=whole_len={total_samples},atrim=end_sample={total_samples}"
    if with_sfx and offsets_samples:
        n = len(offsets_samples)
        if n <= SFX_SINGLE_GRAPH_MAX:
            parts.append(f"[2:a]{_FMT},volume={SFX_VOLUME},asplit={n}" + "".join(f"[w{i}]" for i in range(n)))
            for i, off in enumerate(offsets_samples):
                parts.append(f"[w{i}]adelay={off}S|{off}S[dw{i}]")  # 'S' = muestras: sin redondeo a ms
            parts.append("".join(f"[dw{i}]" for i in range(n)) + f"amix=inputs={n}:dropout_transition=0:normalize=0[sfx]")
        else:
            # Videos largos (100+ cortes): en vez de un único amix de n entradas, grupos de SFX_BATCH_SIZE whooshes que se
            # suman en un segundo amix (normalize=0 = suma, así que el resultado es el mismo)
            groups = [offsets_samples[g : g + SFX_BATCH_SIZE] for g in range(0, n, SFX_BATCH_SIZE)]
            parts.append(f"[2:a]{_FMT},volume={SFX_VOLUME},asplit={len(groups)}" + "".join(f"[wg{g}]" for g in range(len(groups))))
            index = 0
            for g, group in enumerate(groups):
                parts.append(f"[wg{g}]asplit={len(group)}" + "".join(f"[w{index + k}]" for k in range(len(group))))
                for k, off in enumerate(group):
                    parts.append(f"[w{index + k}]adelay={off}S|{off}S[dw{index + k}]")
                parts.append(
                    "".join(f"[dw{index + k}]" for k in range(len(group)))
                    + f"amix=inputs={len(group)}:dropout_transition=0:normalize=0[sg{g}]"
                )
                index += len(group)
            parts.append("".join(f"[sg{g}]" for g in range(len(groups))) + f"amix=inputs={len(groups)}:dropout_transition=0:normalize=0[sfx]")
        parts.append(f"[vmain][bgmd][sfx]amix=inputs=3:duration=first:dropout_transition=0:normalize=0,{tail}[aout]")
    else:
        parts.append(f"[vmain][bgmd]amix=inputs=2:duration=first:dropout_transition=0:normalize=0,{tail}[aout]")
    return ";".join(parts)


# --------------------------------------------------------------------------- #
# Biblioteca de música y nivel de la BGM independiente de la sonoridad de la pista
# --------------------------------------------------------------------------- #

def list_music_tracks(folder: Path) -> list[Path]:
    """Pistas de audio (mp3/m4a/wav) directamente dentro de `folder`, ordenadas por nombre (no recursivo)."""
    if not folder.is_dir():
        return []
    tracks = [p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in MUSIC_EXTENSIONS]
    return sorted(tracks, key=lambda p: p.name.casefold())


def select_library_track(slug: str, profile_name: str, music_dir: Path = MUSIC_DIR) -> Optional[Path]:
    """Elige una pista de la biblioteca de forma determinista según el hash del slug.

    Primero la subcarpeta del perfil; si no tiene pistas, el pool compartido (raíz de assets/music/). None si no hay.
    """
    candidates = list_music_tracks(music_dir / profile_name) or list_music_tracks(music_dir)
    if not candidates:
        return None
    index = int(hashlib.sha256(slug.encode("utf-8")).hexdigest()[:8], 16) % len(candidates)
    return candidates[index]


def _load_loudness_cache(path: Path) -> dict[str, float]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return {str(k): float(v) for k, v in raw.items()} if isinstance(raw, dict) else {}
    except Exception:
        return {}


def _save_loudness_cache(path: Path, data: dict[str, float]) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def measure_integrated_loudness(input_args: list[str], desc: str, ffmpeg_bin: str) -> Optional[float]:
    """LUFS integrados de una entrada (loudnorm en modo medición, sin escribir nada). None si no se puede medir."""
    res = run_cmd(
        [
            ffmpeg_bin, "-hide_banner", "-nostats", "-y", *input_args,
            "-af", f"loudnorm={LOUDNORM_TARGET}:print_format=json", "-f", "null", "-",
        ],
        desc,
    )
    measured = parse_loudnorm_json(res.stderr)
    return None if measured is None else measured["input_i"]


def compute_bgm_gain(voice_lufs: float, music_lufs: float, relative_db: float = BGM_RELATIVE_DB) -> tuple[float, float]:
    """(ganancia en dB, ganancia lineal) para que la música sin ducking quede `relative_db` dB bajo la voz."""
    lo, hi = BGM_GAIN_LIMITS_DB
    gain_db = min(max((voice_lufs + relative_db) - music_lufs, lo), hi)
    return gain_db, round(10 ** (gain_db / 20.0), 4)


def resolve_bgm_gain(
    voice_concat_list: Path,
    voice_wavs: list[Path],
    bgm_path: Path,
    cache_path: Path,
    ffmpeg_bin: str,
) -> tuple[float, Optional[float], Optional[float], Optional[float]]:
    """Mide (con caché en `cache_path`) la voz y la música y devuelve (ganancia lineal, dB, LUFS voz, LUFS música).

    Claves de caché: hash del contenido de la música / hash combinado de los wav de voz. Si algo no se puede medir,
    devuelve la ganancia fija BGM_VOLUME con dB en None (y un aviso).
    """
    cache = _load_loudness_cache(cache_path)
    dirty = False

    music_key = f"music:{file_sha256(bgm_path)}"
    music_lufs = cache.get(music_key)
    if music_lufs is None:
        music_lufs = measure_integrated_loudness(["-i", str(bgm_path)], f"Medición de sonoridad de {bgm_path.name}", ffmpeg_bin)
        if music_lufs is not None:
            cache[music_key], dirty = music_lufs, True

    voice_key = "voice:" + hashlib.sha256("|".join(file_sha256(p) for p in voice_wavs).encode()).hexdigest()
    voice_lufs = cache.get(voice_key)
    if voice_lufs is None:
        voice_lufs = measure_integrated_loudness(
            ["-f", "concat", "-safe", "0", "-i", str(voice_concat_list)], "Medición de sonoridad de la voz", ffmpeg_bin
        )
        if voice_lufs is not None:
            cache[voice_key], dirty = voice_lufs, True

    if dirty:
        _save_loudness_cache(cache_path, cache)

    if music_lufs is None or voice_lufs is None:
        console.print(
            f"[bold yellow]⚠ No se pudo medir la sonoridad de la {'música' if music_lufs is None else 'voz'}; "
            f"se usa una ganancia fija de BGM ({BGM_VOLUME}).[/bold yellow]"
        )
        return BGM_VOLUME, None, voice_lufs, music_lufs
    gain_db, gain_lin = compute_bgm_gain(voice_lufs, music_lufs)
    return gain_lin, gain_db, voice_lufs, music_lufs


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

    missing = [p.name for p in voice_wavs if not p.is_file()]
    if missing:
        raise PipelineError(f"Faltan los wav de locución: {', '.join(missing)}. Ejecuta 'generate' de nuevo.")
    if not bgm_path.is_file():
        raise PipelineError(f"No existe el archivo de música: {bgm_path}")

    ffmpeg_bin = get_ffmpeg_binary()

    # Ganancia de la BGM: se mide la sonoridad de la música y de la voz (con caché) para dejar la música sin ducking
    # BGM_RELATIVE_DB dB bajo la voz, sea cual sea la sonoridad con que se masterizó la pista.
    concat_list = workspace_dir / "audio_concat.txt"
    concat_list.write_text("".join(f"file '{p.as_posix()}'\n" for p in voice_wavs), encoding="utf-8")
    bgm_gain, gain_db, voice_lufs, music_lufs = resolve_bgm_gain(
        concat_list, voice_wavs, bgm_path, audio_dir / "loudness_cache.json", ffmpeg_bin
    )
    if gain_db is not None:
        console.print(
            f"  [dim]Música: {music_lufs:.1f} LUFS | voz: {voice_lufs:.1f} LUFS → ganancia BGM {gain_db:+.1f} dB "
            f"(x{bgm_gain}); objetivo: {BGM_RELATIVE_DB:+g} dB respecto a la voz[/dim]"
        )
    graph = build_mix_filtergraph(total_samples, offsets, with_sfx, bgm_gain)

    mix_final = audio_dir / "mix_final.wav"
    fp = fingerprint(
        kind="mix",
        voices=voice_wavs,
        bgm=bgm_path,
        sfx=whoosh_path if with_sfx else None,
        sfx_volume=SFX_VOLUME,
        bgm_gain=bgm_gain,
        bgm_relative_db=BGM_RELATIVE_DB,
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

    console.print(
        f"[cyan]Mezclando audio: voz + BGM con ducking + {len(offsets) if with_sfx else 0} whooshes, "
        f"loudnorm de dos pasadas a {LOUDNORM_I} LUFS...[/cyan]"
    )

    # 1. Voz: concatenación exacta por muestras de los wav por escena
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
