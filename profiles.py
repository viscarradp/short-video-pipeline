"""Profiles configuration module for Short Video Pipeline."""

from dataclasses import dataclass
from typing import Optional


@dataclass
class VideoProfile:
    name: str
    description: str
    target_scenes_min: int
    target_scenes_max: int
    target_scene_duration_sec: float
    default_voice: str
    voice_rate: str
    global_visual_style: str
    require_characters: bool
    ending_call_to_action: Optional[str]
    system_prompt_instructions: str


PROFILES: dict[str, VideoProfile] = {
    "curiosity": VideoProfile(
        name="curiosity",
        description="Datos curiosos, ciencia, mecánica, cuerpo humano y cómo funcionan las cosas.",
        target_scenes_min=15,
        target_scenes_max=18,
        target_scene_duration_sec=3.5,
        default_voice="es-MX-JorgeNeural",
        voice_rate="+22%",
        global_visual_style="3D isometric cutaway educational render, hyper-detailed, studio lighting, vivid colors, Zack D. Films style, octane render, 8k",
        require_characters=False,
        ending_call_to_action=None,
        system_prompt_instructions="""
- Ritmo vertiginoso y adictivo: exactamente entre 15 y 18 escenas cortas.
- Escena 1: Gancho extremo e intrigante de menos de 3 segundos (ej: "¿Sabías qué ocurre exactamente cuando...?").
- Locución enérgica, datos reveladores paso a paso, vocabulario dinámico en español neutro.
- Prompts visuales en inglés enfocados en cortes transversales, rayos X, zoom anatómico o mecánico en 3D.
- No se requiere consistencia de personajes (dejar characters vacío).
""".strip(),
    ),
    "micronovela": VideoProfile(
        name="micronovela",
        description="Historias dramáticas, secretos familiares, chismes, infidelidades y suspenso.",
        target_scenes_min=10,
        target_scenes_max=12,
        target_scene_duration_sec=5.0,
        default_voice="es-MX-DaliaNeural",
        voice_rate="+8%",
        global_visual_style="Cinematic soap opera photorealistic, dramatic lighting, intense emotional atmosphere, shallow depth of field, 8k, photorealistic film still",
        require_characters=True,
        ending_call_to_action="Parte 2 en el perfil",
        system_prompt_instructions="""
- Tensión dramática ascendente: exactamente entre 10 y 12 escenas de ~5 segundos.
- Escena 1: Confesión o momento de alto impacto dramático que atrape de inmediato.
- MOTOR DE CONSISTENCIA DE PERSONAJES: DEBES definir 2 o 3 personajes en 'characters' con su descripción física fija y detallada en inglés (edad exacta, rasgos faciales, tono de piel, corte de cabello y vestimenta inconfundible).
- En cada escena debes listar en 'characters_in_scene' los IDs de los personajes que aparecen para inyectar su consistencia.
- La última escena DEBE terminar obligatoriamente con un cliffhanger impactante y la frase de cierre: "Parte 2 en el perfil".
""".strip(),
    ),
}


def get_profile(name: str) -> VideoProfile:
    """Retrieve profile by name, defaulting to curiosity if invalid."""
    lowered = name.lower().strip()
    if lowered not in PROFILES:
        raise ValueError(
            f"Perfil desconocido '{name}'. Perfiles disponibles: {list(PROFILES.keys())}"
        )
    return PROFILES[lowered]
