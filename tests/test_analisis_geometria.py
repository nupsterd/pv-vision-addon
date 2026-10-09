from __future__ import annotations

import pytest

from pv_vision.analisis.corrida import roi_en_clip
from pv_vision.analisis.geometria import (
    Linea,
    Mapeo,
    Roi,
    dahua_linea_px,
    extender_a_roi,
    linea_cruza_roi,
    linea_por_defecto,
    parse_linea,
    parse_roi,
)
from pv_vision.config import LINEA_POR_DEFECTO


def test_linea_dahua_en_pixeles_coincide_con_13bi():
    d = dahua_linea_px()
    assert (round(d.x1), round(d.y1), round(d.x2), round(d.y2)) == (746, 713, 1105, 1072)


def test_linea_por_defecto_es_la_dahua_extendida_al_roi():
    lin = linea_por_defecto(parse_roi("960:720:500:360"))
    assert str(lin) == LINEA_POR_DEFECTO == "500,467,1113,1080"


def test_extender_conserva_el_sentido_p1_p2():
    lin = extender_a_roi(Linea(10, 10, 20, 20), Roi(100, 100, 0, 0))
    assert (lin.x1, lin.y1, lin.x2, lin.y2) == (0, 0, 100, 100)
    inv = extender_a_roi(Linea(20, 20, 10, 10), Roi(100, 100, 0, 0))
    assert (inv.x1, inv.y1) == (100, 100)


def test_linea_que_no_cruza_el_roi():
    assert not linea_cruza_roi(Linea(0, 0, 10, 0), Roi(100, 100, 500, 500))
    with pytest.raises(ValueError):
        extender_a_roi(Linea(0, 0, 10, 0), Roi(100, 100, 500, 500))


@pytest.mark.parametrize("texto", ["960:720:500", "a:b:c:d", "32:720:0:0", "960:720:1000:0", "960:720:0:400"])
def test_roi_invalido(texto):
    with pytest.raises(ValueError):
        parse_roi(texto)


@pytest.mark.parametrize("texto", ["1,2,3", "1,2,1,2", "x,1,2,3"])
def test_linea_invalida(texto):
    with pytest.raises(ValueError):
        parse_linea(texto)


def test_mapeo_letterbox_ida_y_vuelta():
    m = Mapeo(Roi(960, 720, 500, 360), 416, "letterbox")
    assert m.contenido == (416, 312)
    x, y = m.a_completo(416.0, 312.0)
    assert (round(x), round(y)) == (1460, 1080)
    ex, ey = m.a_entrada(980.0, 720.0)
    assert m.a_completo(ex, ey) == pytest.approx((980.0, 720.0))


def test_mapeo_estirar():
    m = Mapeo(Roi(960, 720, 500, 360), 384, "estirar")
    assert m.contenido == (384, 384)
    assert m.a_completo(384.0, 384.0) == pytest.approx((1460.0, 1080.0))


def test_roi_escalado_al_secundario():
    r = roi_en_clip(parse_roi("960:720:500:360"), 704, 576)
    assert r == Roi(352, 384, 183, 192)
    assert r.x + r.w <= 704 and r.y + r.h <= 576
