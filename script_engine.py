"""Script generation engine using fal-ai/any-llm (google/gemini-2.5-flash)."""

import json
import os
import re
import time
from enum import Enum
from pathlib import Path
from typing import Optional
from pydantic import BaseModel, Field
from rich.console import Console

import fal_client
from config import FAL_KEY
from pipeline_utils import PipelineError
from profiles import VideoProfile, get_profile

console = Console()


class CameraEffect(str, Enum):
    ZOOM_IN_FAST = "zoom_in_fast"
    ZOOM_IN_SLOW = "zoom_in_slow"
    ZOOM_OUT = "zoom_out"
    PAN_RIGHT = "pan_right"
    PAN_LEFT = "pan_left"


class SceneSchema(BaseModel):
    scene_id: int
    narration_es: str = Field(description="Locución en español neutro, sin acotaciones ni emojis")
    visual_prompt_en: str = Field(description="Descripción visual detallada en inglés")
    characters_in_scene: list[str] = Field(default_factory=list, description="IDs de personajes en la escena")
    camera_effect: str = Field(default="zoom_in_slow", description="Efecto de cámara sugerido")
    highlight_words: list[str] = Field(default_factory=list, description="1 a 3 palabras clave a resaltar en subtítulos")


class VideoProjectSchema(BaseModel):
    title: str
    profile: str
    global_visual_style: str
    characters: dict[str, str] = Field(default_factory=dict, description="character_id -> physical description in English")
    scenes: list[SceneSchema]


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


def build_system_prompt(profile: VideoProfile) -> str:
    """Generate system prompt enforcing JSON schema and profile requirements."""
    schema_template = {
        "title": "Título corto y viral",
        "profile": profile.name,
        "global_visual_style": profile.global_visual_style,
        "characters": {
            "char_1": "Detailed physical description in English (age, hair, clothing, facial traits)"
        } if profile.require_characters else {},
        "scenes": [
            {
                "scene_id": 1,
                "narration_es": "Texto del gancho en español neutro sin emojis ni paréntesis",
                "visual_prompt_en": "Cinematic visual description in English",
                "characters_in_scene": ["char_1"] if profile.require_characters else [],
                "camera_effect": "zoom_in_fast",
                "highlight_words": ["palabra1", "palabra2"]
            }
        ]
    }

    return f"""Eres un guionista experto en viralidad para videos verticales (TikTok, Reels, Shorts).
Tu tarea es producir un guion completo en formato JSON ESTRICTO conforme a la siguiente estructura:

JSON FORMAT TEMPLATE:
{json.dumps(schema_template, indent=2, ensure_ascii=False)}

REGLAS ESPECÍFICAS DEL PERFIL '{profile.name.upper()}':
{profile.system_prompt_instructions}

REGLAS GENERALES OBLIGATORIAS:
1. Responde ÚNICAMENTE con el objeto JSON válido. No agregues explicaciones, saludos ni texto antes o después.
2. Cada narración ('narration_es') debe ser fluida, conversacional, lista para ser leída por un locutor sintético (TTS). CERO emojis, CERO notas de audio o texto entre paréntesis.
3. Cada escena debe sugerir un 'camera_effect' entre: ['zoom_in_fast', 'zoom_in_slow', 'zoom_out', 'pan_right', 'pan_left'].
4. 'highlight_words' debe contener de 1 a 3 palabras exactas presentes en 'narration_es' (palabras impactantes o números).
5. Número de escenas requerido: entre {profile.target_scenes_min} y {profile.target_scenes_max}.
"""


def generate_mock_script(topic: str, profile: VideoProfile) -> VideoProjectSchema:
    """Generador local de guión de demostración (solo con --mock-script; el tema NO se respeta)."""
    console.print(
        "[yellow]⚠️  Modo --mock-script: usando guión de demostración local (no corresponde al tema solicitado).[/yellow]"
    )
    if profile.name == "curiosity":
        scenes = [
            SceneSchema(
                scene_id=1,
                narration_es="¿Sabías qué pasa exactamente dentro de tu motor cuando usas gasolina barata?",
                visual_prompt_en="Extreme close-up 3D isometric cutaway of a car engine cylinder with fuel injector spraying low quality dirty fuel",
                characters_in_scene=[],
                camera_effect="zoom_in_fast",
                highlight_words=["motor", "gasolina"],
            ),
            SceneSchema(
                scene_id=2,
                narration_es="La gasolina de bajo octanaje no resiste la alta compresión del pistón.",
                visual_prompt_en="Detailed 3D educational cutaway of a metallic piston moving up compressing air-fuel mixture with visible pressure waves",
                characters_in_scene=[],
                camera_effect="zoom_in_slow",
                highlight_words=["compresión", "pistón"],
            ),
            SceneSchema(
                scene_id=3,
                narration_es="Esto provoca detonaciones a destiempo conocidas como cascabeleo.",
                visual_prompt_en="Explosive premature flame front shockwave colliding violently inside the combustion chamber cylinder, studio lighting",
                characters_in_scene=[],
                camera_effect="zoom_out",
                highlight_words=["detonaciones", "cascabeleo"],
            ),
            SceneSchema(
                scene_id=4,
                narration_es="Pequeñas explosiones golpean las válvulas y destruyen las cabezas de los pistones.",
                visual_prompt_en="3D cutaway showing micro-cracks forming on a glowing red titanium engine valve under violent thermal stress",
                characters_in_scene=[],
                camera_effect="zoom_in_slow",
                highlight_words=["explosiones", "destruyen"],
            ),
            SceneSchema(
                scene_id=5,
                narration_es="Además, carece de aditivos detergentes que limpien los inyectores.",
                visual_prompt_en="Microscopic view inside a metallic fuel injector nozzle clogged with thick black carbon deposits",
                characters_in_scene=[],
                camera_effect="pan_right",
                highlight_words=["aditivos", "inyectores"],
            ),
            SceneSchema(
                scene_id=6,
                narration_es="Con los días, el carbón se acumula y bloquea el flujo perfecto de combustible.",
                visual_prompt_en="Cross section of combustion intake manifold heavily coated in dark crusty soot and sludge",
                characters_in_scene=[],
                camera_effect="zoom_in_slow",
                highlight_words=["carbón", "bloquea"],
            ),
            SceneSchema(
                scene_id=7,
                narration_es="Tu auto pierde hasta un veinte por ciento de potencia sin que lo notes.",
                visual_prompt_en="Futuristic automotive diagnostic hologram display showing engine power dropping sharply with red warning telemetry",
                characters_in_scene=[],
                camera_effect="zoom_out",
                highlight_words=["veinte", "potencia"],
            ),
            SceneSchema(
                scene_id=8,
                narration_es="El sensor de oxígeno se contamina y envía señales falsas a la computadora.",
                visual_prompt_en="Glowing electronic lambda sensor covered in residue sending corrupted digital electrical pulses to vehicle ECU",
                characters_in_scene=[],
                camera_effect="zoom_in_slow",
                highlight_words=["sensor", "computadora"],
            ),
            SceneSchema(
                scene_id=9,
                narration_es="El convertidor catalítico se sobrecalienta intentando quemar el combustible residual.",
                visual_prompt_en="Cutaway of glowing orange honeycomb catalytic converter melting internally due to extreme unburnt fuel heat",
                characters_in_scene=[],
                camera_effect="zoom_in_fast",
                highlight_words=["convertidor", "sobrecalienta"],
            ),
            SceneSchema(
                scene_id=10,
                narration_es="Una reparación que puede costar más de dos mil dólares.",
                visual_prompt_en="Mechanic workshop workbench with shattered piston rings and damaged metal components under bright studio lights",
                characters_in_scene=[],
                camera_effect="zoom_in_slow",
                highlight_words=["reparación", "dólares"],
            ),
            SceneSchema(
                scene_id=11,
                narration_es="Ahorrar un par de monedas por litro te saldrá diez veces más caro.",
                visual_prompt_en="Split visual showing small coins at a fuel pump on left vs massive engine repair tools on right",
                characters_in_scene=[],
                camera_effect="pan_left",
                highlight_words=["ahorrar", "caro"],
            ),
            SceneSchema(
                scene_id=12,
                narration_es="Revisa siempre el manual del fabricante y usa el octanaje correcto para tu motor.",
                visual_prompt_en="Glossy car owner manual open to fuel specification page next to pristine clean engine bay",
                characters_in_scene=[],
                camera_effect="zoom_in_slow",
                highlight_words=["manual", "octanaje"],
            ),
            SceneSchema(
                scene_id=13,
                narration_es="Comparte esto con quien siempre busca la gasolinera más barata.",
                visual_prompt_en="Modern sports car engine revving smoothly with clean exhaust and pristine blue flame combustion",
                characters_in_scene=[],
                camera_effect="zoom_out",
                highlight_words=["comparte", "gasolinera"],
            ),
        ]
        return VideoProjectSchema(
            title="Qué pasa en tu motor si usas gasolina barata",
            profile=profile.name,
            global_visual_style=profile.global_visual_style,
            characters={},
            scenes=scenes,
        )
    else:
        # Micronovela fallback
        characters = {
            "carlos": "Carlos, a 32-year-old anxious man with messy brown hair, dark stubble, wearing an olive green jacket and grey t-shirt",
            "elena": "Elena, a 55-year-old stern elegant woman with silver-streaked bob haircut, wearing a navy blue blazer and pearl necklace",
        }
        scenes = [
            SceneSchema(
                scene_id=1,
                narration_es="Encontré una carta notarial en la basura que cambió mi vida para siempre.",
                visual_prompt_en="Dramatic close-up of hands trembling holding a crumpled stamped legal eviction notice next to a wooden trash bin",
                characters_in_scene=["carlos"],
                camera_effect="zoom_in_fast",
                highlight_words=["carta", "basura"],
            ),
            SceneSchema(
                scene_id=2,
                narration_es="Mi hermano hipotecó la casa de mi abuela en secreto para pagar una deuda.",
                visual_prompt_en="Dimly lit kitchen table where Carlos confronts the shadowed silhouette of a man over stacks of bank notices",
                characters_in_scene=["carlos"],
                camera_effect="zoom_in_slow",
                highlight_words=["hermano", "hipotecó"],
            ),
            SceneSchema(
                scene_id=3,
                narration_es="La propiedad estaba valorada en más de trescientos mil dólares.",
                visual_prompt_en="Charming vintage suburban two-story brick family house bathed in melancholic dusk lighting with a foreclosure sign",
                characters_in_scene=[],
                camera_effect="zoom_out",
                highlight_words=["propiedad", "dólares"],
            ),
            SceneSchema(
                scene_id=4,
                narration_es="Cuando confronté a Elena, su abogada, me amenazó con llamar a la policía.",
                visual_prompt_en="Luxury mahogany law office interior, Elena with sharp intense gaze pointing a pen authoritatively",
                characters_in_scene=["carlos", "elena"],
                camera_effect="zoom_in_slow",
                highlight_words=["confronté", "amenazó"],
            ),
            SceneSchema(
                scene_id=5,
                narration_es="Pero lo que ellos no sabían es que mi abuela guardaba un segundo testamento.",
                visual_prompt_en="Antique brass key inserting into an old dusty wooden chest hidden beneath attic floorboards",
                characters_in_scene=[],
                camera_effect="zoom_in_fast",
                highlight_words=["segundo", "testamento"],
            ),
            SceneSchema(
                scene_id=6,
                narration_es="Un documento firmado apenas tres días antes de su partida.",
                visual_prompt_en="Close-up of yellowed parchment paper showing handwritten signature in fountain pen ink",
                characters_in_scene=[],
                camera_effect="pan_right",
                highlight_words=["documento", "firmado"],
            ),
            SceneSchema(
                scene_id=7,
                narration_es="En esa carta revelaba quién era el verdadero dueño de todo.",
                visual_prompt_en="Carlos standing stunned under single pendant lamp clutching the document with widened eyes",
                characters_in_scene=["carlos"],
                camera_effect="zoom_in_slow",
                highlight_words=["carta", "dueño"],
            ),
            SceneSchema(
                scene_id=8,
                narration_es="Y el nombre escrito no era ni el de mi hermano ni el mío.",
                visual_prompt_en="Dramatic dark vignette focus on a mysterious red wax seal breaking on an envelope",
                characters_in_scene=[],
                camera_effect="zoom_out",
                highlight_words=["nombre", "escrito"],
            ),
            SceneSchema(
                scene_id=9,
                narration_es="Descubre la verdad oculta que destruyó a nuestra familia. Parte 2 en el perfil.",
                visual_prompt_en="Carlos and Elena in high-tension cinematic standoff outside court entrance under stormy skies",
                characters_in_scene=["carlos", "elena"],
                camera_effect="zoom_in_fast",
                highlight_words=["verdad", "perfil"],
            ),
        ]
        return VideoProjectSchema(
            title=topic,
            profile=profile.name,
            global_visual_style=profile.global_visual_style,
            characters=characters,
            scenes=scenes,
        )


def _word_tokens(text: str) -> list[str]:
    """Lowercase tokens of a text with punctuation stripped (accent-sensitive)."""
    tokens = (re.sub(r"[^\w]", "", t.lower()) for t in text.split())
    return [t for t in tokens if t]


def normalize_project(project: VideoProjectSchema, profile: VideoProfile) -> VideoProjectSchema:
    """Normalize an LLM-produced project in place; raise ValueError if scene count is out of bounds."""
    n_scenes = len(project.scenes)
    if not (profile.target_scenes_min <= n_scenes <= profile.target_scenes_max):
        raise ValueError(
            f"El guión tiene {n_scenes} escenas; el perfil '{profile.name}' requiere entre "
            f"{profile.target_scenes_min} y {profile.target_scenes_max}."
        )

    project.profile = profile.name
    project.global_visual_style = profile.global_visual_style

    for idx, scene in enumerate(project.scenes, start=1):
        scene.scene_id = idx
        narration_tokens = set(_word_tokens(scene.narration_es))
        kept: list[str] = []
        for hw in scene.highlight_words:
            hw_tokens = _word_tokens(hw)
            if hw_tokens and all(t in narration_tokens for t in hw_tokens):
                kept.append(hw)
        scene.highlight_words = kept

    return project


def _write_script(project: VideoProjectSchema, script_path: Path) -> None:
    with open(script_path, "w", encoding="utf-8") as f:
        f.write(project.model_dump_json(indent=2))


def generate_or_load_script(
    topic: str,
    profile_name: str,
    output_dir: Path,
    force: bool = False,
    mock_script: bool = False,
) -> VideoProjectSchema:
    """Load cached script or generate a new one via Fal.ai any-llm (never silently falls back to mock)."""
    script_path = output_dir / "script.json"
    mock_marker = output_dir / "script.mock"

    # Idempotency / Cache Check (a mock script is never a valid cache entry for a real run)
    if script_path.exists() and not force and not mock_script and mock_marker.exists():
        console.print("[yellow]Aviso: el script.json en caché es de demostración (--mock-script); se regenerará con el LLM.[/yellow]")
    elif script_path.exists() and not force:
        try:
            with open(script_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            project = VideoProjectSchema.model_validate(data)
            console.print(f"[green]✓ Reutilizando guion en caché:[/] {script_path}")
            return project
        except Exception as e:
            console.print(f"[yellow]Aviso: script.json existente no es válido ({e}). Regenerando...[/yellow]")

    profile = get_profile(profile_name)

    if mock_script:
        project = generate_mock_script(topic, profile)
        _write_script(project, script_path)
        mock_marker.touch()
        return project

    # Set FAL_KEY in os.environ for fal_client if configured
    if FAL_KEY:
        os.environ["FAL_KEY"] = FAL_KEY

    if not os.getenv("FAL_KEY"):
        raise PipelineError(
            "No se encontró FAL_KEY. Define FAL_KEY en el archivo .env (ver .env.example) "
            "o usa --mock-script para generar un guión de demostración local."
        )

    # Call Fal.ai any-llm (google/gemini-2.5-flash) with up to 3 attempts
    system_prompt = build_system_prompt(profile)
    base_user_prompt = (
        f"Tema del video: {topic}\nPerfil: {profile.name}\n"
        "Genera el guión JSON completo siguiendo todas las reglas."
    )
    user_prompt = base_user_prompt

    console.print(f"[cyan]Llamando a Fal.ai (any-llm / google/gemini-2.5-flash) para '{topic}'...[/cyan]")

    max_retries = 3
    last_error: Optional[Exception] = None

    for attempt in range(1, max_retries + 1):
        try:
            res = fal_client.run(
                "fal-ai/any-llm",
                arguments={
                    "model": "google/gemini-2.5-flash",
                    "prompt": user_prompt,
                    "system_prompt": system_prompt,
                    "temperature": 0.7,
                },
            )
            raw_output = res.get("output", "")
            if not raw_output:
                raise ValueError("Fal.ai any-llm devolvió respuesta vacía")

            cleaned_json = clean_llm_json_response(raw_output)
            data = json.loads(cleaned_json)
            project = VideoProjectSchema.model_validate(data)
            project = normalize_project(project, profile)

            # Persist script only once a valid, normalized project exists
            _write_script(project, script_path)
            if mock_marker.exists():
                mock_marker.unlink()

            console.print(f"[green]✓ Guión generado y validado con Pydantic ({len(project.scenes)} escenas).[/green]")
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
