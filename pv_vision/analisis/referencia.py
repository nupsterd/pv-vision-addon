"""Cuadro de referencia para ajustar ROI y línea (Q6: bajo demanda, desde un archivo ya grabado).

Toma un cuadro del archivo (a 1 s, o el primero si es más corto) a resolución completa y
dibuja: grilla cada 100 px con rótulos, ROI y línea propia en verde con sus coordenadas,
la línea de la Dahua en rojo y una flecha hacia el lado de salida. Todo en píxeles del
cuadro de referencia 1920×1080 (si el archivo tiene otra resolución, se escala).
Se guarda en ``/media/pv_vision/referencia/ref_<archivo>_<hash>.png``.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

import numpy as np

from pv_vision.analisis import dibujo
from pv_vision.analisis.geometria import REF_H, REF_W, Linea, Roi, dahua_linea_px

log = logging.getLogger("pv_vision.analisis.referencia")

GRILLA_PX = 100


def extraer_cuadro(archivo: Path, ancho: int, alto: int, t: float = 1.0) -> np.ndarray | None:
    for seg in (t, 0.0):
        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-nostdin",
            "-loglevel",
            "error",
            "-ss",
            f"{seg:g}",
            "-i",
            str(archivo),
            "-frames:v",
            "1",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-",
        ]
        out = subprocess.run(cmd, capture_output=True, timeout=60, check=False)
        if out.returncode == 0 and len(out.stdout) == ancho * alto * 3:
            return np.frombuffer(out.stdout, np.uint8).reshape(alto, ancho, 3).copy()
    return None


def guardar_png(img: np.ndarray, destino: Path) -> None:
    destino.parent.mkdir(parents=True, exist_ok=True)
    tmp = destino.with_name(destino.name + ".tmp.png")
    h, w = img.shape[:2]
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
        f"{w}x{h}",
        "-i",
        "-",
        "-frames:v",
        "1",
        str(tmp),
    ]
    subprocess.run(cmd, input=img.tobytes(), check=True, capture_output=True, timeout=60)
    tmp.replace(destino)


def flecha_salida(lin: Linea, sentido_salida: str, largo: float = 90.0) -> tuple[float, float, float, float]:
    """Flecha desde el punto medio de la línea hacia el lado de salida."""
    mx, my = (lin.x1 + lin.x2) / 2, (lin.y1 + lin.y2) / 2
    dx, dy = lin.x2 - lin.x1, lin.y2 - lin.y1
    n = np.array([-dy, dx]) / float(np.hypot(dx, dy))  # lado "derecho"
    if sentido_salida != "izq_a_der":
        n = -n
    return mx, my, mx + largo * n[0], my + largo * n[1]


def dibujar(img: np.ndarray, roi: Roi, lin: Linea, sentido_salida: str) -> np.ndarray:
    """Dibuja sobre ``img`` (en píxeles del propio cuadro; escala desde 1920×1080 si hace falta)."""
    h, w = img.shape[:2]
    sx, sy = w / REF_W, h / REF_H
    esc = max(1, round(2 * min(sx, sy)))
    for x in range(0, REF_W, GRILLA_PX):
        dibujo.linea(img, x * sx, 0, x * sx, h - 1, dibujo.BLANCO, 1)
        dibujo.texto(img, x * sx + 3, 3, str(x), dibujo.BLANCO, esc)
    for y in range(GRILLA_PX, REF_H, GRILLA_PX):
        dibujo.linea(img, 0, y * sy, w - 1, y * sy, dibujo.BLANCO, 1)
        dibujo.texto(img, 3, y * sy + 3, str(y), dibujo.BLANCO, esc)
    r = roi.scaled(sx, sy)
    dibujo.rect(img, r.x, r.y, r.x + r.w, r.y + r.h, dibujo.VERDE, 3)
    dibujo.texto(img, r.x + 6, r.y + 6, f"ROI {roi}", dibujo.VERDE, esc)
    d = dahua_linea_px().scaled(sx, sy)
    dibujo.linea(img, d.x1, d.y1, d.x2, d.y2, dibujo.ROJO, 3)
    dibujo.texto(img, d.x1 + 8, d.y1 - 22, "DAHUA", dibujo.ROJO, esc)
    li = lin.scaled(sx, sy)
    dibujo.linea(img, li.x1, li.y1, li.x2, li.y2, dibujo.VERDE, 4)
    dibujo.texto(img, li.x1 + 8, li.y1 + 8, f"LINEA {lin}", dibujo.VERDE, esc)
    fx0, fy0, fx1, fy1 = flecha_salida(li, sentido_salida)
    dibujo.flecha(img, fx0, fy0, fx1, fy1, dibujo.AMARILLO, 4)
    dibujo.texto(img, fx1 + 6, fy1 + 6, "SALIDA", dibujo.AMARILLO, esc)
    return img


def generar(archivo: Path, destino: Path, ancho: int, alto: int, roi: Roi, lin: Linea, sentido_salida: str) -> bool:
    img = extraer_cuadro(archivo, ancho, alto)
    if img is None:
        log.error("No se pudo extraer un cuadro de %s para la referencia.", archivo.name)
        return False
    guardar_png(dibujar(img, roi, lin, sentido_salida), destino)
    log.info("Cuadro de referencia generado: %s", destino)
    return True
