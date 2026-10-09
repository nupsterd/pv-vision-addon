"""Detectores de personas sobre ONNX Runtime (CPU). Solo la clase persona.

El postproceso es numpy puro (testeable sin onnxruntime ni pesos). La sesión ORT se crea
con ``intra_op_num_threads`` = ``analisis_hilos`` y sin espera activa de los hilos
(``allow_spinning`` = 0), para no quemar CPU entre cuadros.

Cajas devueltas en coordenadas del cuadro de ENTRADA del modelo (S×S) como
``(N, 4)`` ``x1, y1, x2, y2`` y puntajes ``(N,)``; el llamador las pasa al cuadro completo
con ``geometria.Mapeo``.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from pv_vision.analisis.modelos import Modelo

NMS_IOU = 0.45
YOLOX_STRIDES = (8, 16, 32)
RFDETR_PERSONA = 1  # índice COCO-91 de "person" en la salida de RF-DETR
IMAGENET_MEAN = np.array([0.485, 0.456, 0.406], np.float32)
IMAGENET_STD = np.array([0.229, 0.224, 0.225], np.float32)


def iou(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """IoU de cada caja de ``a`` (N,4) contra cada caja de ``b`` (M,4) → (N,M)."""
    if len(a) == 0 or len(b) == 0:
        return np.zeros((len(a), len(b)), np.float32)
    tl = np.maximum(a[:, None, :2], b[None, :, :2])
    br = np.minimum(a[:, None, 2:], b[None, :, 2:])
    inter = np.prod(np.clip(br - tl, 0, None), axis=2)
    area_a = np.prod(np.clip(a[:, 2:] - a[:, :2], 0, None), axis=1)
    area_b = np.prod(np.clip(b[:, 2:] - b[:, :2], 0, None), axis=1)
    return inter / (area_a[:, None] + area_b[None, :] - inter + 1e-9)


def nms(boxes: np.ndarray, scores: np.ndarray, umbral: float = NMS_IOU) -> np.ndarray:
    """Índices que sobreviven al NMS, ordenados por puntaje descendente."""
    orden = np.argsort(-scores, kind="stable")
    keep: list[int] = []
    while orden.size:
        i = int(orden[0])
        keep.append(i)
        if orden.size == 1:
            break
        o = iou(boxes[i : i + 1], boxes[orden[1:]])[0]
        orden = orden[1:][o < umbral]
    return np.array(keep, dtype=np.int64)


def _grilla_yolox(size: int) -> tuple[np.ndarray, np.ndarray]:
    grids, strides = [], []
    for st in YOLOX_STRIDES:
        g = size // st
        yv, xv = np.meshgrid(np.arange(g), np.arange(g), indexing="ij")
        grids.append(np.stack((xv, yv), 2).reshape(-1, 2))
        strides.append(np.full((g * g, 1), st))
    return np.concatenate(grids).astype(np.float32), np.concatenate(strides).astype(np.float32)


def yolox_postproceso(out: np.ndarray, size: int, conf: float) -> tuple[np.ndarray, np.ndarray]:
    """Salida cruda de YOLOX ``(1, N, 85)`` (sin decodificar) → cajas y puntajes de persona.

    Decodificación oficial (``demo_postprocess``): ``xy = (o[:2] + grilla) * stride``,
    ``wh = exp(o[2:4]) * stride``; puntaje = objetividad × clase 0 (persona)."""
    o = out[0] if out.ndim == 3 else out
    grid, stride = _grilla_yolox(size)
    if len(o) != len(grid):
        raise ValueError(f"salida YOLOX con {len(o)} anclas; se esperaban {len(grid)} para {size}x{size}")
    score = o[:, 4] * o[:, 5]
    m = score >= conf
    if not np.any(m):
        return np.zeros((0, 4), np.float32), np.zeros((0,), np.float32)
    xy = (o[m, :2] + grid[m]) * stride[m]
    wh = np.exp(np.clip(o[m, 2:4], -10, 10)) * stride[m]
    boxes = np.concatenate([xy - wh / 2, xy + wh / 2], axis=1).astype(np.float32)
    s = score[m].astype(np.float32)
    k = nms(boxes, s)
    return boxes[k], s[k]


def rfdetr_postproceso(dets: np.ndarray, logits: np.ndarray, size: int, conf: float) -> tuple[np.ndarray, np.ndarray]:
    """RF-DETR ``dets (1,Q,4)`` (cx, cy, w, h normalizados) + ``labels (1,Q,91)`` (logits) → persona.

    Sigmoide sobre el logit de persona; sin NMS (las consultas ya son únicas)."""
    d = dets[0] if dets.ndim == 3 else dets
    lg = logits[0] if logits.ndim == 3 else logits
    prob = 1.0 / (1.0 + np.exp(-lg[:, RFDETR_PERSONA].astype(np.float64)))
    m = prob >= conf
    cx, cy, w, h = (d[m] * size).T
    boxes = np.stack([cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2], axis=1).astype(np.float32)
    s = prob[m].astype(np.float32)
    orden = np.argsort(-s, kind="stable")
    return boxes[orden], s[orden]


def preparar_entrada(modelo: Modelo, cuadro: np.ndarray) -> np.ndarray:
    """Cuadro HxWx3 uint8 (ya en el formato del modelo) → tensor NCHW float32."""
    if modelo.familia == "yolox":
        return np.ascontiguousarray(cuadro.transpose(2, 0, 1)[None], dtype=np.float32)
    x = (cuadro.astype(np.float32) / 255.0 - IMAGENET_MEAN) / IMAGENET_STD
    return np.ascontiguousarray(x.transpose(2, 0, 1)[None], dtype=np.float32)


class Detector:
    """Sesión ORT de un modelo del catálogo."""

    def __init__(self, modelo: Modelo, hilos: int, ruta: str | None = None, sesion: Any = None) -> None:
        self.modelo = modelo
        if sesion is None:
            import onnxruntime as ort  # import tardío: los tests unitarios no lo necesitan

            so = ort.SessionOptions()
            so.intra_op_num_threads = hilos
            so.inter_op_num_threads = 1
            so.add_session_config_entry("session.intra_op.allow_spinning", "0")
            sesion = ort.InferenceSession(ruta or modelo.ruta, so, providers=["CPUExecutionProvider"])
        self._s = sesion
        self._entrada = self._s.get_inputs()[0].name
        nombres = [o.name for o in self._s.get_outputs()]
        # RF-DETR exportado por rfdetr 1.11: salidas "dets" y "labels" (por nombre si están).
        self._idx = (nombres.index("dets"), nombres.index("labels")) if {"dets", "labels"} <= set(nombres) else (0, 1)

    def __call__(self, cuadro: np.ndarray, conf: float) -> tuple[np.ndarray, np.ndarray]:
        salidas = self._s.run(None, {self._entrada: preparar_entrada(self.modelo, cuadro)})
        if self.modelo.familia == "yolox":
            return yolox_postproceso(salidas[0], self.modelo.size, conf)
        return rfdetr_postproceso(salidas[self._idx[0]], salidas[self._idx[1]], self.modelo.size, conf)
