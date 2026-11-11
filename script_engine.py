"""Script generation engine using fal-ai openrouter/router (google/gemini-2.5-flash)."""

import json
import os
import re
import time
import unicodedata
from enum import Enum
from pathlib import Path
from typing import Any, Optional
from pydantic import BaseModel, Field
from rich.console import Console

import fal_client
from config import (
    CAMERA_EFFECTS,
    FAL_KEY,
    FLUX_COST_USD,
    MAX_HASHTAGS,
    PROTAGONIST_ID,
    SEEDREAM_COST_USD,
    VideoFormat,
    get_format,
)
from pipeline_utils import CacheManifest, PipelineError, RunStats, fingerprint
from profiles import (
    MAX_TARGET_MINUTES,
    MIN_TARGET_MINUTES,
    VideoProfile,
    clamp_target_minutes,
    get_profile,
    profile_for_minutes,
    profile_for_scenes,
    scene_word_limits,
    total_word_limits,
)
from prompt_templates import (
    CTA_RULE_TEMPLATE,
    PROFILE_INSTRUCTIONS,
    STRICT_HOOK_RULE,
    STRICT_RULE_TEMPLATE,
    STRICT_TOTAL_RULE_TEMPLATE,
    PROTAGONIST_BLOCK_TEMPLATE,
    PROTAGONIST_FRAMING_RULES,
    SYSTEM_PROMPT_TEMPLATE,
)
from styles import VisualStyle, get_style

console = Console()

# LLM del guionista: fal-ai/any-llm quedó obsoleto en fal; openrouter/router es su reemplazo (mismo modelo).
LLM_ENDPOINT = "openrouter/router"
LLM_MODEL = "google/gemini-2.5-flash"
LLM_TEMPERATURE = 0.7
# Tokens de salida: ~250 por escena (narración + prompt visual + JSON; en la práctica salen ~150) más un margen para el
# título, los textos de publicación y los personajes. El tope es la salida máxima de Gemini 2.5 Flash (65 536).
LLM_MAX_TOKENS_PER_SCENE = 250
LLM_MAX_TOKENS_BASE = 2000
LLM_MAX_TOKENS_CAP = 60000

# Con protagonista, el video es B-roll explicativo sin personas y el protagonista solo sale en las ESCENAS CLAVE (el prompt pide
# ~20 %). Debe salir en al menos una escena (sin la etiqueta PROTAGONIST_ID en characters_in_scene el motor de imágenes no usa su
# imagen de referencia); si sale en más del 25 % (o en más de PROTAGONIST_MAX_SCENES), enforce_protagonist_scenes quita el ID de
# las escenas sobrantes de forma determinista.
PROTAGONIST_TARGET_FRACTION = 0.20
PROTAGONIST_MAX_FRACTION = 0.25
PROTAGONIST_MAX_SCENES = 24


class CameraEffect(str, Enum):
    ZOOM_IN_FAST = "zoom_in_fast"
    ZOOM_IN_SLOW = "zoom_in_slow"
    ZOOM_OUT = "zoom_out"
    PAN_RIGHT = "pan_right"
    PAN_LEFT = "pan_left"


assert tuple(e.value for e in CameraEffect) == CAMERA_EFFECTS, "CameraEffect debe coincidir con config.CAMERA_EFFECTS"


class SceneSchema(BaseModel):
    scene_id: int
    narration_es: str = Field(description="Locución en español neutro, sin acotaciones ni emojis")
    visual_prompt_en: str = Field(description="Descripción visual detallada en inglés")
    characters_in_scene: list[str] = Field(default_factory=list, description="IDs de personajes en la escena")
    camera_effect: str = Field(default="zoom_in_slow", description="Efecto de cámara sugerido")
    highlight_words: list[str] = Field(default_factory=list, description="1 a 3 palabras clave a resaltar en subtítulos")


class VideoProjectSchema(BaseModel):
    title: str
    # El LLM ya NO emite profile / global_visual_style / visual_style_id: los fija el código (normalize_project).
    profile: str = ""
    # Estilo visual del video (styles.STYLES). Vacío = guion LEGADO: se compone con global_visual_style (ver
    # image_engine.compose_scene_prompt_legacy) para no invalidar imágenes ya pagadas.
    visual_style_id: str = ""
    # Legado: texto de estilo que usaban los guiones antiguos. En guiones nuevos es solo informativo (= image_suffix).
    global_visual_style: str = ""
    characters: dict[str, str] = Field(default_factory=dict, description="character_id -> physical description in English")
    scenes: list[SceneSchema]
    # Campos de publicación (opcionales: los script.json antiguos sin ellos siguen cargando)
    hook_text: str = Field(default="", description="2 a 6 palabras en español para mostrar en pantalla los primeros segundos")
    post_description: str = Field(default="", description="1-2 frases para el pie del post, terminando en una pregunta")
    hashtags: list[str] = Field(default_factory=list, description="5 a 8 hashtags temáticos sin '#', en minúsculas")
    # Formato de video (config.FORMATS): lo fija el código, no el LLM. Los script.json antiguos sin este campo son verticales.
    video_format: str = "vertical"
    # Slug del protagonista de la biblioteca de personajes (characters/<slug>/); lo fija el código. Vacío = sin protagonista.
    # Su descripción va en characters[PROTAGONIST_ID] y su imagen de referencia la usa el motor de imágenes.
    protagonist_slug: str = ""


def clean_llm_json_response(raw_text: str) -> str:
    """Extract clean JSON string from LLM output, stripping markdown code blocks."""
    text = raw_text.strip()
    # Remove markdown code fences ```json ... ``` or ``` ... ```
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    
    # Locate first { and last }
    first_brace = text.find("{")
    last_brace = text.rfind("}")
    if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
        text = text[first_brace : last_brace + 1]

    return text.strip()


def build_system_prompt(
    profile: VideoProfile, style: VisualStyle, video_format: VideoFormat, protagonist: Optional[Any] = None
) -> str:
    """Generate system prompt enforcing JSON schema, profile requirements, the resolved visual style and the video format.

    `protagonist` (objeto con .name y .description_en, ver character_engine) añade el bloque 'PROTAGONISTA FIJO' y
    cambia el esquema de ejemplo: el protagonista no se define en 'characters' (lo inyecta normalize_project)."""
    if protagonist is not None:
        characters_schema = (
            {"char_1": "Short physical description in English of a SECONDARY character (age, hair, clothing)"}
            if profile.require_characters
            else {}
        )
        scene_characters_example = [PROTAGONIST_ID]
    else:
        characters_schema = (
            {"char_1": "Detailed physical description in English (age, hair, clothing, facial traits)"}
            if profile.require_characters
            else {}
        )
        scene_characters_example = ["char_1"] if profile.require_characters else []
    schema_template = {
        "title": "Título corto y viral",
        "hook_text": "2 a 6 palabras en español, texto en pantalla de los primeros segundos",
        "post_description": "1 o 2 frases para el pie del post; la última es una pregunta que invite a comentar",
        "hashtags": ["hashtag1", "hashtag2", "hashtag3", "hashtag4", "hashtag5"],
        "characters": characters_schema,
        "scenes": [
            {
                "scene_id": 1,
                "narration_es": "Texto del gancho en español neutro sin emojis ni paréntesis",
                "visual_prompt_en": "Cinematic visual description in English",
                "characters_in_scene": scene_characters_example,
                "camera_effect": "zoom_in_fast",
                "highlight_words": ["palabra1", "palabra2"]
            }
        ]
    }

    words_min, budget, words_max = scene_word_limits(profile)
    if profile.target_scenes_min == profile.target_scenes_max:
        scenes_rule = f"EXACTAMENTE {profile.target_scenes_min}"
    else:
        scenes_rule = f"entre {profile.target_scenes_min} y {profile.target_scenes_max}"
        if profile.strict_scenes:
            scenes_rule += f" (objetivo: {(profile.target_scenes_min + profile.target_scenes_max) // 2})"
    strict_rule = hook_rule = ""
    if profile.strict_scenes:
        total = total_word_limits(profile)
        total_rule = (
            STRICT_TOTAL_RULE_TEMPLATE.format(
                total_min=total[0], total_max=total[1], sec_min=profile.total_sec_range[0], sec_max=profile.total_sec_range[1]
            )
            if total is not None and profile.total_sec_range is not None
            else ""
        )
        strict_rule = STRICT_RULE_TEMPLATE.format(words_min=words_min, words_max=words_max, total_rule=total_rule)
        hook_rule = STRICT_HOOK_RULE
    cta_rule = (
        CTA_RULE_TEMPLATE.format(cta=profile.ending_call_to_action) if profile.ending_call_to_action else ""
    )

    protagonist_block = ""
    if protagonist is not None:
        protagonist_block = PROTAGONIST_BLOCK_TEMPLATE.format(
            protagonist_name=protagonist.name,
            protagonist_description=" ".join(str(protagonist.description_en).split()),
            protagonist_id=PROTAGONIST_ID,
            framing_rule=PROTAGONIST_FRAMING_RULES.get(video_format.id, ""),
        )

    return SYSTEM_PROMPT_TEMPLATE.format(
        schema_json=json.dumps(schema_template, indent=2, ensure_ascii=False),
        profile_upper=profile.name.upper(),
        profile_instructions=PROFILE_INSTRUCTIONS[profile.name],
        scenes_rule=scenes_rule,
        words_min=words_min,
        words_max=words_max,
        words_target=budget,
        strict_rule=strict_rule,
        hook_rule=hook_rule,
        scene_seconds=f"{profile.target_scene_duration_sec:g}",
        cta_rule=cta_rule,
        camera_effects=str(list(CAMERA_EFFECTS)),
        style_label=style.label,
        style_guidance=style.scene_guidance,
        protagonist_block=protagonist_block,
        platform_label=video_format.platform_label_es,
    )


# Guiones mock de los perfiles con protagonista: cada escena es (narración, prompt visual, quién sale, efecto de cámara,
# palabras clave). 'quién' = "p" protagonista, "s" personaje secundario, "ps" ambos, "" ninguno. La narración usa {name}.
# Se repiten las escenas intermedias hasta llegar al nº de escenas del perfil (así --minutes también funciona en mock).
_MOCK_SECONDARY_ID = "vecina"
_MOCK_PROTAGONIST_BANKS: dict[str, dict[str, Any]] = {
    "que_pasaria": {
        "title": "¿Qué pasaría si se apagara el mundo?",
        "hook_text": "¿Y si se apagara todo?",
        "post_description": "Un mundo sin electricidad cambia todo en cuestión de días. ¿Tú habrías sobrevivido?",
        "hashtags": ["quepasariasi", "apocalipsis", "supervivencia", "escenarios", "ciencia", "ficcion", "curiosidades"],
        "secondary": {},
        "scenes": [
            ("¿Qué pasaría si mañana todas las luces del planeta se apagaran para siempre? {name} está a punto de descubrirlo.",
             "Wide shot of the main character standing alone on a rooftop at dusk, looking at a vast dark city skyline with every window and street lamp turned off",
             "p", "zoom_in_fast", ["luces", "descubrirlo"]),
            ("En la primera hora, los semáforos se apagan y las calles se llenan de autos detenidos.",
             "Aerial wide shot of an enormous traffic jam of stalled cars on a dark highway, dead traffic lights, long shadows at twilight",
             "", "zoom_out", ["semáforos", "detenidos"]),
            ("{name} baja corriendo las escaleras de su edificio. Los ascensores no funcionan y los vecinos gritan en la oscuridad.",
             "Medium tracking shot of the main character running down a dark concrete stairwell lit only by a weak phone flashlight, worried expression",
             "p", "zoom_in_slow", ["escaleras", "oscuridad"]),
            ("Los hospitales usan sus últimos generadores, pero el combustible apenas alcanza para tres días.",
             "Wide angle shot of a dim hospital corridor lit by red emergency lights, the main character walking past empty gurneys with a worried look",
             "p", "pan_right", ["hospitales", "generadores"]),
            ("Al tercer día, {name} entiende que quedarse en la ciudad es un error y decide buscar agua y comida.",
             "Over-the-shoulder shot of the main character packing a backpack by candlelight in a dim apartment, a map spread on the table",
             "p", "zoom_in_slow", ["agua", "comida"]),
            ("En el supermercado, los estantes están vacíos. Solo quedan latas abandonadas y un silencio incómodo.",
             "Wide shot of the main character walking through a looted supermarket aisle with a flashlight beam, scattered cans on the floor",
             "p", "pan_left", ["estantes", "silencio"]),
            ("Pero eso no es lo peor. Sin refrigeración, el agua potable se contamina en pocos días.",
             "Close-up of the main character's concerned face beside a rusty tap dripping cloudy water into a metal bucket, dim window light",
             "p", "zoom_in_fast", ["peor", "contamina"]),
            ("{name} improvisa un filtro con arena, carbón y una camiseta. No es perfecto, pero salva la jornada.",
             "Close-up of the main character's hands pouring water through a homemade sand and charcoal filter made from a plastic bottle, focused expression",
             "p", "zoom_in_slow", ["filtro", "jornada"]),
            ("Una semana después, la naturaleza empieza a recuperar las calles. Raíces rompen el asfalto y los animales regresan.",
             "Wide shot of an overgrown city street with cracked asphalt, tall grass, abandoned cars and a deer crossing in soft morning light",
             "", "zoom_out", ["naturaleza", "animales"]),
            ("Con el cielo sin contaminación, {name} ve por primera vez millones de estrellas sobre la ciudad.",
             "Low angle wide shot of the main character on a rooftop at night looking up at a breathtaking Milky Way above the dark skyline",
             "p", "zoom_in_slow", ["estrellas", "ciudad"]),
            ("Meses más tarde, {name} cultiva verduras en un parque junto a otros sobrevivientes. Sin red eléctrica, la comunidad lo es todo.",
             "Medium shot of the main character planting seedlings in a community garden inside a ruined city park, other survivors working in the background",
             "p", "pan_right", ["cultiva", "comunidad"]),
            ("Así sería un mundo sin electricidad. Y tú, ¿habrías sobrevivido?",
             "Wide shot at sunrise of the main character standing on a rooftop holding a small oil lamp, golden light spreading over the quiet city",
             "p", "zoom_out", ["electricidad", "sobrevivido"]),
        ],
    },
    "historia": {
        "title": "El mapa del faro olvidado",
        "hook_text": "Tres minutos para cruzar",
        "post_description": "Una aventura contra el reloj en un faro que nadie se atreve a pisar. ¿Tú habrías abierto esa puerta?",
        "hashtags": ["aventura", "historia", "misterio", "tesoro", "faro", "narracion", "cuentos"],
        "secondary": {},
        "scenes": [
            ("Faltan tres minutos para que el puente colapse y {name} todavía está justo en el medio.",
             "Wide shot of the main character running across a rickety wooden rope bridge over a deep canyon, planks falling away behind, dramatic sunset sky",
             "p", "zoom_in_fast", ["puente", "colapse"]),
            ("Todo empezó cuando {name} encontró un mapa antiguo escondido dentro de un libro viejo.",
             "Medium shot of the main character in a dusty attic opening a worn leather book, a folded old map falling out, shafts of light through the window",
             "p", "zoom_in_slow", ["mapa", "antiguo"]),
            ("El mapa señalaba un faro abandonado en una isla donde nadie se atreve a desembarcar.",
             "Wide aerial shot of a tall abandoned lighthouse on a rocky island surrounded by stormy dark sea and crashing waves",
             "", "zoom_out", ["faro", "isla"]),
            ("{name} alquila un pequeño bote y cruza el mar bajo una tormenta que no da tregua.",
             "Wide shot of the main character rowing a small wooden boat across rough gray waves, rain pouring, determined expression",
             "p", "pan_left", ["bote", "tormenta"]),
            ("Al llegar, la puerta del faro estaba cerrada con un candado oxidado y una pista grabada en la piedra.",
             "Medium shot of the main character crouching at a weathered lighthouse door with a rusty padlock, running fingers over carved symbols in the stone",
             "p", "zoom_in_slow", ["candado", "pista"]),
            ("Dentro, una escalera interminable y un reloj de péndulo que marca una hora distinta cada vez.",
             "Low angle shot up a spiral staircase inside the lighthouse, the main character climbing past an antique pendulum clock, dust floating in beams of light",
             "p", "zoom_in_slow", ["escalera", "reloj"]),
            ("Pero {name} no estaba solo: alguien más había seguido el mapa y lo observaba desde la oscuridad.",
             "Medium shot of the main character climbing the spiral stairs while a shadowy silhouette of a stranger watches from a high balcony",
             "p", "zoom_in_fast", ["solo", "oscuridad"]),
            ("El desconocido ofrece un trato: la llave del tesoro a cambio de la mitad. {name} duda un instante.",
             "Over-the-shoulder shot of the main character facing a shadowed stranger holding out a brass key, tense lantern-lit room",
             "p", "zoom_in_slow", ["trato", "tesoro"]),
            ("Sin pensarlo más, {name} rechaza el trato y sale corriendo hacia la cima mientras el faro tiembla.",
             "Wide shot of the main character sprinting up the final steps as dust and stones fall from the shaking lighthouse walls",
             "p", "pan_right", ["rechaza", "tiembla"]),
            ("Arriba, una lente gigante proyectaba un rayo de luz que apuntaba al tesoro escondido entre las rocas.",
             "Wide shot of the main character beside a giant glass lighthouse lens projecting a long beam of light across the sea toward a glittering spot among dark rocks",
             "p", "zoom_out", ["lente", "rayo"]),
            ("{name} baja con la caja justo cuando una ola enorme golpea la costa. Lo logró por segundos.",
             "Wide dramatic shot of the main character clutching a small wooden chest on the rocky shore as a huge wave crashes just behind",
             "p", "zoom_in_fast", ["caja", "ola"]),
            ("Dentro de la caja, otro mapa esperaba. La próxima aventura de {name} acaba de comenzar.",
             "Close-up of the main character's amazed face and hands opening a small chest holding a new folded map glowing in warm morning light",
             "p", "zoom_in_slow", ["otro", "aventura"]),
        ],
    },
    "chisme": {
        "title": "El chisme de la fiesta",
        "hook_text": "Nadie lo cuenta, pero yo sí",
        "post_description": "Un sobre misterioso, una foto vieja y una vecina que lo vio todo. ¿Tú de qué lado estás?",
        "hashtags": ["chisme", "storytime", "drama", "fiesta", "secretos", "vecinos", "chismecito"],
        "secondary": {
            _MOCK_SECONDARY_ID: "Doña Lucha, a 60-year-old nosy neighbor with warm brown skin, curly gray hair, round glasses and a floral apron",
        },
        "scenes": [
            ("Dicen que lo que pasó anoche en la fiesta no se lo cuentan a nadie… pero yo me enteré.",
             "Wide shot of a lively neighborhood party at night under string lights, guests whispering in small groups, the main character at the edge of the crowd",
             "p", "zoom_in_fast", ["fiesta", "enteré"]),
            ("Todo empezó cuando {name} llegó con un regalo enorme y una sonrisa demasiado perfecta.",
             "Medium shot of the main character arriving at a front gate carrying a huge wrapped gift with a forced bright smile, warm porch light",
             "p", "zoom_in_slow", ["regalo", "sonrisa"]),
            ("La vecina Lucha, que lo ve todo desde su ventana, ya tenía el celular listo para grabar.",
             "Medium shot of the neighbor peeking through lace curtains with a phone in her hand, a sly curious expression, seen from outside the window",
             "s", "pan_right", ["ventana", "celular"]),
            ("Según ella, {name} discutió con alguien en el patio trasero. Nadie sabe con quién.",
             "Medium shot of the main character whispering heatedly in a dim backyard while the neighbor watches from behind a hedge",
             "ps", "zoom_in_slow", ["discutió", "patio"]),
            ("Pero espera, que hay más. Un sobre aparece misteriosamente sobre la mesa del pastel.",
             "Close-up of the main character noticing a sealed cream envelope lying next to a birthday cake on a decorated party table, candles flickering",
             "p", "zoom_in_fast", ["sobre", "pastel"]),
            ("{name} abre el sobre con las manos temblando y se queda helado frente a todos los invitados.",
             "Close-up of the main character opening an envelope with trembling hands, shocked wide-eyed expression, blurred guests watching behind",
             "p", "zoom_in_slow", ["temblando", "invitados"]),
            ("La vecina Lucha cuenta que dentro había una foto vieja y una nota sin firma.",
             "Medium shot of the neighbor gesturing dramatically while telling the story to wide-eyed guests, the main character listening pale at the back of the room",
             "ps", "pan_left", ["foto", "nota"]),
            ("Y aquí viene lo mejor. En la foto aparecía {name}, años atrás, con alguien que juró no conocer.",
             "Close-up of an old faded photograph held between the main character's fingers showing two smiling young people, warm nostalgic light",
             "p", "zoom_in_fast", ["mejor", "foto"]),
            ("Los invitados empiezan a murmurar. Unos dicen que es mentira; otros, que siempre lo sospecharon.",
             "Wide shot of the main character in a crowded living room while guests whisper behind their hands and exchange looks, party decorations around",
             "p", "zoom_out", ["murmurar", "sospecharon"]),
            ("{name} sale al patio y se encara con Lucha. Ella jura que solo contó lo que vio.",
             "Medium shot of the main character confronting the neighbor in the backyard at night, she raises both hands innocently, tense atmosphere",
             "ps", "zoom_in_slow", ["encara", "jura"]),
            ("Al final, {name} confiesa la verdad frente a todos, y el chisme se queda corto.",
             "Wide shot of the main character standing in the middle of the living room speaking to a silent crowd, emotional and relieved expression",
             "p", "zoom_in_slow", ["confiesa", "verdad"]),
            ("¿Y tú de qué lado estás, del de {name} o del de Lucha? Cuéntamelo en los comentarios.",
             "Wide shot of the main character and the neighbor sitting at opposite ends of a long party table, the cake between them, guests watching",
             "ps", "zoom_out", ["lado", "comentarios"]),
        ],
    },
}


def _mock_protagonist_script(
    profile: VideoProfile, style: VisualStyle, video_format: VideoFormat, protagonist: Optional[Any]
) -> VideoProjectSchema:
    """Guion mock horizontal de un perfil con protagonista (que_pasaria / historia / chisme).

    Con protagonista usa su nombre, characters[PROTAGONIST_ID] y su slug; sin él inventa un héroe local (id propio, para
    que el motor de imágenes no busque una imagen de referencia que no existe)."""
    bank = _MOCK_PROTAGONIST_BANKS[profile.name]
    if protagonist is not None:
        hero_id, name = PROTAGONIST_ID, protagonist.name
        hero_description = protagonist.description_en
    else:
        hero_id, name = "heroe", "Mateo"
        hero_description = (
            "Mateo, a 28-year-old man with olive skin, short dark wavy hair and a short beard, wearing a green "
            "canvas jacket, a grey t-shirt and worn brown boots"
        )

    # Nº de escenas: mitad del rango del perfil; primera = gancho, última = cierre, y las intermedias se repiten en ciclo.
    n = max((profile.target_scenes_min + profile.target_scenes_max) // 2, 2)
    entries = bank["scenes"]
    middle = entries[1:-1]
    picked = [entries[0]] + [middle[i % len(middle)] for i in range(n - 2)] + [entries[-1]]

    scenes: list[SceneSchema] = []
    for idx, (narration, visual, who, camera, highlights) in enumerate(picked, start=1):
        in_scene = ([hero_id] if "p" in who else []) + ([_MOCK_SECONDARY_ID] if "s" in who else [])
        scenes.append(
            SceneSchema(
                scene_id=idx,
                narration_es=narration.format(name=name),
                visual_prompt_en=visual,
                characters_in_scene=in_scene,
                camera_effect=camera,
                highlight_words=highlights,
            )
        )
    project = VideoProjectSchema(
        title=bank["title"],
        profile=profile.name,
        visual_style_id=style.id,
        global_visual_style=style.image_suffix,
        video_format=video_format.id,
        characters={hero_id: hero_description, **bank["secondary"]},
        scenes=scenes,
        hook_text=bank["hook_text"],
        post_description=bank["post_description"],
        hashtags=bank["hashtags"],
        protagonist_slug=protagonist.slug if protagonist is not None else "",
    )
    if protagonist is not None:
        # Como un guion real: el protagonista solo en las escenas clave (~20 %); el resto es B-roll sin personas
        trim_protagonist_scenes(project, target_protagonist_scenes(len(project.scenes)))
        ensure_no_people_without_protagonist(project)
    light_normalize_project(project, profile)  # frase de cierre del perfil, hashtags... igual que un guion real
    return project


# Guiones mock de los shorts verticales (curiosity, micronovela): 8 escenas de 10 a 12 palabras (la última de micronovela lleva
# el cierre del perfil, "Parte 2 en el perfil", que light_normalize_project añade) = ~30 s de audio.
# Cada entrada: (narración, prompt visual, personajes, efecto de cámara, palabras clave).
_MOCK_CURIOSITY_SCENES: list[tuple[str, str, list[str], str, list[str]]] = [
    ("Tu auto pierde potencia cada vez que cargas gasolina barata sin saberlo.",
     "Extreme close-up 3D isometric cutaway of a car engine cylinder with fuel injector spraying low quality dirty fuel",
     [], "zoom_in_fast", ["potencia", "barata"]),
    ("La gasolina de bajo octanaje no resiste la compresión del pistón.",
     "Detailed 3D educational cutaway of a metallic piston moving up compressing air-fuel mixture with visible pressure waves",
     [], "zoom_in_slow", ["compresión", "pistón"]),
    ("Se enciende antes de tiempo y provoca explosiones diminutas llamadas cascabeleo.",
     "Explosive premature flame front shockwave colliding violently inside the combustion chamber cylinder, studio lighting",
     [], "zoom_out", ["explosiones", "cascabeleo"]),
    ("Cada golpe agrieta las válvulas y desgasta los pistones poco a poco.",
     "3D cutaway showing micro-cracks forming on a glowing red titanium engine valve under violent thermal stress",
     [], "zoom_in_slow", ["válvulas", "pistones"]),
    ("Además no limpia los inyectores y el carbón los tapa.",
     "Microscopic view inside a metallic fuel injector nozzle clogged with thick black carbon deposits",
     [], "pan_right", ["inyectores", "carbón"]),
    ("Pero eso no es lo peor: pierdes veinte por ciento.",
     "Futuristic automotive diagnostic hologram display showing engine power dropping sharply with red warning telemetry",
     [], "zoom_in_fast", ["peor", "veinte"]),
    ("El convertidor se sobrecalienta y repararlo cuesta dos mil dólares.",
     "Cutaway of glowing orange honeycomb catalytic converter melting internally due to extreme unburnt fuel heat",
     [], "zoom_in_fast", ["convertidor", "dólares"]),
    ("Ahorrar unas monedas hoy te sale diez veces más caro mañana, ¿seguro?",
     "Split visual showing small coins at a fuel pump on left vs massive engine repair tools on right",
     [], "zoom_out", ["ahorrar", "caro"]),
]

_MOCK_MICRONOVELA_SCENES: list[tuple[str, str, list[str], str, list[str]]] = [
    ("Encontré una carta notarial en la basura que cambió mi vida.",
     "Dramatic close-up of hands trembling holding a crumpled stamped legal eviction notice next to a wooden trash bin",
     ["carlos"], "zoom_in_fast", ["carta", "basura"]),
    ("Mi hermano hipotecó la casa de mi abuela en secreto.",
     "Dimly lit kitchen table where Carlos confronts the shadowed silhouette of a man over stacks of bank notices",
     ["carlos"], "zoom_in_slow", ["hermano", "hipotecó"]),
    ("La casa valía más de trescientos mil dólares y nadie lo sabía.",
     "Charming vintage suburban two-story brick family house bathed in melancholic dusk lighting with a foreclosure sign",
     [], "zoom_out", ["propiedad", "dólares"]),
    ("Confronté a Elena, su abogada, y amenazó con llamar a la policía.",
     "Luxury mahogany law office interior, Elena with sharp intense gaze pointing a pen authoritatively",
     ["carlos", "elena"], "zoom_in_slow", ["confronté", "amenazó"]),
    ("Pero mi abuela guardaba un segundo testamento escondido en el ático.",
     "Antique brass key inserting into an old dusty wooden chest hidden beneath attic floorboards",
     [], "zoom_in_fast", ["segundo", "testamento"]),
    ("Lo firmó tres días antes de morir y nombraba al verdadero dueño.",
     "Close-up of yellowed parchment paper showing handwritten signature in fountain pen ink",
     [], "pan_right", ["firmado", "dueño"]),
    ("Me quedé helado: el nombre escrito no era el de mi hermano.",
     "Carlos standing stunned under single pendant lamp clutching the document with widened eyes",
     ["carlos"], "zoom_in_slow", ["helado", "nombre"]),
    ("Entonces vi quién firmó como testigo.",
     "Carlos and Elena in high-tension cinematic standoff outside court entrance under stormy skies",
     ["carlos", "elena"], "zoom_in_fast", ["testigo", "firmado"]),
]


def _pick_mock_scenes(bank: list[tuple[str, str, list[str], str, list[str]]], n: int) -> list[SceneSchema]:
    """Las `n` escenas de un banco mock: la primera (gancho), la última (cierre) y las intermedias en ciclo (n = 1 da solo el gancho)."""
    middle = bank[1:-1]
    picked = [bank[0]] if n <= 1 else [bank[0]] + [middle[i % len(middle)] for i in range(n - 2)] + [bank[-1]]
    return [
        SceneSchema(
            scene_id=idx,
            narration_es=narration,
            visual_prompt_en=visual,
            characters_in_scene=list(chars),
            camera_effect=camera,
            highlight_words=list(highlights),
        )
        for idx, (narration, visual, chars, camera, highlights) in enumerate(picked, start=1)
    ]


def generate_mock_script(
    topic: str,
    profile: VideoProfile,
    style_id: str = "",
    format_id: str = "",
    protagonist: Optional[Any] = None,
) -> VideoProjectSchema:
    """Generador local de guión de demostración (solo con --mock-script; el tema NO se respeta).

    `style_id` / `format_id` vacíos = estilo y formato por defecto del perfil. Los perfiles con protagonista
    (que_pasaria, historia, chisme) lo usan si se indica; curiosity y micronovela ignoran `protagonist`."""
    style = get_style(style_id or profile.default_style_id)
    video_format = get_format(format_id or profile.default_format)
    console.print(
        "[yellow]⚠️  Modo --mock-script: usando guión de demostración local (no corresponde al tema solicitado).[/yellow]"
    )
    if profile.name in _MOCK_PROTAGONIST_BANKS:
        return _mock_protagonist_script(profile, style, video_format, protagonist)
    if protagonist is not None:
        console.print(
            f"[yellow]  El guión de demostración del perfil '{profile.name}' no usa protagonista: se ignora '{protagonist.slug}'.[/yellow]"
        )
    n = max((profile.target_scenes_min + profile.target_scenes_max) // 2, 1)  # 8 de 7-9; con --scenes N, exactamente N
    if profile.name == "curiosity":
        bank = _MOCK_CURIOSITY_SCENES
        project = VideoProjectSchema(
            title="Qué pasa en tu motor si usas gasolina barata",
            profile=profile.name,
            visual_style_id=style.id,
            global_visual_style=style.image_suffix,
            video_format=video_format.id,
            characters={},
            scenes=_pick_mock_scenes(bank, n),
            hook_text="Tu motor sufre en silencio",
            post_description=(
                "Ahorrar unos centavos por litro puede costarte miles en reparaciones. "
                "¿Tú qué gasolina le pones a tu auto?"
            ),
            hashtags=["gasolina", "motor", "mecanica", "autos", "datoscuriosos", "ahorro", "octanaje"],
        )
    else:
        # Micronovela fallback
        characters = {
            "carlos": "Carlos, a 32-year-old anxious man with messy brown hair, dark stubble, wearing an olive green jacket and grey t-shirt",
            "elena": "Elena, a 55-year-old stern elegant woman with silver-streaked bob haircut, wearing a navy blue blazer and pearl necklace",
        }
        project = VideoProjectSchema(
            title=topic,
            profile=profile.name,
            visual_style_id=style.id,
            global_visual_style=style.image_suffix,
            video_format=video_format.id,
            characters=characters,
            scenes=_pick_mock_scenes(_MOCK_MICRONOVELA_SCENES, n),
            hook_text="Nadie debía saberlo",
            post_description=(
                "Un secreto familiar escondido durante años acaba de salir a la luz. "
                "¿Tú qué habrías hecho en su lugar?"
            ),
            hashtags=["micronovela", "historiareal", "secretosfamiliares", "drama", "suspenso", "herencia", "chisme"],
        )
    light_normalize_project(project, profile)  # frase de cierre del perfil, hashtags... igual que un guion real
    return project


def _word_tokens(text: str) -> list[str]:
    """Lowercase tokens of a text with punctuation stripped (accent-sensitive)."""
    tokens = (re.sub(r"[^\w]", "", t.lower()) for t in text.split())
    return [t for t in tokens if t]


def normalize_hashtags(tags: list[str], limit: int = MAX_HASHTAGS) -> list[str]:
    """Quita '#', pasa a minúsculas, elimina espacios, acentos y símbolos, descarta duplicados y recorta a `limit`."""
    result: list[str] = []
    for raw in tags:
        text = unicodedata.normalize("NFKD", str(raw).lower())
        text = "".join(c for c in text if not unicodedata.combining(c))
        text = re.sub(r"[^a-z0-9_]", "", text)  # '#', espacios, signos y cualquier otro carácter
        if text and text not in result:
            result.append(text)
    return result[:limit]


def _cta_key(text: str) -> str:
    """Forma comparable de un texto: minúsculas, sin puntuación ni espacios repetidos (conserva los acentos)."""
    return " ".join(re.sub(r"[^\w]+", " ", text.lower()).split())


def ends_with_cta(narration: str, cta: str) -> bool:
    """True si la narración termina con la frase `cta`, ignorando mayúsculas y puntuación."""
    cta_key = _cta_key(cta)
    return bool(cta_key) and _cta_key(narration).endswith(cta_key)


def append_cta(narration: str, cta: str) -> str:
    """Añade ". " + cta al final (sin duplicar el punto si la narración ya termina en .!?…)."""
    base = narration.rstrip()
    sep = " " if base.endswith((".", "!", "?", "…")) else ". "
    return f"{base}{sep}{cta}"


def light_normalize_project(project: VideoProjectSchema, profile: VideoProfile, ensure_cta: bool = True) -> bool:
    """Normalización ligera (también para guiones en caché o editados a mano): sin validar el nº de escenas.

    Renumera scene_id 1..N, fuerza el perfil, normaliza hashtags/textos de publicación y garantiza la frase de
    cierre del perfil (si existe) al final de la última narración. NO toca global_visual_style ni visual_style_id:
    el estilo de un guion existente es suyo (los prompts de imagen ya pagados dependen de él). Devuelve True si
    algo cambió. `ensure_cta=False` (guiones importados: la narración es del autor) no toca ninguna narración.
    """
    before = project.model_dump()
    project.profile = profile.name
    for idx, scene in enumerate(project.scenes, start=1):
        scene.scene_id = idx
    project.hook_text = " ".join(project.hook_text.split())
    project.post_description = " ".join(project.post_description.split())
    project.hashtags = normalize_hashtags(project.hashtags)
    cta = profile.ending_call_to_action
    if ensure_cta and cta and project.scenes and not ends_with_cta(project.scenes[-1].narration_es, cta):
        project.scenes[-1].narration_es = append_cta(project.scenes[-1].narration_es, cta)
    return project.model_dump() != before


def sync_protagonist(project: VideoProjectSchema, protagonist: Any) -> bool:
    """Fija protagonist_slug y characters[PROTAGONIST_ID] = descripción de la biblioteca (el LLM no los decide).

    Descarta cualquier entrada de 'characters' que el LLM haya escrito para el protagonista con otro ID (PROTAGONIST_ID
    con otra capitalización, el slug o el nombre) y deja el protagonista primero. Devuelve True si algo cambió."""
    aliases = {PROTAGONIST_ID, protagonist.slug.lower(), protagonist.name.strip().lower()}
    before = (project.protagonist_slug, dict(project.characters))
    project.protagonist_slug = protagonist.slug
    others = {k: v for k, v in project.characters.items() if k.strip().lower() not in aliases}
    project.characters = {PROTAGONIST_ID: protagonist.description_en, **others}
    return before != (project.protagonist_slug, project.characters)


def canonicalize_protagonist_ids(project: VideoProjectSchema, protagonist: Any) -> None:
    """Unifica los IDs con que el LLM etiquetó al protagonista (PROTAGONIST_ID, slug o nombre -> PROTAGONIST_ID, sin duplicados)."""
    aliases = {PROTAGONIST_ID, protagonist.slug.lower(), protagonist.name.strip().lower()}
    for scene in project.scenes:
        ids: list[str] = []
        for char_id in scene.characters_in_scene:
            canonical = PROTAGONIST_ID if char_id.strip().lower() in aliases else char_id
            if canonical not in ids:
                ids.append(canonical)
        scene.characters_in_scene = ids


def max_protagonist_scenes(n_scenes: int) -> int:
    """Tope de escenas con el protagonista: el 25 % (redondeado, mínimo 1) y nunca más de PROTAGONIST_MAX_SCENES."""
    return min(PROTAGONIST_MAX_SCENES, max(1, round(n_scenes * PROTAGONIST_MAX_FRACTION)))


def target_protagonist_scenes(n_scenes: int) -> int:
    """Escenas clave con el protagonista que se piden (~20 %, mínimo 1, sin pasar del tope)."""
    return min(max_protagonist_scenes(n_scenes), max(1, round(n_scenes * PROTAGONIST_TARGET_FRACTION)))


_MAIN_CHARACTER_RE = re.compile(r"\b(?:the\s+)?main\s+character\b", re.IGNORECASE)


def trim_protagonist_scenes(project: VideoProjectSchema, cap: int) -> int:
    """Deja el ID del protagonista en `cap` escenas, elegidas de forma determinista y repartidas a lo largo del video (siempre
    se conserva la primera con protagonista, que suele ser el gancho), y se lo quita al resto. En las escenas recortadas, la
    frase "the main character" del prompt visual pasa a una figura lejana anónima (sin referencia, el generador de imágenes
    inventaría una cara que no es la del protagonista). Devuelve cuántas escenas recortó."""
    with_ids = [i for i, sc in enumerate(project.scenes) if PROTAGONIST_ID in sc.characters_in_scene]
    if len(with_ids) <= cap:
        return 0
    if cap <= 1:
        keep = {with_ids[0]}
    else:
        keep = {with_ids[round(k * (len(with_ids) - 1) / (cap - 1))] for k in range(cap)}
    trimmed = 0
    for i in with_ids:
        if i in keep:
            continue
        scene = project.scenes[i]
        scene.characters_in_scene = [c for c in scene.characters_in_scene if c != PROTAGONIST_ID]
        scene.visual_prompt_en = _MAIN_CHARACTER_RE.sub("a distant anonymous figure with the face not visible", scene.visual_prompt_en)
        trimmed += 1
    return trimmed


def enforce_protagonist_scenes(project: VideoProjectSchema, protagonist: Any) -> None:
    """Unifica los IDs con que el LLM etiquetó al protagonista y aplica la cuota de escenas clave: al menos una escena (si no,
    lanza ValueError y el reintento le explica el problema al LLM) y no más del 25 % (máximo PROTAGONIST_MAX_SCENES): las
    sobrantes se recortan con trim_protagonist_scenes."""
    canonicalize_protagonist_ids(project, protagonist)
    n_total = len(project.scenes)
    n_with = sum(1 for sc in project.scenes if PROTAGONIST_ID in sc.characters_in_scene)
    if n_total and n_with == 0:
        raise ValueError(
            f"El protagonista '{protagonist.name}' no está etiquetado en ninguna escena; debe salir en las escenas clave "
            f"(~{target_protagonist_scenes(n_total)} de {n_total}). Incluye \"{PROTAGONIST_ID}\" en 'characters_in_scene' de esas "
            "escenas y no lo definas en 'characters'"
        )
    trim_protagonist_scenes(project, max_protagonist_scenes(n_total))


# Escenas SIN el protagonista (video con protagonista): el generador de imágenes de texto a imagen inventa gente al azar si el
# prompt no lo prohíbe (y el estilo pide "expressive characters"), así que el prompt debe decir "no people" explícitamente.
NO_PEOPLE_PHRASE = "No people, no characters."
_NO_PEOPLE_RE = re.compile(r"\b(?:no|zero|without|devoid of)\s+(?:any\s+)?(?:people|characters|humans?|persons?|figures?|one)\b|\bnobody\b|\buninhabited\b", re.IGNORECASE)
# Palabras que delatan que el prompt SÍ describe gente (una multitud de fondo, un personaje de rol...): no se le añade "no people"
_PEOPLE_WORDS_RE = re.compile(
    r"\b(?:people|persons?|men|man|women|woman|boys?|girls?|child(?:ren)?|kids?|crowds?|humans?|humanity|humankind|figures?|"
    r"silhouettes?|famil(?:y|ies)|workers?|scientists?|survivors?|villagers?|citizens?|pedestrians?|tourists?|passengers?|"
    r"drivers?|farmers?|couples?|neighbou?rs?|guests?|strangers?|characters?|population|someone|somebody|everyone|"
    r"riders?|hikers?|soldiers?|doctors?|nurses?|students?|teams?|groups?|pilots?|astronauts?|explorers?|nomads?|"
    r"settlers?|travell?ers?|inhabitants?|residents?|vendors?|hunters?|dancers?|players?|swimmers?)\b",
    re.IGNORECASE,
)


def ensure_no_people_without_protagonist(project: VideoProjectSchema) -> int:
    """Red de seguridad del prompt del LLM: añade "No people, no characters." al 'visual_prompt_en' de cada escena sin el
    protagonista que no lo dice y tampoco describe gente. No toca las escenas con algún personaje en 'characters_in_scene'
    (el secundario definido) ni las que ya hablan de personas (una multitud de fondo que la narración exige). Devuelve
    cuántas escenas cambió."""
    changed = 0
    for scene in project.scenes:
        if scene.characters_in_scene:
            continue
        prompt = scene.visual_prompt_en.strip()
        if not prompt or _NO_PEOPLE_RE.search(prompt) or _PEOPLE_WORDS_RE.search(prompt):
            continue
        scene.visual_prompt_en = f"{prompt if prompt[-1] in '.!?…' else prompt + '.'} {NO_PEOPLE_PHRASE}"
        changed += 1
    return changed


def filter_highlight_words(project: VideoProjectSchema) -> None:
    """Deja en cada escena solo las palabras clave que aparecen en su narración (los subtítulos las buscan ahí)."""
    for scene in project.scenes:
        narration_tokens = set(_word_tokens(scene.narration_es))
        kept: list[str] = []
        for hw in scene.highlight_words:
            hw_tokens = _word_tokens(hw)
            if hw_tokens and all(t in narration_tokens for t in hw_tokens):
                kept.append(hw)
        scene.highlight_words = kept


def normalize_project(
    project: VideoProjectSchema,
    profile: VideoProfile,
    style_id: str = "",
    format_id: str = "",
    protagonist: Optional[Any] = None,
) -> VideoProjectSchema:
    """Normalize an LLM-produced project in place; raise ValueError if scene count is out of bounds (y, en los perfiles
    strict_scenes, si las palabras por escena o totales se salen de los límites: ver validate_strict_scenes).

    Fija el estilo visual y el formato de video resueltos (vacíos = por defecto del perfil); es el único lugar, junto
    con el guion mock, donde se asignan visual_style_id y video_format a un guion nuevo. Con `protagonist` fija
    protagonist_slug y characters[PROTAGONIST_ID] (sobrescribe lo que haya escrito el LLM) y exige que esté etiquetado
    en suficientes escenas."""
    n_scenes = len(project.scenes)
    if not (profile.target_scenes_min <= n_scenes <= profile.target_scenes_max):
        raise ValueError(
            f"El guión tiene {n_scenes} escenas; el perfil '{profile.name}' requiere "
            + (
                f"EXACTAMENTE {profile.target_scenes_min}."
                if profile.target_scenes_min == profile.target_scenes_max
                else f"entre {profile.target_scenes_min} y {profile.target_scenes_max}."
            )
        )

    style = get_style(style_id or profile.default_style_id)
    project.visual_style_id = style.id
    project.global_visual_style = style.image_suffix  # informativo; la composición usa visual_style_id
    project.video_format = get_format(format_id or profile.default_format).id

    if protagonist is not None:
        sync_protagonist(project, protagonist)
        enforce_protagonist_scenes(project, protagonist)
        ensure_no_people_without_protagonist(project)
    else:
        project.protagonist_slug = ""  # lo fija solo el código

    light_normalize_project(project, profile)
    validate_strict_scenes(project, profile)  # tras la frase de cierre del perfil: cuenta dentro del máximo de palabras
    filter_highlight_words(project)

    return project


SCENE_WORDS_HARD_SLACK = 1


def validate_strict_scenes(project: VideoProjectSchema, profile: VideoProfile) -> None:
    """Validación estricta de los shorts verticales (profile.strict_scenes): nº de escenas, palabras por escena (mínimo y máximo)
    y, si el perfil lo define, palabras totales (= duración del audio entre total_sec_range). Lanza ValueError con un mensaje
    que el reintento le devuelve al LLM. No hace nada en los demás perfiles."""
    if not profile.strict_scenes:
        return
    n = len(project.scenes)
    if not (profile.target_scenes_min <= n <= profile.target_scenes_max):
        raise ValueError(
            f"El guion tiene {n} escenas; este short requiere "
            + (
                f"EXACTAMENTE {profile.target_scenes_min}"
                if profile.target_scenes_min == profile.target_scenes_max
                else f"entre {profile.target_scenes_min} y {profile.target_scenes_max}"
            )
            + " escenas."
        )
    # El prompt pide scene_word_limits; aquí se tolera +-SCENE_WORDS_HARD_SLACK por escena (Gemini se pasa a menudo por una
    # palabra). La regla que de verdad acota la duración es la de palabras TOTALES de abajo.
    words_min, _, words_max = scene_word_limits(profile)
    words_min, words_max = max(1, words_min - SCENE_WORDS_HARD_SLACK), words_max + SCENE_WORDS_HARD_SLACK
    counts = [len(sc.narration_es.split()) for sc in project.scenes]
    too_long = [f"escena {sc.scene_id} ({c} palabras)" for sc, c in zip(project.scenes, counts) if c > words_max]
    too_short = [f"escena {sc.scene_id} ({c} palabras)" for sc, c in zip(project.scenes, counts) if c < words_min]
    if too_long:
        raise ValueError(f"Narración demasiado larga: máximo {words_max} palabras por escena. Acorta: {', '.join(too_long)}.")
    if too_short:
        raise ValueError(f"Narración demasiado corta: mínimo {words_min} palabras por escena. Amplía: {', '.join(too_short)}.")
    total = total_word_limits(profile)
    if total is not None and not (total[0] <= sum(counts) <= total[1]):
        sec_min, sec_max = profile.total_sec_range  # type: ignore[misc]
        raise ValueError(
            f"La narración suma {sum(counts)} palabras; debe sumar entre {total[0]} y {total[1]} para que el audio dure "
            f"entre {sec_min:g} y {sec_max:g} segundos."
        )


def _write_script(project: VideoProjectSchema, script_path: Path) -> None:
    with open(script_path, "w", encoding="utf-8") as f:
        f.write(project.model_dump_json(indent=2))


def write_script(project: VideoProjectSchema, script_path: Path) -> None:
    """Escribe script.json (formato canónico del pipeline)."""
    _write_script(project, script_path)


def read_cached_style_id(script_path: Path) -> Optional[str]:
    """visual_style_id del script.json existente: None si no existe o no es válido; "" si es un guion legado."""
    try:
        data = json.loads(Path(script_path).read_text(encoding="utf-8"))
        return VideoProjectSchema.model_validate(data).visual_style_id
    except Exception:
        return None


def read_cached_protagonist_slug(script_path: Path) -> Optional[str]:
    """protagonist_slug del script.json existente: None si no existe o no es válido; "" si el guion no tiene protagonista."""
    try:
        data = json.loads(Path(script_path).read_text(encoding="utf-8"))
        return VideoProjectSchema.model_validate(data).protagonist_slug
    except Exception:
        return None


def script_fingerprint(
    topic: str,
    profile_name: str,
    mock_script: bool,
    protagonist_slug: str = "",
    target_minutes: Optional[float] = None,
    target_scenes: Optional[int] = None,
) -> str:
    """Huella del guion: tema + perfil + flag mock, más protagonista, duración objetivo y nº de escenas forzado SOLO si se indican (así las huellas
    de los guiones sin ellos son idénticas a las de siempre). Ni el estilo ni el formato forman parte."""
    parts: dict[str, Any] = {"kind": "script", "topic": topic, "profile": profile_name, "mock_script": mock_script}
    if protagonist_slug:
        parts["protagonist"] = protagonist_slug
    if target_minutes is not None:
        parts["target_minutes"] = target_minutes
    if target_scenes is not None:
        parts["target_scenes"] = target_scenes
    return fingerprint(**parts)


def resolve_style_id(explicit: Optional[str], script_path: Path, profile: VideoProfile) -> str:
    """Estilo de una ejecución: --style explícito > visual_style_id del guion en caché > default del perfil.

    Un guion legado en caché (sin estilo) sigue siendo legado: devuelve "" (así sus imágenes pagadas se conservan).
    Lanza ValueError (con la lista de estilos) si el id resultante no existe."""
    if explicit:
        return get_style(explicit).id
    cached = read_cached_style_id(script_path)
    if cached is not None:
        return get_style(cached).id if cached else ""
    return get_style(profile.default_style_id).id


def read_cached_format_id(script_path: Path) -> Optional[str]:
    """video_format del script.json existente: None si no existe o no es válido (un guion antiguo sin el campo es "vertical")."""
    try:
        data = json.loads(Path(script_path).read_text(encoding="utf-8"))
        return VideoProjectSchema.model_validate(data).video_format
    except Exception:
        return None


def resolve_format_id(
    explicit: Optional[str], script_path: Path, profile: VideoProfile, run_config_format: Optional[str] = None
) -> str:
    """Formato de una ejecución: --format explícito > video_format del guion en caché > el de run_config.json > default del perfil.

    Lanza ValueError (con la lista de formatos) si el id resultante no existe."""
    if explicit:
        return get_format(explicit).id
    cached = read_cached_format_id(script_path)
    if cached is not None:
        return get_format(cached).id
    if run_config_format:
        return get_format(run_config_format).id
    return get_format(profile.default_format).id


def llm_max_tokens(profile: VideoProfile) -> int:
    """Tokens de salida que se piden al LLM: crece con el nº máximo de escenas del perfil (ver LLM_MAX_TOKENS_*)."""
    return min(LLM_MAX_TOKENS_BASE + LLM_MAX_TOKENS_PER_SCENE * profile.target_scenes_max, LLM_MAX_TOKENS_CAP)


def build_llm_arguments(
    system_prompt: str, user_prompt: str, max_tokens: int, temperature: float = LLM_TEMPERATURE
) -> dict[str, Any]:
    """Argumentos de openrouter/router. 'reasoning' se deja en su valor por defecto (apagado): el JSON no lo necesita y
    los tokens de razonamiento se cobran como salida."""
    return {
        "model": LLM_MODEL,
        "prompt": user_prompt,
        "system_prompt": system_prompt,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }


def llm_response_cost(res: Any) -> float:
    """usage.cost (USD) de la respuesta de openrouter/router; 0.0 si falta o no es un número."""
    try:
        usage = res.get("usage") or {}
        return max(float(usage.get("cost") or 0.0), 0.0)
    except (AttributeError, TypeError, ValueError):
        return 0.0


def extract_llm_output(res: Any) -> str:
    """Texto de la respuesta de openrouter/router. Lanza ValueError si trae 'error' o la salida está vacía
    (cuenta como intento fallido y se reintenta)."""
    if not isinstance(res, dict):
        raise ValueError(f"{LLM_ENDPOINT} devolvió una respuesta inesperada: {str(res)[:200]}")
    error = res.get("error")
    if error:
        raise ValueError(f"{LLM_ENDPOINT} devolvió un error: {str(error)[:300]}")
    output = res.get("output")
    if not isinstance(output, str) or not output.strip():
        raise ValueError(f"{LLM_ENDPOINT} devolvió respuesta vacía")
    return output


def load_cached_project(
    script_path: Path,
    profile: VideoProfile,
    protagonist: Optional[Any] = None,
    style_id: str = "",
    format_id: str = "",
    ensure_cta: bool = True,
) -> VideoProjectSchema:
    """Carga y normaliza el script.json en caché, respetando las ediciones manuales (nunca llama al LLM).

    Aplica la normalización ligera, sincroniza el protagonista con la biblioteca y, si `style_id` / `format_id` difieren
    de los guardados, los actualiza avisando del costo (las imágenes se regeneran porque su prompt compuesto cambia).
    Reescribe script.json solo si algo cambió. Lanza PipelineError si el formato o el estilo no son válidos."""
    with open(script_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    project = VideoProjectSchema.model_validate(data)
    changed = light_normalize_project(project, profile, ensure_cta=ensure_cta)
    if protagonist is not None:
        n_with = sum(1 for sc in project.scenes if PROTAGONIST_ID in sc.characters_in_scene)
        if sync_protagonist(project, protagonist):
            changed = True
            console.print(
                f"[yellow]⚠ La descripción del protagonista '{protagonist.slug}' en la biblioteca cambió respecto "
                f"a la del guion: se actualizó script.json. En modo real se regenerarán las imágenes de las "
                f"{n_with} escenas donde aparece, porque su prompt compuesto cambia.[/yellow]"
            )
    # Costo por imagen del proyecto: Seedream en todas las escenas si hay protagonista; Flux schnell si no
    image_cost = SEEDREAM_COST_USD if project.protagonist_slug else FLUX_COST_USD
    if style_id and style_id != project.visual_style_id:
        new_style = get_style(style_id)
        previous = project.visual_style_id or "legado"
        project.visual_style_id = new_style.id
        project.global_visual_style = new_style.image_suffix  # informativo
        changed = True
        n = len(project.scenes)
        console.print(
            f"[yellow]⚠ Estilo visual cambiado de '{previous}' a '{new_style.id}'. En modo real se "
            f"regenerarán las {n} imágenes (~${n * image_cost:.2f}, unos ${image_cost:g} cada una) "
            "porque el prompt compuesto cambia. Los 'visual_prompt_en' existentes se escribieron para el "
            "estilo anterior: usa --force-script para reescribirlos (también regenera imágenes).[/yellow]"
        )
    if format_id and format_id != project.video_format:
        new_format = get_format(format_id)
        if not project.visual_style_id and new_format.id != "vertical":
            # Los guiones legados componen el prompt con "vertical 9:16" fijo (ver compose_scene_prompt_legacy)
            raise PipelineError(
                f"El guion de {script_path.parent.name} es legado (sin estilo visual) y solo admite el formato "
                f"vertical. Para pasarlo a '{new_format.id}' indica también --style <id> (regenera sus imágenes)."
            )
        previous = project.video_format
        project.video_format = new_format.id
        changed = True
        n = len(project.scenes)
        console.print(
            f"[yellow]⚠ Formato de video cambiado de '{previous}' a '{new_format.id}'. En modo real se "
            f"regenerarán las {n} imágenes (~${n * image_cost:.2f}, unos ${image_cost:g} cada una) y se "
            "re-renderizarán los clips y el video final, porque el prompt, el tamaño de imagen y la geometría "
            "cambian. Los 'visual_prompt_en' existentes se escribieron para el encuadre anterior: usa "
            "--force-script para reescribirlos (también regenera imágenes).[/yellow]"
        )
    try:
        get_format(project.video_format)
    except ValueError as e:
        raise PipelineError(f"script.json: {e}") from e
    if changed:
        _write_script(project, script_path)
        console.print("  [dim]script.json normalizado / actualizado y reescrito.[/dim]")
    return project


def generate_or_load_script(
    topic: str,
    profile_name: str,
    output_dir: Path,
    manifest: CacheManifest,
    force: bool = False,
    mock_script: bool = False,
    stats: Optional[RunStats] = None,
    style_id: str = "",
    format_id: str = "",
    protagonist: Optional[Any] = None,
    target_minutes: Optional[float] = None,
    target_scenes: Optional[int] = None,
) -> VideoProjectSchema:
    """Carga el guion en caché (respetando ediciones manuales) o lo genera con Fal.ai (openrouter/router).

    Huella: tema + perfil + flag mock, más el slug del protagonista y la duración objetivo solo si se indican (ni el
    estilo ni el formato forman parte). Nunca cae silenciosamente a mock.

    `style_id` es el estilo ya resuelto (ver resolve_style_id). En un guion nuevo, vacío = default del perfil. En un
    guion en caché, vacío = no tocar; un id distinto del guardado actualiza script.json (con aviso de coste).
    `format_id` (ver resolve_format_id) funciona igual: un formato distinto del guardado actualiza script.json y las
    imágenes y clips se regeneran porque sus huellas cambian.

    `protagonist` (objeto con .slug, .name y .description_en: ver character_engine) hace que el guion lo use en la mayoría
    de las escenas (characters[PROTAGONIST_ID] y characters_in_scene). Cambiar de protagonista cambia la huella: el guion
    en caché se REGENERA (nombre, escenas y prompts estaban escritos para otro personaje; no hay forma de reutilizarlos).
    `target_minutes` (se acota a 0.5-15) ajusta el rango de escenas del perfil para durar ~esos minutos.
    `target_scenes` (--scenes) fuerza EXACTAMENTE ese nº de escenas (tiene prioridad sobre target_minutes) y la validación
    estricta de los shorts lo respeta.
    """
    script_path = output_dir / "script.json"
    legacy_mock_marker = output_dir / "script.mock"  # marcador de Fase 1, reemplazado por el manifiesto

    profile = get_profile(profile_name)
    effective_minutes: Optional[float] = None
    if target_minutes is not None:
        try:
            effective_minutes = clamp_target_minutes(target_minutes)
        except ValueError as e:
            raise PipelineError(str(e)) from e
        if effective_minutes != round(float(target_minutes), 2):
            console.print(
                f"[yellow]⚠ Duración objetivo {target_minutes:g} min fuera de rango: se usa {effective_minutes:g} min "
                f"(admitido: {MIN_TARGET_MINUTES:g} a {MAX_TARGET_MINUTES:g}).[/yellow]"
            )
        profile = profile_for_minutes(profile, effective_minutes)
        console.print(
            f"  [dim]Duración objetivo ~{effective_minutes:g} min: entre {profile.target_scenes_min} y "
            f"{profile.target_scenes_max} escenas de ~{profile.target_scene_duration_sec:g} s.[/dim]"
        )
    if target_scenes is not None:
        try:
            profile = profile_for_scenes(profile, target_scenes)
        except ValueError as e:
            raise PipelineError(str(e)) from e
        console.print(f"  [dim]Escenas forzadas (--scenes): exactamente {profile.target_scenes_min}.[/dim]")
    protagonist_slug = protagonist.slug if protagonist is not None else ""
    fp = script_fingerprint(topic, profile.name, mock_script, protagonist_slug, effective_minutes, target_scenes)

    if script_path.exists() and not force:
        recorded = manifest.get(script_path)
        if recorded is None:
            # Migración: workspace de Fase 1 sin entrada en el manifiesto.
            legacy_is_mock = legacy_mock_marker.exists()
            if legacy_is_mock == mock_script:
                manifest.record(script_path, fp)
                recorded = fp
                console.print("  [dim]Guion existente adoptado en el manifiesto de caché (workspace anterior).[/dim]")
            elif legacy_is_mock:
                console.print("[yellow]Aviso: el script.json existente es de demostración (--mock-script); se regenerará con el LLM.[/yellow]")
            else:
                console.print("[yellow]Aviso: el script.json existente no es de demostración; se regenerará como guion --mock-script.[/yellow]")
        if recorded == fp:
            try:
                project = load_cached_project(script_path, profile, protagonist, style_id, format_id)
                if legacy_mock_marker.exists():
                    legacy_mock_marker.unlink()
                console.print(f"[green]✓ Guion en caché:[/] {script_path} ({len(project.scenes)} escenas)")
                return project
            except PipelineError:
                raise  # error de formato/estilo del guion en caché: no se regenera (costaría dinero), se informa
            except Exception as e:
                console.print(f"[yellow]Aviso: script.json existente no es válido ({e}). Regenerando...[/yellow]")
        elif recorded is not None:
            console.print(
                "[yellow]Aviso: cambió el tema, el perfil, el modo, el protagonista o la duración objetivo del guion; "
                "se regenerará.[/yellow]"
            )
            cached_slug = read_cached_protagonist_slug(script_path)
            if cached_slug is not None and cached_slug != protagonist_slug:
                console.print(
                    f"[yellow]⚠ El guion existente usa el protagonista '{cached_slug or 'ninguno'}' y esta ejecución "
                    f"'{protagonist_slug or 'ninguno'}': un guion escrito para otro personaje no se puede reutilizar "
                    "(cambian nombres, escenas y prompts), así que se regenera el guion y, en modo real, también las "
                    "imágenes."
                    + (f" Para conservar el protagonista del proyecto, vuelve a indicar --character {cached_slug}." if cached_slug else "")
                    + "[/yellow]"
                )

    def _persist(project: VideoProjectSchema) -> None:
        _write_script(project, script_path)
        manifest.record(script_path, fp)
        if legacy_mock_marker.exists():
            legacy_mock_marker.unlink()
        if stats is not None:
            stats.script = True

    if mock_script:
        project = generate_mock_script(topic, profile, style_id, format_id, protagonist)
        _persist(project)
        return project

    # Set FAL_KEY in os.environ for fal_client if configured
    if FAL_KEY:
        os.environ["FAL_KEY"] = FAL_KEY

    if not os.getenv("FAL_KEY"):
        raise PipelineError(
            "No se encontró FAL_KEY. Define FAL_KEY en el archivo .env (ver .env.example) "
            "o usa --mock-script para generar un guión de demostración local."
        )

    # Call Fal.ai openrouter/router (google/gemini-2.5-flash) with up to 3 attempts
    style = get_style(style_id or profile.default_style_id)
    video_format = get_format(format_id or profile.default_format)
    if protagonist is None and profile.uses_protagonist:
        console.print(
            f"[yellow]Aviso: el perfil '{profile.name}' está pensado para un protagonista recurrente (--character); "
            "sin él, el LLM inventa uno y su aspecto no será consistente entre videos.[/yellow]"
        )
    system_prompt = build_system_prompt(profile, style, video_format, protagonist)
    max_tokens = llm_max_tokens(profile)
    base_user_prompt = (
        f"Tema del video: {topic}\nPerfil: {profile.name}\n"
        "Genera el guión JSON completo siguiendo todas las reglas."
    )
    user_prompt = base_user_prompt

    console.print(f"[cyan]Llamando a Fal.ai ({LLM_ENDPOINT} / {LLM_MODEL}) para '{topic}'...[/cyan]")

    max_retries = 5 if profile.strict_scenes else 3  # los shorts estrictos rechazan más respuestas del LLM
    last_error: Optional[Exception] = None
    llm_cost_total = 0.0

    for attempt in range(1, max_retries + 1):
        try:
            res = fal_client.run(LLM_ENDPOINT, arguments=build_llm_arguments(system_prompt, user_prompt, max_tokens))
            cost = llm_response_cost(res)  # cada llamada se cobra, también las que luego se rechazan
            llm_cost_total += cost
            if stats is not None:
                stats.llm_cost_usd += cost
            raw_output = extract_llm_output(res)

            cleaned_json = clean_llm_json_response(raw_output)
            data = json.loads(cleaned_json)
            project = VideoProjectSchema.model_validate(data)
            project = normalize_project(project, profile, style.id, video_format.id, protagonist)

            # Persist script only once a valid, normalized project exists
            _persist(project)

            console.print(f"[green]✓ Guión generado y validado con Pydantic ({len(project.scenes)} escenas).[/green]")
            if llm_cost_total > 0:
                console.print(f"  [dim]Costo del LLM: ${llm_cost_total:.4f}[/dim]")
            return project

        except Exception as err:
            last_error = err
            if attempt < max_retries:
                wait = 2 ** attempt
                console.print(
                    f"[yellow]Intento {attempt}/{max_retries} falló: {str(err)[:300]}. Reintentando en {wait}s...[/yellow]"
                )
                user_prompt = (
                    f"{base_user_prompt}\n\n"
                    f"Tu respuesta anterior fue rechazada por este error:\n{str(err)[:500]}\n"
                    "Corrige el problema y devuelve ÚNICAMENTE el JSON válido completo."
                )
                time.sleep(wait)
            else:
                console.print(f"[yellow]Intento {attempt}/{max_retries} falló: {str(err)[:300]}.[/yellow]")

    raise PipelineError(
        f"No se pudo generar un guión válido tras {max_retries} intentos. Último error: {str(last_error)[:500]}"
    )
