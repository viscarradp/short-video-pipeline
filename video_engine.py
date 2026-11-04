"""FFmpeg Video Assembly and Rendering Engine (Módulo 5)."""

import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Optional
from rich.console import Console

from config import (
    CAMERA_EFFECTS,
    CLIP_RENDER_WORKERS,
    DEFAULT_CAMERA_EFFECT,
    get_format,
    VIDEO_FPS,
    VIDEO_CRF,
    VIDEO_PIX_FMT,
    FONTS_DIR,
    VideoFormat,
    get_ffmpeg_binary,
)
from pipeline_utils import CacheManifest, PipelineError, RunStats, fingerprint, run_cmd, scene_frames
from script_engine import VideoProjectSchema, normalize_hashtags

console = Console()

# Ajustes del codificador (forman parte de las huellas de clips y video final)
CLIP_PRESET = "veryfast"
FINAL_PRESET = "medium"
FINAL_AUDIO_BITRATE = "192k"


def build_zoompan_filter(
    camera_effect: str,
    frames: int,
    video_format: VideoFormat,
    fps: int = VIDEO_FPS,
) -> str:
    """Build smooth jitter-free zoompan filter using supersampling; emits exactly `frames` frames from ONE input frame."""
    total_frames = max(int(frames), 1)

    # 1. Upscale al doble de la resolución del formato (2160x3840 en vertical; supersampling to avoid integer jitter)
    # 2. Apply smooth zoompan
    # 3. Downscale back to the format's resolution (1080x1920 en vertical) with lanczos
    ss_w, ss_h = video_format.width * 2, video_format.height * 2
    effect = camera_effect if camera_effect in CAMERA_EFFECTS else DEFAULT_CAMERA_EFFECT
    center_x = "iw/2-(iw/zoom/2)"
    center_y = "ih/2-(ih/zoom/2)"
    if effect == "zoom_in_fast":
        zoom_expr, x_expr, y_expr = "min(zoom+0.0022,1.35)", center_x, center_y
    elif effect == "zoom_in_slow":
        zoom_expr, x_expr, y_expr = "min(zoom+0.0012,1.25)", center_x, center_y
    elif effect == "zoom_out":
        zoom_expr, x_expr, y_expr = "if(lte(on,1),1.25,max(1.001,zoom-0.0012))", center_x, center_y
    elif effect == "pan_right":
        zoom_expr, x_expr, y_expr = "1.15", f"(iw-iw/zoom)*(on/{total_frames})", center_y
    elif effect == "pan_left":
        zoom_expr, x_expr, y_expr = "1.15", f"(iw-iw/zoom)*(1-on/{total_frames})", center_y
    else:  # pragma: no cover - CAMERA_EFFECTS y esta cadena deben mantenerse sincronizadas
        raise PipelineError(f"Efecto de cámara sin implementar en video_engine: {effect}")

    filter_str = (
        f"scale={ss_w}:{ss_h},"
        f"zoompan=z='{zoom_expr}':x='{x_expr}':y='{y_expr}':d={total_frames}:s={ss_w}x{ss_h}:fps={fps},"
        f"scale={video_format.width}:{video_format.height}:flags=lanczos"
    )
    return filter_str


def clip_fingerprint(image_path: Path, frames: int, camera_effect: str, video_format: VideoFormat) -> tuple[str, str]:
    """(huella, filtro de video) de un clip: depende del contenido de la imagen, los fotogramas, el efecto y los ajustes."""
    vf_filter = build_zoompan_filter(camera_effect, frames, video_format, VIDEO_FPS)
    fp = fingerprint(
        kind="clip",
        image=image_path,
        frames=frames,
        filter=vf_filter,
        effect=camera_effect,
        width=video_format.width,
        height=video_format.height,
        fps=VIDEO_FPS,
        preset=CLIP_PRESET,
        pix_fmt=VIDEO_PIX_FMT,
        input_mode="single_frame",
    )
    return fp, vf_filter


def encode_scene_clip(image_path: Path, output_clip_path: Path, frames: int, vf_filter: str) -> None:
    """Ejecuta ffmpeg y deja el clip en su sitio (escritura atómica). NO toca el manifiesto: es seguro en hilos."""
    ffmpeg_bin = get_ffmpeg_binary()
    part_path = output_clip_path.with_name(output_clip_path.stem + ".part.mp4")

    # Sin -loop 1: zoompan genera los `frames` fotogramas a partir de una sola imagen de entrada.
    cmd = [
        ffmpeg_bin,
        "-y",
        "-i",
        str(image_path),
        "-vf",
        vf_filter,
        "-frames:v",
        str(frames),
        "-c:v",
        "libx264",
        "-preset",
        CLIP_PRESET,
        "-pix_fmt",
        VIDEO_PIX_FMT,
        "-r",
        str(VIDEO_FPS),
        "-an",
        str(part_path),
    ]

    try:
        run_cmd(cmd, f"Render del clip {output_clip_path.name}")
        os.replace(part_path, output_clip_path)
    finally:
        if part_path.exists():  # un render fallido no deja .part.mp4 huérfanos
            part_path.unlink(missing_ok=True)


def render_scene_clip(
    image_path: Path,
    output_clip_path: Path,
    frames: int,
    camera_effect: str,
    manifest: CacheManifest,
    video_format: VideoFormat,
    force: bool = False,
) -> bool:
    """Render one scene image into an animated MP4 clip of EXACTLY `frames` frames. True if rendered, False on cache hit."""
    fp, vf_filter = clip_fingerprint(image_path, frames, camera_effect, video_format)
    if not force and manifest.is_fresh(output_clip_path, fp) and output_clip_path.stat().st_size > 1000:
        return False
    encode_scene_clip(image_path, output_clip_path, frames, vf_filter)
    manifest.record(output_clip_path, fp)
    return True


def render_all_scene_clips(
    timing_metadata: dict[str, Any],
    images_dir: Path,
    clips_dir: Path,
    manifest: CacheManifest,
    video_format: VideoFormat,
    force: bool = False,
    stats: Optional[RunStats] = None,
    workers: int = CLIP_RENDER_WORKERS,
) -> list[Path]:
    """Render all scene video clips (per-clip cache by image hash + frame count + effect), in parallel.

    Los subprocesos ffmpeg corren en un ThreadPoolExecutor; el manifiesto, las estadísticas y los mensajes se
    actualizan solo en el hilo principal y solo cuando un clip termina bien. Si alguno falla, los demás terminan,
    quedan registrados, y al final se lanza un PipelineError que lista los clips fallidos.
    """
    clips_dir.mkdir(parents=True, exist_ok=True)
    clips: list[Path] = []
    cached_count = 0
    pending: list[tuple[int, Path, Path, int, str, str, str]] = []  # (id, imagen, clip, fotogramas, huella, filtro, detalle)

    scenes = timing_metadata.get("scenes", [])
    console.print(
        f"[cyan]Clips de video dinámicos a {VIDEO_FPS} fps ({video_format.width}x{video_format.height}) "
        f"para {len(scenes)} escenas...[/cyan]"
    )

    for scene in scenes:
        scene_id = scene["scene_id"]
        frames = scene_frames(scene, VIDEO_FPS)
        camera_effect = scene.get("camera_effect", "zoom_in_slow")
        detail = f"{frames} fotogramas, {frames / VIDEO_FPS:.3f}s, {camera_effect}"

        img_path = images_dir / f"scene_{scene_id:02d}.png"
        clip_path = clips_dir / f"clip_{scene_id:02d}.mp4"
        clips.append(clip_path)

        fp, vf_filter = clip_fingerprint(img_path, frames, camera_effect, video_format)
        if not force and manifest.is_fresh(clip_path, fp) and clip_path.stat().st_size > 1000:
            cached_count += 1
            console.print(f"  [green]✓ Clip {scene_id:02d} en caché ({detail})[/green]")
        else:
            pending.append((scene_id, img_path, clip_path, frames, fp, vf_filter, detail))

    rendered = 0
    failures: list[str] = []
    if pending:
        n_workers = max(1, min(workers, len(pending)))
        mode = f"{n_workers} procesos en paralelo" if n_workers > 1 else "1 proceso (secuencial)"
        console.print(f"  [cyan]Renderizando {len(pending)} clips con {mode}...[/cyan]")
        t0 = time.time()
        with ThreadPoolExecutor(max_workers=n_workers) as pool:
            futures = {
                pool.submit(encode_scene_clip, img_path, clip_path, frames, vf_filter): (scene_id, clip_path, fp, detail)
                for scene_id, img_path, clip_path, frames, fp, vf_filter, detail in pending
            }
            for fut in as_completed(futures):
                scene_id, clip_path, fp, detail = futures[fut]
                try:
                    fut.result()
                except Exception as e:  # PipelineError de ffmpeg u OSError al mover el archivo
                    lines = [ln.strip() for ln in str(e).splitlines() if ln.strip()]
                    failures.append(f"clip {scene_id:02d}: {' | '.join(lines[-2:])[:300] if lines else type(e).__name__}")
                    console.print(f"  [red]✗ Clip {scene_id:02d} falló[/red]")
                    continue
                manifest.record(clip_path, fp)  # solo tras un render exitoso, y solo desde el hilo principal
                rendered += 1
                if stats is not None:
                    stats.clips.append(scene_id)
                console.print(f"  [green]✓ Clip {scene_id:02d} renderizado ({detail})[/green]")
        console.print(
            f"  [dim]Etapa de clips: {time.time() - t0:.1f} s con {mode} ({len(pending)} clips a renderizar).[/dim]"
        )

    console.print(f"[bold green]✓ Clips: {rendered} renderizados, {cached_count} en caché.[/bold green]")
    if failures:
        raise PipelineError(
            f"Falló el render de {len(failures)} clip(s); los exitosos quedaron en caché. "
            "Vuelve a ejecutar para reintentar solo los faltantes.\n" + "\n".join(failures)
        )
    return clips


def final_fingerprint(
    clip_inputs: list[Path], total_frames: int, subtitles_ass_path: Path, mix_audio_path: Path, video_format: VideoFormat
) -> str:
    """Huella del video final: clips, subtítulos y mezcla (por contenido), fotogramas, resolución y ajustes del codificador."""
    return fingerprint(
        kind="final",
        clips=clip_inputs,
        total_frames=total_frames,
        subtitles=subtitles_ass_path,
        mix=mix_audio_path,
        width=video_format.width,
        height=video_format.height,
        fps=VIDEO_FPS,
        crf=VIDEO_CRF,
        preset=FINAL_PRESET,
        pix_fmt=VIDEO_PIX_FMT,
        audio_bitrate=FINAL_AUDIO_BITRATE,
    )


def assemble_final_video(
    project: VideoProjectSchema,
    timing_metadata: dict[str, Any],
    workspace_dir: Path,
    subtitles_ass_path: Path,
    mix_audio_path: Path,
    output_final_path: Path,
    manifest: CacheManifest,
    video_format: VideoFormat,
    force: bool = False,
    stats: Optional[RunStats] = None,
) -> Path:
    """Concatenate clips, burn subtitles and mux the pre-mixed (already normalized) audio track."""
    scenes = timing_metadata.get("scenes", [])
    clips_dir = workspace_dir / "clips"
    clip_inputs = [clips_dir / f"clip_{sc['scene_id']:02d}.mp4" for sc in scenes]
    total_frames = sum(scene_frames(sc, VIDEO_FPS) for sc in scenes)
    fp = final_fingerprint(clip_inputs, total_frames, subtitles_ass_path, mix_audio_path, video_format)
    if not force and manifest.is_fresh(output_final_path, fp) and output_final_path.stat().st_size > 10_000:
        console.print(f"[green]✓ Video final en caché:[/] {output_final_path}")
        generate_post_metadata(project, workspace_dir)  # barato; solo reescribe si el guion cambió
        return output_final_path

    ffmpeg_bin = get_ffmpeg_binary()

    # 1. Concatenate video clips without audio (stream copy: frame counts stay exact)
    concat_list_file = workspace_dir / "clips_concat.txt"
    with open(concat_list_file, "w", encoding="utf-8") as f:
        for clip in clip_inputs:
            f.write(f"file '{clip.as_posix()}'\n")

    raw_video_path = workspace_dir / "raw_video.mp4"
    cmd_concat_video = [
        ffmpeg_bin,
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(concat_list_file),
        "-c",
        "copy",
        str(raw_video_path),
    ]
    run_cmd(cmd_concat_video, "Concatenación de clips de video")

    console.print("[cyan]Quemando subtítulos y empaquetando el audio mezclado...[/cyan]")

    # 2. Burn subtitles; the audio is the finished mix_final.wav (no audio filters here)
    escaped_ass = str(subtitles_ass_path).replace("\\", "/").replace(":", "\\:")
    escaped_fontsdir = str(FONTS_DIR).replace("\\", "/").replace(":", "\\:")
    vf = f"subtitles='{escaped_ass}':fontsdir='{escaped_fontsdir}'"

    part_final_path = output_final_path.with_name(output_final_path.stem + ".part.mp4")
    cmd_final = [
        ffmpeg_bin,
        "-y",
        "-i",
        str(raw_video_path),
        "-i",
        str(mix_audio_path),
        "-vf",
        vf,
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-c:v",
        "libx264",
        "-crf",
        str(VIDEO_CRF),
        "-preset",
        FINAL_PRESET,
        "-pix_fmt",
        VIDEO_PIX_FMT,
        "-c:a",
        "aac",
        "-b:a",
        FINAL_AUDIO_BITRATE,
        # Sin -shortest: video y audio miden lo mismo (fotogramas * 1600 muestras).
        "-movflags",
        "+faststart",
        str(part_final_path),
    ]

    run_cmd(cmd_final, "Render final (subtítulos y audio mezclado)")
    os.replace(part_final_path, output_final_path)
    manifest.record(output_final_path, fp)
    if stats is not None:
        stats.final = True
    console.print(f"[bold green]✓ Video final exportado exitosamente:[/] {output_final_path}")

    # Generate post metadata
    generate_post_metadata(project, workspace_dir)

    return output_final_path


PROFILE_FALLBACK_TAGS = {
    "curiosity": ["#datoscuriosos", "#ciencia", "#aprendeentiktok", "#mecanica", "#curiosidades"],
    "micronovela": ["#historiareal", "#suspenso", "#drama", "#chismesito", "#novelas"],
    "que_pasaria": ["#quepasariasi", "#hipotetico", "#historias", "#apocalipsis", "#ficcion"],
    "historia": ["#historias", "#aventura", "#relatos", "#animacion", "#cuentos"],
    "chisme": ["#chisme", "#chismesito", "#drama", "#historiareal", "#salseo"],
}


def build_post_metadata_text(project: VideoProjectSchema) -> str:
    """Texto de post_metadata.txt: título + descripción + hashtags del LLM (con respaldo estático si faltan)
    y siempre las etiquetas genéricas de alcance del formato (VideoFormat.reach_hashtags) al final."""
    description = project.post_description.strip()
    if not description:
        description = (
            "¿Sabías esto? Mira lo que ocurre paso a paso. "
            f"{'Parte 2 disponible en el perfil.' if project.profile == 'micronovela' else 'Comparte con tus amigos.'}"
        )

    tags = [f"#{t}" for t in normalize_hashtags(project.hashtags)]
    if not tags:
        tags = PROFILE_FALLBACK_TAGS.get(project.profile, ["#viral", "#reels", "#tendencia"])
    for reach in get_format(project.video_format).reach_hashtags:
        if reach.lower() not in {t.lower() for t in tags}:
            tags.append(reach)

    return f"""TITULO SUGERIDO:
{project.title}

DESCRIPCIÓN:
{description}

HASHTAGS:
{' '.join(tags)}
"""


def generate_post_metadata(project: VideoProjectSchema, workspace_dir: Path) -> Path:
    """Escribe post_metadata.txt (título, descripción y hashtags listos para copiar); solo reescribe si cambió."""
    metadata_file = workspace_dir / "post_metadata.txt"
    content = build_post_metadata_text(project)
    try:
        unchanged = metadata_file.exists() and metadata_file.read_text(encoding="utf-8") == content
    except Exception:
        unchanged = False
    if not unchanged:
        with open(metadata_file, "w", encoding="utf-8") as f:
            f.write(content)
        console.print(f"[green]✓ Metadatos de publicación generados:[/] {metadata_file}")
    return metadata_file
