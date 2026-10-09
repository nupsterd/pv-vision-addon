from __future__ import annotations

import numpy as np

from pv_vision.analisis.conteo import ContadorLinea
from pv_vision.analisis.geometria import Linea
from pv_vision.analisis.tracker import ByteTrackMinimo

# Línea vertical x = 500 dirigida hacia ARRIBA: su lado "derecho" (normal (-dy, dx)) es x > 500,
# así "izq_a_der" = de x < 500 a x > 500.
LINEA = Linea(500, 1000, 500, 0)


def caja(cx: float, pie_y: float = 600, w: float = 60, h: float = 200) -> np.ndarray:
    return np.array([cx - w / 2, pie_y - h, cx + w / 2, pie_y], np.float32)


def recorrido(xs, conf=0.9):
    """Una persona por cuadro, siguiendo las x dadas (pie en y=600)."""
    return [(np.array([caja(x)]), np.array([conf], np.float32)) for x in xs]


def contar(xs, **kw):
    tr = ByteTrackMinimo(min_hits=1)
    co = ContadorLinea(LINEA, **{"histeresis_px": 12, "cuadros_confirmacion": 2, **kw})
    for i, (c, s) in enumerate(recorrido(xs)):
        co.actualizar(tr.actualizar(c, s), i)
    return co.cruces


# ----------------------------------------------------------------------------- tracker


def test_tracker_mantiene_id_mientras_se_mueve():
    tr = ByteTrackMinimo(min_hits=1)
    ids = {tr.actualizar(c, s)[0].id for c, s in recorrido(range(300, 700, 20))}
    assert ids == {1}


def test_tracker_min_hits():
    tr = ByteTrackMinimo(min_hits=3)
    salidas = [tr.actualizar(c, s) for c, s in recorrido([300, 310, 320, 330])]
    assert [len(x) for x in salidas] == [0, 0, 1, 1]


def test_tracker_segunda_etapa_con_puntaje_bajo():
    tr = ByteTrackMinimo(min_hits=1, conf_alta=0.5, conf_baja=0.1)
    tr.actualizar(np.array([caja(300)]), np.array([0.9]))
    vivas = tr.actualizar(np.array([caja(305)]), np.array([0.2]))  # baja: continúa la pista
    assert [p.id for p in vivas] == [1]
    nuevas = ByteTrackMinimo(min_hits=1).actualizar(np.array([caja(300)]), np.array([0.2]))
    assert nuevas == []  # una detección baja sola no abre pista


def test_tracker_buffer_y_reaparicion():
    tr = ByteTrackMinimo(min_hits=1, buffer_cuadros=2)
    tr.actualizar(np.array([caja(300)]), np.array([0.9]))
    for _ in range(2):
        tr.actualizar(np.zeros((0, 4)), np.zeros(0))
    assert len(tr.pistas) == 1
    assert tr.actualizar(np.array([caja(300)]), np.array([0.9]))[0].id == 1
    for _ in range(3):
        tr.actualizar(np.zeros((0, 4)), np.zeros(0))
    assert tr.pistas == []


def test_tracker_dos_personas_separadas():
    tr = ByteTrackMinimo(min_hits=1)
    for k in range(5):
        vivas = tr.actualizar(np.array([caja(200 + 10 * k), caja(800 - 10 * k)]), np.array([0.9, 0.8]))
    assert sorted(p.id for p in vivas) == [1, 2]


# ----------------------------------------------------------------------------- conteo


def test_cruce_izq_a_der_es_salida():
    cruces = contar(range(400, 620, 15))
    assert [(c.sentido, c.track) for c in cruces] == [("salida", 1)]


def test_cruce_der_a_izq_es_entrada():
    assert [c.sentido for c in contar(range(620, 400, -15))] == ["entrada"]


def test_sentido_salida_invertido():
    assert [c.sentido for c in contar(range(400, 620, 15), sentido_salida="der_a_izq")] == ["entrada"]


def test_persona_detenida_sobre_la_linea_no_cuenta():
    """n27/n28: se para o se acomoda sobre la línea (dentro de la banda) y vuelve a su lado."""
    xs = [420, 440, 460, 480, 495, 505, 498, 508, 503, 495, 470, 450]
    assert contar(xs) == []


def test_persona_detenida_que_luego_cruza_cuenta_una_vez():
    xs = [420, 450, 480, 495, 505, 498, 507, 502, 499, 530, 560, 590]
    cruces = contar(xs)
    assert [c.sentido for c in cruces] == ["salida"]


def test_vuelta_atras_cuenta_salida_y_entrada():
    xs = list(range(420, 600, 20)) + list(range(600, 400, -20))
    assert [c.sentido for c in contar(xs)] == ["salida", "entrada"]


def test_un_cuadro_suelto_del_otro_lado_no_alcanza():
    xs = [440, 465, 490, 515, 490, 465, 440]  # 1 cuadro fuera de la banda del otro lado (ruido)
    assert contar(xs, cuadros_confirmacion=2) == []
    assert len(contar(xs, cuadros_confirmacion=1)) == 2  # sin confirmación: sale y vuelve


def test_aparece_ya_del_otro_lado_no_es_cruce():
    assert contar([600, 620, 640, 660]) == []


def test_cruce_por_el_borde_cuenta_aunque_quede_fuera_del_segmento():
    """La línea propia es una recta de borde a borde del ROI: un cruce por fuera de los extremos
    del segmento (junto al mostrador) también cuenta."""
    co = ContadorLinea(Linea(500, 500, 500, 400), histeresis_px=12, cuadros_confirmacion=2)
    tr = ByteTrackMinimo(min_hits=1)
    for i, x in enumerate(range(420, 620, 20)):
        co.actualizar(tr.actualizar(np.array([caja(x, pie_y=900)]), np.array([0.9])), i)
    assert [c.sentido for c in co.cruces] == ["salida"]


def test_punto_pie_y_centro():
    co_pie = ContadorLinea(LINEA, punto_referencia="pie")
    co_centro = ContadorLinea(LINEA, punto_referencia="centro")
    c = caja(300, pie_y=600, h=200)
    assert co_pie.punto(c) == (300.0, 600.0)
    assert co_centro.punto(c) == (300.0, 500.0)


def test_cuadro_del_cruce_es_el_primero_del_lado_nuevo():
    cruces = contar([420, 450, 480, 530, 560, 590])  # el lado nuevo empieza en el cuadro 3
    assert cruces[0].cuadro == 3


def test_tracker_media_vuelta_conserva_id():
    tr = ByteTrackMinimo(min_hits=1)
    ids = set()
    for c, s in recorrido([440, 465, 490, 515, 490, 465, 440]):
        ids |= {p.id for p in tr.actualizar(c, s)}
    assert ids == {1}
