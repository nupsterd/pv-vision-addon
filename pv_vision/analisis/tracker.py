"""Seguimiento mínimo estilo ByteTrack, en numpy.

Algoritmo basado en ByteTrack (Zhang et al., 2022, https://github.com/ifzhang/ByteTrack,
licencia MIT): asociación en dos etapas — primero las detecciones de puntaje alto contra
todas las pistas, después las de puntaje bajo contra las pistas que quedaron sin pareja —
para no perder a una persona cuando su puntaje cae (oclusión parcial, borde del ROI).
Implementación propia y simplificada: IoU con predicción por velocidad constante (o con la
última caja, lo que coincida mejor) y
asignación voraz (en el pasillo hay pocas personas a la vez), sin filtro de Kalman.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from pv_vision.analisis.detectores import iou

SUAVIZADO_VEL = 0.6  # peso de la velocidad anterior en la predicción


@dataclass
class Pista:
    id: int
    caja: np.ndarray  # x1, y1, x2, y2 en el cuadro completo
    puntaje: float
    vel: np.ndarray = field(default_factory=lambda: np.zeros(4, np.float32))
    perdida: int = 0
    aciertos: int = 1
    puntajes: list[float] = field(default_factory=list)

    def prediccion(self) -> np.ndarray:
        return self.caja + self.vel * (self.perdida + 1)

    def actualizar(self, caja: np.ndarray, puntaje: float) -> None:
        pasos = self.perdida + 1
        self.vel = SUAVIZADO_VEL * self.vel + (1 - SUAVIZADO_VEL) * (caja - self.caja) / pasos
        self.caja = caja.astype(np.float32)
        self.puntaje = float(puntaje)
        self.puntajes.append(float(puntaje))
        self.perdida = 0
        self.aciertos += 1

    @property
    def conf_media(self) -> float:
        return float(np.mean(self.puntajes)) if self.puntajes else self.puntaje


class ByteTrackMinimo:
    def __init__(
        self,
        conf_alta: float = 0.5,
        conf_baja: float = 0.1,
        iou_min: float = 0.3,
        buffer_cuadros: int = 13,
        min_hits: int = 3,
    ) -> None:
        self.conf_alta, self.conf_baja, self.iou_min = conf_alta, conf_baja, iou_min
        self.buffer, self.min_hits = buffer_cuadros, min_hits
        self.pistas: list[Pista] = []
        self._next_id = 1

    def _asociar(self, pistas: list[Pista], cajas: np.ndarray) -> tuple[list[tuple[int, int]], list[int], list[int]]:
        if not pistas or len(cajas) == 0:
            return [], list(range(len(pistas))), list(range(len(cajas)))
        # Máximo entre el IoU con la predicción y con la última caja: si la persona frena o da media
        # vuelta (n2, n28), la predicción por velocidad se pasa de largo y no debe cambiar el id.
        m = np.maximum(
            iou(np.stack([p.prediccion() for p in pistas]), cajas), iou(np.stack([p.caja for p in pistas]), cajas)
        )
        pares: list[tuple[int, int]] = []
        libres_p, libres_d = set(range(len(pistas))), set(range(len(cajas)))
        for idx in np.argsort(-m, axis=None, kind="stable"):
            pi, di = divmod(int(idx), len(cajas))
            if m[pi, di] < self.iou_min:
                break
            if pi in libres_p and di in libres_d:
                pares.append((pi, di))
                libres_p.discard(pi)
                libres_d.discard(di)
        return pares, sorted(libres_p), sorted(libres_d)

    def actualizar(self, cajas: np.ndarray, puntajes: np.ndarray) -> list[Pista]:
        """Un cuadro analizado. Devuelve las pistas confirmadas (``min_hits``) vistas en este cuadro."""
        cajas = np.asarray(cajas, np.float32).reshape(-1, 4)
        puntajes = np.asarray(puntajes, np.float32).reshape(-1)
        alta = puntajes >= self.conf_alta
        baja = (puntajes >= self.conf_baja) & ~alta
        ca, sa, cb, sb = cajas[alta], puntajes[alta], cajas[baja], puntajes[baja]

        pares, libres_p, libres_d = self._asociar(self.pistas, ca)
        for pi, di in pares:
            self.pistas[pi].actualizar(ca[di], sa[di])
        resto = [self.pistas[i] for i in libres_p]
        pares2, libres_p2, _ = self._asociar(resto, cb)
        for pi, di in pares2:
            resto[pi].actualizar(cb[di], sb[di])
        for i in libres_p2:
            resto[i].perdida += 1
        # Solo las detecciones ALTAS sin pareja abren pistas nuevas (como ByteTrack).
        for di in libres_d:
            self.pistas.append(Pista(self._next_id, ca[di].copy(), float(sa[di]), puntajes=[float(sa[di])]))
            self._next_id += 1
        self.pistas = [p for p in self.pistas if p.perdida <= self.buffer]
        return [p for p in self.pistas if p.perdida == 0 and p.aciertos >= self.min_hits]
