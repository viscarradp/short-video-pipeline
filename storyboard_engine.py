"""Importar un guion escrito a mano: el texto del autor es la narración VERBATIM; el LLM solo hace el storyboard.

Flujo (`generate --script-file guion.txt`):
  1. Limpieza determinista (clean_script_text): quita el formato que añaden los chats (Markdown, emojis, acotaciones
     entre paréntesis o corchetes) y normaliza espacios. Una línea con solo `---` es un corte de escena forzado.
  2. División en escenas POR CÓDIGO (split_into_scenes): frases -> empaquetado hacia ~N palabras por escena. La narración
     nunca pasa por el LLM, así que no puede cambiar: " ".join(escenas) == texto limpio (se comprueba con un assert).
  3. Storyboard con el LLM (storyboard_with_llm): dada la narración ya fija, decide la imagen de cada escena (prompt
     visual, personajes en cámara, efecto, palabras clave) y los textos de publicación. Los textos largos van en bloques.
  4. Normalización (normalize_imported_project): estilo, formato y protagonista como en un guion normal, pero sin añadir
     la frase de cierre del perfil, sin cuota de aparición del protagonista y sin tocar la narración.
  5. Caché (import_or_load_script): huella = texto limpio + perfil + protagonista + escena-segundos + versión del
     storyboard. Mismo comando -> todo en caché; texto distinto -> se rehace el storyboard. retry-scene no necesita el .txt.
  6. Estimación (estimate_import): imágenes por pagar y costo, antes de gastar nada.
"""

import hashlib
import json
import os
import re
import time
import unicodedata
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Optional

from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table

import fal_client
from character_engine import Character
from config import (
    CAMERA_EFFECTS,
    DEFAULT_CAMERA_EFFECT,
    FAL_KEY,
    FLUX_ENDPOINT,
    IMAGE_COST_USD,
    PROTAGONIST_ID,
    SEEDREAM_EDIT_ENDPOINT,
    SEEDREAM_T2I_ENDPOINT,
    VideoFormat,
    get_format,
)
from image_engine import MIN_IMAGE_BYTES, classify_recorded_plan, plan_scene_image
from pipeline_utils import CacheManifest, PipelineError, RunStats, file_sha256, fingerprint
from profiles import VideoProfile, effective_words_per_sec, narration_word_budget
from prompt_templates import (
    PROTAGONIST_FRAMING_RULES,
    STORYBOARD_CHARACTERS_RULE_NO_PROTAGONIST,
    STORYBOARD_CHARACTERS_RULE_PROTAGONIST,
    STORYBOARD_PREVIOUS_BLOCK_TEMPLATE,
    STORYBOARD_PROTAGONIST_BLOCK_TEMPLATE,
    STORYBOARD_PROTAGONIST_QUOTA_ALL_TEMPLATE,
    STORYBOARD_PROTAGONIST_QUOTA_TEMPLATE,
    STORYBOARD_SYSTEM_PROMPT_TEMPLATE,
    STORYBOARD_TASKS,
    STORYBOARD_USER_FULL_TEMPLATE,
    STORYBOARD_USER_META_TEMPLATE,
    STORYBOARD_USER_SCENES_TEMPLATE,
)
from script_engine import (
    LLM_ENDPOINT,
    LLM_MAX_TOKENS_BASE,
    LLM_MAX_TOKENS_CAP,
    LLM_MAX_TOKENS_PER_SCENE,
    LLM_MODEL,
    SceneSchema,
    VideoProjectSchema,
    _write_script,
    build_llm_arguments,
    canonicalize_protagonist_ids,
    clean_llm_json_response,
    ensure_no_people_without_protagonist,
    extract_llm_output,
    filter_highlight_words,
    light_normalize_project,
    llm_response_cost,
    load_cached_project,
    sync_protagonist,
)
from styles import VisualStyle, get_style

console = Console()

# Súbela cuando cambie el prompt del storyboard o el divisor de escenas: invalida los guiones importados en caché
# (su huella incluye este número) para que se rehagan con la lógica nueva.
STORYBOARD_VERSION = 1
STORYBOARD_TEMPERATURE = 0.4  # más bajo que el del guionista (0.7): la narración ya está escrita, aquí se busca fidelidad
STORYBOARD_BLOCK_SCENES = 40  # escenas por llamada al LLM; con más, se pide primero lo global y luego los bloques
STORYBOARD_MAX_ATTEMPTS = 3
STORYBOARD_CONTEXT_SCENES = 3  # últimas escenas del bloque anterior que se pasan al siguiente (continuidad)
PROTAGONIST_TARGET_PERCENT = 75  # % de escenas con el protagonista que se PIDE en el mensaje (el modelo se queda corto 1 o 2 escenas)
PROTAGONIST_MIN_PERCENT = 70  # % mínimo que se ACEPTA: por debajo, se repite la llamada contándole el problema; tras el último intento se usa el mejor y se avisa
SOURCE_SCRIPT_FILENAME = "source_script.txt"  # copia del texto limpio dentro del workspace

# Escenas por tamaño: una escena tiene entre 55 % y 145 % de las palabras objetivo (15 -> 8 a 22)
SCENE_MIN_FRAC = 0.55
SCENE_MAX_FRAC = 1.45
MIN_SCENE_SECONDS, MAX_SCENE_SECONDS = 2.0, 20.0  # rango admitido de --scene-seconds


# --------------------------------------------------------------------------- #
# 1. Limpieza del texto
# --------------------------------------------------------------------------- #

# Emojis y símbolos decorativos (no toca ¿ ¡ … — « » ° % ni ninguna letra). El ZWJ (U+200D) y los selectores de variación
# (U+FE0E / U+FE0F) acompañan a los emojis. Las clases se arman con números para que el fuente no lleve caracteres invisibles.
_EMOJI_RANGES = (
    (0x1F000, 0x1FAFF),  # pictogramas, emoticonos, banderas, transporte...
    (0x2190, 0x21FF),  # flechas
    (0x2300, 0x23FF),  # técnicos misceláneos (relojes, reloj de arena)
    (0x25A0, 0x25FF),  # formas geométricas (cuadrados, triángulos de reproducción)
    (0x2600, 0x27BF),  # símbolos misceláneos y dingbats (sol, checks, estrellas, corazones)
    (0x2900, 0x297F),  # flechas suplementarias
    (0x2B00, 0x2BFF),  # flechas y símbolos misceláneos
    (0xE0020, 0xE007F),  # etiquetas de banderas
)
_EMOJI_MODIFIERS = "".join(chr(c) for c in (0x200D, 0xFE0E, 0xFE0F, 0x20E3))  # ZWJ, selectores y keycap: no se listan aparte en el aviso
_EMOJI_RE = re.compile("[" + "".join(f"{chr(a)}-{chr(b)}" for a, b in _EMOJI_RANGES) + _EMOJI_MODIFIERS + "]")
_KEYCAP_RE = re.compile("[0-9#*]" + chr(0xFE0F) + "?" + chr(0x20E3))  # dígito + keycap (el emoji del 1 con recuadro)
_INVISIBLE_RE = re.compile("[" + "".join(chr(c) for c in (0x200B, 0x200C, 0x2060, 0xFEFF, 0x00AD)) + "]")  # ancho cero, BOM, guion blando
_ODD_SPACES_RE = re.compile("[\t" + "".join(chr(c) for c in (0x00A0, 0x2009, 0x202F, 0x205F, 0x3000)) + chr(0x2002) + "-" + chr(0x200A) + "]")  # tabuladores y espacios Unicode raros

_CUT_LINE_RE = re.compile(r"^\s*-{3,}\s*$")  # corte de escena forzado
_RULE_LINE_RE = re.compile(r"^\s*(?:[*_=~]\s*){3,}$")  # ***, ___, === (separadores de Markdown: se descartan)
_FENCE_LINE_RE = re.compile(r"^\s*```[\w-]*\s*$")
_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+")
# Líneas que los chats añaden como rótulos y que no son narración: se descartan enteras (títulos en negrita cortos,
# "Escena 3: El apagón"). Los prefijos de locutor ("Narrador:") se quitan y se conserva el resto de la línea.
_BOLD_LABEL_RE = re.compile(r"^\s*(\*\*|__)(?=\S)(.+?)(?<=\S)\1\s*:?\s*$")
_SECTION_LABEL_RE = re.compile(r"^\s*(?:escena|parte|acto|cap[ií]tulo|secci[oó]n)\s+\d+\b(?:\s*[:.\-–—].*)?$", re.IGNORECASE)
_SPEAKER_RE = re.compile(r"^\s*(?:narrador|narradora|voz en off|voz|locutor|locutora)\s*:\s*", re.IGNORECASE)
LABEL_MAX_WORDS = 8  # un rótulo en negrita o de sección más largo que esto se trata como narración
_QUOTE_RE = re.compile(r"^\s*(?:>\s?)+")
_BULLET_RE = re.compile(r"^\s*[-*+•·▪◦‣]\s+")
_NUMBERED_RE = re.compile(r"^\s*\d{1,2}[.)]\s+")
_LINK_RE = re.compile(r"(!?)\[([^\]\n]*)\]\(([^)\n]*)\)")
_PAREN_RE = re.compile(r"\([^()]*\)")
_BRACKET_RE = re.compile(r"\[[^\[\]]*\]")
_BOLD_RE = re.compile(r"(\*\*|__)(?=\S)(.+?)(?<=\S)\1")
_ITALIC_STAR_RE = re.compile(r"(?<![\w*])\*(?=[^\s*])(.+?)(?<=[^\s*])\*(?![\w*])")
_ITALIC_UNDER_RE = re.compile(r"(?<![\w_])_(?=[^\s_])(.+?)(?<=[^\s_])_(?![\w_])")
_STRIKE_RE = re.compile(r"~~(?=\S)(.+?)(?<=\S)~~")
_STRAY_UNDERSCORE_RE = re.compile(r"(?<!\w)_+|_+(?!\w)")
_SPACE_BEFORE_PUNCT_RE = re.compile(r"[ ]+([,;.:!?…])")
_STRAY_BRACKET_RE = re.compile(r"[()\[\]]")

_TERMINAL_CHARS = ".!?…"
_CLOSERS = "\"'”’»)]"
DIRECTION_SNIPPET_MAX = 40  # caracteres de cada acotación quitada que se muestran en el aviso
DIRECTIONS_SHOWN = 6


@dataclass
class CleanReport:
    """Qué quitó la limpieza (para el aviso amarillo). Las listas no tienen repetidos y conservan el orden."""

    markdown: list[str] = field(default_factory=list)  # tipos de marcado: "#", "**", "_", ">", "viñetas"...
    emojis: list[str] = field(default_factory=list)
    directions: list[str] = field(default_factory=list)  # acotaciones (entre paréntesis o corchetes), con sus delimitadores
    periods_added: int = 0  # párrafos sin puntuación final a los que se añadió un punto
    cuts: int = 0  # líneas '---' (cortes de escena forzados)
    dropped_lines: list[str] = field(default_factory=list)  # títulos y rótulos descartados (no se narran)

    def note_markdown(self, kind: str) -> None:
        if kind not in self.markdown:
            self.markdown.append(kind)

    def summary(self) -> str:
        """Texto del aviso ('' si no se quitó nada). Ya escapado para rich (las acotaciones llevan corchetes)."""
        parts: list[str] = []
        if self.markdown:
            parts.append(f"formato Markdown ({', '.join(self.markdown)})")
        if self.emojis:
            parts.append(f"{len(self.emojis)} emoji(s)/símbolo(s) ({' '.join(self.emojis[:8])}{' ...' if len(self.emojis) > 8 else ''})")
        if self.directions:
            shown = ", ".join(self.directions[:DIRECTIONS_SHOWN])
            more = f" y {len(self.directions) - DIRECTIONS_SHOWN} más" if len(self.directions) > DIRECTIONS_SHOWN else ""
            parts.append(f"{len(self.directions)} acotación(es) ({shown}{more})")
        if self.dropped_lines:
            shown = ", ".join(f'"{line[:DIRECTION_SNIPPET_MAX]}"' for line in self.dropped_lines[:DIRECTIONS_SHOWN])
            more = f" y {len(self.dropped_lines) - DIRECTIONS_SHOWN} más" if len(self.dropped_lines) > DIRECTIONS_SHOWN else ""
            parts.append(f"{len(self.dropped_lines)} título(s)/rótulo(s) que no se narran ({shown}{more})")
        if self.periods_added:
            parts.append(f"añadido el punto final a {self.periods_added} párrafo(s) sin puntuación")
        return escape("; ".join(parts))


@dataclass
class CleanedScript:
    blocks: list[str]  # texto de cada tramo entre cortes '---', con los espacios normalizados
    report: CleanReport

    @property
    def text(self) -> str:
        return " ".join(self.blocks)


def _record_emojis(match: "re.Match[str]", report: CleanReport) -> str:
    char = match.group(0)
    if char not in _EMOJI_MODIFIERS and char not in report.emojis:
        report.emojis.append(char)
    return ""


def _strip_directions(text: str, report: CleanReport) -> str:
    """Quita las acotaciones (paréntesis y corchetes, también anidados o en varias líneas) y los enlaces Markdown ([texto](url) -> texto)."""

    def link(m: "re.Match[str]") -> str:
        report.note_markdown("enlaces")
        return "" if m.group(1) else m.group(2)  # una imagen no tiene texto; un enlace conserva el suyo

    text = _LINK_RE.sub(link, text)
    removed_any = False
    for pattern in (_PAREN_RE, _BRACKET_RE):
        while True:
            def drop(m: "re.Match[str]") -> str:
                snippet = " ".join(m.group(0).split())
                if len(snippet) > DIRECTION_SNIPPET_MAX:
                    snippet = snippet[: DIRECTION_SNIPPET_MAX - 1].rstrip() + "…" + snippet[-1]
                report.directions.append(snippet)
                return " "

            new_text, n = pattern.subn(drop, text)
            if n == 0:
                break
            removed_any = True
            text = new_text
    if removed_any:
        text = _SPACE_BEFORE_PUNCT_RE.sub(r"\1", text)  # "Max (suspira), dijo" -> "Max, dijo"
    return text


def _strip_inline_markdown(line: str, report: CleanReport) -> str:
    """Quita negritas, cursivas, tachado y código en línea conservando el texto."""
    before = line
    for kind, pattern, repl in (
        ("**", _BOLD_RE, r"\2"),
        ("_", _ITALIC_UNDER_RE, r"\1"),
        ("*", _ITALIC_STAR_RE, r"\1"),
        ("~~", _STRIKE_RE, r"\1"),
    ):
        new_line = pattern.sub(repl, line)
        if new_line != line:
            report.note_markdown(kind)
            line = new_line
    # Restos sin pareja
    line = line.replace("`", "").replace("*", "")
    line = _STRAY_UNDERSCORE_RE.sub("", line)
    if line != before and not any(k in report.markdown for k in ("**", "_", "*", "~~")):
        report.note_markdown("símbolos de formato")
    return line


def _ends_sentence(paragraph: str) -> bool:
    stripped = paragraph.rstrip(_CLOSERS)
    return not stripped or not stripped[-1].isalnum()  # termina en puntuación (.!?…:;,—): no se le añade nada


def _is_label_line(line: str) -> bool:
    """Rótulo de chat que no es narración: una línea entera en negrita o de sección, de hasta LABEL_MAX_WORDS palabras."""
    m = _BOLD_LABEL_RE.match(line)
    label = m.group(2) if m else (line if _SECTION_LABEL_RE.match(line) else None)
    return label is not None and len(label.split()) <= LABEL_MAX_WORDS


def clean_script_text(raw: str) -> CleanedScript:
    """Texto crudo -> tramos de texto limpio (separados por las líneas `---`) y el informe de lo que se quitó.

    Quita Markdown (#, **, _, >, viñetas, enlaces, bloques de código), emojis y acotaciones entre (paréntesis) o
    [corchetes]; descarta títulos (# ...), rótulos cortos en negrita y de sección ("Escena 3: ...") y prefijos de
    locutor ("Narrador:"); normaliza los espacios; conserva la puntuación española (¿¡…—«»). Los párrafos y viñetas
    sin puntuación final reciben un punto para que la voz haga la pausa y la división en frases no los una con el
    siguiente. No cambia ninguna palabra de la narración."""
    report = CleanReport()
    text = unicodedata.normalize("NFC", raw).replace("\r\n", "\n").replace("\r", "\n")
    text = _INVISIBLE_RE.sub("", text)
    text = _ODD_SPACES_RE.sub(" ", text)
    text = _KEYCAP_RE.sub("", text)
    text = _EMOJI_RE.sub(lambda m: _record_emojis(m, report), text)
    text = _strip_directions(text, report)

    blocks: list[list[str]] = [[]]  # tramos -> párrafos
    lines_of_paragraph: list[str] = []

    def flush() -> None:
        if lines_of_paragraph:
            paragraph = " ".join(" ".join(lines_of_paragraph).split())
            lines_of_paragraph.clear()
            if paragraph:
                if not _ends_sentence(paragraph):
                    paragraph += "."
                    report.periods_added += 1
                blocks[-1].append(paragraph)

    for line in text.split("\n"):
        if _CUT_LINE_RE.match(line):
            flush()
            report.cuts += 1
            if blocks[-1]:
                blocks.append([])
            continue
        if _FENCE_LINE_RE.match(line):
            report.note_markdown("```")
            flush()
            continue
        if not line.strip():
            flush()
            continue
        if _RULE_LINE_RE.match(line):
            report.note_markdown("separadores")
            flush()
            continue
        standalone = False  # cita o viñeta: es su propio párrafo
        if _HEADING_RE.match(line) or _is_label_line(line):
            if _HEADING_RE.match(line):
                report.note_markdown("#")
            report.dropped_lines.append(" ".join(_HEADING_RE.sub("", line, count=1).strip(" *_").split()))
            flush()
            continue
        line = _SPEAKER_RE.sub("", line, count=1)
        if _QUOTE_RE.match(line):
            line = _QUOTE_RE.sub("", line, count=1)
            report.note_markdown(">")
        if _BULLET_RE.match(line):
            line, standalone = _BULLET_RE.sub("", line, count=1), True
            report.note_markdown("viñetas")
        elif _NUMBERED_RE.match(line):
            line, standalone = _NUMBERED_RE.sub("", line, count=1), True
            report.note_markdown("numeración")
        line = _STRAY_BRACKET_RE.sub(" ", _strip_inline_markdown(line, report))  # un paréntesis o corchete sin pareja tampoco se lee
        if standalone:
            flush()
        lines_of_paragraph.append(line)
        if standalone:
            flush()
    flush()

    return CleanedScript(blocks=[" ".join(paragraphs) for paragraphs in blocks if paragraphs], report=report)


# --------------------------------------------------------------------------- #
# 2. División en frases y en escenas (por código: la narración no cambia)
# --------------------------------------------------------------------------- #

# Abreviaturas tras las que un punto NO cierra la oración (títulos, direcciones...)
_ABBREV_NEVER_ENDS = {
    "sr", "sra", "srta", "dr", "dra", "lic", "ing", "prof", "arq", "av", "avda", "gral", "cnel", "cap", "mtro", "mtra",
    "ud", "uds", "vs", "sta", "sto", "mr", "mrs", "ms", "st", "jr", "dpto", "depto", "col", "cía", "tel", "pág", "págs",
}
# Abreviaturas que pueden terminar una oración: solo la cierran si lo siguiente empieza en mayúscula, ¿ o ¡
_ABBREV_MAY_END = {"etc", "aprox", "ee.uu", "a.m", "p.m", "máx", "mín", "hs"}
_SENTENCE_BOUNDARY_RE = re.compile(r"([.!?…]+)([\"'”’»)\]]*)[ ]+(?=\S)")
_OPENERS = "¿¡«“\"'(["
_WORD_CHAR_RE = re.compile(r"\w")

_CONJUNCTIONS = {"y", "e", "pero", "porque", "aunque", "mientras", "sino", "pues", "cuando"}
# Palabras tras las que no conviene cortar (artículos, preposiciones, conjunciones, posesivos...)
_WEAK_WORDS = {
    "el", "la", "los", "las", "un", "una", "unos", "unas", "de", "del", "al", "a", "en", "con", "por", "para", "sin",
    "sobre", "entre", "hacia", "hasta", "desde", "y", "e", "o", "u", "que", "su", "sus", "mi", "mis", "tu", "tus",
    "se", "lo", "le", "les", "me", "te", "no", "más", "muy", "como", "si",
}
# Costo de cortar en cada tipo de punto (más bajo = mejor); se suma a la distancia relativa al objetivo
_CUT_PENALTY = {"sentence": 0.0, "clause": 2.5, "conjunction": 4.0, "word": 9.0}
_WEAK_END_PENALTY = 4.0


def split_sentences(text: str) -> list[str]:
    """Divide un párrafo en frases (. ! ? …), manteniendo ¿ y ¡ con su frase.

    No corta en decimales (3.5, 1.500: no hay espacio tras el punto), abreviaturas comunes (Sr., Dra., etc.), iniciales
    (J. R.) ni tras unos puntos suspensivos que siguen en minúscula. Las frases se concatenan con un espacio: unirlas
    devuelve el texto de entrada."""
    text = " ".join(text.split())
    sentences: list[str] = []
    start = 0
    for m in _SENTENCE_BOUNDARY_RE.finditer(text):
        terminal, nxt = m.group(1), text[m.end()]
        raw_token = text[start : m.start()].split(" ")[-1] if m.start() > start else ""
        token = raw_token.lstrip(_OPENERS)
        key = token.lower()
        if terminal == ".":
            if key in _ABBREV_NEVER_ENDS or re.fullmatch(r"[A-ZÁÉÍÓÚÑÜ]", token):
                continue
            if key in _ABBREV_MAY_END and not (nxt.isupper() or nxt in "¿¡"):
                continue
        if set(terminal) <= {".", "…"} and (len(terminal) > 1 or terminal == "…") and nxt.islower():
            continue  # puntos suspensivos que siguen la misma frase ("y entonces... lo vio")
        sentences.append(text[start : m.end()].strip())
        start = m.end()
    tail = text[start:].strip()
    if tail:
        sentences.append(tail)
    return sentences


def _count_words(token: str) -> int:
    return 1 if _WORD_CHAR_RE.search(token) else 0  # una raya suelta o un signo no cuentan como palabra


def _boundary_kind(tokens: list[str], i: int, sentence_ends: set[int]) -> str:
    """Tipo del punto de corte entre tokens[i-1] y tokens[i]: oración, cláusula (, ; : — …), conjunción o palabra."""
    if i in sentence_ends:
        return "sentence"
    prev = tokens[i - 1].rstrip(_CLOSERS)
    if prev[-1:] in (",", ";", ":", "—", "–", "…") or tokens[i][:1] in ("—", "–"):
        return "clause"
    if tokens[i].lower().strip(".,;:!?¿¡…\"“”«»()") in _CONJUNCTIONS:
        return "conjunction"
    return "word"


def _cut_cost(kind: str, words: int, target: int, weak_end: bool) -> float:
    return abs(words - target) / target * 10 + _CUT_PENALTY[kind] + (_WEAK_END_PENALTY if weak_end and kind == "word" else 0.0)


def _split_block(text: str, target: int) -> list[str]:
    """Reparte un tramo de texto en escenas de ~`target` palabras (entre SCENE_MIN_FRAC y SCENE_MAX_FRAC del objetivo).

    Cada escena termina en el mejor punto de corte de su ventana: fin de frase > coma/punto y coma/dos puntos/raya >
    antes de una conjunción > cualquier palabra (evitando cortar tras un artículo o preposición). El resto del tramo
    nunca queda más corto que el mínimo salvo que todo el tramo lo sea."""
    sentences = split_sentences(text)
    tokens: list[str] = []
    sentence_ends: set[int] = set()
    for sentence in sentences:
        tokens.extend(sentence.split())
        sentence_ends.add(len(tokens))
    cum = [0]  # palabras acumuladas antes de cada token
    for token in tokens:
        cum.append(cum[-1] + _count_words(token))
    total = cum[-1]
    min_words = max(1, round(target * SCENE_MIN_FRAC))
    max_words = max(min_words + 1, round(target * SCENE_MAX_FRAC))

    scenes: list[str] = []
    start = 0
    while start < len(tokens):
        remaining = total - cum[start]
        end = None
        if remaining <= max_words and remaining < 2 * min_words:
            end = len(tokens)  # lo que queda cabe en una escena y partirlo dejaría un trozo menor que el mínimo
        else:
            # Intentos de más estricto a más laxo: (palabras mínimas de la escena, palabras mínimas del resto)
            for scene_min, rest_min in ((min_words, min_words), (min_words, 1), (1, 1)):
                best_cost = None
                candidates = range(start + 1, len(tokens) + 1) if remaining <= max_words else range(start + 1, len(tokens))
                for i in candidates:
                    words = cum[i] - cum[start]
                    if words > max_words:
                        break
                    rest = total - cum[i]
                    if words < scene_min or (rest and rest < rest_min):
                        continue
                    if i == len(tokens):
                        kind, weak = "sentence", False
                    else:
                        kind = _boundary_kind(tokens, i, sentence_ends)
                        weak = tokens[i - 1].lower().strip(".,;:!?¿¡…\"“”«»()") in _WEAK_WORDS
                    cost = _cut_cost(kind, words, target, weak)
                    if best_cost is None or cost < best_cost:
                        best_cost, end = cost, i
                if end is not None:
                    break
            if end is None:  # último recurso (p. ej. un "texto" sin palabras): corta al llegar al objetivo
                end = start + 1
                while end < len(tokens) and cum[end] - cum[start] < target:
                    end += 1
        scenes.append(" ".join(tokens[start:end]))
        start = end
    return scenes


def split_into_scenes(blocks: list[str], target_words: int) -> list[str]:
    """Escenas de ~`target_words` palabras a partir de los tramos de texto limpio (una escena nunca cruza un corte `---`).

    Garantía: " ".join(escenas) == " ".join(tramos) (solo se corta en espacios, nunca dentro de una palabra)."""
    if target_words < 1:
        raise ValueError("target_words debe ser >= 1")
    scenes: list[str] = []
    for block in blocks:
        scenes.extend(_split_block(block, target_words))
    return scenes


# --------------------------------------------------------------------------- #
# 3. Guion importado (texto limpio + escenas) y su huella
# --------------------------------------------------------------------------- #

@dataclass
class ImportedScript:
    """Texto del autor ya limpio y dividido en escenas; la narración definitiva del video."""

    blocks: list[str]  # tramos limpios (los separa un corte '---')
    scenes: list[str]  # narración de cada escena: partición exacta de los tramos
    scene_seconds: float
    target_words: int  # palabras por escena que se buscaron
    source_name: str = ""

    @property
    def text(self) -> str:
        return " ".join(self.blocks)

    @property
    def word_count(self) -> int:
        return sum(_count_words(t) for t in self.text.split())

    @property
    def canonical_text(self) -> str:
        """Representación estable del texto limpio (incluye dónde están los cortes forzados): lo que se guarda y se hashea."""
        return "\n\n---\n\n".join(self.blocks) + "\n"

    @property
    def source_hash(self) -> str:
        return hashlib.sha256(self.canonical_text.encode("utf-8")).hexdigest()

    def run_config_info(self) -> dict[str, Any]:
        """Lo que run_config.json recuerda de la importación (retry-scene lo usa para saber que es un guion importado)."""
        return {
            "source_sha256": self.source_hash,
            "scene_seconds": self.scene_seconds,
            "source_file": SOURCE_SCRIPT_FILENAME,
            "original_name": self.source_name,
        }


def _fold(text: str) -> str:
    """Minúsculas sin acentos (para buscar el nombre del protagonista)."""
    return "".join(c for c in unicodedata.normalize("NFD", text.lower()) if not unicodedata.combining(c))


def mentions_name(text: str, name: str) -> bool:
    """True si `name` aparece como palabra completa en `text` (sin distinguir mayúsculas ni acentos)."""
    needle = _fold(name.strip())
    return bool(needle) and re.search(rf"(?<!\w){re.escape(needle)}(?!\w)", _fold(text)) is not None


def scene_words_target(profile: VideoProfile, scene_seconds: float) -> int:
    """Palabras por escena para `scene_seconds` con la voz del perfil (misma cuenta que narration_word_budget)."""
    return narration_word_budget(replace(profile, target_scene_duration_sec=scene_seconds))


def prepare_import(
    raw_text: str,
    profile: VideoProfile,
    scene_seconds: float,
    protagonist: Optional[Character] = None,
    source_name: str = "",
) -> ImportedScript:
    """Limpia y divide el texto del autor. Imprime el aviso de lo que se quitó (uno solo) y el del nombre del protagonista.

    Lanza PipelineError si no queda texto o si --scene-seconds está fuera de rango."""
    if not MIN_SCENE_SECONDS <= scene_seconds <= MAX_SCENE_SECONDS:
        raise PipelineError(f"--scene-seconds debe estar entre {MIN_SCENE_SECONDS:g} y {MAX_SCENE_SECONDS:g} segundos (recibido: {scene_seconds:g}).")
    cleaned = clean_script_text(raw_text)
    if not cleaned.blocks:
        raise PipelineError("El guion está vacío (o solo tenía formato, acotaciones y emojis): no queda texto para narrar.")
    summary = cleaned.report.summary()
    if summary:
        console.print(f"[yellow]⚠ Limpieza del texto: se quitó {summary}. La narración final es el texto limpio.[/yellow]")

    target_words = scene_words_target(profile, scene_seconds)
    scenes = split_into_scenes(cleaned.blocks, target_words)
    assert " ".join(scenes) == " ".join(cleaned.blocks), "la división en escenas alteró el texto"

    script = ImportedScript(
        blocks=cleaned.blocks, scenes=scenes, scene_seconds=scene_seconds, target_words=target_words, source_name=source_name
    )
    if protagonist is not None and not mentions_name(script.text, protagonist.name):
        console.print(
            f"[yellow]⚠ El nombre del protagonista '{escape(protagonist.name)}' no aparece en el texto: el storyboard lo pondrá "
            "como guía en la mayoría de las escenas igualmente, pero el texto no lo nombra (se oirá una narración sin él).[/yellow]"
        )
    return script


def import_fingerprint(
    source: ImportedScript, profile_name: str, protagonist_slug: str, mock_script: bool
) -> str:
    """Huella del guion importado: texto limpio + perfil + protagonista + escena-segundos (y las palabras por escena que
    resultan) + versión del storyboard + modo mock. Ni el estilo ni el formato forman parte (igual que en un guion normal)."""
    return fingerprint(
        kind="script_import",
        source=source.source_hash,
        profile=profile_name,
        protagonist=protagonist_slug,
        scene_seconds=source.scene_seconds,
        target_words=source.target_words,
        storyboard_version=STORYBOARD_VERSION,
        mock_script=mock_script,
    )


# --------------------------------------------------------------------------- #
# 4. Storyboard con el LLM
# --------------------------------------------------------------------------- #

@dataclass
class _StoryboardMeta:
    title: str
    hook_text: str
    post_description: str
    hashtags: list[str]
    characters: dict[str, str]


@dataclass
class _SceneVisual:
    visual_prompt_en: str
    characters_in_scene: list[str]
    camera_effect: str
    highlight_words: list[str]


def build_storyboard_system_prompt(
    profile: VideoProfile,
    style: VisualStyle,
    video_format: VideoFormat,
    protagonist: Optional[Character],
    mode: str,
) -> str:
    """Prompt de sistema del storyboard. `mode`: "full" (todo en una llamada), "meta" (solo textos de publicación y
    personajes) o "scenes" (un bloque de escenas, con los personajes ya definidos)."""
    scene_example = {
        "scene_id": 1,
        "visual_prompt_en": "Cinematic visual description in English",
        "characters_in_scene": [PROTAGONIST_ID] if protagonist is not None else ["char_1"],
        "camera_effect": "zoom_in_fast",
        "highlight_words": ["palabra1", "palabra2"],
    }
    characters_example = (
        {"char_1": "Short physical description in English of a SECONDARY character (age, hair, clothing)"}
        if protagonist is not None
        else {"char_1": "Detailed physical description in English (age, hair, clothing, facial traits)"}
    )
    meta_example: dict[str, Any] = {
        "title": "Título corto y viral",
        "hook_text": "2 a 6 palabras en español, texto en pantalla de los primeros segundos",
        "post_description": "1 o 2 frases para el pie del post; la última es una pregunta que invite a comentar",
        "hashtags": ["hashtag1", "hashtag2", "hashtag3", "hashtag4", "hashtag5"],
        "characters": characters_example,
    }
    schema: dict[str, Any] = {"full": {**meta_example, "scenes": [scene_example]}, "meta": meta_example, "scenes": {"scenes": [scene_example]}}[mode]

    protagonist_block = ""
    if protagonist is not None:
        protagonist_block = STORYBOARD_PROTAGONIST_BLOCK_TEMPLATE.format(
            protagonist_name=protagonist.name,
            protagonist_description=" ".join(str(protagonist.description_en).split()),
            protagonist_id=PROTAGONIST_ID,
            framing_rule=PROTAGONIST_FRAMING_RULES.get(video_format.id, ""),
        )
    characters_rule = (
        STORYBOARD_CHARACTERS_RULE_PROTAGONIST if protagonist is not None else STORYBOARD_CHARACTERS_RULE_NO_PROTAGONIST
    ).format()
    return STORYBOARD_SYSTEM_PROMPT_TEMPLATE.format(
        platform_label=video_format.platform_label_es,
        task=STORYBOARD_TASKS[mode],
        schema_json=json.dumps(schema, indent=2, ensure_ascii=False),
        camera_effects=str(list(CAMERA_EFFECTS)),
        characters_rule=characters_rule,
        style_label=style.label,
        style_guidance=style.scene_guidance,
        protagonist_block=protagonist_block,
    )


def _scene_lines(scene_ids: list[int], narrations: dict[int, str]) -> str:
    return "\n".join(f"{i}: {narrations[i]}" for i in scene_ids)


def protagonist_quota_text(protagonist: Optional[Character], n_scenes: int) -> str:
    """Recordatorio con números para el mensaje de usuario ('' sin protagonista): en cuántas de las `n_scenes` escenas de la
    llamada se le pide que salga el protagonista (PROTAGONIST_TARGET_PERCENT %, redondeado hacia arriba) y cuántas pueden quedar sin él."""
    if protagonist is None or n_scenes < 1:
        return ""
    min_with = _min_scenes_with(n_scenes, PROTAGONIST_TARGET_PERCENT)
    max_without = n_scenes - min_with
    template = STORYBOARD_PROTAGONIST_QUOTA_TEMPLATE if max_without > 0 else STORYBOARD_PROTAGONIST_QUOTA_ALL_TEMPLATE
    return template.format(
        protagonist_name=protagonist.name,
        protagonist_id=PROTAGONIST_ID,
        min_with=min_with,
        n_scenes=n_scenes,
        max_without=max_without,
    )


def _min_scenes_with(n_scenes: int, percent: int) -> int:
    """Escenas que son al menos `percent` % de `n_scenes` (techo con enteros: 0.7 * 10 en coma flotante da 7.000000000000001)."""
    return -(-n_scenes * percent // 100)


def _protagonist_share_check(visuals: dict[int, "_SceneVisual"], protagonist: Optional[Character]) -> tuple[int, str]:
    """(escenas con el protagonista, problema) de un tramo recién parseado. problema = "" si llega al mínimo aceptado
    (PROTAGONIST_MIN_PERCENT %) o si no hay protagonista; si no, el texto que se le devuelve al LLM al repetir la llamada."""
    n_with = sum(1 for v in visuals.values() if PROTAGONIST_ID in v.characters_in_scene)
    if protagonist is None or not visuals:
        return n_with, ""
    n = len(visuals)
    needed = _min_scenes_with(n, PROTAGONIST_MIN_PERCENT)
    if n_with >= needed:
        return n_with, ""
    without = [i for i in sorted(visuals) if PROTAGONIST_ID not in visuals[i].characters_in_scene]
    return n_with, (
        f"El protagonista ('{PROTAGONIST_ID}') solo sale en {n_with} de {n} escenas y debe salir en al menos {needed}: es la guía del "
        f"espectador y aparece también en las escenas de datos o explicaciones (las vive, las presencia, las demuestra). Solo los planos "
        f"épicos o de escala pueden quedar sin él. Ponlo (en 'characters_in_scene' y como \"the main character\" en 'visual_prompt_en') "
        f"en más de las escenas donde ahora no sale: {', '.join(str(i) for i in without[:12])}."
    )


def _llm_max_tokens(n_scenes: int) -> int:
    return min(LLM_MAX_TOKENS_BASE + LLM_MAX_TOKENS_PER_SCENE * max(n_scenes, 1), LLM_MAX_TOKENS_CAP)


def _parse_meta(data: dict[str, Any], protagonist: Optional[Character]) -> _StoryboardMeta:
    """Título, textos de publicación y personajes secundarios de la respuesta; ValueError si falta algo esencial."""
    title = " ".join(str(data.get("title") or "").split())
    if not title:
        raise ValueError("falta 'title' (texto no vacío)")
    raw_characters = data.get("characters") or {}
    if not isinstance(raw_characters, dict):
        raise ValueError("'characters' debe ser un objeto {id: descripción en inglés}")
    aliases = {PROTAGONIST_ID} | ({protagonist.slug.lower(), protagonist.name.strip().lower()} if protagonist is not None else set())
    characters: dict[str, str] = {}
    for char_id, description in raw_characters.items():
        char_id, description = str(char_id).strip(), " ".join(str(description or "").split())
        if char_id and description and char_id.lower() not in aliases:  # el protagonista lo añade el código
            characters[char_id] = description
    hashtags = data.get("hashtags") or []
    return _StoryboardMeta(
        title=title,
        hook_text=" ".join(str(data.get("hook_text") or "").split()),
        post_description=" ".join(str(data.get("post_description") or "").split()),
        hashtags=[str(h) for h in hashtags] if isinstance(hashtags, list) else [],
        characters=characters,
    )


def _parse_scene_entries(
    data: dict[str, Any], expected_ids: list[int], known_characters: set[str], protagonist: Optional[Character]
) -> dict[int, _SceneVisual]:
    """Una entrada por escena esperada (ni más ni menos, sin repetidas). ValueError con el detalle si no cuadra."""
    entries = data.get("scenes")
    if not isinstance(entries, list):
        raise ValueError("falta 'scenes' (lista de escenas)")
    found: dict[int, _SceneVisual] = {}
    aliases = {PROTAGONIST_ID} | ({protagonist.slug.lower(), protagonist.name.strip().lower()} if protagonist is not None else set())
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("cada escena debe ser un objeto")
        try:
            scene_id = int(entry.get("scene_id"))
        except (TypeError, ValueError):
            raise ValueError(f"escena sin 'scene_id' entero: {str(entry)[:120]}") from None
        if scene_id in found:
            raise ValueError(f"scene_id {scene_id} repetido")
        visual = " ".join(str(entry.get("visual_prompt_en") or "").split())
        if not visual:
            raise ValueError(f"la escena {scene_id} no tiene 'visual_prompt_en'")
        ids: list[str] = []
        raw_ids = entry.get("characters_in_scene") or []
        for char_id in raw_ids if isinstance(raw_ids, list) else []:
            char_id = str(char_id).strip()
            canonical = PROTAGONIST_ID if (protagonist is not None and char_id.lower() in aliases) else char_id
            if canonical in known_characters and canonical not in ids:
                ids.append(canonical)  # un ID que el LLM no definió no sirve: se descarta
        camera = str(entry.get("camera_effect") or "").strip().lower()
        raw_words = entry.get("highlight_words") or []
        found[scene_id] = _SceneVisual(
            visual_prompt_en=visual,
            characters_in_scene=ids,
            camera_effect=camera if camera in CAMERA_EFFECTS else DEFAULT_CAMERA_EFFECT,
            highlight_words=[str(w) for w in raw_words] if isinstance(raw_words, list) else [],
        )
    missing = [i for i in expected_ids if i not in found]
    extra = sorted(set(found) - set(expected_ids))
    if missing or extra:
        raise ValueError(
            f"se esperaban las {len(expected_ids)} escenas {expected_ids[0]} a {expected_ids[-1]} exactas"
            + (f"; faltan {missing[:12]}" if missing else "")
            + (f"; sobran {extra[:12]}" if extra else "")
        )
    return found


def _call_storyboard_llm(
    system_prompt: str,
    base_user_prompt: str,
    max_tokens: int,
    parse: Callable[[dict[str, Any]], Any],
    stats: Optional[RunStats],
    label: str,
    check: Optional[Callable[[Any], tuple[int, str]]] = None,
) -> Any:
    """Una llamada al LLM con hasta STORYBOARD_MAX_ATTEMPTS intentos: si la respuesta no cuadra (JSON roto, escenas que
    faltan...) se reintenta contándole el error. Cada llamada cobrada se suma a stats.llm_cost_usd.

    `check(resultado) -> (puntaje, problema)` es una exigencia de CALIDAD (no de formato): si devuelve un problema, se repite
    la llamada igual que con un error, pero si ningún intento la cumple se usa el de mayor puntaje en vez de fallar."""
    last_error: Optional[Exception] = None
    best: Optional[tuple[int, Any, str]] = None  # (puntaje, resultado, problema) del mejor intento válido que no pasó el check
    user_prompt = base_user_prompt
    for attempt in range(1, STORYBOARD_MAX_ATTEMPTS + 1):
        try:
            res = fal_client.run(
                LLM_ENDPOINT,
                arguments=build_llm_arguments(system_prompt, user_prompt, max_tokens, STORYBOARD_TEMPERATURE),
            )
            cost = llm_response_cost(res)  # cada llamada se cobra, también las que luego se rechazan
            if stats is not None:
                stats.llm_cost_usd += cost
            data = json.loads(clean_llm_json_response(extract_llm_output(res)))
            if not isinstance(data, dict):
                raise ValueError("la respuesta debe ser un objeto JSON")
            result = parse(data)
            if check is not None:
                score, problem = check(result)
                if problem:
                    if best is None or score > best[0]:
                        best = (score, result, problem)
                    raise ValueError(problem)
            return result
        except Exception as err:
            last_error = err
            if attempt < STORYBOARD_MAX_ATTEMPTS:
                wait = 2 ** attempt
                console.print(
                    f"[yellow]Intento {attempt}/{STORYBOARD_MAX_ATTEMPTS} del storyboard ({label}) falló: "
                    f"{escape(str(err)[:300])}. Reintentando en {wait}s...[/yellow]"
                )
                user_prompt = (
                    f"{base_user_prompt}\n\n"
                    f"Tu respuesta anterior fue rechazada por este error:\n{str(err)[:500]}\n"
                    "Corrige el problema y devuelve ÚNICAMENTE el JSON válido completo."
                )
                time.sleep(wait)
            else:
                console.print(f"[yellow]Intento {attempt}/{STORYBOARD_MAX_ATTEMPTS} del storyboard ({label}) falló: {escape(str(err)[:300])}.[/yellow]")
    if best is not None:
        console.print(f"[yellow]Aviso ({label}): se usa el mejor intento porque ninguno cumplió la exigencia. {escape(best[2][:200])}[/yellow]")
        return best[1]
    raise PipelineError(
        f"No se pudo generar el storyboard ({label}) tras {STORYBOARD_MAX_ATTEMPTS} intentos. Último error: {str(last_error)[:500]}"
    )


def _balanced_chunks(scene_ids: list[int], block_size: int) -> list[list[int]]:
    """Reparte los ids en bloques de tamaño parecido y no mayor que `block_size` (100 escenas -> 34 + 33 + 33)."""
    n_blocks = -(-len(scene_ids) // block_size)
    size = -(-len(scene_ids) // n_blocks)
    return [scene_ids[i : i + size] for i in range(0, len(scene_ids), size)]


def storyboard_with_llm(
    source: ImportedScript,
    profile: VideoProfile,
    style: VisualStyle,
    video_format: VideoFormat,
    protagonist: Optional[Character],
    stats: Optional[RunStats],
) -> VideoProjectSchema:
    """Storyboard del texto importado con openrouter/router (gemini-2.5-flash): una sola llamada hasta
    STORYBOARD_BLOCK_SCENES escenas; con más, una llamada para lo global (título, publicación, personajes secundarios) y
    luego bloques de escenas que reciben los mismos personajes y las últimas escenas ya resueltas (continuidad).

    La narración de cada escena sale SIEMPRE de `source.scenes`, nunca de la respuesta del LLM."""
    if FAL_KEY:
        os.environ["FAL_KEY"] = FAL_KEY
    if not os.getenv("FAL_KEY"):
        raise PipelineError(
            "No se encontró FAL_KEY. Define FAL_KEY en el archivo .env (ver .env.example) "
            "o usa --mock-script para un storyboard de demostración local."
        )
    narrations = {i: text for i, text in enumerate(source.scenes, start=1)}
    scene_ids = list(narrations)
    full_text = source.text
    known_base = {PROTAGONIST_ID} if protagonist is not None else set()
    cost_before = stats.llm_cost_usd if stats is not None else 0.0

    visuals: dict[int, _SceneVisual] = {}
    if len(scene_ids) <= STORYBOARD_BLOCK_SCENES:
        console.print(f"[cyan]Storyboard con Fal.ai ({LLM_ENDPOINT} / {LLM_MODEL}): {len(scene_ids)} escenas en una llamada...[/cyan]")

        def parse_full(data: dict[str, Any]) -> tuple[_StoryboardMeta, dict[int, _SceneVisual]]:
            meta = _parse_meta(data, protagonist)
            return meta, _parse_scene_entries(data, scene_ids, known_base | set(meta.characters), protagonist)

        meta, visuals = _call_storyboard_llm(
            build_storyboard_system_prompt(profile, style, video_format, protagonist, "full"),
            STORYBOARD_USER_FULL_TEMPLATE.format(
                full_text=full_text,
                scene_lines=_scene_lines(scene_ids, narrations),
                protagonist_quota=protagonist_quota_text(protagonist, len(scene_ids)),
                n_scenes=len(scene_ids),
                first_id=scene_ids[0],
                last_id=scene_ids[-1],
            ),
            _llm_max_tokens(len(scene_ids)),
            parse_full,
            stats,
            "escenas 1-%d" % len(scene_ids),
            check=lambda result: _protagonist_share_check(result[1], protagonist),
        )
    else:
        chunks = _balanced_chunks(scene_ids, STORYBOARD_BLOCK_SCENES)
        console.print(
            f"[cyan]Storyboard con Fal.ai ({LLM_ENDPOINT} / {LLM_MODEL}): {len(scene_ids)} escenas, "
            f"1 llamada global + {len(chunks)} bloques de ~{len(chunks[0])} escenas...[/cyan]"
        )
        meta = _call_storyboard_llm(
            build_storyboard_system_prompt(profile, style, video_format, protagonist, "meta"),
            STORYBOARD_USER_META_TEMPLATE.format(full_text=full_text),
            _llm_max_tokens(0),
            lambda data: _parse_meta(data, protagonist),
            stats,
            "global",
        )
        scenes_system = build_storyboard_system_prompt(profile, style, video_format, protagonist, "scenes")
        known = known_base | set(meta.characters)
        characters_json = json.dumps(
            ({PROTAGONIST_ID: f"{protagonist.name} (protagonista fijo)"} if protagonist is not None else {}) | meta.characters,
            indent=2,
            ensure_ascii=False,
        )
        for chunk in chunks:
            previous_ids = [i for i in sorted(visuals) if i < chunk[0]][-STORYBOARD_CONTEXT_SCENES:]
            previous_block = (
                STORYBOARD_PREVIOUS_BLOCK_TEMPLATE.format(
                    previous_lines="\n".join(f"{i}: {visuals[i].visual_prompt_en}" for i in previous_ids)
                )
                if previous_ids
                else ""
            )
            visuals.update(
                _call_storyboard_llm(
                    scenes_system,
                    STORYBOARD_USER_SCENES_TEMPLATE.format(
                        full_text=full_text,
                        characters_json=characters_json,
                        previous_block=previous_block,
                        scene_lines=_scene_lines(chunk, narrations),
                        protagonist_quota=protagonist_quota_text(protagonist, len(chunk)),
                        n_scenes=len(chunk),
                        first_id=chunk[0],
                        last_id=chunk[-1],
                    ),
                    _llm_max_tokens(len(chunk)),
                    lambda data, chunk=chunk: _parse_scene_entries(data, chunk, known, protagonist),
                    stats,
                    f"escenas {chunk[0]}-{chunk[-1]}",
                    check=lambda result: _protagonist_share_check(result, protagonist),
                )
            )
    if stats is not None and stats.llm_cost_usd - cost_before > 0:
        console.print(f"  [dim]Costo del LLM (storyboard): ${stats.llm_cost_usd - cost_before:.4f}[/dim]")

    return VideoProjectSchema(
        title=meta.title,
        hook_text=meta.hook_text,
        post_description=meta.post_description,
        hashtags=meta.hashtags,
        characters=meta.characters,
        scenes=[
            SceneSchema(
                scene_id=i,
                narration_es=narrations[i],
                visual_prompt_en=visuals[i].visual_prompt_en,
                characters_in_scene=visuals[i].characters_in_scene,
                camera_effect=visuals[i].camera_effect,
                highlight_words=visuals[i].highlight_words,
            )
            for i in scene_ids
        ],
    )


# --------------------------------------------------------------------------- #
# 5. Storyboard de demostración (--mock-script), determinista y gratis
# --------------------------------------------------------------------------- #

# Planos de demostración: con el protagonista (se usan en las escenas donde aparece su nombre) y de ambiente (el resto)
_MOCK_SHOTS_PROTAGONIST = [
    ("Wide shot of the main character standing alone in a dim empty street at dusk, long shadows and a distant skyline", "zoom_in_slow"),
    ("Medium tracking shot of the main character walking quickly through a dark corridor lit by a weak flashlight beam, worried expression", "pan_right"),
    ("Close-up of the main character's hands holding a small notebook and a pen under a warm desk lamp, focused look", "zoom_in_fast"),
    ("Low angle shot of the main character looking up at a tall abandoned building against a stormy sky", "zoom_in_slow"),
    ("Over-the-shoulder shot of the main character facing a closed metal door with a thin line of light underneath", "zoom_in_fast"),
    ("Wide aerial view of the main character crossing an empty plaza at sunrise, golden light spreading over the rooftops", "zoom_out"),
    ("Medium shot of the main character sitting on the floor of a small apartment, back against the wall, tired but determined", "pan_left"),
    ("Close-up of the main character's face lit by the blue glow of a phone, eyes wide with surprise", "zoom_in_fast"),
]
_MOCK_SHOTS_SCENERY = [
    ("Wide establishing shot of a quiet city street at night with every window dark and a few stalled cars", "zoom_out"),
    ("Aerial wide shot of a river crossing a sleepy town at dawn, thin mist over the water and tiled rooftops", "pan_right"),
    ("Detail shot of a rusty padlock on a wooden door, shallow depth of field, warm side light", "zoom_in_fast"),
    ("Low angle shot of tall apartment blocks against a heavy overcast sky, a single lit window high above", "zoom_in_slow"),
    ("Wide shot of an empty supermarket aisle with scattered cans and a flickering emergency light", "pan_left"),
    ("Slow pan across a wall of old photographs and handwritten notes pinned under a bare bulb", "pan_right"),
]


def mock_storyboard(
    source: ImportedScript, topic: str, protagonist: Optional[Character]
) -> VideoProjectSchema:
    """Storyboard de demostración: prompts visuales de una lista fija (ni el texto ni el tema se respetan en la imagen) y el
    protagonista etiquetado en las escenas donde aparece su nombre. La narración es la del autor, como en el modo real."""
    console.print(
        "[yellow]⚠️  Modo --mock-script: storyboard de demostración local (los prompts visuales no corresponden al texto; "
        "la narración sí es la del archivo).[/yellow]"
    )
    scenes: list[SceneSchema] = []
    for i, narration in enumerate(source.scenes, start=1):
        shows_protagonist = protagonist is not None and mentions_name(narration, protagonist.name)
        bank = _MOCK_SHOTS_PROTAGONIST if shows_protagonist else _MOCK_SHOTS_SCENERY
        visual, camera = bank[(i - 1) % len(bank)]
        keywords = sorted(
            {w: None for w in (re.sub(r"[^\w]", "", t) for t in narration.split()) if len(w) >= 5}, key=lambda w: (-len(w), narration.find(w))
        )[:2]
        scenes.append(
            SceneSchema(
                scene_id=i,
                narration_es=narration,
                visual_prompt_en=visual,
                characters_in_scene=[PROTAGONIST_ID] if shows_protagonist else [],
                camera_effect=camera,
                highlight_words=keywords,
            )
        )
    title = " ".join(re.sub(r"[_-]+", " ", topic).split())[:60].strip() or "Guion importado"
    first_words = [w for w in (re.sub(r"[^\w¿¡?!]", "", t) for t in source.scenes[0].split()) if w][:4]
    return VideoProjectSchema(
        title=title,
        hook_text=" ".join(first_words),
        post_description="Un video narrado con un guion propio. ¿Tú qué habrías hecho?",
        hashtags=["guionpropio", "video", "historia", "narracion", "ficcion", "curiosidades"],
        characters={},
        scenes=scenes,
    )


# --------------------------------------------------------------------------- #
# 6. Normalización y caché del guion importado
# --------------------------------------------------------------------------- #

MAIN_CHARACTER_PHRASE = "the main character"
# "the main character" repetido pegado a sí mismo: aposición con comas, paréntesis o raya ("the main character, the main
# character,"), "the main character named the main character" o simplemente dos veces seguidas. Se queda con la primera.
_MAIN_CHARACTER_REPEAT_RE = re.compile(
    r"\b(the main character)(?:\s*[,(—–-]\s*|\s+(?:named|called)\s+|\s+)the main character\b(?:\s*[,)—–-])?", re.IGNORECASE
)


def _scrub_protagonist_name(project: VideoProjectSchema, protagonist: Character) -> None:
    """Los prompts visuales llaman al protagonista "the main character": si el LLM puso su nombre, se sustituye.

    El LLM suele escribir una aposición ("The main character, Miguel, is..." o "Miguel, the main character, is..."): al
    sustituir el nombre quedaba "the main character, the main character,", así que después se funde la repetición en una
    sola mención. La sustitución respeta la mayúscula al inicio de la frase ("Miguel looks" -> "The main character looks")."""
    pattern = re.compile(rf"(?<!\w){re.escape(protagonist.name.strip())}(?!\w)", re.IGNORECASE)

    def substitute(m: "re.Match[str]") -> str:
        before = m.string[: m.start()].rstrip()
        return MAIN_CHARACTER_PHRASE.capitalize() if not before or before[-1] in ".!?…" else MAIN_CHARACTER_PHRASE

    for scene in project.scenes:
        text = pattern.sub(substitute, scene.visual_prompt_en)
        scene.visual_prompt_en = _MAIN_CHARACTER_REPEAT_RE.sub(r"\1", text)


def normalize_imported_project(
    project: VideoProjectSchema,
    source: ImportedScript,
    profile: VideoProfile,
    style_id: str = "",
    format_id: str = "",
    protagonist: Optional[Character] = None,
) -> VideoProjectSchema:
    """Normaliza (en el lugar) el storyboard de un guion importado.

    Fija estilo y formato resueltos, sincroniza el protagonista (characters[PROTAGONIST_ID], protagonist_slug) y aplica la
    normalización ligera (hashtags, textos de publicación, scene_id 1..N) SIN la frase de cierre del perfil. No exige
    cuota de escenas con el protagonista (solo avisa si son pocas), no valida presupuestos de palabras ni el nº de
    escenas, y NO toca la narración (se verifica)."""
    style = get_style(style_id or profile.default_style_id)
    project.visual_style_id = style.id
    project.global_visual_style = style.image_suffix  # informativo; la composición usa visual_style_id
    project.video_format = get_format(format_id or profile.default_format).id

    if protagonist is not None:
        sync_protagonist(project, protagonist)
        canonicalize_protagonist_ids(project, protagonist)
        _scrub_protagonist_name(project, protagonist)
        ensure_no_people_without_protagonist(project)
        n_with = sum(1 for sc in project.scenes if PROTAGONIST_ID in sc.characters_in_scene)
        if project.scenes and n_with * 100 < len(project.scenes) * PROTAGONIST_MIN_PERCENT:
            console.print(
                f"[yellow]Nota: el protagonista '{escape(protagonist.name)}' sale en {n_with} de {len(project.scenes)} escenas "
                f"(menos del {PROTAGONIST_MIN_PERCENT} % que pide el formato). Si quieres que salga más, añade "
                f"\"{PROTAGONIST_ID}\" a 'characters_in_scene' de esas escenas en script.json (y su prompt visual debe llamarlo "
                "\"the main character\").[/yellow]"
            )
    else:
        project.protagonist_slug = ""

    light_normalize_project(project, profile, ensure_cta=False)  # la frase de cierre del perfil NO se añade: la narración es del autor
    filter_highlight_words(project)
    if [sc.narration_es for sc in project.scenes] != source.scenes:
        raise PipelineError("Error interno: el storyboard alteró la narración del texto importado.")
    return project


def write_source_script(source: ImportedScript, output_dir: Path) -> Path:
    """Copia el texto limpio al workspace (source_script.txt); es la referencia de lo que se importó."""
    path = output_dir / SOURCE_SCRIPT_FILENAME
    path.write_text(source.canonical_text, encoding="utf-8")
    return path


def import_or_load_script(
    topic: str,
    profile: VideoProfile,
    output_dir: Path,
    manifest: CacheManifest,
    source: Optional[ImportedScript] = None,
    force: bool = False,
    mock_script: bool = False,
    stats: Optional[RunStats] = None,
    style_id: str = "",
    format_id: str = "",
    protagonist: Optional[Character] = None,
) -> VideoProjectSchema:
    """script.json de un proyecto importado: lo carga de la caché (respetando ediciones manuales) o lo genera con el storyboard.

    Con `source` (generate --script-file): huella = texto limpio + perfil + protagonista + escena-segundos + versión del
    storyboard (ver import_fingerprint). Misma huella -> caché, sin llamar al LLM; texto distinto -> se rehace el storyboard
    (con aviso: los prompts nuevos regeneran sus imágenes). Sin `source` (retry-scene: el .txt original no hace falta)
    solo carga script.json y NUNCA llama al LLM. Nunca añade la frase de cierre del perfil ni toca la narración."""
    script_path = output_dir / "script.json"

    if source is None:
        if not script_path.is_file():
            raise PipelineError(f"El proyecto importado no tiene script.json ({script_path}). Ejecuta 'generate --script-file' de nuevo.")
        try:
            project = load_cached_project(script_path, profile, protagonist, style_id, format_id, ensure_cta=False)
        except PipelineError:
            raise
        except Exception as e:
            raise PipelineError(f"script.json no es válido ({e}). Corrígelo o ejecuta 'generate --script-file' de nuevo.") from e
        console.print(f"[green]✓ Guion importado en caché:[/] {script_path} ({len(project.scenes)} escenas)")
        return project

    fp = import_fingerprint(source, profile.name, protagonist.slug if protagonist is not None else "", mock_script)
    if script_path.exists() and not force:
        recorded = manifest.get(script_path)
        if recorded == fp:
            try:
                project = load_cached_project(script_path, profile, protagonist, style_id, format_id, ensure_cta=False)
                console.print(f"[green]✓ Guion importado en caché:[/] {script_path} ({len(project.scenes)} escenas; sin llamar al LLM)")
                return project
            except PipelineError:
                raise  # error de formato/estilo del guion en caché: no se rehace (costaría dinero), se informa
            except Exception as e:
                console.print(f"[yellow]Aviso: script.json existente no es válido ({escape(str(e))}). Rehaciendo el storyboard...[/yellow]")
        else:
            console.print(
                "[yellow]⚠ El texto importado (o el perfil, el protagonista, --scene-seconds o el modo) cambió respecto al del "
                "proyecto: se rehace el storyboard. Los 'visual_prompt_en' nuevos o distintos regeneran sus imágenes (con un "
                "storyboard nuevo, lo normal es que sean casi todas); el panel de estimación muestra el costo antes de pagar.[/yellow]"
            )

    style = get_style(style_id or profile.default_style_id)
    video_format = get_format(format_id or profile.default_format)
    if mock_script:
        project = mock_storyboard(source, topic, protagonist)
    else:
        project = storyboard_with_llm(source, profile, style, video_format, protagonist, stats)
    normalize_imported_project(project, source, profile, style.id, video_format.id, protagonist)

    _write_script(project, script_path)
    manifest.record(script_path, fp)
    write_source_script(source, output_dir)
    if stats is not None:
        stats.script = True
    console.print(f"[green]✓ Storyboard listo y validado ({len(project.scenes)} escenas; narración intacta).[/green]")
    return project


# --------------------------------------------------------------------------- #
# 7. Estimación de costo antes de pagar imágenes
# --------------------------------------------------------------------------- #

@dataclass
class ImportEstimate:
    words: int
    wps: float  # palabras por segundo efectivas de la voz
    seconds: float  # duración estimada = palabras / wps
    scenes: int
    scene_seconds: float
    images_total: int
    images_cached: int  # ya pagadas y vigentes en el workspace (no se vuelven a pagar)
    paid_by_endpoint: dict[str, int]  # endpoint -> imágenes por pagar
    llm_cost_usd: float = 0.0  # storyboard de esta ejecución (ya gastado)

    @property
    def images_to_pay(self) -> int:
        return sum(self.paid_by_endpoint.values())

    @property
    def images_cost_usd(self) -> float:
        return sum(n * IMAGE_COST_USD.get(endpoint, 0.0) for endpoint, n in self.paid_by_endpoint.items())


def estimate_import(
    project: VideoProjectSchema,
    profile: VideoProfile,
    output_dir: Path,
    manifest: CacheManifest,
    protagonist: Optional[Character],
    scene_seconds: float,
    stats: Optional[RunStats] = None,
    force_images: bool = False,
) -> ImportEstimate:
    """Cuántas imágenes habría que pagar (solo lectura): las que no tienen en el workspace una imagen real vigente con la
    huella actual (misma regla que generate_all_images, sin las migraciones de workspaces antiguos). Con `force_images`
    (--force / --force-images) se rehacen todas, así que ninguna cuenta como ya pagada."""
    video_format = get_format(project.video_format)
    ref_sha256 = file_sha256(protagonist.ref_image) if protagonist is not None else None
    cached = 0
    paid: dict[str, int] = {}
    for scene in project.scenes:
        plan = plan_scene_image(scene, project, video_format, protagonist, ref_sha256)
        image_path = output_dir / "images" / f"scene_{scene.scene_id:02d}.png"
        is_cached = not force_images and (
            image_path.exists()
            and image_path.stat().st_size > MIN_IMAGE_BYTES
            and classify_recorded_plan(manifest.get(image_path), plan, video_format) is not None
        )
        if is_cached:
            cached += 1
        else:
            paid[plan.endpoint] = paid.get(plan.endpoint, 0) + 1
    words = sum(_count_words(t) for sc in project.scenes for t in sc.narration_es.split())
    wps = effective_words_per_sec(profile)
    return ImportEstimate(
        words=words,
        wps=wps,
        seconds=words / wps,
        scenes=len(project.scenes),
        scene_seconds=scene_seconds,
        images_total=len(project.scenes),
        images_cached=cached,
        paid_by_endpoint=paid,
        llm_cost_usd=stats.llm_cost_usd if stats is not None else 0.0,
    )


_ENDPOINT_LABELS = {
    FLUX_ENDPOINT: "Flux schnell",
    SEEDREAM_EDIT_ENDPOINT: "Seedream v4.5 con referencia",
    SEEDREAM_T2I_ENDPOINT: "Seedream v4.5 texto a imagen",
}


def _fmt_duration(seconds: float) -> str:
    minutes, secs = divmod(round(seconds), 60)
    return f"{minutes} min {secs:02d} s" if minutes else f"{secs} s"


def render_estimate(est: ImportEstimate, mock_images: bool) -> Panel:
    """Panel con lo que se va a generar y lo que costará (las imágenes ya en caché y el modo mock no cuestan)."""
    table = Table.grid(padding=(0, 2))
    table.add_column(style="cyan", no_wrap=True)
    table.add_column(style="white")
    table.add_row("Palabras", f"{est.words}")
    table.add_row("Duración estimada", f"~{_fmt_duration(est.seconds)} ({est.words} palabras / {est.wps:.2f} palabras por segundo)")
    table.add_row("Escenas", f"{est.scenes} (una imagen nueva cada ~{est.scene_seconds:g} s)")
    if mock_images:
        table.add_row("Imágenes", f"{est.images_total} (modo --mock-images: tarjetas locales, sin costo)")
        table.add_row("Total a pagar", "$0.00")
    else:
        table.add_row("Imágenes por pagar", f"{est.images_to_pay} de {est.images_total}" + (f" ({est.images_cached} ya en caché)" if est.images_cached else ""))
        if est.paid_by_endpoint:
            prices = {IMAGE_COST_USD.get(endpoint, 0.0) for endpoint in est.paid_by_endpoint}
            labels = [_ENDPOINT_LABELS.get(endpoint, endpoint) for endpoint in est.paid_by_endpoint]
            if len(prices) == 1:  # p. ej. Seedream edit y texto a imagen cuestan lo mismo
                detail = f"${prices.pop():g} ({' + '.join(labels)})"
            else:
                detail = ", ".join(f"${IMAGE_COST_USD.get(ep, 0.0):g} ({_ENDPOINT_LABELS.get(ep, ep)})" for ep in est.paid_by_endpoint)
            table.add_row("Costo por imagen", detail)
        table.add_row("Total a pagar", f"[bold]${est.images_cost_usd:.2f}[/bold]")
    if est.llm_cost_usd:
        table.add_row("Storyboard (LLM)", f"${est.llm_cost_usd:.4f} (ya gastado en esta ejecución)")
    return Panel(table, title="[bold]Estimación del video importado[/bold]", border_style="yellow" if est.images_to_pay and not mock_images else "cyan")
