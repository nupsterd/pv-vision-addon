from __future__ import annotations

from datetime import time

import pytest

from pv_vision.schedule import (
    Window,
    active_window,
    next_window_start,
    parse_window,
    parse_windows,
    validate_days,
    window_end,
)
from tests.conftest import bogota

DEFAULT = parse_windows(["06:20-06:50", "11:50-12:20", "18:00-18:50"])
HABILES = [1, 2, 3, 4, 5]
# 2026-10-05 = lunes; 2026-10-09 = viernes; 2026-10-10 = sábado.


def test_parse_window():
    assert parse_window("06:20-06:50") == Window(time(6, 20), time(6, 50))
    assert str(parse_window(" 18:00-18:50 ")) == "18:00-18:50"


@pytest.mark.parametrize("texto", ["23:30-00:30", "18:50-18:00", "06:00-06:00", "6:00-7:00", "06:60-07:00", "x"])
def test_parse_window_rechaza(texto):
    with pytest.raises(ValueError):
        parse_window(texto)


def test_parse_windows_ordena_y_admite_ventanas_contiguas():
    ws = parse_windows(["11:50-12:20", "06:20-06:50", "12:20-12:30"])
    assert [str(w) for w in ws] == ["06:20-06:50", "11:50-12:20", "12:20-12:30"]


def test_parse_windows_rechaza_solape():
    with pytest.raises(ValueError, match="solapan"):
        parse_windows(["06:00-07:00", "06:59-08:00"])


def test_validate_days():
    assert validate_days([5, 1, 3]) == [1, 3, 5]
    for malo in ([], [0], [8], [2, 2], [True]):
        with pytest.raises(ValueError):
            validate_days(malo)


@pytest.mark.parametrize(
    ("hora", "esperada"),
    [
        ((6, 19, 59), None),
        ((6, 20, 0), "06:20-06:50"),  # el inicio está incluido
        ((6, 49, 59), "06:20-06:50"),
        ((6, 50, 0), None),  # el fin está excluido
        ((12, 0, 0), "11:50-12:20"),
        ((18, 49, 0), "18:00-18:50"),
        ((23, 0, 0), None),
    ],
)
def test_grabo_ahora_dia_habil(hora, esperada):
    w = active_window(bogota(2026, 10, 5, *hora), DEFAULT, HABILES)
    assert (str(w) if w else None) == esperada


def test_no_graba_fin_de_semana():
    assert active_window(bogota(2026, 10, 10, 12, 0), DEFAULT, HABILES) is None
    assert active_window(bogota(2026, 10, 11, 18, 10), DEFAULT, HABILES) is None
    assert active_window(bogota(2026, 10, 10, 12, 0), DEFAULT, [6]) is not None


def test_window_end():
    now = bogota(2026, 10, 5, 18, 10, 33)
    assert window_end(now, parse_window("18:00-18:50")) == bogota(2026, 10, 5, 18, 50)


def test_proxima_ventana_mismo_dia():
    assert next_window_start(bogota(2026, 10, 5, 7, 0), DEFAULT, HABILES) == bogota(2026, 10, 5, 11, 50)


def test_proxima_ventana_estando_dentro_de_una_es_la_siguiente():
    assert next_window_start(bogota(2026, 10, 5, 6, 30), DEFAULT, HABILES) == bogota(2026, 10, 5, 11, 50)


def test_proxima_ventana_del_viernes_noche_es_el_lunes():
    assert next_window_start(bogota(2026, 10, 9, 19, 0), DEFAULT, HABILES) == bogota(2026, 10, 12, 6, 20)


def test_proxima_ventana_un_solo_dia_por_semana():
    assert next_window_start(bogota(2026, 10, 5, 6, 30), DEFAULT, [1]) == bogota(2026, 10, 5, 11, 50)
    assert next_window_start(bogota(2026, 10, 5, 19, 0), DEFAULT, [1]) == bogota(2026, 10, 12, 6, 20)


def test_proxima_ventana_sin_dias():
    assert next_window_start(bogota(2026, 10, 5, 7, 0), DEFAULT, []) is None
