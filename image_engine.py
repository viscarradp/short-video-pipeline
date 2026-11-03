"""Image generation engine supporting Fal.ai Flux/schnell and Mock mode (Módulo 3)."""

import asyncio
import math
import os
import random
import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional
import httpx
from PIL import Image, ImageDraw, ImageFont, ImageOps
from rich.console import Console

import fal_client
from config import (
    FAL_KEY,
    FLUX_COST_USD,
    FLUX_IMAGE_SIZE,
    FLUX_IMAGE_SIZE_LEGACY,
    VIDEO_WIDTH,
    VIDEO_HEIGHT,
    FONTS_DIR,
    SUBTITLE_FONT_FILENAME,
)
from pipeline_utils import CacheManifest, PipelineError, RunStats, fingerprint
from script_engine import SceneSchema, VideoProjectSchema
from styles import COMPOSITION_SUFFIX, get_style

console = Console()

# Parámetros de generación (forman parte de la huella de cada imagen)
FLUX_MODEL = "fal-ai/flux/schnell"
FLUX_STEPS = 4
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


def compose_scene_prompt_legacy(scene: SceneSchema, project: VideoProjectSchema) -> str:
    """Composición ANTERIOR (guiones sin visual_style_id). NO MODIFICAR: su salida forma parte de la huella de las
    imágenes ya pagadas; cambiar un solo byte obliga a volver a pagarlas todas."""
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


# Flux schnell: el codificador T5 trunca a ~256 tokens. Estimación barata sin tokenizador: ceil(palabras x 1.35).
PROMPT_TOKEN_LIMIT = 230
TOKENS_PER_WORD = 1.35
CHAR_DESC_WORD_LIMITS = (25, 12)  # recortes progresivos de cada descripción de personaje
MIN_VISUAL_PROMPT_WORDS = 15  # último recurso: el prompt de la escena nunca baja de esto
_warned_long_prompts: set[tuple[int, str]] = set()


def estimate_prompt_tokens(text: str) -> int:
    """Tokens T5 estimados: ceil(palabras x 1.35)."""
    return math.ceil(len(text.split()) * TOKENS_PER_WORD)


def _sentence(text: str) -> str:
    """Texto sin espacios sobrantes y terminado en puntuación de oración (sin duplicar puntos)."""
    t = text.strip()
    if t and t[-1] not in ".!?…":
        t += "."
    return t


def _truncate_words(text: str, max_words: int) -> str:
    words = text.split()
    if len(words) <= max_words:
        return text.strip()
    return " ".join(words[:max_words]).rstrip(" ,;:-")


def _join_style_prompt(visual: str, char_descs: list[str], style_suffix: str) -> str:
    parts = [_sentence(visual)]
    if char_descs:
        parts.append(_sentence("Featuring: " + "; ".join(d.strip().rstrip(" .") for d in char_descs)))
    parts.append(_sentence(style_suffix))
    parts.append(_sentence(COMPOSITION_SUFFIX))
    return " ".join(p for p in parts if p)


def compose_scene_prompt(scene: SceneSchema, project: VideoProjectSchema) -> str:
    """Prompt final de la imagen de una escena.

    Con project.visual_style_id: sujeto -> personajes -> estilo -> composición (lo primero pesa más en Flux).
    Sin él (guion legado): compose_scene_prompt_legacy, byte a byte igual que antes. Si el prompt nuevo supera
    PROMPT_TOKEN_LIMIT tokens estimados, avisa y recorta primero las descripciones de personajes; el estilo y el
    sufijo de composición nunca se eliminan.
    """
    if not project.visual_style_id:
        return compose_scene_prompt_legacy(scene, project)
    try:
        style = get_style(project.visual_style_id)
    except ValueError as e:
        raise PipelineError(f"script.json: {e}") from e

    visual = scene.visual_prompt_en.strip()
    char_descs: list[str] = []
    if scene.characters_in_scene and project.characters:
        for char_id in scene.characters_in_scene:
            if char_id in project.characters:
                char_descs.append(project.characters[char_id].strip())

    prompt = _join_style_prompt(visual, char_descs, style.image_suffix)
    full_tokens = estimate_prompt_tokens(prompt)
    if full_tokens <= PROMPT_TOKEN_LIMIT:
        return prompt

    action = None
    trimmed = char_descs
    for limit in CHAR_DESC_WORD_LIMITS if char_descs else ():
        trimmed = [_truncate_words(d.rstrip(" ."), limit) for d in char_descs]
        prompt = _join_style_prompt(visual, trimmed, style.image_suffix)
        if estimate_prompt_tokens(prompt) <= PROMPT_TOKEN_LIMIT:
            action = f"descripciones de personajes recortadas a {limit} palabras"
            break
    if action is None:
        # Último recurso: acortar el propio prompt visual (palabra a palabra, con un mínimo)
        words = visual.split()
        while len(words) > MIN_VISUAL_PROMPT_WORDS:
            words.pop()
            prompt = _join_style_prompt(" ".join(words).rstrip(" ,;:-"), trimmed, style.image_suffix)
            if estimate_prompt_tokens(prompt) <= PROMPT_TOKEN_LIMIT:
                break
        action = "prompt visual de la escena acortado" + (" y personajes recortados" if char_descs else "")

    key = (scene.scene_id, prompt)
    if key not in _warned_long_prompts:
        _warned_long_prompts.add(key)
        console.print(
            f"  [yellow]⚠ Escena {scene.scene_id:02d}: el prompt compuesto tiene ~{full_tokens} tokens "
            f"(límite {PROMPT_TOKEN_LIMIT}; Flux trunca a ~256); {action} -> ~{estimate_prompt_tokens(prompt)} tokens.[/yellow]"
        )
    return prompt


def fit_cover(im: Image.Image, width: int, height: int) -> Image.Image:
    """Escala la imagen para CUBRIR width x height (sin deformarla) y recorta el sobrante centrado (LANCZOS)."""
    if im.size == (width, height):
        return im
    return ImageOps.fit(im, (width, height), method=Image.Resampling.LANCZOS, centering=(0.5, 0.5))


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
                    fit_cover(im.convert("RGB"), VIDEO_WIDTH, VIDEO_HEIGHT).save(part_path, "PNG")
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


def image_fingerprint(prompt: str, mock_images: bool, image_size: Any = None) -> str:
    """Huella de una imagen: prompt final compuesto + modo mock + modelo + tamaño + pasos."""
    return fingerprint(
        kind="image",
        prompt=prompt,
        mock_images=mock_images,
        model=FLUX_MODEL,
        image_size=FLUX_IMAGE_SIZE if image_size is None else image_size,
        steps=FLUX_STEPS,
    )


def legacy_image_fingerprint(prompt: str) -> str:
    """Huella que tenían las imágenes reales (pagadas) con el tamaño anterior 'portrait_16_9' (576x1024)."""
    return image_fingerprint(prompt, False, image_size=FLUX_IMAGE_SIZE_LEGACY)


def classify_recorded_image(recorded_fp: Optional[str], prompt: str) -> Optional[str]:
    """'current' si la huella registrada es la de una imagen real con los parámetros actuales; 'legacy' si es la de una
    imagen real con el tamaño anterior (se acepta en caché para no volver a pagarla); None en otro caso (mock u obsoleta)."""
    if recorded_fp is None:
        return None
    if recorded_fp == image_fingerprint(prompt, False):
        return "current"
    if recorded_fp == legacy_image_fingerprint(prompt):
        return "legacy"
    return None


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

        # Imagen real (pagada) vigente: su huella registrada coincide con la huella en modo real (tamaño actual o legado)
        recorded_kind = (
            classify_recorded_image(manifest.get(image_path), final_prompt)
            if image_path.exists() and image_path.stat().st_size > MIN_IMAGE_BYTES
            else None
        )
        has_paid_image = mock_images and recorded_kind is not None

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
                if not is_cached and recorded_kind == "legacy":
                    # Legado: imagen real pagada a 576x1024 (portrait_16_9). Se acepta; solo un force/re-roll la rehace.
                    is_cached = True
                    console.print(
                        f"  [dim]Escena {scene.scene_id:02d}: imagen real de tamaño anterior (portrait_16_9) aceptada "
                        f"en caché; usa --force-images o retry-scene para rehacerla a {FLUX_IMAGE_SIZE['width']}x{FLUX_IMAGE_SIZE['height']}[/dim]"
                    )
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
