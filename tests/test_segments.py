from __future__ import annotations

import os
from pathlib import Path

import pytest

from pv_vision.segments import (
    GB,
    has_enough_space,
    list_segments,
    parse_segment_name,
    purge_expired,
    segment_name,
    segment_template,
    select_expired,
)
from tests.conftest import BOGOTA, bogota


def test_nombre_de_segmento_en_hora_local():
    assert segment_name(bogota(2026, 10, 5, 6, 20, 3), "main") == "2026-10-05_06-20-03_main.mp4"
    assert segment_template("sub") == "%Y-%m-%d_%H-%M-%S_sub.mp4"


def test_parse_segment_name_valido():
    assert parse_segment_name("2026-10-05_06-20-03_sub.mp4", BOGOTA) == (bogota(2026, 10, 5, 6, 20, 3), "sub")


@pytest.mark.parametrize(
    "nombre",
    [
        "2026-10-05_06-20-03_main.mp4.tmp",
        "2026-10-05_06-20-03_otro.mp4",
        "2026-10-05_06-20_main.mp4",
        "2026-02-30_06-20-03_main.mp4",
        "x2026-10-05_06-20-03_main.mp4",
        "clip_0001.mp4",
        "conjunto_b",
    ],
)
def test_parse_segment_name_rechaza(nombre):
    assert parse_segment_name(nombre, BOGOTA) is None


def _touch(path: Path, size: int = 10, mtime: float | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\0" * size)
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


def test_list_segments_primer_nivel_sin_symlinks_ni_ajenos(tmp_path):
    _touch(tmp_path / "2026-10-05_06-20-03_main.mp4", 5)
    _touch(tmp_path / "2026-10-05_06-25-03_sub.mp4", 7)
    _touch(tmp_path / "notas.txt")
    _touch(tmp_path / "otra" / "2026-10-05_06-20-03_main.mp4")
    (tmp_path / "2026-10-05_06-30-03_main.mp4").symlink_to(tmp_path / "notas.txt")
    segs = list_segments(tmp_path, BOGOTA)
    assert [(s.path.name, s.size) for s in segs] == [
        ("2026-10-05_06-20-03_main.mp4", 5),
        ("2026-10-05_06-25-03_sub.mp4", 7),
    ]
    assert [s.path.name for s in list_segments(tmp_path, BOGOTA, "sub")] == ["2026-10-05_06-25-03_sub.mp4"]
    assert list_segments(tmp_path / "no-existe", BOGOTA) == []


def test_retencion_por_fecha_del_nombre_no_por_mtime(tmp_path):
    now = bogota(2026, 11, 10, 12, 0)
    # Nombre viejo con mtime de hoy: se borra. Nombre nuevo con mtime de hace un año: se queda.
    viejo = _touch(tmp_path / "2026-10-01_06-20-00_main.mp4", mtime=now.timestamp())
    nuevo = _touch(tmp_path / "2026-11-09_06-20-00_main.mp4", mtime=now.timestamp() - 365 * 86400)
    expirados = select_expired(list_segments(tmp_path, BOGOTA), now, 30)
    assert [s.path for s in expirados] == [viejo]
    assert nuevo.exists()


def test_borde_exacto_de_retencion(tmp_path):
    now = bogota(2026, 11, 10, 12, 0, 0)
    justo = _touch(tmp_path / "2026-10-11_12-00-00_main.mp4")  # exactamente 30 días: se queda
    pasado = _touch(tmp_path / "2026-10-11_11-59-59_main.mp4")  # 30 días + 1 s: se borra
    assert [s.path for s in select_expired(list_segments(tmp_path, BOGOTA), now, 30)] == [pasado]
    assert justo.exists()


def test_purge_no_toca_conjunto_b_ni_subcarpetas_ni_ajenos(tmp_path):
    """/media/pv_vision/conjunto_b/ guarda los clips de evaluación: la retención no entra."""
    now = bogota(2026, 11, 10, 12, 0)
    clips = [
        _touch(tmp_path / "conjunto_b" / "clip_2830.mp4"),
        _touch(tmp_path / "conjunto_b" / "2026-01-01_00-00-00_main.mp4"),  # cumple el patrón, pero es recursivo
        _touch(tmp_path / "conjunto_b" / "sub" / "2025-01-01_00-00-00_sub.mp4"),
    ]
    ajeno = _touch(tmp_path / "2020-01-01_00-00-00_main.mp4.bak")
    vencido = _touch(tmp_path / "2026-09-01_06-20-00_main.mp4", 123)
    (tmp_path / "2020-01-01_00-00-00_sub.mp4").symlink_to(clips[0])
    borrados = purge_expired(tmp_path, now, 30)
    assert [(s.path.name, s.size) for s in borrados] == [("2026-09-01_06-20-00_main.mp4", 123)]
    assert not vencido.exists()
    assert all(c.exists() for c in clips) and ajeno.exists()
    assert (tmp_path / "2020-01-01_00-00-00_sub.mp4").is_symlink()


def test_purge_con_retencion_baja_sobre_archivos_de_prueba(tmp_path):
    now = bogota(2026, 10, 8, 18, 0)
    for d in range(1, 8):
        _touch(tmp_path / f"2026-10-0{d}_06-20-00_main.mp4")
    borrados = purge_expired(tmp_path, now, 2)
    # Límite = 2026-10-06 18:00: el segmento de las 06:20 del 6 ya tiene más de 2 días.
    assert [s.start.day for s in borrados] == [1, 2, 3, 4, 5, 6]
    assert sorted(p.name[:10] for p in tmp_path.iterdir()) == ["2026-10-07"]


def test_umbral_de_espacio():
    assert has_enough_space(10 * GB, 10)
    assert not has_enough_space(10 * GB - 1, 10)
    assert has_enough_space(50 * GB, 10)
