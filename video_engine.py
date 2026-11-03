"""FFmpeg Video Assembly and Rendering Engine (Módulo 5)."""

from pathlib import Path
from typing import Any
from rich.console import Console

from config import (
    VIDEO_WIDTH,
    VIDEO_HEIGHT,
    VIDEO_FPS,
    VIDEO_CRF,
    VIDEO_PIX_FMT,
    SFX_VOLUME,
    BGM_VOLUME,
    FONTS_DIR,
    get_ffmpeg_binary,
)
from pipeline_utils import is_stale, run_cmd
from script_engine import VideoProjectSchema

console = Console()


def build_zoompan_filter(
    camera_effect: str,
    duration_sec: float,
    fps: int = VIDEO_FPS,
) -> str:
    """Build smooth jitter-free zoompan filter using supersampling."""
    total_frames = max(int(duration_sec * fps), 1)

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
    duration_sec: float,
    camera_effect: str,
    force: bool = False,
    extra_inputs: tuple[Path, ...] = (),
) -> Path:
    """Render a single scene image into an animated MP4 clip matching audio duration."""
    if (
        output_clip_path.exists()
        and output_clip_path.stat().st_size > 1000
        and not force
        and not is_stale(output_clip_path, image_path, *extra_inputs)
    ):
        return output_clip_path

    ffmpeg_bin = get_ffmpeg_binary()
    vf_filter = build_zoompan_filter(camera_effect, duration_sec, VIDEO_FPS)

    cmd = [
        ffmpeg_bin,
        "-y",
        "-loop",
        "1",
        "-i",
        str(image_path),
        "-vf",
        vf_filter,
        "-t",
        f"{duration_sec:.3f}",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-pix_fmt",
        VIDEO_PIX_FMT,
        "-r",
        str(VIDEO_FPS),
        "-an",
        str(output_clip_path),
    ]

    run_cmd(cmd, f"Render del clip {output_clip_path.name}")
    return output_clip_path


def render_all_scene_clips(
    timing_metadata: dict[str, Any],
    images_dir: Path,
    clips_dir: Path,
    force: bool = False,
) -> list[Path]:
    """Render all scene video clips."""
    clips_dir.mkdir(parents=True, exist_ok=True)
    clips: list[Path] = []
    metadata_path = images_dir.parent / "audio" / "timing_metadata.json"

    scenes = timing_metadata.get("scenes", [])
    console.print(f"[cyan]Renderizando {len(scenes)} clips de video dinámicos a {VIDEO_FPS} fps...[/cyan]")

    for scene in scenes:
        scene_id = scene["scene_id"]
        duration_sec = scene.get("duration_sec", 3.0)
        camera_effect = scene.get("camera_effect", "zoom_in_slow")

        img_path = images_dir / f"scene_{scene_id:02d}.png"
        clip_path = clips_dir / f"clip_{scene_id:02d}.mp4"

        render_scene_clip(
            image_path=img_path,
            output_clip_path=clip_path,
            duration_sec=duration_sec,
            camera_effect=camera_effect,
            force=force,
            extra_inputs=(metadata_path,),
        )
        clips.append(clip_path)
        console.print(f"  [green]✓ Clip {scene_id:02d} renderizado ({duration_sec:.2f}s, {camera_effect})[/green]")

    return clips


def assemble_final_video(
    project: VideoProjectSchema,
    timing_metadata: dict[str, Any],
    workspace_dir: Path,
    subtitles_ass_path: Path,
    bgm_path: Path,
    whoosh_path: Path,
    output_final_path: Path,
    force: bool = False,
) -> Path:
    """Concatenate clips, mix audio tracks (voice, whoosh SFX, BGM), and burn subtitles."""
    final_inputs = [
        workspace_dir / "clips" / f"clip_{sc['scene_id']:02d}.mp4" for sc in timing_metadata.get("scenes", [])
    ]
    final_inputs += [subtitles_ass_path, workspace_dir / "audio" / "timing_metadata.json"]
    if (
        output_final_path.exists()
        and output_final_path.stat().st_size > 10_000
        and not force
        and not is_stale(output_final_path, *final_inputs)
    ):
        console.print(f"[green]✓ Video final existente en caché:[/] {output_final_path}")
        return output_final_path

    ffmpeg_bin = get_ffmpeg_binary()
    clips_dir = workspace_dir / "clips"
    audio_dir = workspace_dir / "audio"

    scenes = timing_metadata.get("scenes", [])
    total_duration = timing_metadata.get("total_duration_sec", 30.0)

    # 1. Prepare video concatenation demuxer list
    concat_list_file = workspace_dir / "clips_concat.txt"
    with open(concat_list_file, "w", encoding="utf-8") as f:
        for scene in scenes:
            scene_id = scene["scene_id"]
            clip_name = f"clip_{scene_id:02d}.mp4"
            f.write(f"file '{clips_dir / clip_name}'\n")

    # 2. Prepare voice audio concatenation demuxer list
    audio_concat_list_file = workspace_dir / "audio_concat.txt"
    with open(audio_concat_list_file, "w", encoding="utf-8") as f:
        for scene in scenes:
            audio_name = scene["audio_file"]
            f.write(f"file '{audio_dir / audio_name}'\n")

    # Concatenate voice to a single normalized wav
    full_voice_path = workspace_dir / "full_voice.wav"
    cmd_voice = [
        ffmpeg_bin,
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(audio_concat_list_file),
        "-c:a",
        "pcm_s16le",
        str(full_voice_path),
    ]
    run_cmd(cmd_voice, "Concatenación de audio de locución")

    # Concatenate video clips without audio
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

    # 3. Build audio mixing graph: voice (vol 1.0) + whooshes (vol 0.18) + bgm (vol 0.10)
    # Transitions happen at the start of each scene starting from scene 2
    transition_offsets_ms: list[int] = []
    current_acc_ms = 0
    for idx, scene in enumerate(scenes):
        dur_ms = round(scene.get("duration_sec", 3.0) * 1000)
        if idx > 0:
            transition_offsets_ms.append(current_acc_ms)
        current_acc_ms += dur_ms

    console.print(f"[cyan]Construyendo mezcla de audio y quemando subtítulos ({len(transition_offsets_ms)} transiciones SFX)...[/cyan]")

    # Audio filtergraph construction
    # Inputs:
    # 0: raw_video.mp4 (video)
    # 1: full_voice.wav (audio)
    # 2: bgm.mp3 (audio)
    # 3: whoosh.mp3 (audio)
    bgm_fade_start = max(total_duration - 1.0, 0.5)

    filter_complex_parts = []
    # Voice normalization
    filter_complex_parts.append("[1:a]volume=1.0[voice]")

    # BGM with volume 0.10, looping if needed, and 1-second fadeout
    filter_complex_parts.append(
        f"[2:a]aloop=loop=-1:size=2e+09,volume={BGM_VOLUME},"
        f"afade=t=in:ss=0:d=1,afade=t=out:st={bgm_fade_start:.2f}:d=1[bgm]"
    )

    # Whoosh SFX at transitions
    if transition_offsets_ms and whoosh_path.exists():
        num_trans = len(transition_offsets_ms)
        filter_complex_parts.append(f"[3:a]volume={SFX_VOLUME},asplit={num_trans}" + "".join(f"[w{i}]" for i in range(num_trans)))
        for i, delay_ms in enumerate(transition_offsets_ms):
            filter_complex_parts.append(f"[w{i}]adelay={delay_ms}|{delay_ms}[dw{i}]")
        sfx_inputs = "".join(f"[dw{i}]" for i in range(num_trans))
        filter_complex_parts.append(f"{sfx_inputs}amix=inputs={num_trans}:dropout_transition=0:normalize=0[all_sfx]")
        filter_complex_parts.append("[voice][bgm][all_sfx]amix=inputs=3:duration=first:dropout_transition=0:normalize=0,loudnorm=I=-14:TP=-1.5:LRA=11[aout]")
    else:
        filter_complex_parts.append("[voice][bgm]amix=inputs=2:duration=first:dropout_transition=0:normalize=0,loudnorm=I=-14:TP=-1.5:LRA=11[aout]")

    # Subtitles video filter in filter_complex
    escaped_ass = str(subtitles_ass_path).replace("\\", "/").replace(":", "\\:")
    escaped_fontsdir = str(FONTS_DIR).replace("\\", "/").replace(":", "\\:")
    filter_complex_parts.append(f"[0:v]subtitles='{escaped_ass}':fontsdir='{escaped_fontsdir}'[vout]")

    filter_complex_str = ";".join(filter_complex_parts)

    cmd_final = [
        ffmpeg_bin,
        "-y",
        "-i",
        str(raw_video_path),
        "-i",
        str(full_voice_path),
        "-i",
        str(bgm_path),
    ]

    if transition_offsets_ms and whoosh_path.exists():
        cmd_final.extend(["-i", str(whoosh_path)])

    cmd_final.extend([
        "-filter_complex",
        filter_complex_str,
        "-map",
        "[vout]",
        "-map",
        "[aout]",
        "-c:v",
        "libx264",
        "-crf",
        str(VIDEO_CRF),
        "-preset",
        "medium",
        "-pix_fmt",
        VIDEO_PIX_FMT,
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-shortest",
        "-movflags",
        "+faststart",
        str(output_final_path),
    ])

    run_cmd(cmd_final, "Render final (mezcla de audio y subtítulos)")
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
