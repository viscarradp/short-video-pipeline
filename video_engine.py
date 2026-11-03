"""FFmpeg Video Assembly and Rendering Engine (Módulo 5)."""

import os
from pathlib import Path
from typing import Any, Optional
from rich.console import Console

from config import (
    VIDEO_WIDTH,
    VIDEO_HEIGHT,
    VIDEO_FPS,
    VIDEO_CRF,
    VIDEO_PIX_FMT,
    FONTS_DIR,
    get_ffmpeg_binary,
)
from pipeline_utils import CacheManifest, RunStats, fingerprint, run_cmd, scene_frames
from script_engine import VideoProjectSchema

console = Console()

# Ajustes del codificador (forman parte de las huellas de clips y video final)
CLIP_PRESET = "veryfast"
FINAL_PRESET = "medium"
FINAL_AUDIO_BITRATE = "192k"


def build_zoompan_filter(
    camera_effect: str,
    frames: int,
    fps: int = VIDEO_FPS,
) -> str:
    """Build smooth jitter-free zoompan filter using supersampling; emits exactly `frames` frames from ONE input frame."""
    total_frames = max(int(frames), 1)

    # 1. Upscale to 2160x3840 (supersampling to avoid integer jitter)
    # 2. Apply smooth zoompan
    # 3. Downscale back to 1080x1920 with lanczos
    if camera_effect == "zoom_in_fast":
        zoom_expr = "min(zoom+0.0022,1.35)"
        x_expr = "iw/2-(iw/zoom/2)"
        y_expr = "ih/2-(ih/zoom/2)"
    elif camera_effect == "zoom_out":
        zoom_expr = "if(lte(on,1),1.25,max(1.001,zoom-0.0012))"
        x_expr = "iw/2-(iw/zoom/2)"
        y_expr = "ih/2-(ih/zoom/2)"
    elif camera_effect == "pan_right":
        zoom_expr = "1.15"
        x_expr = f"(iw-iw/zoom)*(on/{total_frames})"
        y_expr = "ih/2-(ih/zoom/2)"
    elif camera_effect == "pan_left":
        zoom_expr = "1.15"
        x_expr = f"(iw-iw/zoom)*(1-on/{total_frames})"
        y_expr = "ih/2-(ih/zoom/2)"
    else:  # default zoom_in_slow
        zoom_expr = "min(zoom+0.0012,1.25)"
        x_expr = "iw/2-(iw/zoom/2)"
        y_expr = "ih/2-(ih/zoom/2)"

    filter_str = (
        f"scale=2160:3840,"
        f"zoompan=z='{zoom_expr}':x='{x_expr}':y='{y_expr}':d={total_frames}:s=2160x3840:fps={fps},"
        f"scale={VIDEO_WIDTH}:{VIDEO_HEIGHT}:flags=lanczos"
    )
    return filter_str


def render_scene_clip(
    image_path: Path,
    output_clip_path: Path,
    frames: int,
    camera_effect: str,
    manifest: CacheManifest,
    force: bool = False,
) -> bool:
    """Render one scene image into an animated MP4 clip of EXACTLY `frames` frames. True if rendered, False on cache hit."""
    vf_filter = build_zoompan_filter(camera_effect, frames, VIDEO_FPS)
    fp = fingerprint(
        kind="clip",
        image=image_path,
        frames=frames,
        filter=vf_filter,
        effect=camera_effect,
        width=VIDEO_WIDTH,
        height=VIDEO_HEIGHT,
        fps=VIDEO_FPS,
        preset=CLIP_PRESET,
        pix_fmt=VIDEO_PIX_FMT,
        input_mode="single_frame",
    )
    if not force and manifest.is_fresh(output_clip_path, fp) and output_clip_path.stat().st_size > 1000:
        return False

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

    run_cmd(cmd, f"Render del clip {output_clip_path.name}")
    os.replace(part_path, output_clip_path)
    manifest.record(output_clip_path, fp)
    return True


def render_all_scene_clips(
    timing_metadata: dict[str, Any],
    images_dir: Path,
    clips_dir: Path,
    manifest: CacheManifest,
    force: bool = False,
    stats: Optional[RunStats] = None,
) -> list[Path]:
    """Render all scene video clips (per-clip cache by image hash + frame count + effect)."""
    clips_dir.mkdir(parents=True, exist_ok=True)
    clips: list[Path] = []
    rendered = 0
    cached_count = 0

    scenes = timing_metadata.get("scenes", [])
    console.print(f"[cyan]Clips de video dinámicos a {VIDEO_FPS} fps para {len(scenes)} escenas...[/cyan]")

    for scene in scenes:
        scene_id = scene["scene_id"]
        frames = scene_frames(scene, VIDEO_FPS)
        duration_sec = frames / VIDEO_FPS
        camera_effect = scene.get("camera_effect", "zoom_in_slow")

        img_path = images_dir / f"scene_{scene_id:02d}.png"
        clip_path = clips_dir / f"clip_{scene_id:02d}.mp4"

        did_render = render_scene_clip(
            image_path=img_path,
            output_clip_path=clip_path,
            frames=frames,
            camera_effect=camera_effect,
            manifest=manifest,
            force=force,
        )
        clips.append(clip_path)
        detail = f"{frames} fotogramas, {duration_sec:.3f}s, {camera_effect}"
        if did_render:
            rendered += 1
            if stats is not None:
                stats.clips.append(scene_id)
            console.print(f"  [green]✓ Clip {scene_id:02d} renderizado ({detail})[/green]")
        else:
            cached_count += 1
            console.print(f"  [green]✓ Clip {scene_id:02d} en caché ({detail})[/green]")

    console.print(f"[bold green]✓ Clips: {rendered} renderizados, {cached_count} en caché.[/bold green]")
    return clips


def assemble_final_video(
    project: VideoProjectSchema,
    timing_metadata: dict[str, Any],
    workspace_dir: Path,
    subtitles_ass_path: Path,
    mix_audio_path: Path,
    output_final_path: Path,
    manifest: CacheManifest,
    force: bool = False,
    stats: Optional[RunStats] = None,
) -> Path:
    """Concatenate clips, burn subtitles and mux the pre-mixed (already normalized) audio track."""
    scenes = timing_metadata.get("scenes", [])
    clips_dir = workspace_dir / "clips"
    clip_inputs = [clips_dir / f"clip_{sc['scene_id']:02d}.mp4" for sc in scenes]
    total_frames = sum(scene_frames(sc, VIDEO_FPS) for sc in scenes)
    fp = fingerprint(
        kind="final",
        clips=clip_inputs,
        total_frames=total_frames,
        subtitles=subtitles_ass_path,
        mix=mix_audio_path,
        width=VIDEO_WIDTH,
        height=VIDEO_HEIGHT,
        fps=VIDEO_FPS,
        crf=VIDEO_CRF,
        preset=FINAL_PRESET,
        pix_fmt=VIDEO_PIX_FMT,
        audio_bitrate=FINAL_AUDIO_BITRATE,
    )
    if not force and manifest.is_fresh(output_final_path, fp) and output_final_path.stat().st_size > 10_000:
        console.print(f"[green]✓ Video final en caché:[/] {output_final_path}")
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


def generate_post_metadata(project: VideoProjectSchema, workspace_dir: Path) -> Path:
    """Generate ready-to-copy post description with title and 5 viral hashtags."""
    metadata_file = workspace_dir / "post_metadata.txt"

    profile_tags = {
        "curiosity": ["#datoscuriosos", "#ciencia", "#aprendeentiktok", "#mecanica", "#curiosidades"],
        "micronovela": ["#historiareal", "#suspenso", "#drama", "#chismesito", "#novelas"],
    }
    tags = profile_tags.get(project.profile, ["#viral", "#shorts", "#reels", "#fyp", "#tendencia"])

    content = f"""TITULO SUGERIDO:
{project.title}

DESCRIPCIÓN:
¿Sabías esto? Mira lo que ocurre paso a paso. {'Parte 2 disponible en el perfil.' if project.profile == 'micronovela' else 'Comparte con tus amigos.'}

HASHTAGS:
{' '.join(tags)}
"""
    with open(metadata_file, "w", encoding="utf-8") as f:
        f.write(content)

    console.print(f"[green]✓ Metadatos de publicación generados:[/] {metadata_file}")
    return metadata_file
