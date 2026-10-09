"""ROI, línea de conteo y mapeo de coordenadas.

Todo se expresa en píxeles del cuadro completo de referencia 1920×1080 (el principal de
la Dahua). Si un clip tiene otra resolución (p. ej. el secundario 704×576, mismo campo
visual), ROI y línea se escalan proporcionalmente.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

REF_W, REF_H = 1920, 1080
# Línea `salida 1` de la Dahua en su espacio 0-8191 (§5.9.668): `LeftToRight` = salida.
DAHUA_LINEA_8192 = ((3185, 5409), (4714, 8128))

_ROI_RE = re.compile(r"^\s*(\d+):(\d+):(\d+):(\d+)\s*$")
_LINEA_RE = re.compile(r"^\s*(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)\s*$")


@dataclass(frozen=True)
class Roi:
    w: int
    h: int
    x: int
    y: int

    def __str__(self) -> str:
        return f"{self.w}:{self.h}:{self.x}:{self.y}"

    def scaled(self, sx: float, sy: float) -> Roi:
        return Roi(round(self.w * sx), round(self.h * sy), round(self.x * sx), round(self.y * sy))


@dataclass(frozen=True)
class Linea:
    x1: float
    y1: float
    x2: float
    y2: float

    def __str__(self) -> str:
        return ",".join(f"{v:g}" for v in (self.x1, self.y1, self.x2, self.y2))

    def scaled(self, sx: float, sy: float) -> Linea:
        return Linea(self.x1 * sx, self.y1 * sy, self.x2 * sx, self.y2 * sy)


def parse_roi(text: str, frame_w: int = REF_W, frame_h: int = REF_H) -> Roi:
    m = _ROI_RE.match(text)
    if not m:
        raise ValueError(f"roi {text!r}: formato esperado W:H:X:Y (enteros, píxeles de 1920x1080)")
    w, h, x, y = (int(g) for g in m.groups())
    if w < 64 or h < 64:
        raise ValueError(f"roi {text!r}: ancho y alto deben ser >= 64 px")
    if x + w > frame_w or y + h > frame_h:
        raise ValueError(f"roi {text!r}: se sale del cuadro {frame_w}x{frame_h}")
    return Roi(w, h, x, y)


def parse_linea(text: str) -> Linea:
    m = _LINEA_RE.match(text)
    if not m:
        raise ValueError(f"linea {text!r}: formato esperado x1,y1,x2,y2 (píxeles de 1920x1080)")
    lin = Linea(*(float(g) for g in m.groups()))
    if (lin.x1, lin.y1) == (lin.x2, lin.y2):
        raise ValueError(f"linea {text!r}: los dos extremos son el mismo punto")
    return lin


def dahua_linea_px() -> Linea:
    (x1, y1), (x2, y2) = DAHUA_LINEA_8192
    return Linea(x1 * REF_W / 8192, y1 * REF_H / 8192, x2 * REF_W / 8192, y2 * REF_H / 8192)


def extender_a_roi(lin: Linea, roi: Roi) -> Linea:
    """La recta de ``lin`` recortada a los bordes del ROI, con el mismo sentido p1→p2 (D6)."""
    dx, dy = lin.x2 - lin.x1, lin.y2 - lin.y1
    ts: list[float] = []
    for t in _cortes(lin.x1, dx, roi.x, roi.x + roi.w) + _cortes(lin.y1, dy, roi.y, roi.y + roi.h):
        px, py = lin.x1 + t * dx, lin.y1 + t * dy
        if roi.x - 1e-6 <= px <= roi.x + roi.w + 1e-6 and roi.y - 1e-6 <= py <= roi.y + roi.h + 1e-6:
            ts.append(t)
    if len(ts) < 2:
        raise ValueError("la línea no atraviesa el ROI")
    t0, t1 = min(ts), max(ts)
    return Linea(lin.x1 + t0 * dx, lin.y1 + t0 * dy, lin.x1 + t1 * dx, lin.y1 + t1 * dy)


def _cortes(p0: float, d: float, lo: float, hi: float) -> list[float]:
    if abs(d) < 1e-12:
        return []
    return [(lo - p0) / d, (hi - p0) / d]


def linea_por_defecto(roi: Roi) -> Linea:
    """La línea de la Dahua extendida a los bordes del ROI, redondeada a píxeles enteros."""
    lin = extender_a_roi(dahua_linea_px(), roi)
    return Linea(*(float(round(v)) for v in (lin.x1, lin.y1, lin.x2, lin.y2)))


def linea_cruza_roi(lin: Linea, roi: Roi) -> bool:
    try:
        extender_a_roi(lin, roi)
    except ValueError:
        return False
    return True


@dataclass(frozen=True)
class Mapeo:
    """Cuadro de entrada del modelo (S×S) ↔ cuadro completo del clip.

    ``letterbox``: el ROI se escala manteniendo la proporción a ``nw×nh`` y se pega arriba a la
    izquierda de un lienzo S×S gris (YOLOX). ``estirar``: el ROI se escala a S×S (RF-DETR)."""

    roi: Roi
    size: int
    modo: str  # "letterbox" | "estirar"

    @property
    def escala(self) -> tuple[float, float]:
        nw, nh = self.contenido
        return nw / self.roi.w, nh / self.roi.h

    @property
    def contenido(self) -> tuple[int, int]:
        """Ancho y alto ocupados por la imagen dentro del lienzo S×S."""
        if self.modo == "estirar":
            return self.size, self.size
        r = min(self.size / self.roi.w, self.size / self.roi.h)
        return max(1, round(self.roi.w * r)), max(1, round(self.roi.h * r))

    def a_completo(self, xs, ys):
        """Entrada del modelo → cuadro completo (acepta floats o arrays de numpy)."""
        sx, sy = self.escala
        return xs / sx + self.roi.x, ys / sy + self.roi.y

    def a_entrada(self, xs, ys):
        sx, sy = self.escala
        return (xs - self.roi.x) * sx, (ys - self.roi.y) * sy
