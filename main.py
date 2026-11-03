"""Professional CLI for Automated Short Video Pipeline (main.py)."""

import os
import re
import sys
import time
from pathlib import Path
from typing import Optional
import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from config import (
    BASE_DIR,
    WORKSPACE_DIR,
    AUDIO_DIR,
    FAL_KEY,
)
from env_checker import check_and_prepare_environment
from profiles import get_profile, PROFILES
from script_engine import generate_or_load_script
from tts_engine import generate_tts_sync
from image_engine import generate_images_sync
from subtitle_engine import generate_subtitles_ass
from video_engine import render_all_scene_clips, assemble_final_video

app = typer.Typer(
    name="short-video-gen",
    help="CLI profesional para automatizar la creación de videos verticales (Shorts/Reels/TikTok en 9:16).",
    add_completion=False,
)
console = Console()


def slugify(text: str) -> str:
    """Convert text to a safe filesystem slug."""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_-]+", "-", text)
    return text[:45].strip("-") or "short-video"


@app.command()
def generate(
    profile: str = typer.Option(
        "curiosity",
        "--profile",
        "-p",
        help=f"Perfil de contenido: {list(PROFILES.keys())}",
    ),
    topic: str = typer.Option(
        ...,
        "--topic",
        "-t",
        help="Tema o premisa principal del video.",
    ),
    slug: Optional[str] = typer.Option(
        None,
        "--slug",
        "-s",
        help="Identificador de carpeta para el workspace (por defecto generado del tema).",
    ),
    mock_images: bool = typer.Option(
        False,
        "--mock-images",
        "-m",
        help="Generar imágenes de prueba con Pillow sin consumir créditos de Fal.ai ($0.00).",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="Forzar la regeneración de todas las etapas ignorando la caché.",
    ),
    voice: Optional[str] = typer.Option(
        None,
        "--voice",
        "-v",
        help="Voz TTS de edge-tts (ej. es-MX-JorgeNeural, es-MX-DaliaNeural).",
    ),
    music: Optional[Path] = typer.Option(
        None,
        "--music",
        help="Ruta personalizada a un archivo de música de fondo (.mp3).",
    ),
):
    """Genera un video vertical completo con edición dinámica, SFX, BGM y subtítulos animados."""
    start_time = time.time()

    # 1. Print Welcome Banner
    banner_text = (
        "[bold cyan]⚡ SHORT VIDEO PIPELINE CLI ⚡[/bold cyan]\n"
        "[white]Generador 100% automatizado de videos 9:16 para TikTok, Reels y Shorts[/white]\n"
        f"[dim]Perfil: {profile} | Mock Images: {mock_images} | Force: {force}[/dim]"
    )
    console.print(Panel(banner_text, border_style="cyan"))

    # 2. Validate Profile
    try:
        prof_obj = get_profile(profile)
    except ValueError as e:
        console.print(f"[bold red]Error:[/] {e}")
        raise typer.Exit(code=1)

    # 3. Environment & Assets Check (Fase 0)
    console.rule("[bold cyan]Fase 0: Verificación de Entorno y Assets Base[/bold cyan]")
    try:
        env_status = check_and_prepare_environment()
        console.print(f"[green]✓ FFmpeg verificado con soporte libass:[/] {env_status['ffmpeg']}")
    except Exception as e:
        console.print(f"[bold red]Error en verificación de entorno:[/] {e}")
        raise typer.Exit(code=1)

    # 4. Prepare Workspace
    target_slug = slug if slug else slugify(topic)
    project_workspace = WORKSPACE_DIR / target_slug
    project_workspace.mkdir(parents=True, exist_ok=True)
    console.print(f"[cyan]Workspace activo:[/] {project_workspace}")

    # 5. Módulo 1: Script Engine
    console.rule("[bold cyan]Módulo 1: Motor de Guión y Continuidad[/bold cyan]")
    project_script = generate_or_load_script(
        topic=topic,
        profile_name=prof_obj.name,
        output_dir=project_workspace,
        force=force,
    )

    # 6. Módulo 2: Voice & Timing Engine
    console.rule("[bold cyan]Módulo 2: Motor de Voz y Tiempos al Milisegundo[/bold cyan]")
    timing_metadata = generate_tts_sync(
        project=project_script,
        profile=prof_obj,
        output_dir=project_workspace,
        voice_override=voice,
        force=force,
    )

    # 7. Módulo 3: Image Engine
    console.rule("[bold cyan]Módulo 3: Generador de Imágenes[/bold cyan]")
    image_paths = generate_images_sync(
        project=project_script,
        output_dir=project_workspace,
        mock_images=mock_images,
        force=force,
    )

    # 8. Módulo 4: Subtitle Engine
    console.rule("[bold cyan]Módulo 4: Subtítulos Dinámicos Virales (.ass)[/bold cyan]")
    ass_path = project_workspace / "subtitles.ass"
    generate_subtitles_ass(
        timing_metadata=timing_metadata,
        output_ass_path=ass_path,
    )

    # 9. Módulo 5: Video Engine
    console.rule("[bold cyan]Módulo 5: Ensamblado y Renderizado FFmpeg[/bold cyan]")
    clips_dir = project_workspace / "clips"
    images_dir = project_workspace / "images"
    render_all_scene_clips(
        timing_metadata=timing_metadata,
        images_dir=images_dir,
        clips_dir=clips_dir,
        force=force,
    )

    bgm_file = music if (music and music.exists()) else Path(env_status["bgm"])
    whoosh_file = Path(env_status["whoosh"])
    final_video_path = project_workspace / "final_video.mp4"

    assemble_final_video(
        project=project_script,
        timing_metadata=timing_metadata,
        workspace_dir=project_workspace,
        subtitles_ass_path=ass_path,
        bgm_path=bgm_file,
        whoosh_path=whoosh_file,
        output_final_path=final_video_path,
        force=force,
    )

    elapsed = time.time() - start_time

    # 10. Summary Table
    table = Table(title="🎉 Video Generado con Éxito", border_style="green")
    table.add_column("Propiedad", style="cyan")
    table.add_column("Detalle", style="white")

    table.add_row("Título", project_script.title)
    table.add_row("Perfil", prof_obj.name)
    table.add_row("Escenas", str(len(project_script.scenes)))
    table.add_row("Duración Total", f"{timing_metadata.get('total_duration_sec', 0)} segundos")
    table.add_row("Archivo MP4 Final", str(final_video_path))
    table.add_row("Metadatos Publicación", str(project_workspace / "post_metadata.txt"))
    table.add_row("Tiempo de Proceso", f"{elapsed:.1f} segundos")

    console.print("\n")
    console.print(table)
    console.print("\n[bold green]✓ Todo el pipeline se ejecutó sin errores. Listo para publicar en redes.[/bold green]\n")


if __name__ == "__main__":
    app()
