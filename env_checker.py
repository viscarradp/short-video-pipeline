"""Environment and Asset Checker (Fase 0)."""

import os
import shutil
import urllib.request
from pathlib import Path
from rich.console import Console

from pipeline_utils import run_cmd
from config import (
    ASSETS_DIR,
    FONTS_DIR,
    AUDIO_DIR,
    MUSIC_DIR,
    WORKSPACE_DIR,
    SUBTITLE_FONT_FILENAME,
    SUBTITLE_FONT_URL,
    get_ffmpeg_binary,
    get_ffprobe_binary,
)

console = Console()


def verify_ffmpeg_binary(ffmpeg_bin: str) -> bool:
    """Check if ffmpeg executable exists and runs."""
    try:
        res = run_cmd([ffmpeg_bin, "-version"], "ffmpeg -version")
        return "ffmpeg version" in res.stdout
    except Exception:
        return False


def verify_ffprobe_binary(ffprobe_bin: str) -> bool:
    """Check if ffprobe executable exists and runs."""
    try:
        res = run_cmd([ffprobe_bin, "-version"], "ffprobe -version")
        return "ffprobe version" in res.stdout
    except Exception:
        return False


def verify_libass_support(ffmpeg_bin: str) -> bool:
    """Check if ffmpeg has subtitles/ass filter support."""
    try:
        res = run_cmd([ffmpeg_bin, "-filters"], "ffmpeg -filters")
        has_sub = " subtitles " in res.stdout or " ass " in res.stdout
        return has_sub
    except Exception:
        return False


def ensure_directories() -> None:
    """Create all required project directories."""
    from profiles import PROFILES  # import local: evita ciclos y solo se necesita aquí

    music_dirs = [MUSIC_DIR] + [MUSIC_DIR / name for name in PROFILES]  # raíz = pool compartido + una carpeta por perfil
    for directory in [ASSETS_DIR, FONTS_DIR, AUDIO_DIR, WORKSPACE_DIR, *music_dirs]:
        directory.mkdir(parents=True, exist_ok=True)


def ensure_font_asset() -> Path:
    """Download required bold font if not present."""
    font_path = FONTS_DIR / SUBTITLE_FONT_FILENAME
    if font_path.exists() and font_path.stat().st_size > 1000:
        return font_path

    console.print(f"[yellow]Downloading font: {SUBTITLE_FONT_FILENAME}...[/yellow]")
    headers = {"User-Agent": "ShortVideoPipeline/1.0"}
    req = urllib.request.Request(SUBTITLE_FONT_URL, headers=headers)
    tmp_path = font_path.with_suffix(font_path.suffix + ".tmp")
    with urllib.request.urlopen(req, timeout=30) as resp, open(tmp_path, "wb") as f:
        shutil.copyfileobj(resp, f)
    os.replace(tmp_path, font_path)

    console.print(f"[green]✓ Font saved to {font_path}[/green]")
    return font_path


def ensure_synthetic_audio_assets(ffmpeg_bin: str) -> tuple[Path, Path]:
    """Generate synthetic transition whoosh and ambient background music if missing."""
    whoosh_path = AUDIO_DIR / "whoosh.mp3"
    bgm_path = AUDIO_DIR / "ambient_drone.mp3"

    # Generate whoosh SFX if missing
    if not whoosh_path.exists() or whoosh_path.stat().st_size < 1000:
        console.print("[yellow]Generating synthetic transition whoosh SFX...[/yellow]")
        cmd_whoosh = [
            ffmpeg_bin,
            "-y",
            "-f",
            "lavfi",
            "-i",
            "anoisesrc=d=0.7:c=white:r=44100,lowpass=f=800,volume=2.0,afade=t=in:ss=0:d=0.25,afade=t=out:st=0.25:d=0.45",
            "-q:a",
            "2",
            str(whoosh_path),
        ]
        run_cmd(cmd_whoosh, "Generación del SFX whoosh")
        console.print(f"[green]✓ Generated {whoosh_path}[/green]")

    # Generate ambient background music if missing
    if not bgm_path.exists() or bgm_path.stat().st_size < 1000:
        console.print("[yellow]Generating synthetic ambient background drone...[/yellow]")
        cmd_bgm = [
            ffmpeg_bin,
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=65:duration=90,volume=0.2[s1];sine=frequency=130:duration=90,volume=0.1[s2];anoisesrc=d=90:c=pink:r=44100,lowpass=f=300,volume=0.15[n];[s1][s2]amix=inputs=2[s];[s][n]amix=inputs=2,afade=t=in:ss=0:d=2,afade=t=out:st=88:d=2",
            "-q:a",
            "2",
            str(bgm_path),
        ]
        run_cmd(cmd_bgm, "Generación de la música ambiental")
        console.print(f"[green]✓ Generated {bgm_path}[/green]")

    return whoosh_path, bgm_path


def check_and_prepare_environment() -> dict[str, str]:
    """Verify entire environment, report status, and prepare all default assets."""
    ensure_directories()

    ffmpeg_bin = get_ffmpeg_binary()
    ffprobe_bin = get_ffprobe_binary()

    if not verify_ffmpeg_binary(ffmpeg_bin):
        raise RuntimeError(
            f"FFmpeg binary not found or non-functional at '{ffmpeg_bin}'. Please install FFmpeg."
        )

    if not verify_ffprobe_binary(ffprobe_bin):
        raise RuntimeError(
            f"FFprobe binary not found or non-functional at '{ffprobe_bin}'. Please install FFprobe."
        )

    if not verify_libass_support(ffmpeg_bin):
        raise RuntimeError(
            f"FFmpeg binary at '{ffmpeg_bin}' does NOT have 'subtitles' or 'ass' (libass) filter enabled. "
            "Please install ffmpeg-full or compile with --enable-libass."
        )

    ensure_font_asset()
    ensure_synthetic_audio_assets(ffmpeg_bin)

    return {
        "ffmpeg": ffmpeg_bin,
        "ffprobe": ffprobe_bin,
        "font": str(FONTS_DIR / SUBTITLE_FONT_FILENAME),
        "whoosh": str(AUDIO_DIR / "whoosh.mp3"),
        "bgm": str(AUDIO_DIR / "ambient_drone.mp3"),
        "music_dir": str(MUSIC_DIR),
    }


if __name__ == "__main__":
    status = check_and_prepare_environment()
    console.print("\n[bold green]Environment is 100% ready![/bold green]")
    for k, v in status.items():
        console.print(f"  [cyan]{k}[/cyan]: {v}")
