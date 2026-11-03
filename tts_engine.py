"""Voice and precise timing engine (Módulo 2)."""

import asyncio
import json
from pathlib import Path
from typing import Any, Optional
from rich.console import Console

import edge_tts
from config import get_ffprobe_binary
from pipeline_utils import is_stale, run_cmd
from profiles import VideoProfile
from script_engine import VideoProjectSchema

console = Console()


def get_audio_duration_seconds(audio_path: Path) -> float:
    """Measure exact duration of an audio file using ffprobe."""
    ffprobe_bin = get_ffprobe_binary()
    cmd = [
        ffprobe_bin,
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(audio_path),
    ]
    res = run_cmd(cmd, f"ffprobe de {audio_path.name}")
    return float(res.stdout.strip())


def load_words_sidecar(words_path: Path) -> list[dict[str, Any]]:
    """Carga los tiempos por palabra guardados; devuelve [] si falta o es inválido."""
    if not words_path.exists():
        return []
    try:
        with open(words_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


async def generate_single_scene_audio(
    text: str,
    voice: str,
    rate: str,
    output_mp3: Path,
    max_retries: int = 4,
) -> list[dict[str, Any]]:
    """Synthesize speech using edge-tts with automatic retry and capture precise WordBoundary timing."""
    last_error: Optional[Exception] = None

    for attempt in range(1, max_retries + 1):
        try:
            try:
                communicate = edge_tts.Communicate(
                    text=text,
                    voice=voice,
                    rate=rate,
                    boundary="WordBoundary",
                )
            except TypeError:
                # Versiones antiguas de edge-tts no aceptan 'boundary' (ya emiten WordBoundary)
                communicate = edge_tts.Communicate(text=text, voice=voice, rate=rate)

            words: list[dict[str, Any]] = []
            audio_data = bytearray()

            async for chunk in communicate.stream():
                chunk_type = chunk.get("type")
                if chunk_type == "audio":
                    audio_data.extend(chunk.get("data", b""))
                elif chunk_type == "WordBoundary":
                    raw_offset = chunk.get("offset", 0)
                    raw_duration = chunk.get("duration", 0)
                    word_text = chunk.get("text", "").strip()

                    start_ms = round(raw_offset / 10_000.0)
                    duration_ms = round(raw_duration / 10_000.0)
                    end_ms = start_ms + duration_ms

                    if word_text:
                        words.append({
                            "word": word_text,
                            "start_ms": start_ms,
                            "end_ms": end_ms,
                            "duration_ms": duration_ms,
                        })

            if audio_data:
                # Save audio file
                with open(output_mp3, "wb") as f:
                    f.write(audio_data)
                return words
            else:
                raise RuntimeError("No se recibieron bytes de audio")

        except Exception as err:
            last_error = err
            wait_time = 0.5 * attempt
            await asyncio.sleep(wait_time)

    raise RuntimeError(f"Fallo en síntesis TTS tras {max_retries} intentos: {last_error}")


async def generate_all_scene_audios(
    project: VideoProjectSchema,
    profile: VideoProfile,
    output_dir: Path,
    voice_override: Optional[str] = None,
    force: bool = False,
) -> dict[str, Any]:
    """Generate all scene audio files with word boundary metadata and caching."""
    audio_dir = output_dir / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    metadata_path = audio_dir / "timing_metadata.json"
    script_path = output_dir / "script.json"

    voice = voice_override if voice_override else profile.default_voice
    rate = profile.voice_rate

    # Check cache
    if metadata_path.exists() and not force and not is_stale(metadata_path, script_path):
        try:
            with open(metadata_path, "r", encoding="utf-8") as f:
                cached_data = json.load(f)
            # Verify all scene files exist
            all_exist = True
            for scene_meta in cached_data.get("scenes", []):
                scene_audio = audio_dir / scene_meta["audio_file"]
                if not scene_audio.exists() or scene_audio.stat().st_size == 0:
                    all_exist = False
                    break
            if all_exist and len(cached_data.get("scenes", [])) == len(project.scenes):
                console.print(f"[green]✓ Reutilizando audios y timing en caché:[/] {audio_dir}")
                return cached_data
        except Exception:
            pass

    console.print(f"[cyan]Sintetizando locución para {len(project.scenes)} escenas con edge-tts ({voice}, {rate})...[/cyan]")

    scenes_timing: list[dict[str, Any]] = []
    total_duration = 0.0

    for scene in project.scenes:
        audio_filename = f"scene_{scene.scene_id:02d}.mp3"
        audio_path = audio_dir / audio_filename

        words_path = audio_dir / f"scene_{scene.scene_id:02d}.words.json"

        # Individual cache check
        words: list[dict[str, Any]] = []
        cached = False
        if audio_path.exists() and audio_path.stat().st_size > 500 and not force and not is_stale(audio_path, script_path):
            words = load_words_sidecar(words_path)
            if words:
                cached = True

        if cached:
            real_duration = get_audio_duration_seconds(audio_path)
            console.print(f"  [green]✓ Escena {scene.scene_id:02d}: Audio en caché ({real_duration:.2f}s, {len(words)} palabras)[/green]")
        else:
            words = await generate_single_scene_audio(
                text=scene.narration_es,
                voice=voice,
                rate=rate,
                output_mp3=audio_path,
            )
            with open(words_path, "w", encoding="utf-8") as f:
                json.dump(words, f, indent=2, ensure_ascii=False)
            real_duration = get_audio_duration_seconds(audio_path)
            console.print(f"  [green]✓ Escena {scene.scene_id:02d}: Generado ({real_duration:.2f}s, {len(words)} palabras)[/green]")
            if not words and scene.narration_es.strip():
                console.print(
                    f"  [bold yellow]⚠ Escena {scene.scene_id:02d}: edge-tts no devolvió tiempos por palabra. "
                    "Los subtítulos de esta escena usarán espaciado uniforme (menos preciso).[/bold yellow]"
                )
            await asyncio.sleep(0.2)

        scenes_timing.append({
            "scene_id": scene.scene_id,
            "narration_es": scene.narration_es,
            "audio_file": audio_filename,
            "duration_sec": real_duration,
            "camera_effect": scene.camera_effect,
            "highlight_words": scene.highlight_words,
            "words": words,
        })
        total_duration += real_duration

    timing_metadata = {
        "voice": voice,
        "rate": rate,
        "total_scenes": len(project.scenes),
        "total_duration_sec": round(total_duration, 2),
        "scenes": scenes_timing,
    }

    # Only rewrite when content changed so the mtime (used for downstream staleness) advances on real change
    serialized = json.dumps(timing_metadata, indent=2, ensure_ascii=False)
    try:
        unchanged = metadata_path.exists() and metadata_path.read_text(encoding="utf-8") == serialized
    except Exception:
        unchanged = False
    if not unchanged:
        metadata_path.write_text(serialized, encoding="utf-8")

    console.print(f"[bold green]✓ Todos los audios generados. Duración total: {total_duration:.2f} segundos.[/bold green]")
    return timing_metadata


def generate_tts_sync(
    project: VideoProjectSchema,
    profile: VideoProfile,
    output_dir: Path,
    voice_override: Optional[str] = None,
    force: bool = False,
) -> dict[str, Any]:
    """Synchronous wrapper for generate_all_scene_audios."""
    return asyncio.run(
        generate_all_scene_audios(
            project=project,
            profile=profile,
            output_dir=output_dir,
            voice_override=voice_override,
            force=force,
        )
    )
