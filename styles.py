"""Catálogo modular de estilos visuales.

Cada estilo tiene dos partes:
- image_suffix: texto en inglés que se añade al prompt de CADA imagen (render, luz, paleta, materiales).
- scene_guidance: instrucciones para el LLM sobre cómo escribir 'visual_prompt_en' en este estilo.

Notas de diseño para Flux schnell (4 pasos, encoder T5 limitado a ~256 tokens):
- Lenguaje natural y concreto rinde más que listas de palabras sueltas ("8k, octane render, masterpiece").
- Lo que va primero en el prompt pesa más: el composer pone SUJETO -> personajes -> estilo -> composición.
- Mantener image_suffix por debajo de ~45 palabras para no truncar el final del prompt.
- Los estilos son originales: no se menciona a creadores, estudios ni marcas reales.

Para añadir un estilo nuevo basta con agregar una entrada a STYLES. Cualquier perfil puede usar cualquier
estilo con --style; cada perfil define el suyo por defecto en profiles.py (default_style_id).
"""

from dataclasses import dataclass

from config import FORMATS


@dataclass(frozen=True)
class VisualStyle:
    id: str
    label: str  # nombre corto en español (para --help / listados)
    description: str  # una línea en español
    image_suffix: str  # inglés, se añade a cada prompt de imagen
    scene_guidance: str  # inglés, instrucciones al LLM para escribir visual_prompt_en


# Composición común a todos los estilos: protege la zona del gancho (arriba), los subtítulos y la UI de la
# plataforma (abajo), y deja margen para los efectos de zoom/paneo. Ahora depende del formato de video
# (config.FORMATS[...].composition_hint); este alias es el del formato vertical.
COMPOSITION_SUFFIX = FORMATS["vertical"].composition_hint


STYLES: dict[str, VisualStyle] = {
    # ------------------------------------------------------------------ curiosity
    "explainer_3d": VisualStyle(
        id="explainer_3d",
        label="Explicativo 3D",
        description="Render 3D educativo con cortes transversales; limpio, saturado y muy legible.",
        image_suffix=(
            "Stylized 3D educational render, clean cutaway revealing the inner parts, smooth matte materials "
            "with glowing accents on the key element, soft global illumination, rich teal and warm orange "
            "palette, dark gradient background, crisp edges, shallow depth of field."
        ),
        scene_guidance=(
            "Show the fact physically: cross-sections, cutaways, x-ray views, exploded views or extreme "
            "close-ups of the mechanism or organ. Name the exact object and the exact moment of the process "
            "(e.g. 'a spark plug igniting the fuel mist inside a cylinder'). Add a camera angle "
            "(close-up, top-down, three-quarter view, low angle). Highlight the key part as the visual focus."
        ),
    ),
    "macro_lab": VisualStyle(
        id="macro_lab",
        label="Macro de laboratorio",
        description="Fotografía científica macro fotorrealista; textura y detalle extremos.",
        image_suffix=(
            "Photorealistic macro science photography, extreme detail and real textures, dramatic rim light "
            "against a dark background, glistening surfaces, high contrast, natural colors with one vivid "
            "accent color, shallow depth of field with creamy bokeh."
        ),
        scene_guidance=(
            "Describe a real, photographable subject at macro scale: materials, liquids, cells, insects, "
            "components, chemical reactions, wear and damage. Prefer tangible evidence of the fact over "
            "abstract diagrams. State scale cues (droplet, grain, fingertip) and a camera angle."
        ),
    ),
    "clay_miniature": VisualStyle(
        id="clay_miniature",
        label="Miniatura de plastilina",
        description="Diorama artesanal de plastilina; cálido, simpático y muy reconocible.",
        image_suffix=(
            "Handcrafted claymation miniature diorama, visible fingerprints and soft clay textures, tiny "
            "props, warm tabletop studio lighting, pastel palette with one bold accent color, tilt-shift "
            "depth of field, playful and charming."
        ),
        scene_guidance=(
            "Turn the fact into a tiny physical scene: miniature objects, simplified characters without "
            "faces of real people, exaggerated scale for comic effect. Keep one clear action per scene and "
            "a readable silhouette. Add a camera angle (eye level with the miniature, top-down, close-up)."
        ),
    ),
    # ------------------------------------------------------------------ micronovela
    "telenovela_cine": VisualStyle(
        id="telenovela_cine",
        label="Telenovela cinematográfica",
        description="Fotograma de drama fotorrealista; luz cálida, sombras profundas y emoción en primer plano.",
        image_suffix=(
            "Photorealistic cinematic film still from a Latin American family drama, warm practical lights "
            "with deep shadows, anamorphic 35mm look, natural skin texture, intense emotional expression, "
            "shallow depth of field, subtle film grain."
        ),
        scene_guidance=(
            "Describe ONE dramatic moment: who is in frame (refer to characters by a short role noun such as "
            "'the young woman' or 'the mother-in-law'; their full descriptions are injected automatically), "
            "their exact emotion and gesture, the setting (kitchen at night, hospital hallway, notary office, "
            "rainy street) and the shot type (extreme close-up of the eyes, over-the-shoulder, medium shot, "
            "hands holding a letter). Objects that carry the plot (ring, letter, phone face-down) are great."
        ),
    ),
    "noir_suspense": VisualStyle(
        id="noir_suspense",
        label="Suspenso noir",
        description="Drama oscuro de alto contraste; lluvia, neón y sombras duras para historias de misterio.",
        image_suffix=(
            "Moody neo-noir photorealistic film still, hard low-key lighting with sharp shadows, wet "
            "reflective surfaces, cold blue tones cut by a single warm or red light source, haze in the air, "
            "tense atmosphere, shallow depth of field."
        ),
        scene_guidance=(
            "Favor suspense over explicit action: silhouettes in doorways, faces half in shadow, hands, "
            "objects that hint at a secret, reflections in windows or mirrors. Refer to characters by a "
            "short role noun. Specify the light source and the shot type for every scene."
        ),
    ),
    "graphic_novel": VisualStyle(
        id="graphic_novel",
        label="Novela gráfica",
        description="Ilustración tipo cómic dramático; muy consistente entre escenas y sin efecto 'uncanny'.",
        image_suffix=(
            "Dramatic graphic novel illustration, bold confident ink lines, flat cel shading with painterly "
            "textures, limited palette of deep teal, crimson and warm skin tones, strong chiaroscuro, "
            "expressive faces, single panel composition."
        ),
        scene_guidance=(
            "Write each scene as a single comic panel: one clear emotional beat, expressive faces and body "
            "language, a simple readable background. Refer to characters by a short role noun. Specify the "
            "framing (close-up, medium shot, wide establishing shot) and keep speech bubbles out of frame."
        ),
    ),
    # ------------------------------------------------------------------ que_pasaria / historia / chisme (protagonista recurrente)
    "cine_realista": VisualStyle(
        id="cine_realista",
        label="Cine realista",
        description="Fotograma de película fotorrealista; versátil para escenarios, aventuras y cualquier ambiente.",
        image_suffix=(
            "Photorealistic cinematic film still, natural motivated lighting with soft contrast, true-to-life "
            "textures and skin detail, balanced realistic color grading, shallow depth of field, "
            "subtle film grain, high production value."
        ),
        scene_guidance=(
            "Describe ONE moment as if it were a frame from a feature film: the exact action, the character's "
            "expression and body language, the clothing or gear if it changes, the setting and time of day, "
            "and the shot type (wide establishing shot, medium shot, over-the-shoulder, low angle, close-up). "
            "Favor tangible, believable details and a single visual focus. Change the location and the "
            "lighting from scene to scene so the video never feels repetitive."
        ),
    ),
    "cine_apocaliptico": VisualStyle(
        id="cine_apocaliptico",
        label="Cine apocalíptico",
        description="Fotograma posapocalíptico fotorrealista; polvo, ruinas y colores apagados con acentos de fuego.",
        image_suffix=(
            "Photorealistic post-apocalyptic cinematic film still, drifting dust and ash, ruined crumbling "
            "structures and abandoned wreckage, desaturated earth tones with warm fire and ember accents, "
            "hazy shafts of sunlight, gritty textures, shallow depth of field, subtle film grain."
        ),
        scene_guidance=(
            "Show the collapse of the world around the moment: ruined buildings, abandoned vehicles, "
            "scavenged supplies, smoke, dust storms, makeshift shelters, improvised gear. State the exact "
            "action and the emotion of the character against the scale of the devastation (wide shots work "
            "great), the shot type and the light source (fire, embers, hazy sun, flashlight). No gore: imply "
            "danger with silhouettes, shadows, damage and the environment."
        ),
    ),
    "animacion_3d": VisualStyle(
        id="animacion_3d",
        label="Animación 3D",
        description="Look de película animada 3D estilizada; personajes expresivos, color vivo y luz cálida.",
        image_suffix=(
            "Stylized 3D animated feature film still, expressive characters with appealing exaggerated "
            "proportions, smooth skin and detailed fabric, rich saturated colors, soft cinematic lighting with "
            "warm rim light, polished materials, shallow depth of field."
        ),
        scene_guidance=(
            "Write each scene like a frame from an animated adventure: one clear action with an exaggerated, "
            "readable emotion on the character's face and body, a colorful and detailed environment with depth, "
            "and the shot type (wide establishing shot, medium shot, low angle, close-up). Favor dynamic poses "
            "and expressive reactions, and keep the background readable behind the subject."
        ),
    ),
}


def get_style(style_id: str) -> VisualStyle:
    """Devuelve el estilo por id; lanza ValueError con la lista disponible si no existe."""
    key = (style_id or "").strip().lower()
    if key not in STYLES:
        raise ValueError(f"Estilo desconocido '{style_id}'. Estilos disponibles: {', '.join(STYLES)}")
    return STYLES[key]
