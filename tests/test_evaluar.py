"""``tools/evaluar.py`` con ``cruces.jsonl`` inventados (sin video ni datos reales)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import evaluar  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"
CSV_REAL = Path(__file__).resolve().parents[2] / "reportes" / "b7" / "b7_1_verdad_B_etiquetas.csv"


def _clip(nombre, grupo, ini, fin, cfg="yolox_nano@abc"):
    modelo, h = cfg.split("@")
    return {"kind": "clip", "clip": nombre, "modelo": modelo, "cfg": h, "grupo": grupo,
            "inicio": ini, "fin": fin}  # fmt: skip


def _cruce(nombre, t_abs, sentido="salida", track=1, t_clip=1.0, cfg="yolox_nano@abc"):
    modelo, h = cfg.split("@")
    return {"kind": "cruce", "clip": nombre, "modelo": modelo, "cfg": h, "t_abs": t_abs, "sentido": sentido,
            "track": track, "t_clip": t_clip}  # fmt: skip


def _escribir(path: Path, regs: list[dict]) -> Path:
    path.write_text("\n".join(json.dumps(r) for r in regs) + "\n", encoding="utf-8")
    return path


A = "n01_2026-10-07_18.37.00-18.37.30.mp4"
B = "n01_2026-10-07_18.37.27-18.37.50.mp4"
C2 = "n02_2026-10-08_06.31.20-06.32.10.mp4"


def registros():
    return [
        _clip(A, 1, "2026-10-07T18:37:00-05:00", "2026-10-07T18:37:30-05:00"),
        _clip(B, 1, "2026-10-07T18:37:27-05:00", "2026-10-07T18:37:50-05:00"),
        _cruce(A, "2026-10-07T18:37:16.000-05:00", track=1, t_clip=16.0),
        # la misma persona, en el tramo solapado 27-30 s, vista por los dos clips: cuenta una vez
        _cruce(A, "2026-10-07T18:37:28.400-05:00", track=2, t_clip=28.4),
        _cruce(B, "2026-10-07T18:37:28.500-05:00", track=1, t_clip=1.5),
        _cruce(B, "2026-10-07T18:37:40.000-05:00", sentido="entrada", track=3, t_clip=13.0),
        _clip(C2, 2, "2026-10-08T06:31:20-05:00", "2026-10-08T06:32:10-05:00"),
        _cruce(C2, "2026-10-08T06:31:25.000-05:00", track=1, t_clip=5.0),  # antes de "desde": no cuenta
        _cruce(C2, "2026-10-08T06:31:40.000-05:00", track=2, t_clip=20.0),
        _cruce(C2, "2026-10-08T06:31:50.000-05:00", track=3, t_clip=30.0),  # falso: real = 1
    ]


def test_solape_un_solo_clip_y_metricas(tmp_path):
    datos = evaluar.leer_cruces([_escribir(tmp_path / "c.jsonl", registros())])
    grupos = evaluar.leer_etiquetas(FIXTURES / "etiquetas_sinteticas.csv", evaluar.ZoneInfo("America/Bogota"))
    res = evaluar.evaluar(datos, grupos)["yolox_nano@abc"]
    assert [(r.n, r.real, r.det, r.tp, r.fn, r.fp) for r in res] == [(1, 2, 2, 2, 0, 0), (2, 1, 2, 1, 0, 1)]
    m = evaluar._metricas(res)
    assert (m["tp"], m["real"], m["fp"], m["sensibilidad"]) == (3, 3, 1, 1.0)
    md = evaluar._metricas(res, usar_dahua=True)
    assert (md["tp"], md["sensibilidad"]) == (2, 0.667)
    # nivel 2: grupo 1 debía alertar (2 > 1) y alerta; grupo 2 no debía y alerta (2 > 1) ⇒ FP de alerta
    assert (m["alerta_vp"], m["alerta_fp"], m["alerta_fn"], m["alerta_vn"]) == (1, 1, 0, 0)


def test_solape_gana_el_clip_con_mas_cruces():
    tz = evaluar.ZoneInfo("America/Bogota")
    from datetime import datetime

    a = evaluar.ClipInfo(
        A, 1, datetime(2026, 10, 7, 18, 37, 0, tzinfo=tz), datetime(2026, 10, 7, 18, 37, 30, tzinfo=tz)
    )
    b = evaluar.ClipInfo(
        B, 1, datetime(2026, 10, 7, 18, 37, 27, tzinfo=tz), datetime(2026, 10, 7, 18, 37, 50, tzinfo=tz)
    )
    cr = [
        {"clip": B, "sentido": "salida", "_t": datetime(2026, 10, 7, 18, 37, 28, tzinfo=tz)},
        {"clip": B, "sentido": "salida", "_t": datetime(2026, 10, 7, 18, 37, 29, tzinfo=tz)},
        {"clip": A, "sentido": "salida", "_t": datetime(2026, 10, 7, 18, 37, 29, 500000, tzinfo=tz)},
    ]
    sal = evaluar.salidas_del_grupo([a, b], cr)
    assert [r["clip"] for r in sal] == [B, B]  # A tenía 1 en el solape, B 2: gana B


def test_dedup_entre_corridas_y_salida_md_csv(tmp_path):
    p1 = _escribir(tmp_path / "r1.jsonl", registros())
    p2 = _escribir(tmp_path / "r2.jsonl", registros())  # el mismo análisis repetido
    rc = evaluar.main([str(p1), str(p2), "--etiquetas", str(FIXTURES / "etiquetas_sinteticas.csv"),
                       "--salida", str(tmp_path / "out")])  # fmt: skip
    assert rc == 0
    md = (tmp_path / "out" / "evaluacion.md").read_text()
    assert "| yolox_nano@abc | 2 | 3 | 3 | 0 | 1 | 100.0% |" in md
    assert "Casos difíciles" in md and "juntas_separadas" in md
    csv_txt = (tmp_path / "out" / "evaluacion.csv").read_text().splitlines()
    assert csv_txt[0].startswith("config,n,real,dahua,det,tp,fn,fp") and len(csv_txt) == 3


def test_sin_registros_de_clip(tmp_path):
    p = _escribir(tmp_path / "c.jsonl", [r for r in registros() if r["kind"] == "cruce"])
    assert evaluar.main([str(p), "--etiquetas", str(FIXTURES / "etiquetas_sinteticas.csv")]) == 1


@pytest.mark.skipif(not CSV_REAL.exists(), reason="el CSV real de B no se versiona: solo en el ThinkPad")
def test_contra_el_csv_real(tmp_path):
    """Un cruces.jsonl inventado para el grupo 25 (2 reales, Dahua 1) contra las etiquetas reales."""
    n25 = "n25_2026-10-07_18.37.04-18.37.45.mp4"
    regs = [
        _clip(n25, 25, "2026-10-07T18:37:04-05:00", "2026-10-07T18:37:45-05:00"),
        _cruce(n25, "2026-10-07T18:37:16.200-05:00", track=1, t_clip=12.2),
        _cruce(n25, "2026-10-07T18:37:29.000-05:00", track=2, t_clip=25.0),
    ]
    rc = evaluar.main([str(_escribir(tmp_path / "c.jsonl", regs)), "--etiquetas", str(CSV_REAL),
                       "--salida", str(tmp_path / "out")])  # fmt: skip
    assert rc == 0
    filas = (tmp_path / "out" / "evaluacion.csv").read_text().splitlines()
    assert filas[1].startswith("yolox_nano@abc,25,2,1,2,2,0,0")
