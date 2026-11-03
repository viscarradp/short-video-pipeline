"""Script generation engine using fal-ai/any-llm (google/gemini-2.5-flash)."""

import json
import os
import re
import time
import unicodedata
from enum import Enum
from pathlib import Path
from typing import Optional
from pydantic import BaseModel, Field
from rich.console import Console

import fal_client
from config import CAMERA_EFFECTS, FAL_KEY, FLUX_COST_USD, MAX_HASHTAGS, SCENE_WORDS_TOLERANCE
from pipeline_utils import CacheManifest, PipelineError, RunStats, fingerprint
from profiles import VideoProfile, get_profile, narration_word_budget
from prompt_templates import CTA_RULE_TEMPLATE, PROFILE_INSTRUCTIONS, SYSTEM_PROMPT_TEMPLATE
from styles import VisualStyle, get_style

console = Console()


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


def build_system_prompt(profile: VideoProfile, style: VisualStyle) -> str:
    """Generate system prompt enforcing JSON schema, profile requirements and the resolved visual style."""
    schema_template = {
        "title": "Título corto y viral",
        "hook_text": "2 a 6 palabras en español, texto en pantalla de los primeros segundos",
        "post_description": "1 o 2 frases para el pie del post; la última es una pregunta que invite a comentar",
        "hashtags": ["hashtag1", "hashtag2", "hashtag3", "hashtag4", "hashtag5"],
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

    budget = narration_word_budget(profile)
    tol = SCENE_WORDS_TOLERANCE
    cta_rule = (
        CTA_RULE_TEMPLATE.format(cta=profile.ending_call_to_action) if profile.ending_call_to_action else ""
    )

    return SYSTEM_PROMPT_TEMPLATE.format(
        schema_json=json.dumps(schema_template, indent=2, ensure_ascii=False),
        profile_upper=profile.name.upper(),
        profile_instructions=PROFILE_INSTRUCTIONS[profile.name],
        scenes_min=profile.target_scenes_min,
        scenes_max=profile.target_scenes_max,
        words_min=max(budget - tol, 1),
        words_max=budget + tol,
        words_target=budget,
        scene_seconds=f"{profile.target_scene_duration_sec:g}",
        cta_rule=cta_rule,
        camera_effects=str(list(CAMERA_EFFECTS)),
        style_label=style.label,
        style_guidance=style.scene_guidance,
    )


def generate_mock_script(topic: str, profile: VideoProfile, style_id: str = "") -> VideoProjectSchema:
    """Generador local de guión de demostración (solo con --mock-script; el tema NO se respeta).

    `style_id` vacío = estilo por defecto del perfil."""
    style = get_style(style_id or profile.default_style_id)
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
            visual_style_id=style.id,
            global_visual_style=style.image_suffix,
            characters={},
            scenes=scenes,
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
            visual_style_id=style.id,
            global_visual_style=style.image_suffix,
            characters=characters,
            scenes=scenes,
            hook_text="Nadie debía saberlo",
            post_description=(
                "Un secreto familiar escondido durante años acaba de salir a la luz. "
                "¿Tú qué habrías hecho en su lugar?"
            ),
            hashtags=["micronovela", "historiareal", "secretosfamiliares", "drama", "suspenso", "herencia", "chisme"],
        )


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


def light_normalize_project(project: VideoProjectSchema, profile: VideoProfile) -> bool:
    """Normalización ligera (también para guiones en caché o editados a mano): sin validar el nº de escenas.

    Renumera scene_id 1..N, fuerza el perfil, normaliza hashtags/textos de publicación y garantiza la frase de
    cierre del perfil (si existe) al final de la última narración. NO toca global_visual_style ni visual_style_id:
    el estilo de un guion existente es suyo (los prompts de imagen ya pagados dependen de él). Devuelve True si
    algo cambió.
    """
    before = project.model_dump()
    project.profile = profile.name
    for idx, scene in enumerate(project.scenes, start=1):
        scene.scene_id = idx
    project.hook_text = " ".join(project.hook_text.split())
    project.post_description = " ".join(project.post_description.split())
    project.hashtags = normalize_hashtags(project.hashtags)
    cta = profile.ending_call_to_action
    if cta and project.scenes and not ends_with_cta(project.scenes[-1].narration_es, cta):
        project.scenes[-1].narration_es = append_cta(project.scenes[-1].narration_es, cta)
    return project.model_dump() != before


def normalize_project(project: VideoProjectSchema, profile: VideoProfile, style_id: str = "") -> VideoProjectSchema:
    """Normalize an LLM-produced project in place; raise ValueError if scene count is out of bounds.

    Fija el estilo visual resuelto (`style_id` vacío = por defecto del perfil); es el único lugar, junto con el
    guion mock, donde se asigna visual_style_id a un guion nuevo."""
    n_scenes = len(project.scenes)
    if not (profile.target_scenes_min <= n_scenes <= profile.target_scenes_max):
        raise ValueError(
            f"El guión tiene {n_scenes} escenas; el perfil '{profile.name}' requiere entre "
            f"{profile.target_scenes_min} y {profile.target_scenes_max}."
        )

    style = get_style(style_id or profile.default_style_id)
    project.visual_style_id = style.id
    project.global_visual_style = style.image_suffix  # informativo; la composición usa visual_style_id

    light_normalize_project(project, profile)

    for scene in project.scenes:
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


def generate_or_load_script(
    topic: str,
    profile_name: str,
    output_dir: Path,
    manifest: CacheManifest,
    force: bool = False,
    mock_script: bool = False,
    stats: Optional[RunStats] = None,
    style_id: str = "",
) -> VideoProjectSchema:
    """Carga el guion en caché (respetando ediciones manuales) o lo genera con Fal.ai any-llm.

    Huella: tema + perfil + flag mock (el estilo NO forma parte). Nunca cae silenciosamente a mock.

    `style_id` es el estilo ya resuelto (ver resolve_style_id). En un guion nuevo, vacío = default del perfil. En un
    guion en caché, vacío = no tocar; un id distinto del guardado actualiza script.json (con aviso de coste).
    """
    script_path = output_dir / "script.json"
    legacy_mock_marker = output_dir / "script.mock"  # marcador de Fase 1, reemplazado por el manifiesto

    profile = get_profile(profile_name)
    fp = fingerprint(kind="script", topic=topic, profile=profile.name, mock_script=mock_script)

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
                with open(script_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                project = VideoProjectSchema.model_validate(data)
                changed = light_normalize_project(project, profile)
                if style_id and style_id != project.visual_style_id:
                    new_style = get_style(style_id)
                    previous = project.visual_style_id or "legado"
                    project.visual_style_id = new_style.id
                    project.global_visual_style = new_style.image_suffix  # informativo
                    changed = True
                    n = len(project.scenes)
                    console.print(
                        f"[yellow]⚠ Estilo visual cambiado de '{previous}' a '{new_style.id}'. En modo real se "
                        f"regenerarán las {n} imágenes (~${n * FLUX_COST_USD:.2f}, unos ${FLUX_COST_USD} cada una) "
                        "porque el prompt compuesto cambia. Los 'visual_prompt_en' existentes se escribieron para el "
                        "estilo anterior: usa --force-script para reescribirlos (también regenera imágenes).[/yellow]"
                    )
                if changed:
                    _write_script(project, script_path)
                    console.print("  [dim]script.json normalizado / actualizado y reescrito.[/dim]")
                if legacy_mock_marker.exists():
                    legacy_mock_marker.unlink()
                console.print(f"[green]✓ Guion en caché:[/] {script_path} ({len(project.scenes)} escenas)")
                return project
            except Exception as e:
                console.print(f"[yellow]Aviso: script.json existente no es válido ({e}). Regenerando...[/yellow]")
        elif recorded is not None:
            console.print("[yellow]Aviso: cambió el tema, el perfil o el modo del guion; se regenerará.[/yellow]")

    def _persist(project: VideoProjectSchema) -> None:
        _write_script(project, script_path)
        manifest.record(script_path, fp)
        if legacy_mock_marker.exists():
            legacy_mock_marker.unlink()
        if stats is not None:
            stats.script = True

    if mock_script:
        project = generate_mock_script(topic, profile, style_id)
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

    # Call Fal.ai any-llm (google/gemini-2.5-flash) with up to 3 attempts
    style = get_style(style_id or profile.default_style_id)
    system_prompt = build_system_prompt(profile, style)
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
            project = normalize_project(project, profile, style.id)

            # Persist script only once a valid, normalized project exists
            _persist(project)

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
