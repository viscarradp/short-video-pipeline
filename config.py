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

# Subtitle styling
# Nombre que libass debe usar: el "family" legado del TTF (name ID 1) con Bold=0. Con "Montserrat" + Bold=-1 libass
# elegía una Montserrat-Bold del sistema (o negrita falsa) en lugar del archivo ExtraBold incluido en assets/fonts.
SUBTITLE_FONT_NAME = "Montserrat ExtraBold"
SUBTITLE_FONT_BOLD = 0  # el peso ya está en el nombre de la familia
SUBTITLE_FONT_FILENAME = "Montserrat-ExtraBold.ttf"
SUBTITLE_FONT_URL = "https://raw.githubusercontent.com/JulietaUla/Montserrat/master/fonts/ttf/Montserrat-ExtraBold.ttf"

# Audio: línea de tiempo exacta por muestras (1 fotograma = SAMPLES_PER_FRAME muestras, sin deriva)
AUDIO_SAMPLE_RATE = 48000
AUDIO_CHANNELS = 2  # estéreo en todo el pipeline (wav por escena y mezcla)
assert AUDIO_SAMPLE_RATE % VIDEO_FPS == 0, "AUDIO_SAMPLE_RATE debe ser múltiplo de VIDEO_FPS"
SAMPLES_PER_FRAME = AUDIO_SAMPLE_RATE // VIDEO_FPS  # 1600 a 48 kHz / 30 fps

# Audio volumes
SFX_VOLUME = 0.18
BGM_VOLUME = 0.18  # más alto que antes (0.10) porque el ducking la baja bajo la voz

# Ducking de la BGM bajo la voz (sidechaincompress, la voz es la cadena lateral)
DUCK_THRESHOLD = 0.03  # nivel lineal de la voz a partir del cual se comprime la BGM
DUCK_RATIO = 8  # cuánto se comprime lo que supera el umbral
DUCK_ATTACK_MS = 15  # rapidez con que baja la BGM al empezar a hablar
DUCK_RELEASE_MS = 350  # rapidez con que sube la BGM al callar la voz

# Normalización de volumen final (loudnorm de dos pasadas)
LOUDNORM_I = -14  # LUFS integrados objetivo
LOUDNORM_TP = -1.5  # techo de true peak (dBTP); el AAC añade algo de sobrepico
LOUDNORM_LRA = 11  # rango de sonoridad objetivo


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
