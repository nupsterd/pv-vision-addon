"""Video de depuración de un clip (Q5: solo bajo demanda, retención 7 días).

Se dibuja sobre el cuadro de ENTRADA del modelo (lo que vio el detector): contenido del ROI,
la línea propia (verde) y la de la Dahua (roja), la banda de histéresis (amarilla), las
cajas confirmadas con su id, el punto de referencia y un destello naranja en el borde
cuando se cuenta un cruce. H.264 ``libx264 veryfast crf 28`` a ``25 / cada_n`` fps.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np

from pv_vision.analisis import dibujo
from pv_vision.analisis.geometria import Linea, Mapeo

DESTELLO_CUADROS = 3


class VideoDepuracion:
    def __init__(
        self, destino: Path, mapeo: Mapeo, fps: float, linea: Linea, dahua: Linea, histeresis_px: float, bgr: bool
    ) -> None:
        """``linea`` y ``dahua`` en píxeles del cuadro completo del clip (ya escaladas si no es 1920×1080)."""
        self.destino = destino
        self.mapeo = mapeo
        self.bgr = bgr
        self.linea = linea
        self.dahua = dahua
        self.hist = histeresis_px
        self._destello = 0
        destino.parent.mkdir(parents=True, exist_ok=True)
        s = mapeo.size
        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-nostdin",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-s",
            f"{s}x{s}",
            "-r",
            f"{fps:g}",
            "-i",
            "-",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "28",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(destino),
        ]
        self._proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def _seg(self, lin: Linea) -> tuple[float, float, float, float]:
        x1, y1 = self.mapeo.a_entrada(lin.x1, lin.y1)
        x2, y2 = self.mapeo.a_entrada(lin.x2, lin.y2)
        return float(x1), float(y1), float(x2), float(y2)

    def cuadro(self, imagen: np.ndarray, pistas, puntos, hubo_cruce: bool) -> None:
        img = imagen[:, :, ::-1].copy() if self.bgr else imagen.copy()
        sx, _ = self.mapeo.escala
        dx, dy = self.linea.x2 - self.linea.x1, self.linea.y2 - self.linea.y1
        n = np.array([-dy, dx]) / float(np.hypot(dx, dy)) * self.hist
        for k in (-1, 1):
            off = Linea(
                self.linea.x1 + k * n[0], self.linea.y1 + k * n[1], self.linea.x2 + k * n[0], self.linea.y2 + k * n[1]
            )
            dibujo.linea(img, *self._seg(off), dibujo.AMARILLO, 1)
        dibujo.linea(img, *self._seg(self.dahua), dibujo.ROJO, 2)
        dibujo.linea(img, *self._seg(self.linea), dibujo.VERDE, 2)
        nw, nh = self.mapeo.contenido
        dibujo.rect(img, 0, 0, nw, nh, dibujo.VERDE, 1)
        for pista, (px, py) in zip(pistas, puntos, strict=True):
            x1, y1 = self.mapeo.a_entrada(pista.caja[0], pista.caja[1])
            x2, y2 = self.mapeo.a_entrada(pista.caja[2], pista.caja[3])
            dibujo.rect(img, x1, y1, x2, y2, dibujo.CIAN, 2)
            dibujo.texto(img, x1 + 2, max(0, y1 - 10), f"#{pista.id}", dibujo.CIAN, 1)
            ex, ey = self.mapeo.a_entrada(px, py)
            dibujo.punto(img, float(ex), float(ey), dibujo.BLANCO, max(2, round(6 * sx)))
        if hubo_cruce:
            self._destello = DESTELLO_CUADROS
        if self._destello > 0:
            dibujo.rect(img, 0, 0, img.shape[1], img.shape[0], dibujo.NARANJA, 6)
            self._destello -= 1
        assert self._proc.stdin is not None
        self._proc.stdin.write(img.tobytes())

    def cerrar(self) -> bool:
        assert self._proc.stdin is not None
        try:
            self._proc.stdin.close()
        except BrokenPipeError:
            pass
        return self._proc.wait(timeout=120) == 0
