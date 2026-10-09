from __future__ import annotations

import numpy as np
import pytest

from pv_vision.analisis.detectores import (
    Detector,
    iou,
    nms,
    preparar_entrada,
    rfdetr_postproceso,
    yolox_postproceso,
)
from pv_vision.analisis.modelos import MODELOS


def _salida_yolox(size: int = 416) -> np.ndarray:
    n = sum((size // s) ** 2 for s in (8, 16, 32))
    return np.zeros((1, n, 85), np.float32)


def test_iou_y_nms():
    a = np.array([[0, 0, 10, 10], [1, 1, 11, 11], [50, 50, 60, 60]], np.float32)
    assert iou(a[:1], a[1:2])[0, 0] == pytest.approx(81 / 119, rel=1e-3)
    keep = nms(a, np.array([0.9, 0.8, 0.7], np.float32), 0.45)
    assert keep.tolist() == [0, 2]


def test_yolox_decodifica_grilla_y_stride():
    out = _salida_yolox()
    # anclaje de stride 8 en la celda (x=10, y=5): índice 5*52+10
    i = 5 * 52 + 10
    out[0, i, :4] = [0.5, 0.5, np.log(4.0), np.log(8.0)]  # centro (10.5*8, 5.5*8), caja 32x64
    out[0, i, 4], out[0, i, 5] = 0.9, 0.9
    boxes, scores = yolox_postproceso(out, 416, 0.1)
    assert len(boxes) == 1
    assert boxes[0] == pytest.approx([84 - 16, 44 - 32, 84 + 16, 44 + 32])
    assert scores[0] == pytest.approx(0.81)


def test_yolox_solo_persona_y_nms():
    out = _salida_yolox()
    out[0, 0, :] = 0
    out[0, 0, 4], out[0, 0, 6] = 0.99, 0.99  # objeto de otra clase: no es persona
    out[0, 1, :4] = [0.0, 0.0, np.log(4.0), np.log(4.0)]
    out[0, 1, 4:6] = [0.9, 0.9]
    out[0, 2, :4] = [-1.0, 0.0, np.log(4.0), np.log(4.0)]  # casi la misma caja, menor puntaje
    out[0, 2, 4:6] = [0.8, 0.8]
    boxes, scores = yolox_postproceso(out, 416, 0.1)
    assert len(boxes) == 1 and scores[0] == pytest.approx(0.81)


def test_yolox_tamano_incorrecto():
    with pytest.raises(ValueError):
        yolox_postproceso(_salida_yolox(416), 640, 0.1)


def test_rfdetr_sigmoide_persona_sin_nms():
    dets = np.array([[[0.5, 0.5, 0.2, 0.4], [0.51, 0.5, 0.2, 0.4], [0.1, 0.1, 0.05, 0.05]]], np.float32)
    logits = np.full((1, 3, 91), -10.0, np.float32)
    logits[0, 0, 1] = 3.0  # persona, p≈0.95
    logits[0, 1, 1] = 0.0  # persona, p=0.5 (casi la misma caja: sin NMS se queda)
    logits[0, 2, 2] = 5.0  # bicicleta: no es persona
    boxes, scores = rfdetr_postproceso(dets, logits, 384, 0.1)
    assert len(boxes) == 2
    assert scores[0] == pytest.approx(1 / (1 + np.exp(-3.0)))
    assert boxes[0] == pytest.approx([0.4 * 384, 0.3 * 384, 0.6 * 384, 0.7 * 384])


def test_preparar_entrada_yolox_y_rfdetr():
    img = np.full((416, 416, 3), 114, np.uint8)
    x = preparar_entrada(MODELOS["yolox_nano"], img)
    assert x.shape == (1, 3, 416, 416) and x.dtype == np.float32 and x.max() == 114
    img2 = np.zeros((384, 384, 3), np.uint8)
    y = preparar_entrada(MODELOS["rfdetr_nano"], img2)
    assert y.shape == (1, 3, 384, 384)
    assert y[0, 0, 0, 0] == pytest.approx(-0.485 / 0.229)


class _Sesion:
    def __init__(self, nombres, salidas):
        self._nombres, self._salidas = nombres, salidas

    def get_inputs(self):
        return [type("I", (), {"name": "input"})()]

    def get_outputs(self):
        return [type("O", (), {"name": n})() for n in self._nombres]

    def run(self, _out, feeds):
        assert list(feeds) == ["input"]
        return self._salidas


def test_detector_rfdetr_toma_salidas_por_nombre():
    dets = np.array([[[0.5, 0.5, 0.2, 0.4]]], np.float32)
    logits = np.full((1, 1, 91), -10.0, np.float32)
    logits[0, 0, 1] = 4.0
    det = Detector(MODELOS["rfdetr_nano"], 1, sesion=_Sesion(["labels", "dets"], [logits, dets]))
    boxes, scores = det(np.zeros((384, 384, 3), np.uint8), 0.5)
    assert len(boxes) == 1 and scores[0] > 0.9
