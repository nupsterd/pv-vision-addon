"""Decodificación de un clip con ffmpeg hijo → cuadros listos para el modelo por tubería.

``-threads 2`` decodifica el H.265 (hay que decodificar los 25 fps: los P dependen del
anterior); ``crop`` al ROI, ``select`` 1 de cada N, ``showinfo`` (da el ``pts`` de cada cuadro
que sale), ``scale`` + ``pad`` al tamaño del modelo y ``rawvideo`` en el formato del modelo.
Los ``pts`` se leen del stderr en un hilo y se emparejan en orden con los cuadros.
"""

from __future__ import annotations

import logging
import queue
import re
import subprocess
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from pv_vision.analisis.geometria import Mapeo

log = logging.getLogger("pv_vision.analisis.decodificar")

GRIS_YOLOX = "0x727272"  # 114, el relleno del preproceso oficial de YOLOX
_PTS_RE = re.compile(r"\bn:\s*\d+\s+pts:\s*-?\d+\s+pts_time:(-?[\d.]+)")


def filtro(mapeo: Mapeo, cada_n: int) -> str:
    r = mapeo.roi
    partes = [f"crop={r.w}:{r.h}:{r.x}:{r.y}"]
    if cada_n > 1:
        partes.append(f"select=not(mod(n\\,{cada_n}))")
    partes.append("showinfo")
    nw, nh = mapeo.contenido
    if mapeo.modo == "estirar":
        partes.append(f"scale={mapeo.size}:{mapeo.size}:flags=bilinear")
    else:
        partes.append(f"scale={nw}:{nh}:flags=bilinear")
        partes.append(f"pad={mapeo.size}:{mapeo.size}:0:0:color={GRIS_YOLOX}")
    return ",".join(partes)


def comando(clip: Path, mapeo: Mapeo, cada_n: int, pix_fmt: str) -> list[str]:
    return [
        "ffmpeg",
        "-hide_banner",
        "-nostdin",
        "-loglevel",
        "info",
        "-threads",
        "2",
        "-i",
        str(clip),
        "-an",
        "-vf",
        filtro(mapeo, cada_n),
        "-fps_mode",
        "passthrough",
        "-f",
        "rawvideo",
        "-pix_fmt",
        pix_fmt,
        "-",
    ]


@dataclass
class Cuadro:
    indice: int
    t_clip: float  # segundos desde el primer cuadro del clip
    imagen: np.ndarray  # S×S×3 uint8


class Decodificador:
    """Itera los cuadros analizados de un clip. ``espera_s`` acumula el tiempo bloqueado leyendo."""

    def __init__(self, clip: Path, mapeo: Mapeo, cada_n: int, pix_fmt: str) -> None:
        self.cmd = comando(clip, mapeo, cada_n, pix_fmt)
        self.size = mapeo.size
        self.espera_s = 0.0
        self.returncode: int | None = None
        self._pts: queue.Queue[float | None] = queue.Queue()
        self._ultimas: list[str] = []

    def _leer_stderr(self, pipe) -> None:
        for linea in pipe:
            m = _PTS_RE.search(linea)
            if m:
                self._pts.put(float(m.group(1)))
            elif "showinfo" not in linea:
                self._ultimas = (self._ultimas + [linea.rstrip()])[-5:]
        self._pts.put(None)

    def __iter__(self) -> Iterator[Cuadro]:
        n_bytes = self.size * self.size * 3
        proc = subprocess.Popen(self.cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL)
        assert proc.stdout is not None and proc.stderr is not None
        import io

        hilo = threading.Thread(
            target=self._leer_stderr, args=(io.TextIOWrapper(proc.stderr, errors="replace"),), daemon=True
        )
        hilo.start()
        pts0: float | None = None
        i = 0
        try:
            while True:
                t = time.perf_counter()
                buf = proc.stdout.read(n_bytes)
                self.espera_s += time.perf_counter() - t
                if len(buf) < n_bytes:
                    break
                pts = self._pts.get(timeout=30)
                if pts is None:
                    break
                if pts0 is None:
                    pts0 = pts
                img = np.frombuffer(buf, np.uint8).reshape(self.size, self.size, 3)
                yield Cuadro(i, round(pts - pts0, 3), img)
                i += 1
        finally:
            if proc.poll() is None:
                proc.kill()
            self.returncode = proc.wait()
            hilo.join(timeout=2)
        if self.returncode not in (0, -9) and i == 0:
            log.warning("ffmpeg no entregó cuadros (código %s): %s", self.returncode, " | ".join(self._ultimas))
