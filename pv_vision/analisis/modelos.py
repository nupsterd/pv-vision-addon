"""Catálogo de modelos ONNX (solo la clase persona). Sin dependencias: lo usa la validación.

Los archivos los baja el ``Dockerfile`` en el build con sha256 fijado (ver ``NOTICE``):
YOLOX de la release ``0.1.1rc0`` de Megvii; RF-DETR exportados por nosotros y publicados
como assets de la release ``modelos-v1`` de ``pv-vision-addon``.
"""

from __future__ import annotations

from dataclasses import dataclass

MODELOS_DIR = "/opt/modelos"


@dataclass(frozen=True)
class Modelo:
    nombre: str
    archivo: str
    familia: str  # "yolox" | "rfdetr"
    size: int  # entrada cuadrada S×S
    preproc: str  # "letterbox" (YOLOX) | "estirar" (RF-DETR, como su propio predict)
    pix_fmt: str  # formato que ffmpeg entrega por la tubería

    @property
    def ruta(self) -> str:
        return f"{MODELOS_DIR}/{self.archivo}"


MODELOS: dict[str, Modelo] = {
    m.nombre: m
    for m in (
        Modelo("yolox_nano", "yolox_nano.onnx", "yolox", 416, "letterbox", "bgr24"),
        Modelo("yolox_tiny", "yolox_tiny.onnx", "yolox", 416, "letterbox", "bgr24"),
        Modelo("yolox_s", "yolox_s.onnx", "yolox", 640, "letterbox", "bgr24"),
        Modelo("rfdetr_nano", "rfdetr_nano.onnx", "rfdetr", 384, "estirar", "rgb24"),
        Modelo("rfdetr_nano_int8", "rfdetr_nano_int8.onnx", "rfdetr", 384, "estirar", "rgb24"),
        Modelo("rfdetr_small", "rfdetr_small.onnx", "rfdetr", 512, "estirar", "rgb24"),
    )
}
