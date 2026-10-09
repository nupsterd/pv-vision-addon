from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from pv_vision.analisis.clips import listar, parse_nombre
from tests.conftest import BOGOTA, bogota


def test_nombre_conjunto_b():
    c = parse_nombre(Path("/m/n25_2026-10-07_18.37.04-18.37.45.mp4"), BOGOTA)
    assert c is not None and c.conjunto == "B" and c.grupo == 25
    assert c.inicio == bogota(2026, 10, 7, 18, 37, 4) and c.fin == bogota(2026, 10, 7, 18, 37, 45)
    assert c.hora_abs(12.36) == bogota(2026, 10, 7, 18, 37, 4) + timedelta(seconds=12.36)


def test_nombre_conjunto_c():
    c = parse_nombre(Path("2026-10-09_06-20-03_main.mp4"), BOGOTA)
    assert c is not None and c.conjunto == "C" and c.grupo is None and c.fin is None
    assert c.hora_abs(1.5).isoformat(timespec="milliseconds") == "2026-10-09T06:20:04.500-05:00"


def test_clip_b_que_cruza_la_medianoche():
    c = parse_nombre(Path("n01_2026-10-07_23.59.50-00.00.20.mp4"), BOGOTA)
    assert c is not None and c.fin == bogota(2026, 10, 8, 0, 0, 20)


def test_nombres_invalidos():
    for n in (
        "clip.mp4",
        "n1_2026-10-07_18-37-04.mp4",
        "2026-10-09_06-20-03_otro.mp4",
        "n25_2026-13-07_18.37.04-18.37.45.mp4",
    ):
        assert parse_nombre(Path(n), BOGOTA) is None


def test_listar_primer_nivel_sin_symlinks_ordenado(tmp_path):
    for n in ("n02_2026-10-01_06.34.38-06.35.12.mp4", "n01_2026-09-30_16.43.42-16.44.07.mp4", "otro.mp4"):
        (tmp_path / n).write_bytes(b"x")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "n03_2026-10-02_06.38.16-06.38.47.mp4").write_bytes(b"x")
    (tmp_path / "n04_2026-10-03_06.38.16-06.38.47.mp4").symlink_to(tmp_path / "otro.mp4")
    assert [c.grupo for c in listar(tmp_path, BOGOTA)] == [1, 2]
    assert listar(tmp_path / "no-existe", BOGOTA) == []
