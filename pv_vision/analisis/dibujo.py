"""Dibujo mínimo en numpy (sin OpenCV ni PIL): líneas, rectángulos, puntos y texto 5×7.

Lo usan el cuadro de referencia y el video de depuración. Imágenes HxWx3 uint8 RGB.
"""

from __future__ import annotations

import numpy as np

VERDE = (40, 220, 60)
ROJO = (235, 40, 40)
AMARILLO = (250, 210, 0)
BLANCO = (255, 255, 255)
NEGRO = (0, 0, 0)
NARANJA = (255, 140, 0)
CIAN = (0, 210, 230)

# Fuente 5×7: cada glifo son 7 filas de 5 bits.
_FUENTE = {
    "0": ("01110", "10001", "10011", "10101", "11001", "10001", "01110"),
    "1": ("00100", "01100", "00100", "00100", "00100", "00100", "01110"),
    "2": ("01110", "10001", "00001", "00010", "00100", "01000", "11111"),
    "3": ("11110", "00001", "00001", "01110", "00001", "00001", "11110"),
    "4": ("00010", "00110", "01010", "10010", "11111", "00010", "00010"),
    "5": ("11111", "10000", "11110", "00001", "00001", "10001", "01110"),
    "6": ("00110", "01000", "10000", "11110", "10001", "10001", "01110"),
    "7": ("11111", "00001", "00010", "00100", "01000", "01000", "01000"),
    "8": ("01110", "10001", "10001", "01110", "10001", "10001", "01110"),
    "9": ("01110", "10001", "10001", "01111", "00001", "00010", "01100"),
    ",": ("00000", "00000", "00000", "00000", "00110", "00100", "01000"),
    ".": ("00000", "00000", "00000", "00000", "00000", "00110", "00110"),
    ":": ("00000", "00110", "00110", "00000", "00110", "00110", "00000"),
    "-": ("00000", "00000", "00000", "11111", "00000", "00000", "00000"),
    "(": ("00010", "00100", "01000", "01000", "01000", "00100", "00010"),
    ")": ("01000", "00100", "00010", "00010", "00010", "00100", "01000"),
    " ": ("00000",) * 7,
    "A": ("01110", "10001", "10001", "11111", "10001", "10001", "10001"),
    "D": ("11110", "10001", "10001", "10001", "10001", "10001", "11110"),
    "E": ("11111", "10000", "10000", "11110", "10000", "10000", "11111"),
    "H": ("10001", "10001", "10001", "11111", "10001", "10001", "10001"),
    "I": ("01110", "00100", "00100", "00100", "00100", "00100", "01110"),
    "L": ("10000", "10000", "10000", "10000", "10000", "10000", "11111"),
    "N": ("10001", "11001", "10101", "10011", "10001", "10001", "10001"),
    "O": ("01110", "10001", "10001", "10001", "10001", "10001", "01110"),
    "P": ("11110", "10001", "10001", "11110", "10000", "10000", "10000"),
    "R": ("11110", "10001", "10001", "11110", "10100", "10010", "10001"),
    "S": ("01111", "10000", "10000", "01110", "00001", "00001", "11110"),
    "T": ("11111", "00100", "00100", "00100", "00100", "00100", "00100"),
    "U": ("10001", "10001", "10001", "10001", "10001", "10001", "01110"),
    "X": ("10001", "10001", "01010", "00100", "01010", "10001", "10001"),
    "#": ("01010", "01010", "11111", "01010", "11111", "01010", "01010"),
}
_GLIFOS = {k: np.array([[c == "1" for c in fila] for fila in v], bool) for k, v in _FUENTE.items()}


def _clip(img: np.ndarray, x0: int, y0: int, x1: int, y1: int) -> tuple[int, int, int, int]:
    h, w = img.shape[:2]
    return max(0, x0), max(0, y0), min(w, x1), min(h, y1)


def rect_lleno(img: np.ndarray, x0: float, y0: float, x1: float, y1: float, color) -> None:
    a, b, c, d = _clip(img, int(round(x0)), int(round(y0)), int(round(x1)), int(round(y1)))
    if a < c and b < d:
        img[b:d, a:c] = color


def rect(img: np.ndarray, x0: float, y0: float, x1: float, y1: float, color, grosor: int = 2) -> None:
    rect_lleno(img, x0, y0, x1, y0 + grosor, color)
    rect_lleno(img, x0, y1 - grosor, x1, y1, color)
    rect_lleno(img, x0, y0, x0 + grosor, y1, color)
    rect_lleno(img, x1 - grosor, y0, x1, y1, color)


def punto(img: np.ndarray, x: float, y: float, color, radio: int = 4) -> None:
    a, b, c, d = _clip(img, int(x - radio), int(y - radio), int(x + radio) + 1, int(y + radio) + 1)
    if a >= c or b >= d:
        return
    yy, xx = np.ogrid[b:d, a:c]
    img[b:d, a:c][(xx - x) ** 2 + (yy - y) ** 2 <= radio * radio] = color


def linea(img: np.ndarray, x0: float, y0: float, x1: float, y1: float, color, grosor: int = 2) -> None:
    """Segmento grueso: todos los píxeles a distancia <= grosor/2 del segmento."""
    r = max(grosor / 2, 0.5)
    a, b, c, d = _clip(
        img, int(min(x0, x1) - r - 1), int(min(y0, y1) - r - 1), int(max(x0, x1) + r + 2), int(max(y0, y1) + r + 2)
    )
    if a >= c or b >= d:
        return
    yy, xx = np.mgrid[b:d, a:c].astype(np.float32)
    dx, dy = x1 - x0, y1 - y0
    L2 = dx * dx + dy * dy or 1e-9
    t = np.clip(((xx - x0) * dx + (yy - y0) * dy) / L2, 0, 1)
    dist2 = (xx - (x0 + t * dx)) ** 2 + (yy - (y0 + t * dy)) ** 2
    sub = img[b:d, a:c]
    sub[dist2 <= r * r] = color


def flecha(img: np.ndarray, x0: float, y0: float, x1: float, y1: float, color, grosor: int = 3) -> None:
    linea(img, x0, y0, x1, y1, color, grosor)
    ang = np.arctan2(y1 - y0, x1 - x0)
    largo = max(10.0, 0.3 * float(np.hypot(x1 - x0, y1 - y0)))
    for da in (2.6, -2.6):
        linea(img, x1, y1, x1 + largo * np.cos(ang + da), y1 + largo * np.sin(ang + da), color, grosor)


def texto(img: np.ndarray, x: float, y: float, s: str, color, escala: int = 2, fondo=NEGRO) -> None:
    """Texto en mayúsculas con la fuente 5×7 (caracteres desconocidos ⇒ espacio)."""
    s = s.upper()
    x, y = int(round(x)), int(round(y))
    if fondo is not None:
        rect_lleno(img, x - escala, y - escala, x + len(s) * 6 * escala + escala, y + 8 * escala, fondo)
    for i, ch in enumerate(s):
        g = _GLIFOS.get(ch, _GLIFOS[" "])
        big = np.kron(g, np.ones((escala, escala), bool))
        gx, gy = x + i * 6 * escala, y
        a, b, c, d = _clip(img, gx, gy, gx + big.shape[1], gy + big.shape[0])
        if a < c and b < d:
            img[b:d, a:c][big[b - gy : d - gy, a - gx : c - gx]] = color
