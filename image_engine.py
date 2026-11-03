"""Image generation engine supporting Fal.ai Flux/schnell and Mock mode (Módulo 3)."""

import asyncio
import os
import random
import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
import httpx
from PIL import Image, ImageDraw, ImageFont
from rich.console import Console

import fal_client
from config import (
    FAL_KEY,
    VIDEO_WIDTH,
    VIDEO_HEIGHT,
    FONTS_DIR,
    SUBTITLE_FONT_FILENAME,
)
from pipeline_utils import CacheManifest, PipelineError, RunStats, fingerprint
from script_engine import SceneSchema, VideoProjectSchema

console = Console()

# Parámetros de generación (forman parte de la huella de cada imagen)
FLUX_MODEL = "fal-ai/flux/schnell"
FLUX_IMAGE_SIZE = "portrait_16_9"
FLUX_STEPS = 4
FLUX_COST_USD = 0.003  # costo estimado por imagen
MIN_IMAGE_BYTES = 5000


@dataclass
class ImageOverrides:
    """Escenas cuya imagen debe re-generarse sí o sí (re-roll), con semilla opcional."""

    scene_ids: frozenset[int] = frozenset()
    seed: Optional[int] = None


def create_mock_scene_image(
    scene: SceneSchema,
    output_path: Path,
    width: int = VIDEO_WIDTH,
    height: int = VIDEO_HEIGHT,
    seed: Optional[int] = None,
) -> Path:
    """Generate a sleek local mock image with a dark gradient and prompt text (cost $0.00)."""
    # Create vertical gradient background
    img = Image.new("RGB", (width, height))
    draw = ImageDraw.Draw(img)

    # Gradient colors: Deep navy to midnight violet
    top_color = (15, 20, 35)
    bottom_color = (30, 20, 50)
    for y in range(height):
        ratio = y / height
        r = int(top_color[0] * (1 - ratio) + bottom_color[0] * ratio)
        g = int(top_color[1] * (1 - ratio) + bottom_color[1] * ratio)
        b = int(top_color[2] * (1 - ratio) + bottom_color[2] * ratio)
        draw.line([(0, y), (width, y)], fill=(r, g, b))

    # Outer neon border
    border_color = (80, 120, 220)
    draw.rectangle([(40, 40), (width - 40, height - 40)], outline=border_color, width=4)

    # Load font if available
    font_file = FONTS_DIR / SUBTITLE_FONT_FILENAME
    try:
        font_badge = ImageFont.truetype(str(font_file), 48) if font_file.exists() else ImageFont.load_default()
        font_title = ImageFont.truetype(str(font_file), 36) if font_file.exists() else ImageFont.load_default()
        font_body = ImageFont.truetype(str(font_file), 30) if font_file.exists() else ImageFont.load_default()
    except Exception:
        font_badge = font_title = font_body = ImageFont.load_default()

    # Draw Scene Badge
    badge_text = f"ESCENA {scene.scene_id:02d} [MOCK]"
    badge_bg = (235, 75, 90)
    draw.rounded_rectangle([(100, 160), (width - 100, 240)], radius=15, fill=badge_bg)
    draw.text((width // 2, 200), badge_text, fill=(255, 255, 255), font=font_badge, anchor="mm")

    # Camera effect tag
    effect_text = f"EFECTO: {scene.camera_effect.upper()}"
    draw.text((width // 2, 290), effect_text, fill=(0, 240, 200), font=font_title, anchor="mm")

    # Narration preview box
    draw.rounded_rectangle([(80, 360), (width - 80, 720)], radius=12, fill=(25, 30, 50, 200), outline=(50, 70, 100), width=2)
    draw.text((110, 390), "VOZ / LOCUCIÓN (ES):", fill=(255, 200, 0), font=font_title)
    wrapped_narration = textwrap.fill(scene.narration_es, width=42)
    draw.text((110, 450), wrapped_narration, fill=(240, 240, 240), font=font_body)

    # Visual prompt box
    draw.rounded_rectangle([(80, 760), (width - 80, 1300)], radius=12, fill=(20, 25, 45, 200), outline=(70, 90, 140), width=2)
    draw.text((110, 790), "FLUX VISUAL PROMPT (EN):", fill=(0, 220, 255), font=font_title)
    wrapped_prompt = textwrap.fill(scene.visual_prompt_en, width=42)
    draw.text((110, 850), wrapped_prompt, fill=(200, 220, 240), font=font_body)

    # Keywords box
    if scene.highlight_words:
        kw_text = "PALABRAS CLAVE: " + ", ".join(scene.highlight_words)
        draw.text((width // 2, 1360), kw_text, fill=(57, 255, 20), font=font_title, anchor="mm")

    # Re-roll tag: makes a retried mock card visibly different
    if seed is not None:
        draw.text((width // 2, 1450), f"RETRY - SEED {seed}", fill=(255, 140, 60), font=font_title, anchor="mm")

    # Footer notice
    footer_text = "MODO DE DEPURACIÓN -- COSTO $0.00"
    draw.text((width // 2, height - 120), footer_text, fill=(160, 170, 190), font=font_title, anchor="mm")

    tmp_path = output_path.with_name(output_path.stem + ".part.png")
    img.save(tmp_path, "PNG")
    os.replace(tmp_path, output_path)
    return output_path


def compose_scene_prompt(scene: SceneSchema, project: VideoProjectSchema) -> str:
    """Build the final composite prompt injecting character consistency."""
    parts = [project.global_visual_style.strip()]

    # Inject character physical descriptions if characters present in this scene
    if scene.characters_in_scene and project.characters:
        char_descriptions = []
        for char_id in scene.characters_in_scene:
            if char_id in project.characters:
                char_descriptions.append(project.characters[char_id].strip())
        if char_descriptions:
            parts.append("Featuring: " + "; ".join(char_descriptions))

    parts.append(scene.visual_prompt_en.strip())
    parts.append("vertical 9:16 composition, vibrant details, high resolution, no text, no watermarks, cinematic lighting")

    return ". ".join(parts)


async def generate_single_flux_image(
    prompt: str,
    output_path: Path,
    semaphore: asyncio.Semaphore,
    max_attempts: int = 3,
    seed: Optional[int] = None,
) -> Path:
    """Call fal-ai/flux/schnell (with retries, NSFW check) and download image to disk.

    If `seed` is given it is used on every attempt (except after an NSFW rejection, which needs a new seed);
    otherwise each attempt uses a fresh random seed.
    """
    if FAL_KEY:
        os.environ["FAL_KEY"] = FAL_KEY

    loop = asyncio.get_running_loop()
    last_error: Optional[Exception] = None
    use_fixed_seed = seed is not None

    for attempt in range(1, max_attempts + 1):
        try:
            attempt_seed = seed if use_fixed_seed else random.randint(0, 2**31 - 1)
            async with semaphore:
                res = await loop.run_in_executor(
                    None,
                    lambda: fal_client.run(
                        FLUX_MODEL,
                        arguments={
                            "prompt": prompt,
                            "image_size": FLUX_IMAGE_SIZE,
                            "num_inference_steps": FLUX_STEPS,
                            "enable_safety_checker": True,
                            "seed": attempt_seed,
                        },
                    ),
                )

                images = res.get("images", [])
                if not images or not images[0].get("url"):
                    raise ValueError(f"Fal.ai flux/schnell no devolvió imágenes: {res}")

                nsfw_flags = res.get("has_nsfw_concepts") or []
                if nsfw_flags and nsfw_flags[0]:
                    use_fixed_seed = False  # the same seed would be rejected again
                    raise ValueError("La imagen fue marcada como NSFW por el filtro de seguridad")

                image_url = images[0]["url"]

                # Download image
                async with httpx.AsyncClient(timeout=60.0) as client:
                    resp = await client.get(image_url)
                    resp.raise_for_status()
                    image_bytes = resp.content

            # Save and ensure 1080x1920 PNG
            temp_path = output_path.with_suffix(".tmp")
            with open(temp_path, "wb") as f:
                f.write(image_bytes)

            part_path = output_path.with_name(output_path.stem + ".part.png")
            try:
                with Image.open(temp_path) as im:
                    rgb_im = im.convert("RGB")
                    if rgb_im.size != (VIDEO_WIDTH, VIDEO_HEIGHT):
                        rgb_im = rgb_im.resize((VIDEO_WIDTH, VIDEO_HEIGHT), Image.Resampling.LANCZOS)
                    rgb_im.save(part_path, "PNG")
                os.replace(part_path, output_path)
            finally:
                for leftover in (temp_path, part_path):
                    if leftover.exists():
                        leftover.unlink()

            return output_path

        except Exception as err:
            last_error = err
            if attempt < max_attempts:
                wait = 2 ** attempt
                console.print(
                    f"  [yellow]Intento {attempt}/{max_attempts} falló para {output_path.name}: "
                    f"{str(err)[:200]}. Reintentando en {wait}s...[/yellow]"
                )
                await asyncio.sleep(wait)

    raise PipelineError(f"{output_path.name}: {last_error}")


def image_fingerprint(prompt: str, mock_images: bool) -> str:
    """Huella de una imagen: prompt final compuesto + modo mock + modelo + tamaño + pasos."""
    return fingerprint(
        kind="image",
        prompt=prompt,
        mock_images=mock_images,
        model=FLUX_MODEL,
        image_size=FLUX_IMAGE_SIZE,
        steps=FLUX_STEPS,
    )


async def generate_all_images(
    project: VideoProjectSchema,
    output_dir: Path,
    manifest: CacheManifest,
    mock_images: bool = False,
    force: bool = False,
    overrides: Optional[ImageOverrides] = None,
    stats: Optional[RunStats] = None,
) -> list[Path]:
    """Generate or reuse scene images, cached per scene by content fingerprint."""
    overrides = overrides or ImageOverrides()
    images_dir = output_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)

    image_paths: list[Path] = []
    # (scene, image_path, fingerprint, seed, legacy_marker)
    pending: list[tuple[SceneSchema, Path, str, Optional[int], Path]] = []
    cached_count = 0

    console.print(
        f"[cyan]Procesando imágenes para {len(project.scenes)} escenas "
        f"({'MODO MOCK LOCAL' if mock_images else 'PRODUCCIÓN FAL.AI FLUX'})...[/cyan]"
    )

    for scene in project.scenes:
        image_path = images_dir / f"scene_{scene.scene_id:02d}.png"
        legacy_marker = image_path.with_suffix(".mock")  # marcador de Fase 1, reemplazado por el manifiesto
        image_paths.append(image_path)

        final_prompt = compose_scene_prompt(scene, project)
        fp = image_fingerprint(final_prompt, mock_images)
        reroll = scene.scene_id in overrides.scene_ids
        forced = force or reroll

        # Imagen real (pagada) vigente: su huella registrada coincide con la huella en modo real
        has_paid_image = (
            mock_images
            and image_path.exists()
            and image_path.stat().st_size > MIN_IMAGE_BYTES
            and manifest.get(image_path) == image_fingerprint(final_prompt, False)
        )

        is_cached = False
        if has_paid_image and not forced:
            is_cached = True
            console.print(f"  [dim]Escena {scene.scene_id:02d}: imagen real conservada (modo mock)[/dim]")
        elif has_paid_image and forced:
            console.print(
                f"  [yellow]⚠ Escena {scene.scene_id:02d}: se reemplazará una imagen real (pagada) por una tarjeta mock.[/yellow]"
            )
        if not forced and not has_paid_image:
            if manifest.has(image_path):
                is_cached = manifest.is_fresh(image_path, fp) and image_path.stat().st_size > MIN_IMAGE_BYTES
            elif image_path.exists():
                # Migración: imagen de un workspace de Fase 1 sin entrada en el manifiesto
                if legacy_marker.exists():
                    console.print(f"  [yellow]Escena {scene.scene_id:02d}: la imagen existente es un mock (legado); se regenerará[/yellow]")
                elif not mock_images and image_path.stat().st_size > MIN_IMAGE_BYTES:
                    manifest.record(image_path, fp)
                    is_cached = True
                    console.print(
                        f"  [dim]Escena {scene.scene_id:02d}: imagen existente adoptada en el manifiesto de caché (workspace anterior)[/dim]"
                    )
        if is_cached:
            cached_count += 1
            if not has_paid_image:
                console.print(f"  [green]✓ Escena {scene.scene_id:02d}: Imagen en caché[/green] ({image_path.name})")
            continue

        seed: Optional[int] = None
        if reroll:
            seed = overrides.seed if overrides.seed is not None else random.randint(0, 2**31 - 1)
        pending.append((scene, image_path, fp, seed, legacy_marker))

    generated = 0
    failures: list[str] = []

    if pending and mock_images:
        for scene, image_path, fp, seed, legacy_marker in pending:
            create_mock_scene_image(scene, image_path, seed=seed)
            manifest.record(image_path, fp)
            if legacy_marker.exists():
                legacy_marker.unlink()
            generated += 1
            if stats is not None:
                stats.images.append(scene.scene_id)
            tag = f", seed {seed}" if seed is not None else ""
            console.print(f"  [green]✓ Escena {scene.scene_id:02d}: Mock generado (Pillow{tag})[/green]")
    elif pending:
        if FAL_KEY:
            os.environ["FAL_KEY"] = FAL_KEY
        if not os.getenv("FAL_KEY"):
            raise PipelineError(
                "No se encontró FAL_KEY. Define FAL_KEY en el archivo .env (ver .env.example) "
                "o usa --mock-images para generar imágenes de depuración locales."
            )
        semaphore = asyncio.Semaphore(3)  # Maximum 3 concurrent requests to Fal.ai
        console.print(f"[cyan]Ejecutando {len(pending)} peticiones concurrentes a Fal.ai Flux (máx. 3 simultáneas)...[/cyan]")
        results = await asyncio.gather(
            *(
                generate_single_flux_image(compose_scene_prompt(scene, project), image_path, semaphore, seed=seed)
                for scene, image_path, _fp, seed, _marker in pending
            ),
            return_exceptions=True,
        )
        for (scene, image_path, fp, seed, legacy_marker), result in zip(pending, results):
            if isinstance(result, BaseException):
                console.print(f"  [red]✗ Escena {scene.scene_id:02d}: {result}[/red]")
                failures.append(f"escena {scene.scene_id:02d}: {result}")
                continue
            manifest.record(image_path, fp)
            if legacy_marker.exists():
                legacy_marker.unlink()
            generated += 1
            if stats is not None:
                stats.images.append(scene.scene_id)
            console.print(f"  [green]✓ Escena {scene.scene_id:02d}: Imagen Flux/schnell descargada[/green]")

    console.print(f"[bold green]✓ Imágenes: {generated} generadas, {cached_count} en caché.[/bold green]")
    if failures:
        raise PipelineError(
            f"Falló la generación de {len(failures)} imagen(es); las exitosas quedaron en caché. "
            "Vuelve a ejecutar para reintentar solo las faltantes.\n" + "\n".join(failures)
        )
    return image_paths


def generate_images_sync(
    project: VideoProjectSchema,
    output_dir: Path,
    manifest: CacheManifest,
    mock_images: bool = False,
    force: bool = False,
    overrides: Optional[ImageOverrides] = None,
    stats: Optional[RunStats] = None,
) -> list[Path]:
    """Synchronous wrapper for generate_all_images."""
    return asyncio.run(
        generate_all_images(
            project=project,
            output_dir=output_dir,
            manifest=manifest,
            mock_images=mock_images,
            force=force,
            overrides=overrides,
            stats=stats,
        )
    )
