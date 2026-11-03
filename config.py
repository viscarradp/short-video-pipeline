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
SUBTITLE_FONT_NAME = "Montserrat"
SUBTITLE_FONT_FILENAME = "Montserrat-ExtraBold.ttf"
SUBTITLE_FONT_URL = "https://raw.githubusercontent.com/JulietaUla/Montserrat/master/fonts/ttf/Montserrat-ExtraBold.ttf"

# Audio volumes
SFX_VOLUME = 0.18
BGM_VOLUME = 0.10


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
