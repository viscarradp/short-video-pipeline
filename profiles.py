"""Profiles configuration module for Short Video Pipeline."""

import re
from dataclasses import dataclass
from typing import Optional

from config import WORDS_PER_SEC_BASE
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


PROFILES: dict[str, VideoProfile] = {
    "curiosity": VideoProfile(
        name="curiosity",
        description="Datos curiosos, ciencia, mecánica, cuerpo humano y cómo funcionan las cosas.",
        target_scenes_min=15,
        target_scenes_max=18,
        target_scene_duration_sec=3.5,
        default_voice="es-MX-JorgeNeural",
        voice_rate="+22%",
        default_style_id="explainer_3d",
        require_characters=False,
        ending_call_to_action=None,
    ),
    "micronovela": VideoProfile(
        name="micronovela",
        description="Historias dramáticas, secretos familiares, chismes, infidelidades y suspenso.",
        target_scenes_min=10,
        target_scenes_max=12,
        target_scene_duration_sec=5.0,
        default_voice="es-MX-DaliaNeural",
        voice_rate="+8%",
        default_style_id="telenovela_cine",
        require_characters=True,
        ending_call_to_action="Parte 2 en el perfil",
    ),
}


def _validate_profiles() -> None:
    """Sanity check al importar: cada perfil debe tener instrucciones del guionista y un estilo por defecto válido."""
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


_validate_profiles()


def parse_voice_rate(rate: str) -> float:
    """'+22%' -> 0.22, '-10%' -> -0.10, '+0%' -> 0.0. Lanza ValueError si el formato no es el de edge-tts."""
    m = re.fullmatch(r"\s*([+-]?\d+(?:\.\d+)?)\s*%\s*", rate or "")
    if not m:
        raise ValueError(f"voice_rate inválido: {rate!r} (se esperaba algo como '+22%')")
    return float(m.group(1)) / 100.0


def narration_word_budget(profile: VideoProfile) -> int:
    """Palabras objetivo por escena: duración objetivo x palabras/seg base x (1 + velocidad de la voz), redondeado."""
    words = profile.target_scene_duration_sec * WORDS_PER_SEC_BASE * (1 + parse_voice_rate(profile.voice_rate))
    return max(1, round(words))


def get_profile(name: str) -> VideoProfile:
    """Retrieve profile by name, defaulting to curiosity if invalid."""
    lowered = name.lower().strip()
    if lowered not in PROFILES:
        raise ValueError(
            f"Perfil desconocido '{name}'. Perfiles disponibles: {list(PROFILES.keys())}"
        )
    return PROFILES[lowered]
