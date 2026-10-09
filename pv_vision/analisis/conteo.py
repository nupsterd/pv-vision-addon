"""Conteo de cruces de una línea con histéresis.

La línea es dirigida (``x1,y1 → x2,y2``, píxeles del cuadro completo). Con la imagen en
coordenadas de pantalla (y hacia abajo), el lado "derecho" de la línea es el de la normal
``(-dy, dx)``: ``izq_a_der`` = pasar del lado izquierdo al derecho (el ``LeftToRight`` de la
Dahua con la línea en el mismo sentido). ``sentido_salida`` dice cuál de los dos es salida.

Histéresis: dentro de la banda ``±histeresis_px`` alrededor de la línea se conserva el
último lado estable. Un lado nuevo se confirma recién tras ``cuadros_confirmacion`` cuadros
analizados seguidos fuera de la banda, de ese lado. Un cruce se cuenta solo de lado
estable a lado estable: quien se detiene o se acomoda sobre la línea (n27, n28) no cuenta,
y quien cruza y vuelve cuenta una salida y una entrada.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from pv_vision.analisis.geometria import Linea


@dataclass
class Cruce:
    track: int
    sentido: str  # "salida" | "entrada"
    cuadro: int  # índice del cuadro analizado donde empezó el lado nuevo
    punto: tuple[float, float]
    conf_media: float


@dataclass
class _Estado:
    lado: int | None = None  # lado estable: +1 derecha, -1 izquierda
    candidato: int | None = None
    racha: int = 0
    inicio: int = 0
    punto: tuple[float, float] = (0.0, 0.0)


@dataclass
class ContadorLinea:
    linea: Linea
    sentido_salida: str = "izq_a_der"
    histeresis_px: float = 12.0
    cuadros_confirmacion: int = 2
    punto_referencia: str = "pie"
    cruces: list[Cruce] = field(default_factory=list)

    def __post_init__(self) -> None:
        d = np.array([self.linea.x2 - self.linea.x1, self.linea.y2 - self.linea.y1], np.float64)
        self._p1 = np.array([self.linea.x1, self.linea.y1], np.float64)
        self._n = np.array([-d[1], d[0]]) / np.hypot(*d)  # normal hacia la "derecha"
        self._salida_lado = 1 if self.sentido_salida == "izq_a_der" else -1
        self._estados: dict[int, _Estado] = {}

    def punto(self, caja: np.ndarray) -> tuple[float, float]:
        x1, y1, x2, y2 = (float(v) for v in caja)
        return ((x1 + x2) / 2, y2 if self.punto_referencia == "pie" else (y1 + y2) / 2)

    def distancia(self, p: tuple[float, float]) -> float:
        """Distancia con signo del punto a la línea (+ = lado derecho)."""
        return float((np.array(p) - self._p1) @ self._n)

    def actualizar(self, pistas, cuadro: int) -> list[Cruce]:
        nuevos: list[Cruce] = []
        for pista in pistas:
            est = self._estados.setdefault(pista.id, _Estado())
            p = self.punto(pista.caja)
            dist = self.distancia(p)
            if abs(dist) <= self.histeresis_px:
                est.candidato, est.racha = None, 0
                continue
            lado = 1 if dist > 0 else -1
            if lado == est.lado:
                est.candidato, est.racha = None, 0
                continue
            if lado != est.candidato:
                est.candidato, est.racha, est.inicio, est.punto = lado, 0, cuadro, p
            est.racha += 1
            if est.racha < self.cuadros_confirmacion:
                continue
            anterior, est.lado = est.lado, lado
            est.candidato, est.racha = None, 0
            if anterior is None:
                continue  # primer lado estable de la pista: no es un cruce
            c = Cruce(
                track=pista.id,
                sentido="salida" if lado == self._salida_lado else "entrada",
                cuadro=est.inicio,
                punto=(round(est.punto[0], 1), round(est.punto[1], 1)),
                conf_media=round(pista.conf_media, 3),
            )
            self.cruces.append(c)
            nuevos.append(c)
        return nuevos
