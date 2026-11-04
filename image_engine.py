"""Image generation engine supporting Fal.ai Flux/schnell, Seedream v4.5 (protagonist projects) and Mock mode (Módulo 3)."""

import asyncio
import math
import os
import random
import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional
import httpx
from PIL import Image, ImageDraw, ImageFont, ImageOps
from rich.console import Console

import fal_client
from character_engine import Character, load_character
from config import (
    FAL_KEY,
    FLUX_ENDPOINT,
    FLUX_IMAGE_SIZE_LEGACY,
    FONTS_DIR,
    PROTAGONIST_ID,
    SEEDREAM_EDIT_ENDPOINT,
    SEEDREAM_T2I_ENDPOINT,
    SUBTITLE_FONT_FILENAME,
    VideoFormat,
    get_format,
    seedream_size_valid,
)
from pipeline_utils import CacheManifest, PipelineError, RunStats, file_sha256, fingerprint
from script_engine import SceneSchema, VideoProjectSchema
from styles import get_style

console = Console()

# Parámetros de generación (forman parte de la huella de cada imagen)
FLUX_MODEL = FLUX_ENDPOINT
FLUX_STEPS = 4
MIN_IMAGE_BYTES = 5000

# Escenas donde aparece el protagonista (Seedream edit): el prefijo le dice al modelo que conserve la identidad de la
# imagen de referencia; la descripción física ya viaja en el "Featuring:" de compose_scene_prompt (no se repite).
PROTAGONIST_EDIT_PREFIX = "The same person as in the reference image (same face, hairstyle and build), now: "


@dataclass
class ImageOverrides:
    """Escenas cuya imagen debe re-generarse sí o sí (re-roll), con semilla opcional."""

    scene_ids: frozenset[int] = frozenset()
    seed: Optional[int] = None


MOCK_FLUX_LABEL = "FLUX VISUAL PROMPT (EN):"


def create_mock_scene_image(
    scene: SceneSchema,
    output_path: Path,
    video_format: VideoFormat,
    seed: Optional[int] = None,
    model_label: str = MOCK_FLUX_LABEL,
    ref_image: Optional[Path] = None,
    ref_label: str = "",
) -> Path:
    """Generate a sleek local mock image with a dark gradient and prompt text (cost $0.00).

    `model_label` marks the model the real image would use; `ref_image` (protagonist edit scenes) is pasted as a small
    thumbnail in the lower-right corner, captioned with `ref_label` (the character slug)."""
    width, height = video_format.width, video_format.height
    landscape = width > height
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

    # Disposición: vertical = cajas apiladas; horizontal = narración y prompt en dos columnas, dentro de una zona segura
    # (el zoom Ken Burns llega a 1.35x y recorta los bordes del cuadro: los márgenes evitan que se corten las cajas)
    if landscape:
        badge_box, badge_y, effect_y = (170, 100, width - 170, 170), 135, 210
        narration_box = (170, 250, width // 2 - 20, 790)
        prompt_box = (width // 2 + 20, 250, width - 170, 790)
        keywords_y, seed_y, footer_y, wrap_width = 840, 890, 950, 40
    else:
        badge_box, badge_y, effect_y = (100, 160, width - 100, 240), 200, 290
        narration_box = (80, 360, width - 80, 720)
        prompt_box = (80, 760, width - 80, 1300)
        keywords_y, seed_y, footer_y, wrap_width = 1360, 1450, height - 120, 42

    # Draw Scene Badge
    badge_text = f"ESCENA {scene.scene_id:02d} [MOCK]"
    badge_bg = (235, 75, 90)
    draw.rounded_rectangle([badge_box[:2], badge_box[2:]], radius=15, fill=badge_bg)
    draw.text((width // 2, badge_y), badge_text, fill=(255, 255, 255), font=font_badge, anchor="mm")

    # Camera effect tag
    effect_text = f"EFECTO: {scene.camera_effect.upper()}"
    draw.text((width // 2, effect_y), effect_text, fill=(0, 240, 200), font=font_title, anchor="mm")

    # Narration preview box
    nx, ny = narration_box[:2]
    draw.rounded_rectangle([narration_box[:2], narration_box[2:]], radius=12, fill=(25, 30, 50, 200), outline=(50, 70, 100), width=2)
    draw.text((nx + 30, ny + 30), "VOZ / LOCUCIÓN (ES):", fill=(255, 200, 0), font=font_title)
    wrapped_narration = textwrap.fill(scene.narration_es, width=wrap_width)
    draw.text((nx + 30, ny + 90), wrapped_narration, fill=(240, 240, 240), font=font_body)

    # Visual prompt box
    px, py = prompt_box[:2]
    draw.rounded_rectangle([prompt_box[:2], prompt_box[2:]], radius=12, fill=(20, 25, 45, 200), outline=(70, 90, 140), width=2)
    draw.text((px + 30, py + 30), model_label, fill=(0, 220, 255), font=font_title)
    wrapped_prompt = textwrap.fill(scene.visual_prompt_en, width=wrap_width)
    draw.text((px + 30, py + 90), wrapped_prompt, fill=(200, 220, 240), font=font_body)

    # Keywords box
    if scene.highlight_words:
        kw_text = "PALABRAS CLAVE: " + ", ".join(scene.highlight_words)
        draw.text((width // 2, keywords_y), kw_text, fill=(57, 255, 20), font=font_title, anchor="mm")

    # Re-roll tag: makes a retried mock card visibly different
    if seed is not None:
        draw.text((width // 2, seed_y), f"RETRY - SEED {seed}", fill=(255, 140, 60), font=font_title, anchor="mm")

    # Protagonist reference thumbnail (edit scenes), in the free lower-right corner
    if ref_image is not None and Path(ref_image).is_file():
        thumb_size, thumb_x, thumb_y = (165, width - 170 - 165, 805) if landscape else (240, width - 80 - 240, 1480)
        with Image.open(ref_image) as ref:
            thumb = ImageOps.fit(ref.convert("RGB"), (thumb_size, thumb_size), method=Image.Resampling.LANCZOS)
        img.paste(thumb, (thumb_x, thumb_y))
        draw.rectangle([(thumb_x, thumb_y), (thumb_x + thumb_size, thumb_y + thumb_size)], outline=(0, 220, 255), width=3)
        caption = f"REF {ref_label[:12]}".strip()
        draw.text((thumb_x - 14, thumb_y + thumb_size // 2), caption, fill=(0, 220, 255), font=font_body, anchor="rm")

    # Footer notice
    footer_text = "MODO DE DEPURACIÓN -- COSTO $0.00"
    draw.text((width // 2, footer_y), footer_text, fill=(160, 170, 190), font=font_title, anchor="mm")

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


def _join_style_prompt(visual: str, char_descs: list[str], style_suffix: str, composition_hint: str) -> str:
    parts = [_sentence(visual)]
    if char_descs:
        parts.append(_sentence("Featuring: " + "; ".join(d.strip().rstrip(" .") for d in char_descs)))
    parts.append(_sentence(style_suffix))
    parts.append(_sentence(composition_hint))
    return " ".join(p for p in parts if p)


def compose_scene_prompt(
    scene: SceneSchema, project: VideoProjectSchema, token_limit: Optional[int] = PROMPT_TOKEN_LIMIT
) -> str:
    """Prompt final de la imagen de una escena.

    Con project.visual_style_id: sujeto -> personajes -> estilo -> composición del formato del proyecto (lo primero
    pesa más en Flux). Sin él (guion legado, siempre vertical): compose_scene_prompt_legacy, byte a byte igual que
    antes. Si el prompt nuevo supera `token_limit` tokens estimados, avisa y recorta primero las descripciones de
    personajes; el estilo y el sufijo de composición nunca se eliminan. `token_limit=None` desactiva el recorte (Seedream
    no trunca a ~256 tokens como el T5 de Flux).
    """
    if not project.visual_style_id:
        return compose_scene_prompt_legacy(scene, project)
    try:
        style = get_style(project.visual_style_id)
        composition_hint = get_format(project.video_format).composition_hint
    except ValueError as e:
        raise PipelineError(f"script.json: {e}") from e

    visual = scene.visual_prompt_en.strip()
    char_descs: list[str] = []
    if scene.characters_in_scene and project.characters:
        for char_id in scene.characters_in_scene:
            if char_id in project.characters:
                char_descs.append(project.characters[char_id].strip())

    prompt = _join_style_prompt(visual, char_descs, style.image_suffix, composition_hint)
    full_tokens = estimate_prompt_tokens(prompt)
    if token_limit is None or full_tokens <= token_limit:
        return prompt

    action = None
    trimmed = char_descs
    for limit in CHAR_DESC_WORD_LIMITS if char_descs else ():
        trimmed = [_truncate_words(d.rstrip(" ."), limit) for d in char_descs]
        prompt = _join_style_prompt(visual, trimmed, style.image_suffix, composition_hint)
        if estimate_prompt_tokens(prompt) <= token_limit:
            action = f"descripciones de personajes recortadas a {limit} palabras"
            break
    if action is None:
        # Último recurso: acortar el propio prompt visual (palabra a palabra, con un mínimo)
        words = visual.split()
        while len(words) > MIN_VISUAL_PROMPT_WORDS:
            words.pop()
            prompt = _join_style_prompt(" ".join(words).rstrip(" ,;:-"), trimmed, style.image_suffix, composition_hint)
            if estimate_prompt_tokens(prompt) <= token_limit:
                break
        action = "prompt visual de la escena acortado" + (" y personajes recortados" if char_descs else "")

    key = (scene.scene_id, prompt)
    if key not in _warned_long_prompts:
        _warned_long_prompts.add(key)
        console.print(
            f"  [yellow]⚠ Escena {scene.scene_id:02d}: el prompt compuesto tiene ~{full_tokens} tokens "
            f"(límite {token_limit}; Flux trunca a ~256); {action} -> ~{estimate_prompt_tokens(prompt)} tokens.[/yellow]"
        )
    return prompt


def fit_cover(im: Image.Image, width: int, height: int) -> Image.Image:
    """Escala la imagen para CUBRIR width x height (sin deformarla) y recorta el sobrante centrado (LANCZOS)."""
    if im.size == (width, height):
        return im
    return ImageOps.fit(im, (width, height), method=Image.Resampling.LANCZOS, centering=(0.5, 0.5))


async def _generate_fal_image(
    model: str,
    build_arguments: Callable[[int], dict],
    output_path: Path,
    semaphore: asyncio.Semaphore,
    out_size: Optional[tuple[int, int]],
    max_attempts: int = 3,
    seed: Optional[int] = None,
) -> Path:
    """Call a Fal.ai image endpoint (with retries, NSFW check) and download the image to disk as PNG.

    `build_arguments(seed)` returns the arguments of the call. If `seed` is given it is used on every attempt (except
    after an NSFW rejection, which needs a new seed); otherwise each attempt uses a fresh random seed. `out_size` is the
    (width, height) to cover-crop the result to; None saves the image as it comes.
    """
    if FAL_KEY:
        os.environ["FAL_KEY"] = FAL_KEY

    loop = asyncio.get_running_loop()
    last_error: Optional[Exception] = None
    use_fixed_seed = seed is not None
    model_name = model.removeprefix("fal-ai/")

    for attempt in range(1, max_attempts + 1):
        try:
            attempt_seed = seed if use_fixed_seed else random.randint(0, 2**31 - 1)
            arguments = build_arguments(attempt_seed)
            async with semaphore:
                res = await loop.run_in_executor(None, lambda: fal_client.run(model, arguments=arguments))

                images = res.get("images", [])
                if not images or not images[0].get("url"):
                    raise ValueError(f"Fal.ai {model_name} no devolvió imágenes: {res}")

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

            # Save and ensure a PNG of the video format's resolution (cover + center crop, never stretched)
            temp_path = output_path.with_suffix(".tmp")
            with open(temp_path, "wb") as f:
                f.write(image_bytes)

            part_path = output_path.with_name(output_path.stem + ".part.png")
            try:
                with Image.open(temp_path) as im:
                    rgb = im.convert("RGB")
                    (fit_cover(rgb, *out_size) if out_size else rgb).save(part_path, "PNG")
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


async def generate_single_flux_image(
    prompt: str,
    output_path: Path,
    semaphore: asyncio.Semaphore,
    video_format: VideoFormat,
    max_attempts: int = 3,
    seed: Optional[int] = None,
) -> Path:
    """Call fal-ai/flux/schnell (with retries, NSFW check) and download image to disk.

    If `seed` is given it is used on every attempt (except after an NSFW rejection, which needs a new seed);
    otherwise each attempt uses a fresh random seed.
    """
    return await _generate_fal_image(
        FLUX_MODEL,
        lambda attempt_seed: {
            "prompt": prompt,
            "image_size": video_format.flux_image_size,
            "num_inference_steps": FLUX_STEPS,
            "enable_safety_checker": True,
            "seed": attempt_seed,
        },
        output_path,
        semaphore,
        (video_format.width, video_format.height),
        max_attempts,
        seed,
    )


def build_seedream_arguments(
    prompt: str, image_size: dict, seed: int, image_urls: Optional[list[str]] = None
) -> dict:
    """Argumentos de una llamada a Seedream v4.5: texto a imagen, o edición si se pasan `image_urls` (referencias)."""
    arguments: dict[str, Any] = {
        "prompt": prompt,
        "image_size": image_size,
        "num_images": 1,
        "enable_safety_checker": True,
        "seed": seed,
    }
    if image_urls:
        arguments["image_urls"] = image_urls
    return arguments


async def generate_single_seedream_image(
    prompt: str,
    output_path: Path,
    semaphore: asyncio.Semaphore,
    endpoint: str,
    image_size: dict,
    image_urls: Optional[list[str]] = None,
    out_size: Optional[tuple[int, int]] = None,
    max_attempts: int = 3,
    seed: Optional[int] = None,
) -> Path:
    """Call Seedream v4.5 (text-to-image, or edit with the reference `image_urls`) with retries and download the image.

    Same retry / seed / NSFW rules as generate_single_flux_image. `out_size` = (width, height) to cover-crop to
    (the video format's resolution); None keeps the generated size (character reference candidates)."""
    if endpoint == SEEDREAM_EDIT_ENDPOINT and not image_urls:
        raise PipelineError(f"{output_path.name}: el endpoint de edición de Seedream requiere la imagen de referencia (image_urls).")
    if not seedream_size_valid(image_size):
        raise PipelineError(f"{output_path.name}: image_size {image_size} no es válido para Seedream v4.5 (ver config.seedream_size_valid).")
    return await _generate_fal_image(
        endpoint,
        lambda attempt_seed: build_seedream_arguments(prompt, image_size, attempt_seed, image_urls),
        output_path,
        semaphore,
        out_size,
        max_attempts,
        seed,
    )


def image_fingerprint(prompt: str, mock_images: bool, image_size: Any) -> str:
    """Huella de una imagen: prompt final compuesto + modo mock + modelo + tamaño (flux_image_size del formato) + pasos."""
    return fingerprint(
        kind="image",
        prompt=prompt,
        mock_images=mock_images,
        model=FLUX_MODEL,
        image_size=image_size,
        steps=FLUX_STEPS,
    )


def legacy_image_fingerprint(prompt: str) -> str:
    """Huella que tenían las imágenes reales (pagadas) con el tamaño anterior 'portrait_16_9' (576x1024)."""
    return image_fingerprint(prompt, False, FLUX_IMAGE_SIZE_LEGACY)


def classify_recorded_image(recorded_fp: Optional[str], prompt: str, video_format: VideoFormat) -> Optional[str]:
    """'current' si la huella registrada es la de una imagen real con los parámetros actuales; 'legacy' si es la de una
    imagen real vertical con el tamaño anterior (se acepta en caché para no volver a pagarla); None en otro caso (mock
    u obsoleta). El tamaño 'portrait_16_9' solo existió en vertical: en otros formatos no hay imágenes legadas."""
    if recorded_fp is None:
        return None
    if recorded_fp == image_fingerprint(prompt, False, video_format.flux_image_size):
        return "current"
    if video_format.id == "vertical" and recorded_fp == legacy_image_fingerprint(prompt):
        return "legacy"
    return None


def seedream_image_fingerprint(
    prompt: str, endpoint: str, image_size: Any, ref_sha256: Optional[str], mock_images: bool
) -> str:
    """Huella de una imagen Seedream: prompt final + endpoint + tamaño + sha256 de la referencia (solo edición) + modo mock.

    Cambiar ref.png del personaje cambia la huella exactamente de las escenas donde aparece (las de edición)."""
    return fingerprint(
        kind="image",
        prompt=prompt,
        mock_images=mock_images,
        model=endpoint,
        image_size=image_size,
        ref_sha256=ref_sha256,
    )


@dataclass(frozen=True)
class ImagePlan:
    """Cómo se genera la imagen de una escena: modelo, prompt final enviado, tamaño pedido y referencia (solo edición)."""

    endpoint: str  # FLUX_MODEL | SEEDREAM_EDIT_ENDPOINT | SEEDREAM_T2I_ENDPOINT
    prompt: str
    image_size: dict
    ref_image: Optional[Path] = None  # ref.png del protagonista (solo SEEDREAM_EDIT_ENDPOINT)
    ref_sha256: Optional[str] = None
    ref_slug: str = ""

    @property
    def is_flux(self) -> bool:
        return self.endpoint == FLUX_MODEL

    @property
    def short_name(self) -> str:
        """Nombre corto del modelo para los mensajes de consola."""
        return {
            FLUX_MODEL: "Flux/schnell",
            SEEDREAM_EDIT_ENDPOINT: "Seedream v4.5 edit + referencia",
            SEEDREAM_T2I_ENDPOINT: "Seedream v4.5 t2i",
        }[self.endpoint]

    @property
    def mock_label(self) -> str:
        """Rótulo del modelo en la tarjeta mock."""
        if self.is_flux:
            return MOCK_FLUX_LABEL
        if self.endpoint == SEEDREAM_EDIT_ENDPOINT:
            return "SEEDREAM EDIT + REF:"
        return "SEEDREAM T2I - PROMPT (EN):"

    def fingerprint(self, mock_images: bool) -> str:
        if self.is_flux:
            return image_fingerprint(self.prompt, mock_images, self.image_size)
        return seedream_image_fingerprint(self.prompt, self.endpoint, self.image_size, self.ref_sha256, mock_images)


def plan_scene_image(
    scene: SceneSchema,
    project: VideoProjectSchema,
    video_format: VideoFormat,
    character: Optional[Character] = None,
    ref_sha256: Optional[str] = None,
) -> ImagePlan:
    """Modelo y prompt de la imagen de una escena.

    Sin protagonist_slug: Flux schnell con el prompt de siempre (huella idéntica a la de los videos ya pagados). Con él,
    TODAS las escenas usan Seedream v4.5 (look coherente): edición con la referencia del personaje (`character`) si el
    protagonista aparece en la escena, texto a imagen en el resto. `ref_sha256` = hash de ref.png si ya se calculó."""
    if not project.protagonist_slug:
        return ImagePlan(FLUX_MODEL, compose_scene_prompt(scene, project), video_format.flux_image_size)
    prompt = compose_scene_prompt(scene, project, token_limit=None)
    if PROTAGONIST_ID in scene.characters_in_scene:
        if character is None:
            raise PipelineError(f"Escena {scene.scene_id:02d}: falta el personaje '{project.protagonist_slug}' de la biblioteca.")
        return ImagePlan(
            SEEDREAM_EDIT_ENDPOINT,
            PROTAGONIST_EDIT_PREFIX + prompt,
            video_format.seedream_image_size,
            ref_image=character.ref_image,
            ref_sha256=ref_sha256 or file_sha256(character.ref_image),
            ref_slug=character.slug,
        )
    return ImagePlan(SEEDREAM_T2I_ENDPOINT, prompt, video_format.seedream_image_size)


def classify_recorded_plan(recorded_fp: Optional[str], plan: ImagePlan, video_format: VideoFormat) -> Optional[str]:
    """Como classify_recorded_image pero para cualquier modelo: 'current' / 'legacy' (solo Flux vertical) / None."""
    if plan.is_flux:
        return classify_recorded_image(recorded_fp, plan.prompt, video_format)
    return "current" if recorded_fp is not None and recorded_fp == plan.fingerprint(False) else None


async def upload_reference(ref_image: Path) -> str:
    """Sube ref.png a Fal.ai una sola vez por ejecución; todas las escenas de edición reutilizan la URL."""
    if FAL_KEY:
        os.environ["FAL_KEY"] = FAL_KEY
    loop = asyncio.get_running_loop()
    try:
        return await loop.run_in_executor(None, lambda: fal_client.upload_file(ref_image))
    except Exception as e:
        raise PipelineError(f"No se pudo subir la imagen de referencia {ref_image} a Fal.ai: {e}") from e


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
    try:
        video_format = get_format(project.video_format)
    except ValueError as e:
        raise PipelineError(f"script.json: {e}") from e

    # Proyecto con protagonista: la referencia de identidad (ref.png) sale de la biblioteca de personajes. Solo se necesita
    # si alguna escena lo muestra (esas usan Seedream edit); un personaje borrado se avisa aquí, antes de gastar nada.
    character: Optional[Character] = None
    ref_sha256: Optional[str] = None
    if project.protagonist_slug and any(PROTAGONIST_ID in sc.characters_in_scene for sc in project.scenes):
        character = load_character(project.protagonist_slug)
        ref_sha256 = file_sha256(character.ref_image)

    image_paths: list[Path] = []
    # (scene, image_path, plan, fingerprint, seed, legacy_marker)
    pending: list[tuple[SceneSchema, Path, ImagePlan, str, Optional[int], Path]] = []
    cached_count = 0
    stale_kept: list[int] = []  # retry-scene: escenas no seleccionadas con huella desactualizada que se conservan

    if mock_images:
        mode_label = "MODO MOCK LOCAL"
    elif project.protagonist_slug:
        mode_label = "PRODUCCIÓN FAL.AI SEEDREAM v4.5"
    else:
        mode_label = "PRODUCCIÓN FAL.AI FLUX"
    protagonist_note = f", protagonista '{project.protagonist_slug}'" if project.protagonist_slug else ""
    console.print(
        f"[cyan]Procesando imágenes para {len(project.scenes)} escenas "
        f"({mode_label}, formato {video_format.id} {video_format.width}x{video_format.height}{protagonist_note})...[/cyan]"
    )

    for scene in project.scenes:
        image_path = images_dir / f"scene_{scene.scene_id:02d}.png"
        legacy_marker = image_path.with_suffix(".mock")  # marcador de Fase 1, reemplazado por el manifiesto
        image_paths.append(image_path)

        plan = plan_scene_image(scene, project, video_format, character, ref_sha256)
        fp = plan.fingerprint(mock_images)
        reroll = scene.scene_id in overrides.scene_ids
        forced = force or reroll

        # Imagen real (pagada) vigente: su huella registrada coincide con la huella en modo real (tamaño actual o legado)
        recorded_kind = (
            classify_recorded_plan(manifest.get(image_path), plan, video_format)
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
                        f"en caché; usa --force-images o retry-scene para rehacerla a {video_format.flux_image_size['width']}x{video_format.flux_image_size['height']}[/dim]"
                    )
            elif image_path.exists():
                # Migración: imagen de un workspace de Fase 1 sin entrada en el manifiesto
                if legacy_marker.exists():
                    console.print(f"  [yellow]Escena {scene.scene_id:02d}: la imagen existente es un mock (legado); se regenerará[/yellow]")
                elif plan.is_flux and not mock_images and image_path.stat().st_size > MIN_IMAGE_BYTES:
                    manifest.record(image_path, fp)
                    is_cached = True
                    console.print(
                        f"  [dim]Escena {scene.scene_id:02d}: imagen existente adoptada en el manifiesto de caché (workspace anterior)[/dim]"
                    )
            if (
                not is_cached
                and overrides.scene_ids  # retry-scene: solo se pagan las escenas seleccionadas o las que no tienen imagen
                and image_path.exists()
                and image_path.stat().st_size > MIN_IMAGE_BYTES
                and not legacy_marker.exists()
            ):
                is_cached = True
                stale_kept.append(scene.scene_id)
        if is_cached:
            cached_count += 1
            if not has_paid_image:
                console.print(f"  [green]✓ Escena {scene.scene_id:02d}: Imagen en caché[/green] ({image_path.name})")
            continue

        seed: Optional[int] = None
        if reroll:
            seed = overrides.seed if overrides.seed is not None else random.randint(0, 2**31 - 1)
        pending.append((scene, image_path, plan, fp, seed, legacy_marker))

    if stale_kept:
        console.print(
            f"  [yellow]⚠ Escena(s) {', '.join(f'{i:02d}' for i in stale_kept)}: imagen conservada con huella desactualizada "
            "(no se paga); usa --force-images o inclúyelas en --scene para rehacerlas.[/yellow]"
        )

    generated = 0
    failures: list[str] = []

    if pending and mock_images:
        for scene, image_path, plan, fp, seed, legacy_marker in pending:
            create_mock_scene_image(
                scene, image_path, video_format, seed=seed, model_label=plan.mock_label,
                ref_image=plan.ref_image, ref_label=plan.ref_slug,
            )
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

        # La referencia del protagonista se sube una sola vez; todas las escenas de edición reutilizan la URL
        ref_url: Optional[str] = None
        if any(plan.endpoint == SEEDREAM_EDIT_ENDPOINT for _sc, _p, plan, _fp, _seed, _m in pending):
            ref_url = await upload_reference(character.ref_image)
            console.print(f"[dim]Referencia '{character.slug}' subida a Fal.ai (se reutiliza en todas las escenas de edición).[/dim]")

        async def generate_one(plan: ImagePlan, image_path: Path, seed: Optional[int]) -> Path:
            if plan.is_flux:
                return await generate_single_flux_image(plan.prompt, image_path, semaphore, video_format, seed=seed)
            return await generate_single_seedream_image(
                plan.prompt,
                image_path,
                semaphore,
                plan.endpoint,
                plan.image_size,
                image_urls=[ref_url] if plan.endpoint == SEEDREAM_EDIT_ENDPOINT else None,
                out_size=(video_format.width, video_format.height),
                seed=seed,
            )

        used_models = ", ".join(sorted({plan.short_name for _sc, _p, plan, _fp, _seed, _m in pending}))
        console.print(f"[cyan]Ejecutando {len(pending)} peticiones concurrentes a Fal.ai {used_models} (máx. 3 simultáneas)...[/cyan]")
        results = await asyncio.gather(
            *(generate_one(plan, image_path, seed) for _scene, image_path, plan, _fp, seed, _marker in pending),
            return_exceptions=True,
        )
        for (scene, image_path, plan, fp, seed, legacy_marker), result in zip(pending, results):
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
                stats.count_paid_image(plan.endpoint)
            console.print(f"  [green]✓ Escena {scene.scene_id:02d}: Imagen {plan.short_name} descargada[/green]")

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
