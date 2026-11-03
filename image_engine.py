"""Image generation engine supporting Fal.ai Flux/schnell and Mock mode (Módulo 3)."""

import asyncio
import os
import textwrap
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
from script_engine import SceneSchema, VideoProjectSchema

console = Console()


def create_mock_scene_image(
    scene: SceneSchema,
    output_path: Path,
    width: int = VIDEO_WIDTH,
    height: int = VIDEO_HEIGHT,
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

    # Footer notice
    footer_text = "MODO DE DEPURACIÓN -- COSTO $0.00"
    draw.text((width // 2, height - 120), footer_text, fill=(160, 170, 190), font=font_title, anchor="mm")

    img.save(output_path, "PNG")
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
) -> Path:
    """Call fal-ai/flux/schnell and download image to disk."""
    async with semaphore:
        if FAL_KEY:
            os.environ["FAL_KEY"] = FAL_KEY

        loop = asyncio.get_running_loop()
        res = await loop.run_in_executor(
            None,
            lambda: fal_client.run(
                "fal-ai/flux/schnell",
                arguments={
                    "prompt": prompt,
                    "image_size": "portrait_16_9",
                    "num_inference_steps": 4,
                    "enable_safety_checker": False,
                },
            ),
        )

        images = res.get("images", [])
        if not images or not images[0].get("url"):
            raise ValueError(f"Fal.ai flux/schnell no devolvió imágenes: {res}")

        image_url = images[0]["url"]

        # Download image and ensure 1080x1920
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.get(image_url)
            resp.raise_for_status()
            image_bytes = resp.content

        # Save and ensure format
        temp_path = output_path.with_suffix(".tmp")
        with open(temp_path, "wb") as f:
            f.write(image_bytes)

        with Image.open(temp_path) as im:
            rgb_im = im.convert("RGB")
            if rgb_im.size != (VIDEO_WIDTH, VIDEO_HEIGHT):
                rgb_im = rgb_im.resize((VIDEO_WIDTH, VIDEO_HEIGHT), Image.Resampling.LANCZOS)
            rgb_im.save(output_path, "PNG")

        if temp_path.exists():
            temp_path.unlink()

        return output_path


async def generate_all_images(
    project: VideoProjectSchema,
    output_dir: Path,
    mock_images: bool = False,
    force: bool = False,
) -> list[Path]:
    """Generate or retrieve all scene images with idempotency cache."""
    images_dir = output_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)

    semaphore = asyncio.Semaphore(3)  # Maximum 3 concurrent requests to Fal.ai
    image_paths: list[Path] = []
    tasks = []

    console.print(
        f"[cyan]Procesando imágenes para {len(project.scenes)} escenas "
        f"({'MODO MOCK LOCAL' if mock_images else 'PRODUCCIÓN FAL.AI FLUX'})...[/cyan]"
    )

    for scene in project.scenes:
        image_path = images_dir / f"scene_{scene.scene_id:02d}.png"
        image_paths.append(image_path)

        # Idempotency / Cache Check
        if image_path.exists() and image_path.stat().st_size > 5000 and not force:
            console.print(f"  [green]✓ Escena {scene.scene_id:02d}: Imagen en caché[/green] ({image_path.name})")
            continue

        if mock_images or not os.getenv("FAL_KEY"):
            if not mock_images and not os.getenv("FAL_KEY"):
                console.print(f"  [yellow]⚠️ FAL_KEY no detectada. Usando mock para escena {scene.scene_id:02d}[/yellow]")
            create_mock_scene_image(scene, image_path)
            console.print(f"  [green]✓ Escena {scene.scene_id:02d}: Mock generado (Pillow)[/green]")
        else:
            final_prompt = compose_scene_prompt(scene, project)
            task = generate_single_flux_image(final_prompt, image_path, semaphore)
            tasks.append((scene.scene_id, task))

    if tasks:
        console.print(f"[cyan]Ejecutando {len(tasks)} peticiones concurrentes a Fal.ai Flux...[/cyan]")
        for scene_id, task in tasks:
            try:
                await task
                console.print(f"  [green]✓ Escena {scene_id:02d}: Imagen Flux/schnell descargada[/green]")
            except Exception as err:
                console.print(f"  [red]✗ Error en escena {scene_id:02d}: {err}. Fallback a mock image.[/red]")
                fallback_scene = next(s for s in project.scenes if s.scene_id == scene_id)
                create_mock_scene_image(fallback_scene, images_dir / f"scene_{scene_id:02d}.png")

    console.print("[bold green]✓ Todas las imágenes listas en 1080x1920.[/bold green]")
    return image_paths


def generate_images_sync(
    project: VideoProjectSchema,
    output_dir: Path,
    mock_images: bool = False,
    force: bool = False,
) -> list[Path]:
    """Synchronous wrapper for generate_all_images."""
    return asyncio.run(
        generate_all_images(
            project=project,
            output_dir=output_dir,
            mock_images=mock_images,
            force=force,
        )
    )
