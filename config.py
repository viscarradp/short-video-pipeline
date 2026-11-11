"""Configuration module for the Short Video Pipeline."""

import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from dotenv import load_dotenv

# Base paths
BASE_DIR = Path(__file__).resolve().parent
ASSETS_DIR = BASE_DIR / "assets"
FONTS_DIR = ASSETS_DIR / "fonts"
AUDIO_DIR = ASSETS_DIR / "audio"
MUSIC_DIR = ASSETS_DIR / "music"  # biblioteca de música de fondo: subcarpeta por perfil + raíz como pool compartido
WORKSPACE_DIR = BASE_DIR / "workspace"
CHARACTERS_DIR = BASE_DIR / "characters"  # biblioteca de personajes persistentes: characters/<slug>/character.json + ref.png

# Load .env file
load_dotenv(BASE_DIR / ".env")

# Fal.ai API Key
FAL_KEY = os.getenv("FAL_KEY", "").strip()

# Personaje protagonista persistente (perfiles con uses_protagonist): id fijo que usa el guion en
# characters / characters_in_scene; su identidad visual sale de la imagen de referencia de la biblioteca.
PROTAGONIST_ID = "protagonist"

# Seedream v4.5 (ByteDance, vía Fal.ai): image_size {"width","height"} válido si AMBOS lados están en [1920, 4096] o si el
# total de píxeles está en [2560x1440, 4096x4096] (verificado en el OpenAPI de Fal). Se pide 2560x1440 / 1440x2560 y
# luego se recorta/escala al tamaño del formato.
SEEDREAM_MIN_SIDE, SEEDREAM_MAX_SIDE = 1920, 4096
SEEDREAM_MIN_PIXELS, SEEDREAM_MAX_PIXELS = 2560 * 1440, 4096 * 4096


def seedream_size_valid(size: dict) -> bool:
    """True si el image_size cumple las restricciones de Seedream v4.5 (ver arriba)."""
    w, h = int(size["width"]), int(size["height"])
    sides_ok = SEEDREAM_MIN_SIDE <= w <= SEEDREAM_MAX_SIDE and SEEDREAM_MIN_SIDE <= h <= SEEDREAM_MAX_SIDE
    pixels_ok = SEEDREAM_MIN_PIXELS <= w * h <= SEEDREAM_MAX_PIXELS
    return sides_ok or pixels_ok


# Formatos de video: toda la geometría que depende del aspecto (resolución, imagen Flux, subtítulos, gancho y textos
# del prompt) vive aquí. Los motores reciben el VideoFormat del proyecto (script.json -> video_format); no leen
# VIDEO_WIDTH / SUBTITLE_* directamente.
@dataclass(frozen=True)
class VideoFormat:
    id: str
    width: int  # resolución de salida (px); también PlayResX del .ass
    height: int  # PlayResY del .ass: los Fontsize del .ass están en estas unidades
    flux_image_size: dict  # tamaño que se pide a Flux (múltiplos de 16, <= ~1 megapíxel: Fal factura schnell por megapíxel)
    seedream_image_size: dict  # tamaño que se pide a Seedream v4.5 (proyectos con protagonista; ver seedream_size_valid)
    composition_hint: str  # inglés; sufijo de composición de cada prompt de imagen (zonas libres para gancho y subtítulos)
    platform_label_es: str  # español; destino del video en el prompt del guionista
    subtitle_font_size: int
    subtitle_outline: int
    subtitle_shadow: int
    subtitle_margin_lr: int  # márgenes laterales del .ass; también del estilo del gancho
    subtitle_max_line_width_px: int  # ninguna línea de un bloque debe superarlo (los bloques anchos se parten)
    subtitle_margin_v: int  # MarginV: distancia (px a PlayResY) del borde inferior del bloque al borde inferior del cuadro (alineación 2)
    subtitle_chunk_words: tuple[int, int]  # (mín, máx) palabras por bloque de subtítulos
    subtitle_pop_in_ms: int  # animación de aparición de cada bloque (escala 80 % -> 100 %); 0 = sin animación
    hook_font_size: int
    hook_pos_y_frac: float  # borde superior del texto gancho (fracción de la altura)
    hook_box_padding: int
    reach_hashtags: tuple[str, ...]  # etiquetas genéricas de alcance que se añaden al final de post_metadata.txt


FORMATS: dict[str, VideoFormat] = {
    # 9:16 (TikTok, Reels, Shorts). NO modificar: sus valores forman parte de las huellas de caché de los videos ya pagados.
    "vertical": VideoFormat(
        id="vertical",
        width=1080,
        height=1920,
        flux_image_size={"width": 768, "height": 1360},  # ~1 megapíxel, ~9:16
        seedream_image_size={"width": 1440, "height": 2560},  # 9:16 exacto: el recorte a 1080x1920 es solo un reescalado
        composition_hint=(
            "Vertical 9:16 frame, main subject centered with clear separation from the background, "
            "simple uncluttered top and bottom areas. No text, no letters, no captions, no logos, no watermark."
        ),
        platform_label_es="videos verticales en español (TikTok, Reels, Shorts)",
        subtitle_font_size=100,
        subtitle_outline=8,  # contorno grueso para leer sobre cualquier imagen
        subtitle_shadow=4,
        subtitle_margin_lr=90,  # (1080 - 2*90) = 900 px disponibles; libass ajusta el texto dentro de esos márgenes
        subtitle_max_line_width_px=900,
        subtitle_margin_v=620,  # centro-bajo: el bloque queda a ~64 % de la altura (zona segura; evita el 20 % inferior)
        subtitle_chunk_words=(2, 3),
        subtitle_pop_in_ms=80,  # pop-in corto al aparecer cada bloque
        hook_font_size=110,
        hook_pos_y_frac=0.15,  # evita el 12 % superior de la UI de la plataforma
        hook_box_padding=16,
        reach_hashtags=("#shorts", "#fyp", "#parati"),
    ),
    # 16:9 (YouTube). A PlayResY=1080 los Fontsize son más chicos que a 1920; subtítulos en el tercio inferior clásico (MarginV 90).
    "horizontal": VideoFormat(
        id="horizontal",
        width=1920,
        height=1080,
        flux_image_size={"width": 1280, "height": 720},  # 0.92 megapíxeles: queda bajo la unidad de facturación de 1 MP
        seedream_image_size={"width": 2560, "height": 1440},  # 16:9 exacto: el recorte a 1920x1080 es solo un reescalado
        composition_hint=(
            "Horizontal 16:9 widescreen cinematic frame, subject placed on the rule of thirds, "
            "leave empty headroom in the top fifth and keep the lower fifth simple. No text, no letters, no captions, no logos, no watermark."
        ),
        platform_label_es="videos horizontales en español para YouTube (formato 16:9, pantalla ancha)",
        subtitle_font_size=56,  # subtítulo clásico de YouTube: caben bloques de 5 a 7 palabras en una línea de <= 1480 px
        subtitle_outline=4,
        subtitle_shadow=2,
        subtitle_margin_lr=200,  # (1920 - 2*200) = 1520 px disponibles
        subtitle_max_line_width_px=1480,
        subtitle_margin_v=90,  # tercio inferior clásico de YouTube: ~90 px del borde inferior
        subtitle_chunk_words=(5, 7),
        subtitle_pop_in_ms=0,  # subtítulos clásicos: el bloque aparece sin animación
        hook_font_size=84,
        hook_pos_y_frac=0.08,
        hook_box_padding=12,
        reach_hashtags=(),  # en YouTube horizontal las etiquetas genéricas no aportan alcance
    ),
}
DEFAULT_FORMAT = "vertical"
for _fmt in FORMATS.values():
    assert seedream_size_valid(_fmt.seedream_image_size), f"seedream_image_size inválido en el formato {_fmt.id}"


def get_format(format_id: str) -> VideoFormat:
    """Devuelve el formato por id; lanza ValueError con la lista disponible si no existe."""
    key = (format_id or "").strip().lower()
    if key not in FORMATS:
        raise ValueError(f"Formato desconocido '{format_id}'. Formatos disponibles: {', '.join(FORMATS)}")
    return FORMATS[key]


# Alias de compatibilidad: los valores del formato vertical (los motores usan el VideoFormat que reciben, no estos).
_VERTICAL = FORMATS["vertical"]

# Video output specifications
VIDEO_WIDTH = _VERTICAL.width
VIDEO_HEIGHT = _VERTICAL.height
VIDEO_FPS = 30
VIDEO_CRF = 20
VIDEO_PIX_FMT = "yuv420p"

# Guion: presupuesto de palabras por escena
# Palabras por segundo de una voz neutra a velocidad normal (+0%); se escala por el voice_rate del perfil.
WORDS_PER_SEC_BASE = 2.7
SCENE_WORDS_TOLERANCE = 3  # el LLM puede desviarse +-3 palabras del presupuesto de cada escena (se usa en el prompt; los shorts verticales fijan sus límites en profiles.SHORT_SCENE_WORDS y se validan por código)

# Efectos de cámara permitidos (Ken Burns). ÚNICA fuente de verdad: los usan el prompt del guionista
# (script_engine.build_system_prompt) y el despachador de zoompan (video_engine.build_zoompan_filter).
CAMERA_EFFECTS = ("zoom_in_fast", "zoom_in_slow", "zoom_out", "pan_right", "pan_left")
DEFAULT_CAMERA_EFFECT = "zoom_in_slow"  # respaldo si un guion trae un efecto desconocido

# Metadatos de publicación: etiquetas genéricas de alcance que siempre se añaden al final (mantenerlas pocas: 2-3)
REACH_HASHTAGS = list(_VERTICAL.reach_hashtags)  # alias de compatibilidad; usar VideoFormat.reach_hashtags
MAX_HASHTAGS = 8  # tope de hashtags temáticos del guion

# Subtitle styling
# Nombre que libass debe usar: el "family" legado del TTF (name ID 1) con Bold=0. Con "Montserrat" + Bold=-1 libass
# elegía una Montserrat-Bold del sistema (o negrita falsa) en lugar del archivo ExtraBold incluido en assets/fonts.
SUBTITLE_FONT_NAME = "Montserrat ExtraBold"
SUBTITLE_FONT_BOLD = 0  # el peso ya está en el nombre de la familia
SUBTITLE_FONT_FILENAME = "Montserrat-ExtraBold.ttf"
SUBTITLE_FONT_URL = "https://raw.githubusercontent.com/JulietaUla/Montserrat/master/fonts/ttf/Montserrat-ExtraBold.ttf"

# Tamaño y geometría: libass renderiza Fontsize=100 con ~65 px de em en esta fuente (medido con ffmpeg/libass).
# Lo que depende del formato (tamaño, contorno, márgenes, posición) está en FORMATS; aquí solo lo común a todos.
SUBTITLE_LIBASS_EM_RATIO = 0.68  # px de em por unidad de Fontsize: 0.65 medido + ~5 % de margen; solo para estimar anchos
SUBTITLE_SPACING = 2
# Colores ASS (&HBBGGRR&): palabra activa del karaoke, palabra clave y texto normal
SUBTITLE_NORMAL_COLOR = "&H00FFFFFF&"  # blanco
SUBTITLE_ACTIVE_COLOR = "&H0000FFFF&"  # amarillo
SUBTITLE_KEYWORD_COLOR = "&H00FFFF00&"  # cian (si una palabra clave es la activa gana el amarillo)
SUBTITLE_ACTIVE_SCALE = 112  # % de escala de la palabra activa (pop); el resto va a 100
# Texto gancho (hook): arriba, en mayúsculas, caja de color
HOOK_MAX_SEC = 3.0  # el gancho dura hasta el fin de la escena 1 o esto, lo que sea menor
HOOK_TEXT_COLOR = "&H00FFFFFF&"
HOOK_BOX_COLOR = "&H003C1EE6&"  # caja carmesí

# Alias de compatibilidad con el formato vertical (ver FORMATS)
SUBTITLE_FONT_SIZE = _VERTICAL.subtitle_font_size
SUBTITLE_OUTLINE = _VERTICAL.subtitle_outline
SUBTITLE_SHADOW = _VERTICAL.subtitle_shadow
SUBTITLE_MARGIN_LR = _VERTICAL.subtitle_margin_lr
SUBTITLE_MAX_LINE_WIDTH_PX = _VERTICAL.subtitle_max_line_width_px
SUBTITLE_MARGIN_V = _VERTICAL.subtitle_margin_v
HOOK_FONT_SIZE = _VERTICAL.hook_font_size
HOOK_POS_Y_FRAC = _VERTICAL.hook_pos_y_frac
HOOK_BOX_PADDING = _VERTICAL.hook_box_padding

# Imágenes (Flux schnell): el tamaño de cada formato está en FORMATS (flux_image_size; múltiplos de 16, <= ~1 megapíxel:
# Fal factura schnell por megapíxel). Alias del formato vertical:
FLUX_IMAGE_SIZE = _VERTICAL.flux_image_size
FLUX_COST_USD = 0.003  # costo estimado por imagen Flux schnell (~1 megapíxel)
FLUX_IMAGE_SIZE_LEGACY = "portrait_16_9"  # 576x1024; las imágenes pagadas con este tamaño se siguen aceptando en caché

# Endpoints de imagen de Fal.ai y su costo por imagen (USD). Los videos SIN protagonista usan Flux schnell; los que tienen
# protagonista usan Seedream v4.5 en todas las escenas (look coherente): edición con la imagen de referencia del personaje
# en las escenas donde aparece, texto a imagen en el resto. Seedream cobra un precio fijo por imagen en ambos endpoints.
FLUX_ENDPOINT = "fal-ai/flux/schnell"
SEEDREAM_EDIT_ENDPOINT = "fal-ai/bytedance/seedream/v4.5/edit"
SEEDREAM_T2I_ENDPOINT = "fal-ai/bytedance/seedream/v4.5/text-to-image"
SEEDREAM_COST_USD = 0.04
# Flux dev (gancho del tier 'hybrid'): parámetros verificados en la API de Fal (image_size, num_inference_steps=28 por
# defecto, guidance_scale=3.5 por defecto, enable_safety_checker=true por defecto). Fal lo factura por megapíxel de salida
# ($0.025/MP) REDONDEANDO HACIA ARRIBA al megapíxel entero (1 MP = 1.000.000 px, estimación conservadora): 1280x720
# (0.92 MP) = $0.025; 768x1360 (1.04 MP, vertical) = 2 MP = $0.05. No se pudo confirmar la regla exacta en la página de
# precios de Fal; si Fal midiera 1 MP = 1.048.576 px, el vertical costaría $0.025 y esta estimación quedaría por encima.
FLUX_DEV_ENDPOINT = "fal-ai/flux/dev"
FLUX_DEV_STEPS = 28
FLUX_DEV_GUIDANCE_SCALE = 3.5
FLUX_DEV_COST_PER_MP_USD = 0.025
HYBRID_DEV_SCENES = frozenset({1, 2})  # tier 'hybrid': estas escenas (el gancho) usan Flux dev; el resto, schnell

# Imágenes reutilizadas del pack local del personaje (tier character-hybrid + --use-local-pack): no llaman a la API, $0.
LOCAL_PACK_ENDPOINT = "local/character-pack"

# Tiers de imagen (--image-tier). 'legacy' = comportamiento anterior (schnell en todo / Seedream en todo con protagonista):
# es el que usan los proyectos existentes sin tier guardado en run_config.json, para no invalidar imágenes ya pagadas.
IMAGE_TIER_FAST = "fast"
IMAGE_TIER_HYBRID = "hybrid"
IMAGE_TIER_CHARACTER_HYBRID = "character-hybrid"
IMAGE_TIER_LEGACY = "legacy"
IMAGE_TIERS = (IMAGE_TIER_FAST, IMAGE_TIER_HYBRID, IMAGE_TIER_CHARACTER_HYBRID, IMAGE_TIER_LEGACY)

# Precio por imagen de los endpoints de precio fijo. Flux dev depende del tamaño: ver image_unit_cost_usd. Los resúmenes de
# costo usan RunStats.paid_usd (suma exacta por endpoint, con el precio de cada imagen).
IMAGE_COST_USD: dict[str, float] = {
    FLUX_ENDPOINT: FLUX_COST_USD,
    FLUX_DEV_ENDPOINT: FLUX_DEV_COST_PER_MP_USD,  # por megapíxel; con tamaño usa flux_dev_cost_usd
    SEEDREAM_EDIT_ENDPOINT: SEEDREAM_COST_USD,
    SEEDREAM_T2I_ENDPOINT: SEEDREAM_COST_USD,
    LOCAL_PACK_ENDPOINT: 0.0,
}


def flux_dev_cost_usd(image_size: dict) -> float:
    """Costo de una imagen Flux dev: $0.025 x megapíxeles del tamaño pedido, redondeados hacia arriba (mínimo 1 MP).

    Fal cuenta 1 MP = 1024x1024 px: 768x1360 (1 044 480 px) y 1280x720 quedan en 1 MP = $0.025."""
    megapixels = max(1, -(-int(image_size["width"]) * int(image_size["height"]) // (1024 * 1024)))
    return megapixels * FLUX_DEV_COST_PER_MP_USD


def image_unit_cost_usd(endpoint: str, image_size: dict | None = None) -> float:
    """Precio de UNA imagen del endpoint (Flux dev según el tamaño pedido; el resto, precio fijo)."""
    if endpoint == FLUX_DEV_ENDPOINT and image_size is not None:
        return flux_dev_cost_usd(image_size)
    return IMAGE_COST_USD.get(endpoint, 0.0)

# Audio: línea de tiempo exacta por muestras (1 fotograma = SAMPLES_PER_FRAME muestras, sin deriva)
AUDIO_SAMPLE_RATE = 48000
AUDIO_CHANNELS = 2  # estéreo en todo el pipeline (wav por escena y mezcla)
assert AUDIO_SAMPLE_RATE % VIDEO_FPS == 0, "AUDIO_SAMPLE_RATE debe ser múltiplo de VIDEO_FPS"
SAMPLES_PER_FRAME = AUDIO_SAMPLE_RATE // VIDEO_FPS  # 1600 a 48 kHz / 30 fps

# Audio volumes
SFX_VOLUME = 0.18
BGM_VOLUME = 0.18  # ganancia lineal de respaldo: solo se usa si no se puede medir la sonoridad de la música o la voz
# Nivel de la música SIN ducking respecto a la voz (LUFS integrados, música - voz). Es una perilla creativa:
# -18 = música discreta; -12 = más presente; -24 = casi imperceptible. La ganancia se calcula midiendo ambas pistas,
# porque las canciones vienen masterizadas a sonoridades muy distintas.
BGM_RELATIVE_DB = -18
BGM_GAIN_LIMITS_DB = (-40.0, 30.0)  # tope de seguridad de la ganancia calculada
MUSIC_EXTENSIONS = (".mp3", ".m4a", ".wav")

# Ducking de la BGM bajo la voz (sidechaincompress, la voz es la cadena lateral)
DUCK_THRESHOLD = 0.03  # nivel lineal de la voz a partir del cual se comprime la BGM
DUCK_RATIO = 8  # cuánto se comprime lo que supera el umbral
DUCK_ATTACK_MS = 15  # rapidez con que baja la BGM al empezar a hablar
DUCK_RELEASE_MS = 350  # rapidez con que sube la BGM al callar la voz

# Normalización de volumen final (loudnorm de dos pasadas)
LOUDNORM_I = -14  # LUFS integrados objetivo
LOUDNORM_TP = -1.5  # techo de true peak (dBTP); el AAC añade algo de sobrepico
LOUDNORM_LRA = 11  # rango de sonoridad objetivo


# Render de clips en paralelo (subprocesos ffmpeg)
def _default_clip_workers() -> int:
    """Variable de entorno CLIP_RENDER_WORKERS si es un entero >= 1; si no, max(1, min(4, cpus // 2))."""
    try:
        return max(1, int(os.getenv("CLIP_RENDER_WORKERS", "")))
    except ValueError:
        return max(1, min(4, (os.cpu_count() or 2) // 2))


CLIP_RENDER_WORKERS = _default_clip_workers()


def get_ffmpeg_binary() -> str:
    """Find the best ffmpeg binary available (prioritizing ffmpeg-full with libass)."""
    # Check custom env var if user set it
    custom_ffmpeg = os.getenv("FFMPEG_PATH")
    if custom_ffmpeg and os.path.exists(custom_ffmpeg) and os.access(custom_ffmpeg, os.X_OK):
        return custom_ffmpeg

    # Check ffmpeg-full from Homebrew (keg-only)
    brew_full = Path("/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg")
    if brew_full.exists() and os.access(brew_full, os.X_OK):
        return str(brew_full)

    # Check /usr/local/opt/ffmpeg-full (Intel Mac)
    intel_full = Path("/usr/local/opt/ffmpeg-full/bin/ffmpeg")
    if intel_full.exists() and os.access(intel_full, os.X_OK):
        return str(intel_full)

    # Fall back to PATH
    system_ffmpeg = shutil.which("ffmpeg")
    if system_ffmpeg:
        return system_ffmpeg

    return "ffmpeg"


def get_ffprobe_binary() -> str:
    """Find the best ffprobe binary available."""
    custom_ffprobe = os.getenv("FFPROBE_PATH")
    if custom_ffprobe and os.path.exists(custom_ffprobe) and os.access(custom_ffprobe, os.X_OK):
        return custom_ffprobe

    brew_full = Path("/opt/homebrew/opt/ffmpeg-full/bin/ffprobe")
    if brew_full.exists() and os.access(brew_full, os.X_OK):
        return str(brew_full)

    intel_full = Path("/usr/local/opt/ffmpeg-full/bin/ffprobe")
    if intel_full.exists() and os.access(intel_full, os.X_OK):
        return str(intel_full)

    system_ffprobe = shutil.which("ffprobe")
    if system_ffprobe:
        return system_ffprobe

    return "ffprobe"
