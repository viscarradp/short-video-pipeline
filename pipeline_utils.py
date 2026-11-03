"""Utilidades compartidas del pipeline: error tipado, ejecución de comandos y caché por huella de contenido."""

import hashlib
import json
import os
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from config import AUDIO_SAMPLE_RATE, SAMPLES_PER_FRAME, VIDEO_FPS

# Se incluye en TODA huella: súbelo cuando cambie el formato de los artefactos para invalidar la caché completa.
CACHE_VERSION = 1

MANIFEST_FILENAME = "cache_manifest.json"


class PipelineError(Exception):
    """Error controlado del pipeline (se muestra al usuario sin traceback)."""


def run_cmd(cmd: list[str], desc: str, **kwargs) -> subprocess.CompletedProcess:
    """Ejecuta un comando capturando salida; lanza PipelineError si falla o no existe el binario."""
    try:
        res = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            **kwargs,
        )
    except FileNotFoundError as e:
        raise PipelineError(f"{desc} falló: no se encontró el ejecutable '{cmd[0]}' ({e}).") from e
    except OSError as e:
        raise PipelineError(f"{desc} falló: no se pudo ejecutar '{cmd[0]}' ({e}).") from e

    if res.returncode != 0:
        stderr_tail = (res.stderr or "")[-2000:]
        raise PipelineError(f"{desc} falló (código {res.returncode}):\n{stderr_tail}")
    return res


# --------------------------------------------------------------------------- #
# Huellas de contenido y manifiesto de caché
# --------------------------------------------------------------------------- #

def file_sha256(path: Path) -> str:
    """SHA-256 del contenido del archivo (lectura en bloques de 1 MB); 'missing' si no existe."""
    p = Path(path)
    if not p.is_file():
        return "missing"
    digest = hashlib.sha256()
    try:
        with open(p, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        return "missing"
    return digest.hexdigest()


def _json_default(value: Any) -> Any:
    if isinstance(value, Path):
        return file_sha256(value)
    raise TypeError(f"Valor no serializable en huella: {type(value).__name__}")


def fingerprint(**parts: Any) -> str:
    """SHA-256 de un JSON canónico de las partes. Los valores Path se reemplazan por el hash de su contenido."""
    payload = {"_cache_version": CACHE_VERSION, **parts}
    canonical = json.dumps(
        payload,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        default=_json_default,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class CacheManifest:
    """Mapa artefacto -> huella en workspace/<slug>/cache_manifest.json (claves relativas, estilo posix)."""

    def __init__(self, workspace: Path):
        self.workspace = Path(workspace)
        self.path = self.workspace / MANIFEST_FILENAME
        self._data: dict[str, str] = self._load()

    def _load(self) -> dict[str, str]:
        if not self.path.exists():
            return {}
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            if isinstance(raw, dict):
                return {str(k): str(v) for k, v in raw.items()}
        except Exception:
            pass
        return {}  # manifiesto corrupto: se reconstruye (todo se considera obsoleto)

    def _key(self, path: Path) -> str:
        p = Path(path)
        base = self.workspace.resolve()
        try:
            rel = p.resolve().relative_to(base)
        except ValueError as e:
            raise ValueError(f"{p} no está dentro del workspace {base}") from e
        return rel.as_posix()

    def get(self, path: Path) -> Optional[str]:
        return self._data.get(self._key(path))

    def has(self, path: Path) -> bool:
        return self._key(path) in self._data

    def is_fresh(self, path: Path, fp: str) -> bool:
        """True si el archivo existe Y la huella registrada coincide."""
        return Path(path).exists() and self._data.get(self._key(path)) == fp

    def record(self, path: Path, fp: str) -> None:
        self._data[self._key(path)] = fp
        self._save()

    def _save(self) -> None:
        self.workspace.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(self._data, indent=2, sort_keys=True, ensure_ascii=False), encoding="utf-8")
        for attempt in range(5):
            try:
                os.replace(tmp, self.path)
                return
            except PermissionError:
                # Windows: antivirus/OneDrive pueden bloquear brevemente el destino
                if attempt == 4:
                    raise
                time.sleep(0.1 * (attempt + 1))


@dataclass
class RunStats:
    """Qué se regeneró realmente en una ejecución (los cachés no aparecen aquí)."""

    script: bool = False
    audio: list[int] = field(default_factory=list)
    images: list[int] = field(default_factory=list)
    clips: list[int] = field(default_factory=list)
    mix: bool = False
    final: bool = False


# --------------------------------------------------------------------------- #
# Línea de tiempo exacta (fotogramas <-> muestras de audio)
# --------------------------------------------------------------------------- #

def frames_for_samples(samples: int, sample_rate: int = AUDIO_SAMPLE_RATE, fps: int = VIDEO_FPS) -> int:
    """Fotogramas de video que cubren `samples` muestras de audio: ceil(samples * fps / sample_rate), mínimo 1."""
    return max(-(-samples * fps // sample_rate), 1)


def scene_frames(scene: dict[str, Any], fps: int = VIDEO_FPS) -> int:
    """Nº de fotogramas de una escena del timing_metadata (con respaldo desde duration_sec)."""
    frames = scene.get("frames")
    if frames:
        return int(frames)
    return max(round(float(scene.get("duration_sec", 3.0)) * fps), 1)


def scene_start_frames(scenes: list[dict[str, Any]], fps: int = VIDEO_FPS) -> list[int]:
    """Fotograma de inicio acumulado de cada escena."""
    starts: list[int] = []
    acc = 0
    for sc in scenes:
        starts.append(acc)
        acc += scene_frames(sc, fps)
    return starts
