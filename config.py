"""Configuration module for the Short Video Pipeline."""

import os
import shutil
from pathlib import Path
from dotenv import load_dotenv

# Base paths
BASE_DIR = Path(__file__).resolve().parent
ASSETS_DIR = BASE_DIR / "assets"
FONTS_DIR = ASSETS_DIR / "fonts"
AUDIO_DIR = ASSETS_DIR / "audio"
MUSIC_DIR = ASSETS_DIR / "music"  # biblioteca de música de fondo: subcarpeta por perfil + raíz como pool compartido
WORKSPACE_DIR = BASE_DIR / "workspace"

# Load .env file
load_dotenv(BASE_DIR / ".env")

# Fal.ai API Key
FAL_KEY = os.getenv("FAL_KEY", "").strip()

# Video output specifications
VIDEO_WIDTH = 1080
VIDEO_HEIGHT = 1920
VIDEO_FPS = 30
VIDEO_CRF = 20
VIDEO_PIX_FMT = "yuv420p"

# Guion: presupuesto de palabras por escena
# Palabras por segundo de una voz neutra a velocidad normal (+0%); se escala por el voice_rate del perfil.
WORDS_PER_SEC_BASE = 2.7
SCENE_WORDS_TOLERANCE = 3  # el LLM puede desviarse +-3 palabras del presupuesto de cada escena

# Efectos de cámara permitidos (Ken Burns). ÚNICA fuente de verdad: los usan el prompt del guionista
# (script_engine.build_system_prompt) y el despachador de zoompan (video_engine.build_zoompan_filter).
CAMERA_EFFECTS = ("zoom_in_fast", "zoom_in_slow", "zoom_out", "pan_right", "pan_left")
DEFAULT_CAMERA_EFFECT = "zoom_in_slow"  # respaldo si un guion trae un efecto desconocido

# Metadatos de publicación: etiquetas genéricas de alcance que siempre se añaden al final (mantenerlas pocas: 2-3)
REACH_HASHTAGS = ["#shorts", "#fyp", "#parati"]
MAX_HASHTAGS = 8  # tope de hashtags temáticos del guion

# Subtitle styling
# Nombre que libass debe usar: el "family" legado del TTF (name ID 1) con Bold=0. Con "Montserrat" + Bold=-1 libass
# elegía una Montserrat-Bold del sistema (o negrita falsa) en lugar del archivo ExtraBold incluido en assets/fonts.
SUBTITLE_FONT_NAME = "Montserrat ExtraBold"
SUBTITLE_FONT_BOLD = 0  # el peso ya está en el nombre de la familia
SUBTITLE_FONT_FILENAME = "Montserrat-ExtraBold.ttf"
SUBTITLE_FONT_URL = "https://raw.githubusercontent.com/JulietaUla/Montserrat/master/fonts/ttf/Montserrat-ExtraBold.ttf"

# Tamaño y geometría (a PlayResX=1080). libass renderiza Fontsize=100 con ~65 px de em en esta fuente (medido con ffmpeg/libass).
SUBTITLE_LIBASS_EM_RATIO = 0.68  # px de em por unidad de Fontsize: 0.65 medido + ~5 % de margen; solo para estimar anchos
SUBTITLE_FONT_SIZE = 100
SUBTITLE_OUTLINE = 8  # contorno grueso para leer sobre cualquier imagen
SUBTITLE_SHADOW = 4
SUBTITLE_SPACING = 2
SUBTITLE_MARGIN_LR = 90  # (1080 - 2*90) = 900 px disponibles; libass ajusta el texto dentro de esos márgenes
SUBTITLE_MAX_LINE_WIDTH_PX = 900  # ninguna línea de un bloque debe superarlo: los bloques anchos se parten en bloques más chicos
SUBTITLE_POS_Y_FRAC = 0.64  # centro vertical de los subtítulos (zona segura: 60-68 % de la altura; evita el 20 % inferior)
# Colores ASS (&HBBGGRR&): palabra activa del karaoke, palabra clave y texto normal
SUBTITLE_NORMAL_COLOR = "&H00FFFFFF&"  # blanco
SUBTITLE_ACTIVE_COLOR = "&H0000FFFF&"  # amarillo
SUBTITLE_KEYWORD_COLOR = "&H00FFFF00&"  # cian (si una palabra clave es la activa gana el amarillo)
SUBTITLE_ACTIVE_SCALE = 112  # % de escala de la palabra activa (pop); el resto va a 100
# Texto gancho (hook): arriba, en mayúsculas, caja de color
HOOK_FONT_SIZE = 110
HOOK_POS_Y_FRAC = 0.15  # borde superior del texto gancho (15 % de la altura; evita el 12 % superior de la UI)
HOOK_MAX_SEC = 3.0  # el gancho dura hasta el fin de la escena 1 o esto, lo que sea menor
HOOK_TEXT_COLOR = "&H00FFFFFF&"
HOOK_BOX_COLOR = "&H003C1EE6&"  # caja carmesí
HOOK_BOX_PADDING = 16

# Imágenes (Flux schnell): ~1 megapíxel con aspecto ~9:16 (ambos lados múltiplos de 16). Fal factura schnell por megapíxel.
FLUX_IMAGE_SIZE = {"width": 768, "height": 1360}
FLUX_COST_USD = 0.003  # costo estimado por imagen Flux schnell (~1 megapíxel)
FLUX_IMAGE_SIZE_LEGACY = "portrait_16_9"  # 576x1024; las imágenes pagadas con este tamaño se siguen aceptando en caché

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
