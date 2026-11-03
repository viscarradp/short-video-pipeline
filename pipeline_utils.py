"""Utilidades compartidas del pipeline: error tipado y ejecución de comandos externos."""

import subprocess
from pathlib import Path


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


def is_stale(target: Path, *inputs: Path) -> bool:
    """True si target no existe o algún input existente es más nuevo (mtime) que target."""
    if not target.exists():
        return True
    target_mtime = target.stat().st_mtime
    return any(p.exists() and p.stat().st_mtime > target_mtime for p in inputs)
