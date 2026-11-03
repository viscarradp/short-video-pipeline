"""Viral dynamic subtitle constructor with ASS format and pop-in animation (Módulo 4)."""

import json
import re
from pathlib import Path
from typing import Any
from rich.console import Console

from config import (
    VIDEO_FPS,
    VIDEO_WIDTH,
    VIDEO_HEIGHT,
    SUBTITLE_FONT_NAME,
    SUBTITLE_FONT_BOLD,
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


def build_ass_header(font_name: str = SUBTITLE_FONT_NAME, bold: int = SUBTITLE_FONT_BOLD) -> str:
    """Generate ASS script header with 1080x1920 resolution and viral style."""
    return f"""[Script Info]
Title: TikTok Viral Dynamic Subtitles
ScriptType: v4.00+
WrapStyle: 0
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709
PlayResX: {VIDEO_WIDTH}
PlayResY: {VIDEO_HEIGHT}

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: ViralTikTok,{font_name},72,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,{bold},0,0,0,100,100,2,0,1,6,3,2,60,60,620,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def chunk_words(words: list[dict[str, Any]], max_chunk_size: int = 3) -> list[list[dict[str, Any]]]:
    """Group words into high-retention blocks of 2 to 3 words maximum."""
    if not words:
        return []

    chunks: list[list[dict[str, Any]]] = []
    i = 0
    total = len(words)

    while i < total:
        remaining = total - i
        # If remaining is 4, prefer two 2-word chunks rather than 3 then 1
        if remaining == 4:
            take = 2
        elif remaining <= max_chunk_size:
            take = remaining
        else:
            take = 2  # default to punchy 2-word groups

        chunk = words[i : i + take]
        chunks.append(chunk)
        i += take

    return chunks


def clamp_chunk_ends(events: list[dict[str, Any]], timeline_end_ms: int) -> None:
    """Recorta in situ el fin de cada bloque al inicio del siguiente (menos 1 cs) y al final de la línea de tiempo.

    Evita que dos líneas se apilen en pantalla cuando el mínimo de 350 ms alargó un bloque. Cada evento es un dict
    con start_ms/end_ms en la línea de tiempo continua; se asume ordenado por inicio. Nunca deja un bloque con
    duración menor a 1 cs.
    """
    for idx, ev in enumerate(events):
        limit = events[idx + 1]["start_ms"] - CHUNK_GAP_MS if idx + 1 < len(events) else timeline_end_ms
        ev["end_ms"] = max(min(ev["end_ms"], limit), ev["start_ms"] + CHUNK_GAP_MS)


def generate_subtitles_ass(
    timing_metadata: dict[str, Any],
    output_ass_path: Path,
    font_name: str = SUBTITLE_FONT_NAME,
) -> Path:
    """Calculate cumulative continuous timeline and produce styled animated ASS subtitles."""
    console.print("[cyan]Generando subtítulos dinámicos estilo TikTok (.ass)...[/cyan]")

    header = build_ass_header(font_name=font_name)
    events: list[dict[str, Any]] = []
    fps = timing_metadata.get("fps", VIDEO_FPS)

    cumulative_frames = 0

    for scene in timing_metadata.get("scenes", []):
        frames = scene_frames(scene, fps)
        scene_duration_ms = round(frames * 1000 / fps)
        cumulative_offset_ms = round(cumulative_frames * 1000 / fps)  # sin acumular redondeos
        raw_words = scene.get("words", [])
        highlight_keywords = {
            clean_word_for_matching(w) for w in scene.get("highlight_words", [])
        }

        # Fallback if words list is empty: synthesize evenly distributed word timings
        if not raw_words:
            narration = scene.get("narration_es", "")
            tokens = narration.split()
            if tokens:
                step_ms = scene_duration_ms / max(len(tokens), 1)
                for idx, token in enumerate(tokens):
                    start = int(idx * step_ms)
                    end = int((idx + 1) * step_ms)
                    raw_words.append({
                        "word": token,
                        "start_ms": start,
                        "end_ms": end,
                        "duration_ms": end - start,
                    })

        # Group words into chunks of 2-3 words
        word_chunks = chunk_words(raw_words, max_chunk_size=3)

        for chunk in word_chunks:
            if not chunk:
                continue

            chunk_start_ms = cumulative_offset_ms + chunk[0]["start_ms"]
            chunk_end_ms = cumulative_offset_ms + chunk[-1]["end_ms"]

            # Ensure minimum visibility duration (clamp_chunk_ends later trims overlaps with the next chunk)
            if chunk_end_ms - chunk_start_ms < MIN_CHUNK_MS:
                chunk_end_ms = chunk_start_ms + MIN_CHUNK_MS

            # Format words inside chunk with uppercase and keyword highlighting
            formatted_words = []
            for item in chunk:
                raw_token = item.get("word", "")
                clean_token = clean_word_for_matching(raw_token)
                display_word = sanitize_ass_text(raw_token).upper()

                # Check if word should be highlighted (match or contains numbers)
                is_highlight = clean_token in highlight_keywords or any(c.isdigit() for c in clean_token)
                if is_highlight:
                    # Bright Yellow highlight: &H0000FFFF&, restore to White: &H00FFFFFF&
                    formatted_words.append(r"{\c&H0000FFFF&}" + display_word + r"{\c&H00FFFFFF&}")
                else:
                    formatted_words.append(display_word)

            text_content = " ".join(formatted_words)

            # Pop-in bounce animation transform: start at 120% scale, shrink to 100% in 65ms
            animation_tag = r"{\fscx120\fscy120\t(0,65,\fscx100\fscy100)}"
            dialogue_text = f"{animation_tag}{text_content}"

            events.append({"start_ms": chunk_start_ms, "end_ms": chunk_end_ms, "text": dialogue_text})

        # Advance continuous cumulative timeline by exact (frame-based) scene duration
        cumulative_frames += frames

    clamp_chunk_ends(events, round(cumulative_frames * 1000 / fps))
    dialogue_lines = [
        f"Dialogue: 0,{ms_to_ass_time(ev['start_ms'])},{ms_to_ass_time(ev['end_ms'])},ViralTikTok,,0,0,0,,{ev['text']}"
        for ev in events
    ]

    full_ass_content = header + "\n".join(dialogue_lines) + "\n"

    # Only rewrite when content changed (cheap to regenerate; the final-video fingerprint hashes the .ass)
    try:
        unchanged = output_ass_path.exists() and output_ass_path.read_text(encoding="utf-8") == full_ass_content
    except Exception:
        unchanged = False
    if not unchanged:
        with open(output_ass_path, "w", encoding="utf-8") as f:
            f.write(full_ass_content)

    console.print(f"[green]✓ Archivo ASS generado:[/] {output_ass_path} ({len(dialogue_lines)} bloques animados)")
    return output_ass_path
