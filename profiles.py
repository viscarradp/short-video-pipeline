"""Profiles configuration module for Short Video Pipeline."""

import math
import re
from dataclasses import dataclass, replace
from typing import Optional

from config import FORMATS, SCENE_WORDS_TOLERANCE, WORDS_PER_SEC_BASE
from prompt_templates import PROFILE_INSTRUCTIONS
from styles import STYLES


@dataclass
class VideoProfile:
    name: str
    description: str
    target_scenes_min: int
    target_scenes_max: int
    target_scene_duration_sec: float
    default_voice: str
    voice_rate: str
    default_style_id: str  # estilo visual (styles.STYLES) que usan los guiones NUEVOS de este perfil
    require_characters: bool
    ending_call_to_action: Optional[str]
    default_format: str = "vertical"  # formato de video (config.FORMATS) que usan los videos NUEVOS de este perfil
    uses_protagonist: bool = False  # el perfil se hace con un protagonista recurrente de la biblioteca de personajes (--character)
    # Palabras/seg EFECTIVAS medidas con esta voz y velocidad (incluye los ~0.6 s de silencio final de edge-tts).
    # None = estimación WORDS_PER_SEC_BASE x (1 + voice_rate), que sobrestima la velocidad real de las voces es-MX.
    words_per_sec: Optional[float] = None
    # Shorts verticales de 30-35 s: validación ESTRICTA del guion del LLM (nº de escenas, palabras por escena y duración total).
    strict_scenes: bool = False
    # (mínimo, objetivo, máximo) de palabras por escena; None = objetivo x (1 +- SCENE_WORDS_TOLERANCE) (ver scene_word_limits)
    scene_words: Optional[tuple[int, int, int]] = None
    # (mín, máx) segundos de audio total que debe tener el guion (se estima con words_per_sec); None = no se valida
    total_sec_range: Optional[tuple[float, float]] = None


# Shorts verticales (curiosity, micronovela) a "+12%". MEDIDO con edge-tts real (es-MX-JorgeNeural y es-MX-DaliaNeural; el
# audio de cada escena incluye ~0.7 s de silencio final): con frases cotidianas salen ~2.9 palabras/seg, con texto denso de
# datos 2.75, y en shorts REALES de salud (cortisol, cafeína, términos largos) 93 palabras = 37.3 s (2.50/s) y 84 palabras =
# 34.5 s (2.43/s). Se fija 2.4 (el caso lento real): el total queda en 68-81 palabras (~28-33.5 s); 8 x 10 = 80 palabras.
SHORT_WORDS_PER_SEC = 2.4
SHORT_SCENE_WORDS = (9, 10, 11)  # (mín, objetivo, máx) palabras por escena; el máximo es regla dura (ver validate_strict_scenes)
SHORT_TOTAL_SEC = (28.0, 34.0)  # audio total estimado del short (regla dura: 28-35 s; el tope 34 deja 1 s de margen de error)

# Perfiles horizontales a "+12%": medido con ambas voces con frases de ~15 a 17 palabras: Jorge 2.95 y Dalia 2.99 palabras/seg
# (con frases de ~19 palabras suben a ~3.1); 15 palabras = ~5 s por escena.
LONG_WORDS_PER_SEC = 2.97

PROFILES: dict[str, VideoProfile] = {
    "curiosity": VideoProfile(
        name="curiosity",
        description="Datos curiosos, ciencia, mecánica, cuerpo humano y cómo funcionan las cosas.",
        target_scenes_min=7,
        target_scenes_max=9,  # objetivo 8 escenas de ~4 s = short de 30-35 s
        target_scene_duration_sec=4.0,
        default_voice="es-MX-JorgeNeural",
        voice_rate="+12%",
        words_per_sec=SHORT_WORDS_PER_SEC,
        default_style_id="explainer_3d",
        require_characters=False,
        ending_call_to_action=None,
        strict_scenes=True,
        scene_words=SHORT_SCENE_WORDS,
        total_sec_range=SHORT_TOTAL_SEC,
    ),
    "micronovela": VideoProfile(
        name="micronovela",
        description="Historias dramáticas, secretos familiares, chismes, infidelidades y suspenso.",
        target_scenes_min=7,
        target_scenes_max=9,  # objetivo 8 escenas de ~4 s = short de 30-35 s
        target_scene_duration_sec=4.0,
        default_voice="es-MX-DaliaNeural",
        voice_rate="+12%",
        words_per_sec=SHORT_WORDS_PER_SEC,
        default_style_id="telenovela_cine",
        require_characters=True,
        ending_call_to_action="Parte 2 en el perfil",  # cuenta dentro del máximo de palabras de la última escena
        strict_scenes=True,
        scene_words=SHORT_SCENE_WORDS,
        total_sec_range=SHORT_TOTAL_SEC,
    ),
    # Perfiles de YouTube horizontal con protagonista recurrente: ~2 minutos por defecto (24 escenas de 5 s); --minutes los alarga o acorta.
    "que_pasaria": VideoProfile(
        name="que_pasaria",
        description="Escenarios hipotéticos '¿Qué pasaría si...?' (apocalipsis, mundos alterados) con un protagonista recurrente.",
        target_scenes_min=22,
        target_scenes_max=26,
        target_scene_duration_sec=5.0,
        default_voice="es-MX-JorgeNeural",
        voice_rate="+12%",
        words_per_sec=LONG_WORDS_PER_SEC,
        default_style_id="cine_realista",
        require_characters=True,
        ending_call_to_action="Suscríbete para el siguiente escenario",
        default_format="horizontal",
        uses_protagonist=True,
    ),
    "historia": VideoProfile(
        name="historia",
        description="Historias de aventura narradas, protagonizadas por un personaje recurrente.",
        target_scenes_min=22,
        target_scenes_max=26,
        target_scene_duration_sec=5.0,
        default_voice="es-MX-JorgeNeural",
        voice_rate="+12%",
        words_per_sec=LONG_WORDS_PER_SEC,
        default_style_id="cine_realista",
        require_characters=True,
        ending_call_to_action="Suscríbete para la siguiente aventura",
        default_format="horizontal",
        uses_protagonist=True,
    ),
    "chisme": VideoProfile(
        name="chisme",
        description="Chismes narrados con un protagonista recurrente y personajes secundarios; admite escenarios hipotéticos.",
        target_scenes_min=22,
        target_scenes_max=26,
        target_scene_duration_sec=5.0,
        default_voice="es-MX-DaliaNeural",
        voice_rate="+12%",
        words_per_sec=LONG_WORDS_PER_SEC,
        default_style_id="telenovela_cine",  # fotograma de drama; con escenarios fantásticos usa --style cine_realista
        require_characters=True,  # al menos un personaje secundario (lo exige PROFILE_INSTRUCTIONS['chisme'])
        ending_call_to_action="Suscríbete para más chisme",
        default_format="horizontal",
        uses_protagonist=True,
    ),
}


def _validate_profiles() -> None:
    """Sanity check al importar: cada perfil debe tener instrucciones del guionista, un estilo y un formato por defecto válidos."""
    for name, prof in PROFILES.items():
        if name != prof.name:
            raise AssertionError(f"PROFILES['{name}'] tiene name='{prof.name}'")
        if not PROFILE_INSTRUCTIONS.get(name, "").strip():
            raise AssertionError(f"El perfil '{name}' no tiene instrucciones en prompt_templates.PROFILE_INSTRUCTIONS")
        if prof.default_style_id not in STYLES:
            raise AssertionError(
                f"El perfil '{name}' usa default_style_id='{prof.default_style_id}', que no existe en styles.STYLES "
                f"({', '.join(STYLES)})"
            )
        if prof.default_format not in FORMATS:
            raise AssertionError(
                f"El perfil '{name}' usa default_format='{prof.default_format}', que no existe en config.FORMATS "
                f"({', '.join(FORMATS)})"
            )


_validate_profiles()


def parse_voice_rate(rate: str) -> float:
    """'+22%' -> 0.22, '-10%' -> -0.10, '+0%' -> 0.0. Lanza ValueError si el formato no es el de edge-tts."""
    m = re.fullmatch(r"\s*([+-]?\d+(?:\.\d+)?)\s*%\s*", rate or "")
    if not m:
        raise ValueError(f"voice_rate inválido: {rate!r} (se esperaba algo como '+22%')")
    return float(m.group(1)) / 100.0


def effective_words_per_sec(profile: VideoProfile) -> float:
    """Palabras por segundo efectivas de la voz del perfil: las medidas (words_per_sec) o, si no hay, la base x (1 + velocidad)."""
    return profile.words_per_sec or WORDS_PER_SEC_BASE * (1 + parse_voice_rate(profile.voice_rate))


def narration_word_budget(profile: VideoProfile) -> int:
    """Palabras objetivo por escena: duración objetivo x palabras/seg efectivas (ver effective_words_per_sec), redondeado."""
    words = profile.target_scene_duration_sec * effective_words_per_sec(profile)
    return max(1, round(words))


def scene_word_limits(profile: VideoProfile) -> tuple[int, int, int]:
    """(mínimo, objetivo, máximo) de palabras por escena: los fijos del perfil (scene_words) o el presupuesto +- SCENE_WORDS_TOLERANCE."""
    if profile.scene_words is not None:
        return profile.scene_words
    budget = narration_word_budget(profile)
    return max(budget - SCENE_WORDS_TOLERANCE, 1), budget, budget + SCENE_WORDS_TOLERANCE


def total_word_limits(profile: VideoProfile) -> Optional[tuple[int, int]]:
    """(mín, máx) de palabras de TODO el guion para que el audio dure total_sec_range; None si el perfil no lo valida."""
    if profile.total_sec_range is None:
        return None
    wps = effective_words_per_sec(profile)
    return math.ceil(profile.total_sec_range[0] * wps), math.floor(profile.total_sec_range[1] * wps)


# Duración objetivo (--minutes): rango admitido y holgura del nº de escenas alrededor del objetivo
MIN_TARGET_MINUTES = 0.5
MAX_TARGET_MINUTES = 15.0
SCENES_TOLERANCE_FRAC = 0.10


def clamp_target_minutes(minutes: float) -> float:
    """Acota la duración objetivo a [MIN_TARGET_MINUTES, MAX_TARGET_MINUTES] (2 decimales). Lanza ValueError si no es un número."""
    if not math.isfinite(float(minutes)):
        raise ValueError(f"Duración objetivo inválida: {minutes!r}")
    return round(min(max(float(minutes), MIN_TARGET_MINUTES), MAX_TARGET_MINUTES), 2)


def profile_for_minutes(profile: VideoProfile, minutes: float) -> VideoProfile:
    """Copia del perfil con el rango de escenas para durar ~`minutes` (ya acotado con clamp_target_minutes).

    Objetivo = round(minutos x 60 / duración de escena); el rango es +-10 %. No modifica el perfil original."""
    n = round(minutes * 60 / profile.target_scene_duration_sec)
    scenes_min = max(round(n * (1 - SCENES_TOLERANCE_FRAC)), 1)
    scenes_max = max(round(n * (1 + SCENES_TOLERANCE_FRAC)), scenes_min)
    return replace(profile, target_scenes_min=scenes_min, target_scenes_max=scenes_max, total_sec_range=None)


MIN_FORCED_SCENES = 1
MAX_FORCED_SCENES = 300


def profile_for_scenes(profile: VideoProfile, scenes: int) -> VideoProfile:
    """Copia del perfil con EXACTAMENTE `scenes` escenas (--scenes: pruebas baratas). Como profile_for_minutes, no modifica el
    original; la duración total deja de validarse (depende del nº de escenas) y el máximo de palabras por escena sigue vigente."""
    if not (MIN_FORCED_SCENES <= int(scenes) <= MAX_FORCED_SCENES):
        raise ValueError(f"--scenes debe estar entre {MIN_FORCED_SCENES} y {MAX_FORCED_SCENES} (recibido: {scenes!r})")
    return replace(profile, target_scenes_min=int(scenes), target_scenes_max=int(scenes), total_sec_range=None)


def get_profile(name: str) -> VideoProfile:
    """Retrieve profile by name, defaulting to curiosity if invalid."""
    lowered = name.lower().strip()
    if lowered not in PROFILES:
        raise ValueError(
            f"Perfil desconocido '{name}'. Perfiles disponibles: {list(PROFILES.keys())}"
        )
    return PROFILES[lowered]
