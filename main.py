"""Professional CLI for Automated Short Video Pipeline (main.py).

Comandos:
  generate          Genera un video completo (caché por huella de contenido por escena).
  retry-scene       Regenera escenas concretas (imagen y/o narración) y reensambla el video.
  styles            Lista los estilos visuales disponibles (opción --style de generate).
  create-character  Crea un personaje persistente (imagen de referencia) para los videos con protagonista.
  characters        Lista los personajes de la biblioteca (opción --character de generate).
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

from character_engine import (
    DEFAULT_CANDIDATES,
    MAX_CANDIDATES,
    Character,
    available_slugs,
    choose_candidate,
    create_character,
    create_hint,
    list_characters,
    load_character,
    validate_slug,
)
from config import BASE_DIR, CHARACTERS_DIR, FAL_KEY, FORMATS, IMAGE_COST_USD, SEEDREAM_COST_USD, WORKSPACE_DIR, get_format
from env_checker import check_and_prepare_environment
from pipeline_utils import CacheManifest, PipelineError, RunStats
from profiles import VideoProfile, get_profile, PROFILES
from script_engine import (
    VideoProjectSchema,
    generate_or_load_script,
    light_normalize_project,
    resolve_format_id,
    resolve_style_id,
    write_script,
)
from styles import STYLES
from tts_engine import generate_tts_sync
from image_engine import ImageOverrides, generate_images_sync
from subtitle_engine import generate_subtitles_ass
from audio_engine import mix_audio, select_library_track
from video_engine import render_all_scene_clips, assemble_final_video

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass

app = typer.Typer(
    name="short-video-gen",
    help=(
        "CLI profesional para automatizar la creación de videos cortos: verticales (Shorts/Reels/TikTok, 9:16) "
        "u horizontales (YouTube, 16:9).\n\n"
        "Comandos: 'generate' crea un video completo; 'retry-scene' regenera escenas concretas de un proyecto existente; "
        "'styles' lista los estilos visuales; 'create-character' y 'characters' gestionan los personajes persistentes "
        "(protagonista con la misma cara en todas las escenas)."
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
    music: Optional[str] = None  # ruta de la pista usada (--music o elegida de assets/music/); None = sin elegir/drone
    music_source: Optional[str] = None  # "explicit" (--music) | "library" (assets/music/ por hash del slug)
    mock_script: bool = False
    mock_images: bool = False
    style: str = ""  # estilo visual resuelto (styles.STYLES); "" = guion legado sin estilo
    video_format: str = "vertical"  # formato de video resuelto (config.FORMATS); los run_config.json antiguos son verticales
    character: str = ""  # slug del personaje protagonista (characters/<slug>/); "" = sin protagonista
    minutes: Optional[float] = None  # duración objetivo del video (--minutes); None = la del perfil

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
                music_source=data.get("music_source") or ("explicit" if data.get("music") else None),
                mock_script=bool(data.get("mock_script", False)),
                mock_images=bool(data.get("mock_images", False)),
                style=str(data.get("style") or ""),
                video_format=str(data.get("video_format") or "vertical"),
                character=str(data.get("character") or ""),
                minutes=_as_minutes(data.get("minutes")),
            )
        except (OSError, ValueError, KeyError, TypeError) as e:
            raise PipelineError(f"{RUN_CONFIG_FILENAME} es inválido o está incompleto ({e}). Ejecuta 'generate' de nuevo para recrearlo.") from e


def _as_minutes(value: Any) -> Optional[float]:
    """Duración objetivo guardada en run_config.json: número > 0, o None si falta o no es válida."""
    try:
        minutes = float(value)
    except (TypeError, ValueError):
        return None
    return minutes if minutes > 0 else None


def _read_run_config_raw(workspace: Path) -> dict[str, Any]:
    """Contenido crudo del run_config.json existente; {} si no hay o no se puede leer."""
    try:
        data = json.loads((workspace / RUN_CONFIG_FILENAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _read_run_config_format(workspace: Path) -> Optional[str]:
    """video_format del run_config.json existente; None si no hay (o no se puede leer) o es de una versión sin el campo."""
    return str(_read_run_config_raw(workspace).get("video_format") or "") or None


def resolve_character(slug: str, profile: VideoProfile, mock_images: bool) -> Optional[Character]:
    """Personaje de una ejecución: None si no hay (y el perfil no lo exige). Lanza PipelineError si el perfil usa
    protagonista y no se indicó ninguno, si no existe, o si es un retrato mock y se van a pagar imágenes reales."""
    if not slug:
        if profile.uses_protagonist:
            available = available_slugs()
            raise PipelineError(
                f"El perfil '{profile.name}' usa un protagonista recurrente: indica un personaje con --character SLUG.\n"
                f"Personajes disponibles: {', '.join(available) if available else 'ninguno'}.\n"
                f"Crea uno con: {create_hint()}"
            )
        return None
    character = load_character(slug)
    if character.mock and not mock_images:
        raise PipelineError(
            f"El personaje '{slug}' se creó con --mock (retrato de prueba dibujado con Pillow): solo sirve con --mock-images. "
            "Para generar imágenes reales crea un personaje sin --mock o con --from-image."
        )
    return character


@dataclass
class ForceFlags:
    """Etapas cuya caché se ignora (un --force total activa las cuatro)."""

    script: bool = False
    audio: bool = False
    images: bool = False
    video: bool = False  # clips + mezcla de audio + video final

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


def resolve_music(workspace: Path, config: RunConfig, profile_name: str, env_status: dict[str, str]) -> Path:
    """Pista de fondo: --music > pista ya registrada en run_config.json > biblioteca assets/music/ > drone sintético.

    La elección de la biblioteca es determinista (hash del slug) y se guarda en run_config.json para que las
    ejecuciones posteriores y retry-scene usen exactamente la misma pista (la caché de la mezcla sigue válida).
    """
    if config.music:
        recorded = Path(config.music)
        if recorded.is_file():
            return recorded
        if config.music_source == "explicit":
            raise PipelineError(f"El archivo de música indicado en --music ya no existe: {recorded}")
        console.print(f"[yellow]Aviso: la pista de música registrada ya no existe ({recorded}); se elegirá otra.[/yellow]")
        config.music = config.music_source = None

    track = select_library_track(workspace.name, profile_name)
    if track is not None:
        config.music, config.music_source = str(track.resolve()), "library"
        config.save(workspace)
        console.print(f"[green]✓ Música de la biblioteca:[/] {track.name} [dim](elegida por hash del slug; guardada en run_config.json)[/dim]")
        return track

    console.print(
        "[yellow]⚠ Sin música en assets/music/: el video no tendrá música de fondo real "
        "(se usa el drone sintético incluido).[/yellow]"
    )
    return Path(env_status["bgm"])


def run_pipeline(
    workspace: Path,
    config: RunConfig,
    force: ForceFlags,
    env_status: dict[str, str],
    image_overrides: Optional[ImageOverrides] = None,
    persist_config: bool = False,
) -> PipelineResult:
    """Las 5 etapas del pipeline, compartidas por 'generate' y 'retry-scene'. Lanza PipelineError ante fallos.

    persist_config=True (generate) guarda run_config.json solo cuando el guion ya se generó o cargó con éxito: si esa
    etapa falla, el run_config.json anterior sigue coincidiendo con el script.json existente (retry-scene no recalcula).
    """
    profile = get_profile(config.profile)
    manifest = CacheManifest(workspace)
    stats = RunStats()
    protagonist = resolve_character(config.character, profile, config.mock_images)

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
        style_id=config.style,
        format_id=config.video_format,
        protagonist=protagonist,
        target_minutes=config.minutes,
    )
    if protagonist is not None and project.protagonist_slug != protagonist.slug:
        console.print(
            f"[yellow]⚠ El guion en caché no usa el personaje '{protagonist.slug}' "
            f"(protagonist_slug='{project.protagonist_slug}'): las imágenes siguen al guion. "
            "Usa --force-script para regenerarlo con el personaje (también regenera imágenes).[/yellow]"
        )
    if (
        config.style != project.visual_style_id
        or config.video_format != project.video_format
        or config.character != project.protagonist_slug
    ):
        # El estilo, el formato y el protagonista reales son los del guion (p. ej. recién generado con los defaults del perfil)
        config.style = project.visual_style_id
        config.video_format = project.video_format
        config.character = project.protagonist_slug
        persist_config = True
    if persist_config:
        config.save(workspace)
    video_format = get_format(project.video_format)  # ya validado por generate_or_load_script

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
    generate_subtitles_ass(
        timing_metadata=timing_metadata, output_ass_path=ass_path, video_format=video_format, hook_text=project.hook_text
    )

    # Módulo 5: Video Engine
    console.rule("[bold cyan]Módulo 5: Ensamblado y Renderizado FFmpeg[/bold cyan]")
    render_all_scene_clips(
        timing_metadata=timing_metadata,
        images_dir=workspace / "images",
        clips_dir=workspace / "clips",
        manifest=manifest,
        video_format=video_format,
        force=force.video,
        stats=stats,
    )

    bgm_file = resolve_music(workspace, config, profile.name, env_status)
    whoosh_file = Path(env_status["whoosh"])
    final_video_path = workspace / "final_video.mp4"

    mix_path = mix_audio(
        timing_metadata=timing_metadata,
        workspace_dir=workspace,
        bgm_path=bgm_file,
        whoosh_path=whoosh_file,
        manifest=manifest,
        force=force.video,
        stats=stats,
    )

    assemble_final_video(
        project=project,
        timing_metadata=timing_metadata,
        workspace_dir=workspace,
        subtitles_ass_path=ass_path,
        mix_audio_path=mix_path,
        output_final_path=final_video_path,
        manifest=manifest,
        video_format=video_format,
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
        help="Re-renderizar solo los clips, la mezcla de audio y el video final.",
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
        help="Ruta a una pista de música de fondo (.mp3/.m4a/.wav). Sin esta opción se elige una de assets/music/.",
    ),
    style: Optional[str] = typer.Option(
        None,
        "--style",
        help=(
            f"Estilo visual: {', '.join(STYLES)}. Por defecto: el del guion en caché, o el del perfil si es nuevo "
            "(ver 'python main.py styles'). Cambiarlo en un video existente regenera todas sus imágenes."
        ),
    ),
    video_format: Optional[str] = typer.Option(
        None,
        "--format",
        help=(
            f"Formato de video: {', '.join(FORMATS)} (vertical 1080x1920 para Shorts/Reels/TikTok; horizontal "
            "1920x1080 para YouTube). Por defecto: el del proyecto existente, o el del perfil si es nuevo. "
            "Cambiarlo en un video existente regenera todas sus imágenes y clips."
        ),
    ),
    character: Optional[str] = typer.Option(
        None,
        "--character",
        help=(
            "Slug de un personaje de la biblioteca (ver 'python main.py characters'): protagonista con la misma cara en todas "
            "las escenas donde aparece. Obligatorio en los perfiles con protagonista (que_pasaria, historia, chisme). "
            "Con protagonista las imágenes usan Seedream v4.5 (~$0.04 c/u) en lugar de Flux."
        ),
    ),
    minutes: Optional[float] = typer.Option(
        None,
        "--minutes",
        help="Duración objetivo del video en minutos (ajusta el número de escenas del guion). Por defecto, la del perfil.",
    ),
):
    """Genera un video completo (vertical 9:16 u horizontal 16:9) con edición dinámica, SFX, BGM y subtítulos animados."""
    start_time = time.time()

    force_flags = ForceFlags.all() if force else ForceFlags(force_script, force_audio, force_images, force_video)

    # 1. Print Welcome Banner
    banner_text = (
        "[bold cyan]⚡ SHORT VIDEO PIPELINE CLI ⚡[/bold cyan]\n"
        "[white]Generador 100% automatizado de videos verticales (9:16) y horizontales (16:9) para TikTok, Reels, Shorts y YouTube[/white]\n"
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
    if slug is not None and not SLUG_PATTERN.fullmatch(slug):
        console.print("[bold red]Error:[/] --slug solo puede contener letras, números, guiones y guiones bajos (A-Z, a-z, 0-9, - y _).")
        raise typer.Exit(code=1)
    if music is not None and not music.is_file():
        console.print(f"[bold red]Error:[/] el archivo de música indicado en --music no existe: {music}")
        raise typer.Exit(code=1)

    # Estilo visual: --style > guion en caché > default del perfil (se valida antes de tocar nada)
    target_slug = slug if slug else slugify(topic)
    try:
        style_id = resolve_style_id(style, WORKSPACE_DIR / target_slug / "script.json", prof_obj)
    except ValueError as e:
        console.print(f"[bold red]Error:[/] {escape(str(e))}")
        raise typer.Exit(code=1)
    console.print(f"[cyan]Estilo visual:[/] {style_id or 'legado (guion sin estilo; se conserva su prompt anterior)'}")

    # Formato de video: --format > guion en caché > run_config.json > default del perfil (se valida antes de tocar nada)
    try:
        format_id = resolve_format_id(
            video_format,
            WORKSPACE_DIR / target_slug / "script.json",
            prof_obj,
            _read_run_config_format(WORKSPACE_DIR / target_slug),
        )
    except ValueError as e:
        console.print(f"[bold red]Error:[/] {escape(str(e))}")
        raise typer.Exit(code=1)
    chosen_format = get_format(format_id)
    console.print(f"[cyan]Formato de video:[/] {chosen_format.id} ({chosen_format.width}x{chosen_format.height})")

    # Personaje protagonista y duración objetivo: --character / --minutes > run_config.json del proyecto existente
    # (se validan antes de tocar nada; el guion en caché no depende de ellos salvo que se regenere)
    previous_config = _read_run_config_raw(WORKSPACE_DIR / target_slug)
    character_slug = character if character is not None else str(previous_config.get("character") or "")
    target_minutes = minutes if minutes is not None else _as_minutes(previous_config.get("minutes"))
    if minutes is not None and minutes <= 0:
        console.print("[bold red]Error:[/] --minutes debe ser mayor que 0.")
        raise typer.Exit(code=1)
    try:
        chosen_character = resolve_character(character_slug, prof_obj, mock_images)
    except PipelineError as e:
        _print_pipeline_error(e)
        raise typer.Exit(code=1)
    if chosen_character is not None:
        console.print(f"[cyan]Personaje protagonista:[/] {chosen_character.name} ({chosen_character.slug})")
    if target_minutes is not None:
        console.print(f"[cyan]Duración objetivo:[/] {target_minutes:g} min")

    # 3. Environment & Assets Check (Fase 0)
    env_status = _check_environment()

    try:
        # 4. Prepare Workspace (run_config.json se guarda en run_pipeline, una vez que el guion está listo)
        project_workspace = WORKSPACE_DIR / target_slug
        project_workspace.mkdir(parents=True, exist_ok=True)
        console.print(f"[cyan]Workspace activo:[/] {project_workspace}")

        config = RunConfig(
            topic=topic,
            profile=prof_obj.name,
            voice=voice,
            music=str(music.resolve()) if music else None,
            music_source="explicit" if music else None,
            mock_script=mock_script,
            mock_images=mock_images,
            style=style_id,
            video_format=format_id,
            character=character_slug,
            minutes=target_minutes,
        )

        # 5. Las 5 etapas
        result = run_pipeline(project_workspace, config, force_flags, env_status, persist_config=True)
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
    table.add_row("Estilo visual", result.project.visual_style_id or "legado (sin estilo)")
    final_format = get_format(result.project.video_format)
    table.add_row("Formato", f"{final_format.id} ({final_format.width}x{final_format.height})")
    table.add_row("Escenas", str(len(result.project.scenes)))
    table.add_row("Duración Total", f"{result.timing_metadata.get('total_duration_sec', 0):.2f} segundos ({result.timing_metadata.get('total_frames', 0)} fotogramas)")
    if result.project.protagonist_slug:
        table.add_row("Protagonista", result.project.protagonist_slug)
    table.add_row("Regenerado", _stats_line(result.stats))
    table.add_row("Costo Fal.ai", _cost_summary(result.stats))
    table.add_row("Archivo MP4 Final", str(result.final_video_path))
    table.add_row("Metadatos Publicación", str(project_workspace / "post_metadata.txt"))
    table.add_row("Tiempo de Proceso", f"{elapsed:.1f} segundos")

    console.print("\n")
    console.print(table)
    console.print("\n[bold green]✓ Todo el pipeline se ejecutó sin errores. Listo para publicar en redes.[/bold green]\n")


def _cost_summary(stats: RunStats) -> str:
    """Costo REAL de la ejecución: imágenes pagadas por endpoint (cantidad x precio de config.IMAGE_COST_USD), guion (LLM)
    y total. Lo servido de caché y los modos mock no cuestan nada."""
    lines = [
        f"{count} imagen(es) {endpoint.removeprefix('fal-ai/')} × ${IMAGE_COST_USD.get(endpoint, 0.0):g} = "
        f"${count * IMAGE_COST_USD.get(endpoint, 0.0):.3f}"
        for endpoint, count in stats.paid_images.items()
    ]
    if stats.llm_cost_usd:
        lines.append(f"guion (LLM): ${stats.llm_cost_usd:.4f}")
    if not lines:
        return "$0.000 (sin gasto: caché o modo mock)"
    lines.append(f"TOTAL: ${stats.total_cost_usd():.4f}")
    return "\n".join(lines)


def _stats_line(stats: RunStats) -> str:
    return (
        f"guion: {'sí' if stats.script else 'no'} | audio: {len(stats.audio)} | imágenes: {len(stats.images)} | "
        f"clips: {len(stats.clips)} | mezcla de audio: {'sí' if stats.mix else 'no (en caché)'} | "
        f"video final: {'sí' if stats.final else 'no (en caché)'}"
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
        if not SLUG_PATTERN.fullmatch(slug):
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
        resolve_character(config.character, get_profile(config.profile), config.mock_images)  # falla pronto si falta el personaje

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
        config.style = project.visual_style_id  # retry-scene siempre usa el estilo y el formato del guion
        config.video_format = project.video_format
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

    table = Table(title="🔄 Escenas Regeneradas", border_style="green")
    table.add_column("Propiedad", style="cyan")
    table.add_column("Detalle", style="white")
    table.add_row("Escenas indicadas", _fmt_ids(scene_ids))
    table.add_row("Audio regenerado", _fmt_ids(stats.audio))
    table.add_row("Imagen regenerada", _fmt_ids(stats.images))
    table.add_row("Clip re-renderizado", _fmt_ids(stats.clips))
    table.add_row("Mezcla de audio", "regenerada" if stats.mix else "sin cambios (en caché)")
    table.add_row("Video final", "re-renderizado" if stats.final else "sin cambios (en caché)")
    table.add_row("Costo Fal.ai", _cost_summary(stats))
    table.add_row("Archivo MP4 Final", str(result.final_video_path))
    table.add_row("Tiempo de Proceso", f"{elapsed:.1f} segundos")
    console.print("\n")
    console.print(table)
    console.print("\n[bold green]✓ Escena(s) regenerada(s) y video final actualizado.[/bold green]\n")


# --------------------------------------------------------------------------- #
# Comando: styles
# --------------------------------------------------------------------------- #

@app.command("styles")
def list_styles():
    """Lista los estilos visuales disponibles para --style y los perfiles que los usan por defecto."""
    table = Table(title="Estilos visuales (usa: generate --style <id>)", border_style="cyan")
    table.add_column("ID", style="bold cyan", no_wrap=True)
    table.add_column("Nombre", style="white")
    table.add_column("Descripción", style="white")
    table.add_column("Por defecto en", style="green")
    for style_id, st in STYLES.items():
        defaults = [name for name, prof in PROFILES.items() if prof.default_style_id == style_id]
        table.add_row(st.id, st.label, st.description, ", ".join(defaults) if defaults else "-")
    console.print(table)


# --------------------------------------------------------------------------- #
# Comandos: create-character y characters
# --------------------------------------------------------------------------- #

def _display_path(path: Path) -> str:
    """Ruta relativa al repositorio si es posible (más corta en las tablas)."""
    try:
        return path.resolve().relative_to(BASE_DIR).as_posix()
    except ValueError:
        return str(path)


@app.command("create-character")
def create_character_cmd(
    slug: str = typer.Argument(..., help="Identificador del personaje (carpeta characters/<slug>/; letras, números, - y _)."),
    name: Optional[str] = typer.Option(None, "--name", help="Nombre del personaje (obligatorio al crearlo)."),
    description: Optional[str] = typer.Option(
        None,
        "--description",
        help="Descripción física EN INGLÉS (obligatoria al crearlo): edad, rasgos, pelo, ropa. Se inyecta en los prompts de las escenas.",
    ),
    style: Optional[str] = typer.Option(
        None,
        "--style",
        help=f"Estilo visual de las candidatas: {', '.join(STYLES)} (ver 'python main.py styles'). Sin él, retrato neutro sin estilo.",
    ),
    candidates: int = typer.Option(
        DEFAULT_CANDIDATES,
        "--candidates",
        help=f"Cuántas candidatas generar con Seedream v4.5 (1 a {MAX_CANDIDATES}; ~${SEEDREAM_COST_USD:g} cada una).",
    ),
    from_image: Optional[Path] = typer.Option(
        None,
        "--from-image",
        help="Importar una imagen propia como referencia en vez de generar candidatas (sin costo).",
    ),
    choose: Optional[int] = typer.Option(
        None,
        "--choose",
        help="Candidata (1, 2...) que será ref.png. Con un personaje ya creado solo cambia la referencia, sin generar nada.",
    ),
    mock: bool = typer.Option(False, "--mock", help="Dibujar retratos de prueba con Pillow ($0.00); solo sirven con --mock-images."),
):
    """Crea un personaje persistente (o cambia su referencia con --choose): el protagonista conserva la misma cara en todos los videos."""
    try:
        validate_slug(slug)
        if slug in available_slugs():
            if choose is None:
                raise PipelineError(
                    f"El personaje '{slug}' ya existe. Usa --choose N para cambiar su referencia, otro slug para crear uno nuevo, "
                    f"o borra {_display_path(CHARACTERS_DIR / slug)} para rehacerlo."
                )
            ignored = [flag for flag, value in (("--name", name), ("--description", description), ("--style", style), ("--from-image", from_image)) if value]
            if ignored:
                console.print(f"[yellow]Aviso: {', '.join(ignored)} se ignora(n): el personaje ya existe (solo se cambia la referencia).[/yellow]")
            char = choose_candidate(slug, choose)
            console.print(f"[bold green]✓ Referencia de '{slug}' cambiada a {char.chosen}[/bold green] -> {_display_path(char.ref_image)}")
            console.print(
                "[dim]Los videos que usan este personaje regeneran solo las escenas donde aparece (la huella incluye el hash de ref.png).[/dim]"
            )
            return
        if not name and not description and choose is not None:
            load_character(slug)  # --choose sobre un personaje inexistente: error estándar con los slugs disponibles
        if not name or not description:
            raise PipelineError(f"--name y --description son obligatorios al crear un personaje. Ejemplo:\n{create_hint(slug)}")
        char = create_character(
            slug,
            name,
            description,
            style_id=style or "",
            candidates=candidates,
            from_image=from_image,
            choose=choose,
            mock=mock,
        )
    except PipelineError as e:
        _print_pipeline_error(e)
        raise typer.Exit(code=1)

    table = Table(title="Personaje creado", border_style="green")
    table.add_column("Propiedad", style="cyan")
    table.add_column("Detalle", style="white")
    table.add_row("Slug", char.slug)
    table.add_row("Nombre", escape(char.name))
    table.add_row("Descripción", escape(char.description_en))
    table.add_row("Estilo", char.style_id or "sin estilo (retrato neutro)")
    table.add_row("Origen", "imagen importada" if char.source == "imported" else ("retratos mock (Pillow)" if char.mock else "Seedream v4.5 text-to-image"))
    if char.candidates:
        table.add_row("Candidatas", "\n".join(_display_path(char.dir / c) for c in char.candidates))
    table.add_row("Referencia (ref.png)", f"{_display_path(char.ref_image)}" + (f" (= {char.chosen})" if char.candidates else ""))
    if char.source == "generated" and not char.mock:
        table.add_row("Costo Fal.ai", f"{len(char.candidates)} imagen(es) × ${SEEDREAM_COST_USD:g} = ${len(char.candidates) * SEEDREAM_COST_USD:.3f}")
    console.print(table)
    if len(char.candidates) > 1:
        console.print(f"[dim]Revisa las candidatas y elige otra sin generar nada: python main.py create-character {char.slug} --choose N[/dim]")
    if char.mock:
        console.print("[yellow]Personaje mock: úsalo solo con --mock-images (para producción crea uno sin --mock o con --from-image).[/yellow]")
    console.print(f"[bold green]✓ Listo. Úsalo con:[/bold green] python main.py generate --profile <perfil> --topic \"...\" --character {char.slug}")


@app.command("characters")
def list_characters_cmd():
    """Lista los personajes de la biblioteca (characters/) para usarlos con 'generate --character <slug>'."""
    chars = list_characters()
    if not chars:
        console.print(f"[yellow]No hay personajes en {_display_path(CHARACTERS_DIR)}.[/yellow] Crea uno con: {escape(create_hint())}")
        return
    table = Table(title="Personajes (usa: generate --character <slug>)", border_style="cyan")
    table.add_column("Slug", style="bold cyan", no_wrap=True)
    table.add_column("Nombre", style="white")
    table.add_column("Descripción", style="white")
    table.add_column("Referencia", style="green", overflow="fold")
    for char in chars:
        desc = char.description_en if len(char.description_en) <= 70 else char.description_en[:67].rstrip() + "..."
        table.add_row(char.slug, escape(char.name), escape(desc), _display_path(char.ref_image) + (" (mock)" if char.mock else ""))
    console.print(table)


if __name__ == "__main__":
    # Ayuda amistosa para el estilo antiguo: `python main.py --profile ...` (sin subcomando)
    if len(sys.argv) > 1 and sys.argv[1].startswith("-") and sys.argv[1] not in ("--help", "-h"):
        console.print(
            "[bold yellow]Aviso:[/] ahora main.py usa subcomandos. Usa [bold]python main.py generate[/bold] "
            f"{escape(' '.join(sys.argv[1:]))}\n"
            "Ejecuta [bold]python main.py --help[/bold] para ver los comandos disponibles (generate, retry-scene, styles, create-character, characters).\n"
        )
        raise SystemExit(2)
    app()
