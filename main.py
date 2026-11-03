"""Professional CLI for Automated Short Video Pipeline (main.py).

Comandos:
  generate      Genera un video completo (caché por huella de contenido por escena).
  retry-scene   Regenera escenas concretas (imagen y/o narración) y reensambla el video.
"""

import json
import os
import re
import sys
import time
import unicodedata
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional
import typer
from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from config import FAL_KEY, WORKSPACE_DIR
from env_checker import check_and_prepare_environment
from pipeline_utils import CacheManifest, PipelineError, RunStats
from profiles import get_profile, PROFILES
from script_engine import (
    VideoProjectSchema,
    generate_or_load_script,
    light_normalize_project,
    write_script,
)
from tts_engine import generate_tts_sync
from image_engine import FLUX_COST_USD, ImageOverrides, generate_images_sync
from subtitle_engine import generate_subtitles_ass
from video_engine import render_all_scene_clips, assemble_final_video

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass

app = typer.Typer(
    name="short-video-gen",
    help=(
        "CLI profesional para automatizar la creación de videos verticales (Shorts/Reels/TikTok en 9:16).\n\n"
        "Comandos: 'generate' crea un video completo; 'retry-scene' regenera escenas concretas de un proyecto existente."
    ),
    add_completion=False,
    no_args_is_help=True,
)
console = Console()


SLUG_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
RUN_CONFIG_FILENAME = "run_config.json"


def slugify(text: str) -> str:
    """Convert text to a safe ASCII filesystem slug ([a-z0-9-], max 60 chars)."""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.encode("ascii", "ignore").decode("ascii").lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = re.sub(r"-{2,}", "-", text).strip("-")
    text = text[:60].strip("-")
    return text or "video"


# --------------------------------------------------------------------------- #
# Configuración de la ejecución y orquestación compartida
# --------------------------------------------------------------------------- #

@dataclass
class RunConfig:
    """Opciones de una ejecución; se persisten en run_config.json para que retry-scene las reutilice."""

    topic: str
    profile: str
    voice: Optional[str] = None
    music: Optional[str] = None
    mock_script: bool = False
    mock_images: bool = False

    def save(self, workspace: Path) -> None:
        (workspace / RUN_CONFIG_FILENAME).write_text(
            json.dumps(asdict(self), indent=2, ensure_ascii=False), encoding="utf-8"
        )

    @classmethod
    def load(cls, workspace: Path) -> "RunConfig":
        path = workspace / RUN_CONFIG_FILENAME
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return cls(
                topic=str(data["topic"]),
                profile=str(data["profile"]),
                voice=data.get("voice") or None,
                music=data.get("music") or None,
                mock_script=bool(data.get("mock_script", False)),
                mock_images=bool(data.get("mock_images", False)),
            )
        except (OSError, ValueError, KeyError, TypeError) as e:
            raise PipelineError(f"{RUN_CONFIG_FILENAME} es inválido o está incompleto ({e}). Ejecuta 'generate' de nuevo para recrearlo.") from e


@dataclass
class ForceFlags:
    """Etapas cuya caché se ignora (un --force total activa las cuatro)."""

    script: bool = False
    audio: bool = False
    images: bool = False
    video: bool = False  # clips + video final

    @classmethod
    def all(cls) -> "ForceFlags":
        return cls(True, True, True, True)

    def describe(self) -> str:
        active = [name for name, on in asdict(self).items() if on]
        return "+".join(active) if active else "no"


@dataclass
class PipelineResult:
    project: VideoProjectSchema
    timing_metadata: dict[str, Any]
    final_video_path: Path
    stats: RunStats = field(default_factory=RunStats)


def run_pipeline(
    workspace: Path,
    config: RunConfig,
    force: ForceFlags,
    env_status: dict[str, str],
    image_overrides: Optional[ImageOverrides] = None,
) -> PipelineResult:
    """Las 5 etapas del pipeline, compartidas por 'generate' y 'retry-scene'. Lanza PipelineError ante fallos."""
    profile = get_profile(config.profile)
    manifest = CacheManifest(workspace)
    stats = RunStats()

    # Módulo 1: Script Engine
    console.rule("[bold cyan]Módulo 1: Motor de Guión y Continuidad[/bold cyan]")
    project = generate_or_load_script(
        topic=config.topic,
        profile_name=profile.name,
        output_dir=workspace,
        manifest=manifest,
        force=force.script,
        mock_script=config.mock_script,
        stats=stats,
    )

    # Módulo 2: Voice & Timing Engine
    console.rule("[bold cyan]Módulo 2: Motor de Voz y Tiempos al Milisegundo[/bold cyan]")
    timing_metadata = generate_tts_sync(
        project=project,
        profile=profile,
        output_dir=workspace,
        manifest=manifest,
        voice_override=config.voice,
        force=force.audio,
        stats=stats,
    )

    # Módulo 3: Image Engine
    console.rule("[bold cyan]Módulo 3: Generador de Imágenes[/bold cyan]")
    generate_images_sync(
        project=project,
        output_dir=workspace,
        manifest=manifest,
        mock_images=config.mock_images,
        force=force.images,
        overrides=image_overrides,
        stats=stats,
    )

    # Módulo 4: Subtitle Engine
    console.rule("[bold cyan]Módulo 4: Subtítulos Dinámicos Virales (.ass)[/bold cyan]")
    ass_path = workspace / "subtitles.ass"
    generate_subtitles_ass(timing_metadata=timing_metadata, output_ass_path=ass_path)

    # Módulo 5: Video Engine
    console.rule("[bold cyan]Módulo 5: Ensamblado y Renderizado FFmpeg[/bold cyan]")
    render_all_scene_clips(
        timing_metadata=timing_metadata,
        images_dir=workspace / "images",
        clips_dir=workspace / "clips",
        manifest=manifest,
        force=force.video,
        stats=stats,
    )

    bgm_file = Path(config.music) if config.music else Path(env_status["bgm"])
    whoosh_file = Path(env_status["whoosh"])
    final_video_path = workspace / "final_video.mp4"

    assemble_final_video(
        project=project,
        timing_metadata=timing_metadata,
        workspace_dir=workspace,
        subtitles_ass_path=ass_path,
        bgm_path=bgm_file,
        whoosh_path=whoosh_file,
        output_final_path=final_video_path,
        manifest=manifest,
        force=force.video,
        stats=stats,
    )

    return PipelineResult(
        project=project,
        timing_metadata=timing_metadata,
        final_video_path=final_video_path,
        stats=stats,
    )


def _check_environment() -> dict[str, str]:
    """Fase 0: verifica ffmpeg/libass y prepara assets base."""
    console.rule("[bold cyan]Fase 0: Verificación de Entorno y Assets Base[/bold cyan]")
    try:
        env_status = check_and_prepare_environment()
    except Exception as e:
        console.print(f"[bold red]Error en verificación de entorno:[/] {e}")
        raise typer.Exit(code=1)
    console.print(f"[green]✓ FFmpeg verificado con soporte libass:[/] {env_status['ffmpeg']}")
    return env_status


def _print_pipeline_error(e: PipelineError) -> None:
    console.print(Panel(Text(str(e)), title="[bold red]Error del pipeline[/bold red]", border_style="red"))


def _fmt_ids(ids: list[int]) -> str:
    return ", ".join(f"{i:02d}" for i in sorted(ids)) if ids else "ninguno"


# --------------------------------------------------------------------------- #
# Comando: generate
# --------------------------------------------------------------------------- #

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
    mock_script: bool = typer.Option(
        False,
        "--mock-script",
        help="Usar un guión de demostración local en lugar de Fal.ai (no respeta el tema; $0.00).",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="Forzar la regeneración de TODAS las etapas ignorando la caché.",
    ),
    force_script: bool = typer.Option(
        False,
        "--force-script",
        help="Regenerar solo el guión. OJO: un guión nuevo suele significar prompts nuevos y, por tanto, volver a pagar las imágenes.",
    ),
    force_audio: bool = typer.Option(
        False,
        "--force-audio",
        help="Regenerar solo la locución (todas las escenas).",
    ),
    force_images: bool = typer.Option(
        False,
        "--force-images",
        help="Regenerar solo las imágenes (todas las escenas; consume créditos de Fal.ai sin --mock-images).",
    ),
    force_video: bool = typer.Option(
        False,
        "--force-video",
        help="Re-renderizar solo los clips y el video final.",
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

    force_flags = ForceFlags.all() if force else ForceFlags(force_script, force_audio, force_images, force_video)

    # 1. Print Welcome Banner
    banner_text = (
        "[bold cyan]⚡ SHORT VIDEO PIPELINE CLI ⚡[/bold cyan]\n"
        "[white]Generador 100% automatizado de videos 9:16 para TikTok, Reels y Shorts[/white]\n"
        f"[dim]Perfil: {profile} | Mock Script: {mock_script} | Mock Images: {mock_images} | Force: {force_flags.describe()}[/dim]"
    )
    console.print(Panel(banner_text, border_style="cyan"))

    # 2. Validate Profile
    try:
        prof_obj = get_profile(profile)
    except ValueError as e:
        console.print(f"[bold red]Error:[/] {e}")
        raise typer.Exit(code=1)

    # Validate user-supplied options
    if slug is not None and not SLUG_PATTERN.match(slug):
        console.print("[bold red]Error:[/] --slug solo puede contener letras, números, guiones y guiones bajos (A-Z, a-z, 0-9, - y _).")
        raise typer.Exit(code=1)
    if music is not None and not music.is_file():
        console.print(f"[bold red]Error:[/] el archivo de música indicado en --music no existe: {music}")
        raise typer.Exit(code=1)

    # 3. Environment & Assets Check (Fase 0)
    env_status = _check_environment()

    try:
        # 4. Prepare Workspace + run_config.json
        target_slug = slug if slug else slugify(topic)
        project_workspace = WORKSPACE_DIR / target_slug
        project_workspace.mkdir(parents=True, exist_ok=True)
        console.print(f"[cyan]Workspace activo:[/] {project_workspace}")

        config = RunConfig(
            topic=topic,
            profile=prof_obj.name,
            voice=voice,
            music=str(music.resolve()) if music else None,
            mock_script=mock_script,
            mock_images=mock_images,
        )
        config.save(project_workspace)

        # 5. Las 5 etapas
        result = run_pipeline(project_workspace, config, force_flags, env_status)
    except PipelineError as e:
        _print_pipeline_error(e)
        raise typer.Exit(code=1)

    elapsed = time.time() - start_time

    # 6. Summary Table
    table = Table(title="🎉 Video Generado con Éxito", border_style="green")
    table.add_column("Propiedad", style="cyan")
    table.add_column("Detalle", style="white")

    table.add_row("Título", result.project.title)
    table.add_row("Perfil", prof_obj.name)
    table.add_row("Escenas", str(len(result.project.scenes)))
    table.add_row("Duración Total", f"{result.timing_metadata.get('total_duration_sec', 0)} segundos")
    table.add_row("Regenerado", _stats_line(result.stats))
    table.add_row("Archivo MP4 Final", str(result.final_video_path))
    table.add_row("Metadatos Publicación", str(project_workspace / "post_metadata.txt"))
    table.add_row("Tiempo de Proceso", f"{elapsed:.1f} segundos")

    console.print("\n")
    console.print(table)
    console.print("\n[bold green]✓ Todo el pipeline se ejecutó sin errores. Listo para publicar en redes.[/bold green]\n")


def _stats_line(stats: RunStats) -> str:
    return (
        f"guion: {'sí' if stats.script else 'no'} | audio: {len(stats.audio)} | imágenes: {len(stats.images)} | "
        f"clips: {len(stats.clips)} | video final: {'sí' if stats.final else 'no (en caché)'}"
    )


# --------------------------------------------------------------------------- #
# Comando: retry-scene
# --------------------------------------------------------------------------- #

@app.command("retry-scene")
def retry_scene(
    slug: str = typer.Option(..., "--slug", "-s", help="Slug del proyecto existente en workspace/."),
    scene: Optional[list[int]] = typer.Option(
        None,
        "--scene",
        help="(Requerido) Escena a regenerar (1, 2, 3...). Repetible: --scene 3 --scene 5.",
    ),
    prompt: Optional[str] = typer.Option(
        None,
        "--prompt",
        help="Nuevo prompt visual (inglés) para la escena; se guarda en script.json. Solo con una única --scene.",
    ),
    narration: Optional[str] = typer.Option(
        None,
        "--narration",
        help="Nueva narración para la escena; se guarda en script.json. Solo con una única --scene. "
        "Si es lo único que se indica, la imagen se conserva.",
    ),
    seed: Optional[int] = typer.Option(
        None,
        "--seed",
        help="Semilla para la nueva imagen (por defecto, una aleatoria nueva).",
    ),
):
    """Regenera una o más escenas (imagen nueva y/o narración corregida) y reensambla el video final."""
    start_time = time.time()
    console.print(Panel(
        f"[bold cyan]🔄 RETRY-SCENE[/bold cyan]\n[dim]Proyecto: {slug}[/dim]",
        border_style="cyan",
    ))

    try:
        # --- Validación de argumentos y proyecto ---
        if not SLUG_PATTERN.match(slug):
            raise PipelineError("--slug solo puede contener letras, números, guiones y guiones bajos (A-Z, a-z, 0-9, - y _).")
        workspace = WORKSPACE_DIR / slug
        script_path = workspace / "script.json"
        if not workspace.is_dir():
            raise PipelineError(f"No existe el proyecto '{slug}' en {WORKSPACE_DIR}. Genera el video primero con 'generate'.")
        if not script_path.is_file():
            raise PipelineError(f"El proyecto '{slug}' no tiene script.json. Ejecuta 'generate' primero.")
        if not (workspace / RUN_CONFIG_FILENAME).is_file():
            raise PipelineError(
                f"El proyecto '{slug}' no tiene {RUN_CONFIG_FILENAME} (¿se generó con una versión anterior?). "
                "Ejecuta 'generate' de nuevo con las mismas opciones para crearlo."
            )
        config = RunConfig.load(workspace)

        if not scene:
            raise PipelineError("Indica al menos una escena con --scene N (repetible).")
        scene_ids = sorted(set(scene))
        if (prompt is not None or narration is not None) and len(scene_ids) != 1:
            raise PipelineError("--prompt y --narration solo se pueden usar con exactamente una --scene.")
        if prompt is not None and not prompt.strip():
            raise PipelineError("--prompt no puede estar vacío.")
        if narration is not None and not narration.strip():
            raise PipelineError("--narration no puede estar vacío.")
        if seed is not None and seed < 0:
            raise PipelineError("--seed debe ser un entero mayor o igual a 0.")

        try:
            project = VideoProjectSchema.model_validate_json(script_path.read_text(encoding="utf-8"))
        except Exception as e:
            raise PipelineError(f"script.json de '{slug}' no es válido: {e}") from e
        light_normalize_project(project, get_profile(config.profile))  # ids 1..N, igual que el pipeline
        existing_ids = [sc.scene_id for sc in project.scenes]
        missing = [i for i in scene_ids if i not in existing_ids]
        if missing:
            raise PipelineError(
                f"Escena(s) inexistente(s): {', '.join(map(str, missing))}. "
                f"El guión tiene {len(existing_ids)} escenas (1 a {len(existing_ids)})."
            )

        # Regla de re-roll: toda escena seleccionada se re-genera salvo que SOLO se pase --narration
        narration_only = narration is not None and prompt is None
        reroll_ids = [] if narration_only else scene_ids
        if narration_only and seed is not None:
            console.print("[yellow]Aviso: --seed se ignora porque solo se cambia la narración (la imagen se conserva).[/yellow]")
        if reroll_ids and not config.mock_images and not (FAL_KEY or os.getenv("FAL_KEY")):
            raise PipelineError(
                "No se encontró FAL_KEY para regenerar la imagen. Define FAL_KEY en el archivo .env "
                "o genera el proyecto con --mock-images."
            )

        env_status = _check_environment()

        # --- Persistir ediciones en script.json ---
        if prompt is not None or narration is not None:
            target = next(sc for sc in project.scenes if sc.scene_id == scene_ids[0])
            if prompt is not None:
                target.visual_prompt_en = prompt.strip()
            if narration is not None:
                target.narration_es = narration.strip()
            write_script(project, script_path)
            console.print(f"[green]✓ script.json actualizado (escena {scene_ids[0]:02d}).[/green]")

        console.print(
            f"[cyan]Escenas a rehacer:[/] {_fmt_ids(scene_ids)} | "
            f"[cyan]re-roll de imagen:[/] {_fmt_ids(reroll_ids)}"
            + (f" | [cyan]seed:[/] {seed}" if seed is not None and reroll_ids else "")
        )

        result = run_pipeline(
            workspace,
            config,
            ForceFlags(),
            env_status,
            image_overrides=ImageOverrides(scene_ids=frozenset(reroll_ids), seed=seed),
        )
    except PipelineError as e:
        _print_pipeline_error(e)
        raise typer.Exit(code=1)

    elapsed = time.time() - start_time
    stats = result.stats
    real_images = 0 if config.mock_images else len(stats.images)
    cost = real_images * FLUX_COST_USD

    table = Table(title="🔄 Escenas Regeneradas", border_style="green")
    table.add_column("Propiedad", style="cyan")
    table.add_column("Detalle", style="white")
    table.add_row("Escenas indicadas", _fmt_ids(scene_ids))
    table.add_row("Audio regenerado", _fmt_ids(stats.audio))
    table.add_row("Imagen regenerada", _fmt_ids(stats.images))
    table.add_row("Clip re-renderizado", _fmt_ids(stats.clips))
    table.add_row("Video final", "re-renderizado" if stats.final else "sin cambios (en caché)")
    table.add_row(
        "Costo Fal.ai estimado",
        f"${cost:.3f} ({real_images} imagen(es) Flux × ${FLUX_COST_USD})" if not config.mock_images else "$0.000 (modo mock)",
    )
    table.add_row("Archivo MP4 Final", str(result.final_video_path))
    table.add_row("Tiempo de Proceso", f"{elapsed:.1f} segundos")
    console.print("\n")
    console.print(table)
    console.print("\n[bold green]✓ Escena(s) regenerada(s) y video final actualizado.[/bold green]\n")


if __name__ == "__main__":
    # Ayuda amistosa para el estilo antiguo: `python main.py --profile ...` (sin subcomando)
    if len(sys.argv) > 1 and sys.argv[1].startswith("-") and sys.argv[1] not in ("--help", "-h"):
        console.print(
            "[bold yellow]Aviso:[/] ahora main.py usa subcomandos. Usa [bold]python main.py generate[/bold] "
            f"{escape(' '.join(sys.argv[1:]))}\n"
            "Ejecuta [bold]python main.py --help[/bold] para ver los comandos disponibles (generate, retry-scene).\n"
        )
        raise SystemExit(2)
    app()
