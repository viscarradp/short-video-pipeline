"""Viral dynamic subtitle constructor with ASS format: karaoke word highlight + hook text overlay (Módulo 4)."""

import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Optional
from PIL import ImageFont
from rich.console import Console

from config import (
    FONTS_DIR,
    HOOK_BOX_COLOR,
    HOOK_MAX_SEC,
    HOOK_TEXT_COLOR,
    SUBTITLE_ACTIVE_COLOR,
    SUBTITLE_ACTIVE_SCALE,
    SUBTITLE_FONT_BOLD,
    SUBTITLE_FONT_FILENAME,
    SUBTITLE_FONT_NAME,
    SUBTITLE_KEYWORD_COLOR,
    SUBTITLE_LIBASS_EM_RATIO,
    SUBTITLE_NORMAL_COLOR,
    SUBTITLE_SPACING,
    VIDEO_FPS,
    VideoFormat,
)
from pipeline_utils import scene_frames

MIN_CHUNK_MS = 350  # visibilidad mínima de un bloque (se recorta si chocaría con el siguiente)
CHUNK_GAP_MS = 10  # 1 cs de separación entre un bloque y el siguiente

console = Console()


def ms_to_ass_time(ms: int) -> str:
    """Convert millisecond integer to ASS timestamp format H:MM:SS.cs."""
    if ms < 0:
        ms = 0
    hours = ms // 3_600_000
    minutes = (ms % 3_600_000) // 60_000
    seconds = (ms % 60_000) // 1_000
    centiseconds = (ms % 1_000) // 10
    return f"{hours}:{minutes:02d}:{seconds:02d}.{centiseconds:02d}"


def clean_word_for_matching(word: str) -> str:
    """Strip punctuation and lowercase for keyword comparison."""
    return re.sub(r"[^\w]", "", word.lower())


def sanitize_ass_text(text: str) -> str:
    """Neutraliza caracteres especiales de ASS (llaves y barra invertida) en texto derivado de la narración."""
    return text.replace("{", "(").replace("}", ")").replace("\\", "/")


def build_ass_header(video_format: VideoFormat, font_name: str = SUBTITLE_FONT_NAME, bold: int = SUBTITLE_FONT_BOLD) -> str:
    """Cabecera ASS a la resolución del formato (PlayRes = resolución del video) con el estilo de subtítulos
    (ViralTikTok) y el del texto gancho (Hook).

    ViralTikTok usa alineación inferior-central (2) con MarginV = subtitle_margin_v del formato; el Hook, superior-central (8).
    Ambos se posicionan además con \\pos en cada evento (la zona segura está en el formato).
    El Hook usa BorderStyle 3: caja opaca del color de OutlineColour con hook_box_padding px de relleno.
    """
    return f"""[Script Info]
Title: TikTok Viral Dynamic Subtitles
ScriptType: v4.00+
WrapStyle: 0
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709
PlayResX: {video_format.width}
PlayResY: {video_format.height}

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: ViralTikTok,{font_name},{video_format.subtitle_font_size},{SUBTITLE_NORMAL_COLOR[:-1]},&H000000FF,&H00000000,&H80000000,{bold},0,0,0,100,100,{SUBTITLE_SPACING},0,1,{video_format.subtitle_outline},{video_format.subtitle_shadow},2,{video_format.subtitle_margin_lr},{video_format.subtitle_margin_lr},{video_format.subtitle_margin_v},1
Style: Hook,{font_name},{video_format.hook_font_size},{HOOK_TEXT_COLOR[:-1]},&H000000FF,{HOOK_BOX_COLOR[:-1]},&H80000000,{bold},0,0,0,100,100,{SUBTITLE_SPACING},0,3,{video_format.hook_box_padding},0,8,{video_format.subtitle_margin_lr},{video_format.subtitle_margin_lr},0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


# --------------------------------------------------------------------------- #
# Medición de ancho (para partir bloques que no caben en subtitle_max_line_width_px del formato)
# --------------------------------------------------------------------------- #

@lru_cache(maxsize=1)
def _measure_font() -> Optional[Any]:
    """Fuente PIL a 1000 px para medir avances de glifos; None si falta el archivo (se usa una estimación)."""
    try:
        return ImageFont.truetype(str(FONTS_DIR / SUBTITLE_FONT_FILENAME), 1000)
    except Exception:
        return None


def measure_text_width(text: str, video_format: VideoFormat) -> float:
    """Ancho estimado en px (a PlayResX) de `text` en una línea: avance de glifos + espaciado + contorno."""
    font = _measure_font()
    em_px = video_format.subtitle_font_size * SUBTITLE_LIBASS_EM_RATIO
    if font is not None:
        advance = font.getlength(text) * em_px / 1000.0
    else:  # sin la fuente: estimación conservadora para mayúsculas
        advance = 0.78 * em_px * len(text)
    return advance + SUBTITLE_SPACING * len(text) + 2 * video_format.subtitle_outline


def chunk_display_width(display_words: list[str], video_format: VideoFormat) -> float:
    """Ancho de un bloque en su peor caso: la línea completa más el crecimiento de la palabra activa más ancha (pop)."""
    full = measure_text_width(" ".join(display_words), video_format)
    widest = max((measure_text_width(w, video_format) for w in display_words), default=0.0)
    return full + widest * (SUBTITLE_ACTIVE_SCALE - 100) / 100.0


def chunk_words(words: list[dict[str, Any]], max_chunk_size: int = 3, min_chunk_size: int = 2) -> list[list[dict[str, Any]]]:
    """Agrupa las palabras en bloques de `min_chunk_size` a `max_chunk_size` palabras.

    Con máximos de hasta 3 (vertical: 2-3 palabras): grupos de 2, sin dejar una palabra suelta al final. Con máximos
    mayores (horizontal: 5-7): reparte las palabras en el menor número de bloques de tamaño parejo que respeta el máximo
    (11 palabras -> 6 + 5); si hay menos que `min_chunk_size`, queda un solo bloque."""
    if not words:
        return []

    total = len(words)
    chunks: list[list[dict[str, Any]]] = []
    if max_chunk_size > 3:
        n_chunks = max(-(-total // max_chunk_size), 1)
        base, extra = divmod(total, n_chunks)
        i = 0
        for k in range(n_chunks):
            take = base + (1 if k < extra else 0)
            chunks.append(words[i : i + take])
            i += take
        return chunks

    i = 0
    while i < total:
        remaining = total - i
        # If remaining is 4, prefer two 2-word chunks rather than 3 then 1
        if remaining == 4:
            take = 2
        elif remaining <= max_chunk_size:
            take = remaining
        else:
            take = max(min_chunk_size, 2)  # default to punchy 2-word groups
        chunks.append(words[i : i + take])
        i += take

    return chunks


def _balanced_split(chunk: list[dict[str, Any]], parts: int) -> list[list[dict[str, Any]]]:
    """Parte `chunk` en `parts` trozos consecutivos de tamaño parejo (los primeros, uno más grande si no es exacto)."""
    base, extra = divmod(len(chunk), parts)
    result, i = [], 0
    for k in range(parts):
        take = base + (1 if k < extra else 0)
        result.append(chunk[i : i + take])
        i += take
    return result


def fit_chunks_to_width(
    chunks: list[list[dict[str, Any]]],
    display: Callable[[dict[str, Any]], str],
    video_format: VideoFormat,
) -> list[list[dict[str, Any]]]:
    """Parte los bloques cuyo ancho supera subtitle_max_line_width_px en el menor número de bloques parejos que sí caben
    (en el peor caso, palabras sueltas), sin dejar una palabra huérfana si se puede evitar.

    Una palabra suelta nunca se parte (si aun así excede el ancho, libass la ajusta dentro de los márgenes).
    """
    max_width = video_format.subtitle_max_line_width_px
    fitted: list[list[dict[str, Any]]] = []
    for chunk in chunks:
        if not chunk:
            continue
        for parts in range(1, len(chunk) + 1):
            pieces = _balanced_split(chunk, parts)
            if parts == len(chunk) or all(
                chunk_display_width([display(w) for w in piece], video_format) <= max_width for piece in pieces
            ):
                fitted.extend(pieces)
                break
    return fitted


def clamp_chunk_ends(events: list[dict[str, Any]], timeline_end_ms: int) -> None:
    """Recorta in situ el fin de cada bloque al inicio del siguiente (menos 1 cs) y al final de la línea de tiempo.

    Evita que dos líneas se apilen en pantalla cuando el mínimo de 350 ms alargó un bloque. Cada evento es un dict
    con start_ms/end_ms en la línea de tiempo continua; se asume ordenado por inicio. Nunca deja un bloque con
    duración menor a 1 cs.
    """
    for idx, ev in enumerate(events):
        limit = events[idx + 1]["start_ms"] - CHUNK_GAP_MS if idx + 1 < len(events) else timeline_end_ms
        ev["end_ms"] = max(min(ev["end_ms"], limit), ev["start_ms"] + CHUNK_GAP_MS)


# --------------------------------------------------------------------------- #
# Karaoke: un evento ASS por porción de tiempo de cada palabra del bloque
# --------------------------------------------------------------------------- #

def karaoke_slices(start_ms: int, end_ms: int, word_starts: list[int]) -> list[tuple[int, int, int]]:
    """Porciones (inicio, fin, índice de palabra) contiguas que cubren exactamente [start_ms, end_ms).

    La palabra i está activa desde su inicio hasta el inicio de la siguiente (los silencios entre palabras del mismo
    bloque se le asignan a la anterior); la última llega hasta `end_ms`. Las porciones de menos de 1 cs se descartan
    y la anterior se extiende, así que nunca quedan huecos.
    """
    bounds: list[int] = []
    last = start_ms
    for ws in word_starts:
        last = min(max(ws, last), end_ms)  # monótono y dentro del bloque
        bounds.append(last)
    bounds[0] = start_ms  # el bloque aparece con su primera palabra activa
    slices: list[tuple[int, int, int]] = []
    for i, b in enumerate(bounds):
        e = bounds[i + 1] if i + 1 < len(bounds) else end_ms
        if e - b < CHUNK_GAP_MS:
            if slices:
                s0, _e0, i0 = slices[-1]
                slices[-1] = (s0, e, i0)  # la porción previa absorbe el tramo descartado
            continue
        slices.append((b, e, i))
    return slices


def chunk_base_scale(display_words: list[str], video_format: VideoFormat) -> int:
    """Escala (%) de todo el bloque: 100 salvo que, ya dividido, aún no quepa (una palabra larguísima): se encoge."""
    max_width = video_format.subtitle_max_line_width_px
    width = chunk_display_width(display_words, video_format)
    return 100 if width <= max_width else max(int(100 * max_width / width), 40)


POP_IN_START_PERCENT = 80  # escala inicial (%) de la animación de aparición de un bloque


def render_chunk_text(
    display_words: list[str],
    keyword_flags: list[bool],
    active_idx: int,
    video_format: VideoFormat,
    base_scale: int = 100,
    pop_in: bool = False,
) -> str:
    """Texto del evento: palabra activa en amarillo con pop, palabras clave en su color, el resto en blanco.

    `base_scale` (%) encoge todo el bloque cuando una sola palabra es más ancha que el máximo. `pop_in=True` (solo el primer
    evento de cada bloque, y solo si el formato define subtitle_pop_in_ms) hace que el bloque aparezca creciendo de
    POP_IN_START_PERCENT % a su escala en subtitle_pop_in_ms (\\t por palabra, porque cada palabra fija su propia escala).
    """
    active_scale = round(SUBTITLE_ACTIVE_SCALE * base_scale / 100)
    pop_ms = video_format.subtitle_pop_in_ms if pop_in else 0

    def pop(target: int) -> str:
        """Etiquetas de escala de una palabra: fija, o animada desde POP_IN_START_PERCENT % hasta `target`."""
        if pop_ms <= 0:
            return f"\\fscx{target}\\fscy{target}"
        start = round(base_scale * POP_IN_START_PERCENT / 100)
        return f"\\fscx{start}\\fscy{start}\\t(0,{pop_ms},\\fscx{target}\\fscy{target})"

    parts = []
    for i, word in enumerate(display_words):
        if i == active_idx:
            parts.append(
                f"{{\\c{SUBTITLE_ACTIVE_COLOR}{pop(active_scale)}}}{word}"
                f"{{\\c{SUBTITLE_NORMAL_COLOR}\\fscx{base_scale}\\fscy{base_scale}}}"
            )
        elif keyword_flags[i]:
            parts.append(f"{{\\c{SUBTITLE_KEYWORD_COLOR}{pop(base_scale) if pop_ms > 0 else ''}}}{word}{{\\c{SUBTITLE_NORMAL_COLOR}}}")
        elif pop_ms > 0:
            parts.append(f"{{{pop(base_scale)}}}{word}")
        else:
            parts.append(word)
    scale_tag = f"\\fscx{base_scale}\\fscy{base_scale}" if base_scale != 100 else ""
    pos_y = video_format.height - video_format.subtitle_margin_v
    pos = f"{{\\an2\\pos({video_format.width // 2},{pos_y}){scale_tag}}}"
    return pos + " ".join(parts)


def build_karaoke_events(timing_metadata: dict[str, Any], video_format: VideoFormat) -> tuple[list[dict[str, Any]], int]:
    """Eventos de subtítulos (uno por palabra activa) y duración total en ms. Nunca hay dos eventos a la vez.

    Cada evento: start_ms, end_ms, text, chunk (nº de bloque), active (índice de la palabra activa en el bloque).
    """
    fps = timing_metadata.get("fps", VIDEO_FPS)
    chunk_events: list[dict[str, Any]] = []
    cumulative_frames = 0

    def display(item: dict[str, Any]) -> str:
        return sanitize_ass_text(item.get("word", "")).upper()

    for scene in timing_metadata.get("scenes", []):
        frames = scene_frames(scene, fps)
        scene_duration_ms = round(frames * 1000 / fps)
        cumulative_offset_ms = round(cumulative_frames * 1000 / fps)  # sin acumular redondeos
        raw_words = list(scene.get("words", []))
        highlight_keywords = {clean_word_for_matching(w) for w in scene.get("highlight_words", [])}

        # Fallback if words list is empty: synthesize evenly distributed word timings
        if not raw_words:
            tokens = scene.get("narration_es", "").split()
            if tokens:
                step_ms = scene_duration_ms / max(len(tokens), 1)
                for idx, token in enumerate(tokens):
                    start = int(idx * step_ms)
                    end = int((idx + 1) * step_ms)
                    raw_words.append({"word": token, "start_ms": start, "end_ms": end, "duration_ms": end - start})

        # Bloques de subtitle_chunk_words palabras del formato (2-3 vertical, 5-7 horizontal), partidos si no caben en el ancho máximo
        min_words, max_words = video_format.subtitle_chunk_words
        for chunk in fit_chunks_to_width(chunk_words(raw_words, max_words, min_words), display, video_format):
            if not chunk:
                continue
            chunk_start_ms = cumulative_offset_ms + chunk[0]["start_ms"]
            chunk_end_ms = cumulative_offset_ms + chunk[-1]["end_ms"]
            # Ensure minimum visibility duration (clamp_chunk_ends later trims overlaps with the next chunk)
            if chunk_end_ms - chunk_start_ms < MIN_CHUNK_MS:
                chunk_end_ms = chunk_start_ms + MIN_CHUNK_MS

            keyword_flags = []
            for item in chunk:
                clean_token = clean_word_for_matching(item.get("word", ""))
                # palabra clave: coincide con highlight_words o contiene números
                keyword_flags.append(clean_token in highlight_keywords or any(c.isdigit() for c in clean_token))

            chunk_events.append({
                "start_ms": chunk_start_ms,
                "end_ms": chunk_end_ms,
                "display": [display(item) for item in chunk],
                "keywords": keyword_flags,
                "word_starts": [cumulative_offset_ms + item["start_ms"] for item in chunk],
            })

        # Advance continuous cumulative timeline by exact (frame-based) scene duration
        cumulative_frames += frames

    total_ms = round(cumulative_frames * 1000 / fps)
    clamp_chunk_ends(chunk_events, total_ms)

    events: list[dict[str, Any]] = []
    for chunk_idx, ce in enumerate(chunk_events):
        for slice_idx, (start, end, active) in enumerate(karaoke_slices(ce["start_ms"], ce["end_ms"], ce["word_starts"])):
            events.append({
                "start_ms": start,
                "end_ms": end,
                "text": render_chunk_text(
                    ce["display"], ce["keywords"], active, video_format, chunk_base_scale(ce["display"], video_format),
                    pop_in=slice_idx == 0,  # el pop-in solo al aparecer el bloque, no en cada cambio de palabra activa
                ),
                "scale": chunk_base_scale(ce["display"], video_format),
                "chunk": chunk_idx,
                "active": active,
            })
    return events, total_ms


def build_hook_event(hook_text: str, timing_metadata: dict[str, Any], video_format: VideoFormat) -> Optional[dict[str, Any]]:
    """Evento del texto gancho: de 0 al fin de la escena 1 (máx. HOOK_MAX_SEC). None si no hay texto."""
    clean = " ".join(sanitize_ass_text(hook_text or "").split()).upper()
    if not clean:
        return None
    fps = timing_metadata.get("fps", VIDEO_FPS)
    scenes = timing_metadata.get("scenes", [])
    scene1_ms = round(scene_frames(scenes[0], fps) * 1000 / fps) if scenes else round(HOOK_MAX_SEC * 1000)
    end_ms = min(scene1_ms, round(HOOK_MAX_SEC * 1000))
    top_y = round(video_format.height * video_format.hook_pos_y_frac)
    return {"start_ms": 0, "end_ms": end_ms, "text": f"{{\\an8\\pos({video_format.width // 2},{top_y})}}{clean}"}


def generate_subtitles_ass(
    timing_metadata: dict[str, Any],
    output_ass_path: Path,
    video_format: VideoFormat,
    font_name: str = SUBTITLE_FONT_NAME,
    hook_text: str = "",
) -> Path:
    """Calculate cumulative continuous timeline and produce styled karaoke ASS subtitles (+ optional hook overlay)."""
    console.print("[cyan]Generando subtítulos dinámicos estilo TikTok (.ass)...[/cyan]")

    header = build_ass_header(video_format, font_name=font_name)
    events, _total_ms = build_karaoke_events(timing_metadata, video_format)
    dialogue_lines = [
        f"Dialogue: 0,{ms_to_ass_time(ev['start_ms'])},{ms_to_ass_time(ev['end_ms'])},ViralTikTok,,0,0,0,,{ev['text']}"
        for ev in events
    ]
    chunks = len({ev["chunk"] for ev in events})

    hook = build_hook_event(hook_text, timing_metadata, video_format)
    if hook:
        # Capa 1 (sobre los subtítulos); va primero para quedar ordenado por inicio
        dialogue_lines.insert(
            0,
            f"Dialogue: 1,{ms_to_ass_time(hook['start_ms'])},{ms_to_ass_time(hook['end_ms'])},Hook,,0,0,0,,{hook['text']}",
        )

    full_ass_content = header + "\n".join(dialogue_lines) + "\n"

    # Only rewrite when content changed (cheap to regenerate; the final-video fingerprint hashes the .ass)
    try:
        unchanged = output_ass_path.exists() and output_ass_path.read_text(encoding="utf-8") == full_ass_content
    except Exception:
        unchanged = False
    if not unchanged:
        with open(output_ass_path, "w", encoding="utf-8") as f:
            f.write(full_ass_content)

    console.print(
        f"[green]✓ Archivo ASS generado:[/] {output_ass_path} "
        f"({chunks} bloques, {len(events)} eventos karaoke{', con texto gancho' if hook else ''})"
    )
    return output_ass_path
