"""Biblioteca de personajes persistentes: el protagonista recurrente de los videos con protagonista.

Cada personaje vive en characters/<slug>/ (carpeta ignorada por git):
  character.json    nombre, descripción física en inglés, estilo, origen y qué candidata es la referencia
  ref.png           imagen de referencia de identidad: image_engine se la pasa a Seedream v4.5 edit para que el
                    protagonista conserve la misma cara en todas las escenas (es una COPIA de la candidata elegida)
  candidate_NN.png  candidatas generadas con Seedream v4.5 text-to-image (no existen en personajes importados)
"""

import asyncio
import json
import os
import random
import re
import shutil
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw, ImageFont
from rich.console import Console

from config import (
    CHARACTERS_DIR,
    FAL_KEY,
    FONTS_DIR,
    SEEDREAM_COST_USD,
    SEEDREAM_T2I_ENDPOINT,
    SUBTITLE_FONT_FILENAME,
)
from pipeline_utils import PipelineError
from styles import get_style

console = Console()

CHARACTER_FILENAME = "character.json"
REF_FILENAME = "ref.png"
CANDIDATE_FILENAME = "candidate_{:02d}.png"
SLUG_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")

REFERENCE_IMAGE_SIZE = {"width": 2048, "height": 2048}  # cuadrada; ambos lados dentro de [1920, 4096] (ver config)
DEFAULT_CANDIDATES = 2
MAX_CANDIDATES = 6  # tope de seguridad: cada candidata cuesta SEEDREAM_COST_USD
IMPORT_MAX_SIDE = 2048  # las imágenes importadas se reducen a este lado máximo (la subida a Fal.ai es más ligera)
MOCK_PORTRAIT_SIZE = 1024

# Hoja de identidad neutra: se coloca AL FINAL del prompt para que encuadre, fondo y luz prevalezcan sobre el estilo
REFERENCE_SHEET_PROMPT = (
    "Character reference sheet portrait: front-facing upper body, looking straight at the camera, neutral expression, "
    "plain light-grey background, even soft studio lighting, sharp detailed face, no text, no letters, no watermark."
)


@dataclass
class Character:
    slug: str
    name: str
    description_en: str  # descripción física en inglés; el guion la inyecta en characters[PROTAGONIST_ID]
    ref_image: Path  # characters/<slug>/ref.png
    dir: Path
    style_id: str = ""  # estilo con el que se generaron las candidatas ("" = sin estilo)
    source: str = "generated"  # "generated" (Seedream t2i) | "imported" (--from-image)
    mock: bool = False  # retrato de prueba dibujado con Pillow: no sirve como referencia en producción
    created_at: str = ""
    candidates: list[str] = field(default_factory=list)  # nombres de archivo de las candidatas (vacío si es importado)
    chosen: str = ""  # archivo de la candidata copiada a ref.png ("imported" si viene de --from-image)


def character_dir(slug: str) -> Path:
    return CHARACTERS_DIR / slug


def validate_slug(slug: str) -> str:
    """Slug seguro para usar como nombre de carpeta; lanza PipelineError si no lo es."""
    if not SLUG_PATTERN.fullmatch(slug or ""):
        raise PipelineError(
            f"Slug de personaje inválido '{slug}': solo letras, números, guiones y guiones bajos (A-Z, a-z, 0-9, - y _)."
        )
    return slug


def _write_character(char: Character) -> None:
    data = asdict(char)
    data["ref_image"] = REF_FILENAME  # rutas relativas a la carpeta del personaje: la biblioteca se puede mover
    data.pop("dir")
    tmp = char.dir / (CHARACTER_FILENAME + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, char.dir / CHARACTER_FILENAME)


def _read_character(directory: Path) -> Character:
    """Lee characters/<slug>/character.json; lanza PipelineError si falta o está dañado, o si falta ref.png."""
    slug = directory.name
    json_path = directory / CHARACTER_FILENAME
    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
        char = Character(
            slug=slug,
            name=str(data["name"]),
            description_en=str(data["description_en"]),
            ref_image=directory / REF_FILENAME,
            dir=directory,
            style_id=str(data.get("style_id") or ""),
            source=str(data.get("source") or "generated"),
            mock=bool(data.get("mock", False)),
            created_at=str(data.get("created_at") or ""),
            candidates=[str(c) for c in data.get("candidates") or []],
            chosen=str(data.get("chosen") or ""),
        )
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise PipelineError(f"{json_path} es inválido o está incompleto ({e}). Vuelve a crear el personaje '{slug}'.") from e
    if not char.ref_image.is_file():
        raise PipelineError(f"Al personaje '{slug}' le falta su imagen de referencia: {char.ref_image}")
    return char


def available_slugs() -> list[str]:
    """Slugs de los personajes de la biblioteca (carpetas con character.json), ordenados."""
    if not CHARACTERS_DIR.is_dir():
        return []
    return sorted(d.name for d in CHARACTERS_DIR.iterdir() if d.is_dir() and (d / CHARACTER_FILENAME).is_file())


def create_hint(slug: str = "<slug>") -> str:
    return (
        f'python main.py create-character {slug} --name "Nombre" '
        '--description "English physical description" [--style ID] [--from-image ruta.png]'
    )


def load_character(slug: str) -> Character:
    """Personaje por slug; PipelineError con los slugs disponibles si no existe."""
    validate_slug(slug)
    directory = character_dir(slug)
    if not (directory / CHARACTER_FILENAME).is_file():
        available = available_slugs()
        raise PipelineError(
            f"No existe el personaje '{slug}' en {CHARACTERS_DIR}. "
            f"Personajes disponibles: {', '.join(available) if available else 'ninguno'}.\n"
            f"Créalo con: {create_hint(slug)}"
        )
    return _read_character(directory)


def list_characters() -> list[Character]:
    """Todos los personajes válidos de la biblioteca (los dañados se omiten con un aviso)."""
    chars: list[Character] = []
    for slug in available_slugs():
        try:
            chars.append(_read_character(character_dir(slug)))
        except PipelineError as e:
            console.print(f"[yellow]Aviso: personaje '{slug}' omitido: {e}[/yellow]")
    return chars


def build_reference_prompt(description_en: str, style_id: str = "") -> str:
    """Prompt de las candidatas: descripción -> sufijo del estilo (si hay) -> hoja de identidad neutra (al final)."""
    parts = [description_en.strip().rstrip(" .") + "."]
    if style_id:
        parts.append(get_style(style_id).image_suffix.strip())
    parts.append(REFERENCE_SHEET_PROMPT)
    return " ".join(parts)


def _set_reference(char: Character, source: Path) -> None:
    """ref.png = copia de `source` (copia, no enlace simbólico: la carpeta debe poder moverse en cualquier sistema)."""
    tmp = char.dir / "ref.part.png"
    shutil.copyfile(source, tmp)
    os.replace(tmp, char.ref_image)


def choose_candidate(slug: str, index: int) -> Character:
    """Apunta ref.png a la candidata `index` (1-based) sin generar nada. Cambia la referencia: las escenas con el
    protagonista de los videos que usan este personaje se regeneran la próxima vez (su huella incluye el hash de ref.png)."""
    char = load_character(slug)
    name = CANDIDATE_FILENAME.format(index)
    if name not in char.candidates or not (char.dir / name).is_file():
        options = ", ".join(c for c in char.candidates) if char.candidates else "ninguna (personaje importado)"
        raise PipelineError(f"El personaje '{slug}' no tiene la candidata {index} ({name}). Candidatas disponibles: {options}.")
    _set_reference(char, char.dir / name)
    char.chosen = name
    _write_character(char)
    return char


def _draw_mock_portrait(path: Path, name: str, index: int) -> None:
    """Retrato de prueba dibujado con Pillow (costo $0): una paleta distinta por candidata para distinguirlas."""
    size = MOCK_PORTRAIT_SIZE
    palettes = [
        ((226, 172, 140), (60, 40, 30), (70, 110, 190)),  # piel, pelo, camisa
        ((150, 100, 70), (20, 20, 25), (190, 80, 90)),
        ((240, 200, 170), (200, 150, 60), (60, 150, 110)),
        ((110, 75, 55), (110, 110, 120), (150, 90, 170)),
    ]
    skin, hair, shirt = palettes[(index - 1) % len(palettes)]
    img = Image.new("RGB", (size, size), (205, 207, 212))  # fondo gris claro liso, como la hoja de identidad real
    draw = ImageDraw.Draw(img)
    cx = size // 2
    draw.ellipse([cx - 330, int(size * 0.66), cx + 330, int(size * 1.35)], fill=shirt)  # hombros
    draw.rectangle([cx - 55, int(size * 0.55), cx + 55, int(size * 0.72)], fill=skin)  # cuello
    draw.ellipse([cx - 190, int(size * 0.14), cx + 190, int(size * 0.62)], fill=hair)  # pelo
    draw.ellipse([cx - 165, int(size * 0.20), cx + 165, int(size * 0.60)], fill=skin)  # cara
    for ex in (cx - 65, cx + 65):
        draw.ellipse([ex - 22, int(size * 0.37), ex + 22, int(size * 0.37) + 30], fill=(30, 30, 35))  # ojos
    draw.arc([cx - 60, int(size * 0.45), cx + 60, int(size * 0.53)], 20, 160, fill=(120, 50, 50), width=7)  # boca
    font_file = FONTS_DIR / SUBTITLE_FONT_FILENAME
    try:
        font = ImageFont.truetype(str(font_file), 40) if font_file.exists() else ImageFont.load_default()
    except Exception:
        font = ImageFont.load_default()
    draw.text((cx, 60), f"{name[:28]}  [MOCK {index:02d}]", fill=(40, 40, 50), font=font, anchor="mm")
    img.save(path, "PNG")


def _import_image(source: Path, dest: Path) -> None:
    """Valida la imagen del usuario y la guarda como PNG RGB (lado máximo IMPORT_MAX_SIDE)."""
    try:
        with Image.open(source) as im:
            im.load()
            rgb = im.convert("RGB")
    except Exception as e:
        raise PipelineError(f"--from-image no es una imagen válida ({source}): {e}") from e
    if max(rgb.size) > IMPORT_MAX_SIDE:
        scale = IMPORT_MAX_SIDE / max(rgb.size)
        rgb = rgb.resize((round(rgb.width * scale), round(rgb.height * scale)), Image.Resampling.LANCZOS)
    rgb.save(dest, "PNG")


async def _generate_candidates(prompt: str, directory: Path, count: int) -> tuple[list[str], list[str]]:
    """Genera `count` candidatas con Seedream v4.5 text-to-image (máx. 3 simultáneas). Devuelve (nombres, errores)."""
    from image_engine import generate_single_seedream_image  # import local: image_engine importa este módulo (evita el ciclo)

    semaphore = asyncio.Semaphore(3)
    names = [CANDIDATE_FILENAME.format(i) for i in range(1, count + 1)]
    results = await asyncio.gather(
        *(
            generate_single_seedream_image(
                prompt,
                directory / name,
                semaphore,
                SEEDREAM_T2I_ENDPOINT,
                REFERENCE_IMAGE_SIZE,
                seed=random.randint(0, 2**31 - 1),
            )
            for name in names
        ),
        return_exceptions=True,
    )
    done = [n for n, r in zip(names, results) if not isinstance(r, BaseException)]
    errors = [str(r) for r in results if isinstance(r, BaseException)]  # PipelineError ya empieza con el nombre del archivo
    return done, errors


def create_character(
    slug: str,
    name: str,
    description_en: str,
    style_id: str = "",
    candidates: int = DEFAULT_CANDIDATES,
    from_image: Optional[Path] = None,
    choose: Optional[int] = None,
    mock: bool = False,
) -> Character:
    """Crea characters/<slug>/ con su imagen de referencia.

    Por defecto genera `candidates` candidatas con Seedream v4.5 text-to-image (SEEDREAM_COST_USD cada una) y deja
    ref.png = candidate_01 (o la `choose`). Con `from_image` importa la imagen del usuario como referencia (sin costo).
    Con `mock` dibuja retratos de prueba con Pillow (sin costo; no sirven como referencia en producción). Para cambiar
    la referencia de un personaje ya creado, sin generar nada, usa choose_candidate().
    """
    validate_slug(slug)
    name, description_en = name.strip(), description_en.strip()
    if not name:
        raise PipelineError("--name no puede estar vacío.")
    if not description_en:
        raise PipelineError("--description no puede estar vacío (descripción física del personaje, en inglés).")
    directory = character_dir(slug)
    if (directory / CHARACTER_FILENAME).is_file():
        raise PipelineError(
            f"El personaje '{slug}' ya existe en {directory}. Usa --choose N para cambiar su referencia, "
            "otro slug para crear uno nuevo, o borra esa carpeta para rehacerlo."
        )
    if style_id:
        try:
            get_style(style_id)
        except ValueError as e:
            raise PipelineError(str(e)) from e
    if from_image is not None:
        if choose is not None:
            raise PipelineError("--choose no se puede usar con --from-image (no hay candidatas).")
        if not Path(from_image).is_file():
            raise PipelineError(f"--from-image no existe: {from_image}")
    else:
        if not 1 <= candidates <= MAX_CANDIDATES:
            raise PipelineError(f"--candidates debe estar entre 1 y {MAX_CANDIDATES}.")
        if choose is not None and not 1 <= choose <= candidates:
            raise PipelineError(f"--choose {choose} fuera de rango: se generan {candidates} candidata(s).")
        if not mock:
            if FAL_KEY:
                os.environ["FAL_KEY"] = FAL_KEY
            if not os.getenv("FAL_KEY"):
                raise PipelineError(
                    "No se encontró FAL_KEY. Define FAL_KEY en el archivo .env, usa --mock para un retrato de prueba "
                    "($0.00) o importa una imagen propia con --from-image."
                )

    directory.mkdir(parents=True, exist_ok=True)
    char = Character(
        slug=slug,
        name=name,
        description_en=description_en,
        ref_image=directory / REF_FILENAME,
        dir=directory,
        style_id=style_id,
        created_at=datetime.now().astimezone().isoformat(timespec="seconds"),
    )

    try:
        if from_image is not None:
            _import_image(Path(from_image), char.ref_image)
            char.source, char.chosen = "imported", "imported"
            console.print(f"[green]✓ Imagen importada como referencia (sin costo):[/] {char.ref_image}")
        elif mock:
            char.mock = True
            for i in range(1, candidates + 1):
                _draw_mock_portrait(directory / CANDIDATE_FILENAME.format(i), name, i)
                char.candidates.append(CANDIDATE_FILENAME.format(i))
            console.print(f"[green]✓ {candidates} retrato(s) mock dibujado(s) con Pillow ($0.00).[/green]")
        else:
            prompt = build_reference_prompt(description_en, style_id)
            console.print(
                f"[cyan]Generando {candidates} candidata(s) de referencia con Seedream v4.5 text-to-image "
                f"(2048x2048, ~${candidates * SEEDREAM_COST_USD:.2f})...[/cyan]"
            )
            console.print(f"[dim]Prompt: {prompt}[/dim]")
            done, errors = asyncio.run(_generate_candidates(prompt, directory, candidates))
            for err in errors:
                console.print(f"  [red]✗ {err}[/red]")
            if not done:
                raise PipelineError("No se pudo generar ninguna candidata de referencia.\n" + "\n".join(errors))
            char.candidates = done
            console.print(f"[green]✓ {len(done)} candidata(s) generada(s) (~${len(done) * SEEDREAM_COST_USD:.2f}).[/green]")

        if from_image is None:
            wanted = CANDIDATE_FILENAME.format(choose or 1)
            if wanted not in char.candidates:  # la elegida (o la 1) falló: se usa la primera que sí se generó
                console.print(f"[yellow]Aviso: {wanted} no se generó; ref.png usará {char.candidates[0]}.[/yellow]")
                wanted = char.candidates[0]
            _set_reference(char, directory / wanted)
            char.chosen = wanted
        _write_character(char)
    except BaseException:
        if not any(directory.iterdir()):
            directory.rmdir()  # un intento fallido sin archivos no deja una carpeta vacía en la biblioteca
        raise
    return char
