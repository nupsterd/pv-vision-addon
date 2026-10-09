"""``ffprobe`` de un archivo de video: validez, duración y resolución.

Lo usan el grabador (descartar segmentos sin video al cerrar una sesión) y el análisis
(saltear clips inválidos o de menos de 1 s).
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

MIN_DURACION_S = 1.0
FFPROBE_TIMEOUT = 20


@dataclass(frozen=True)
class InfoVideo:
    duracion: float
    ancho: int
    alto: int
    codec: str
    fps: float | None = None


def probar(path: str | Path) -> InfoVideo | None:
    """``InfoVideo`` del primer stream de video, o ``None`` si el archivo no es un video válido."""
    try:
        out = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=codec_name,width,height,avg_frame_rate,r_frame_rate:format=duration",
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=FFPROBE_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    try:
        d = json.loads(out.stdout)
        st = d["streams"][0]
        fps = _fraccion(st.get("avg_frame_rate")) or _fraccion(st.get("r_frame_rate"))
        return InfoVideo(
            float(d["format"]["duration"]), int(st["width"]), int(st["height"]), str(st["codec_name"]), fps
        )
    except (ValueError, KeyError, IndexError, TypeError):
        return None


def _fraccion(txt: str | None) -> float | None:
    try:
        num, _, den = (txt or "").partition("/")
        val = float(num) / float(den or 1)
    except (ValueError, ZeroDivisionError):
        return None
    return val if val > 0 else None


def motivo_descarte(info: InfoVideo | None, min_s: float = MIN_DURACION_S) -> str | None:
    """Por qué un archivo no sirve como video (``None`` = sirve)."""
    if info is None:
        return "sin video válido"
    if info.duracion < min_s:
        return f"dura {info.duracion:.2f} s (< {min_s:g} s)"
    return None
