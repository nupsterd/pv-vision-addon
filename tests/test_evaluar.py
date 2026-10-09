"""``tools/evaluar.py`` con ``cruces.jsonl`` inventados (sin video ni datos reales)."""

from __future__ import annotations

import contextlib
import csv
import io
import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import evaluar  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"
CSV_REAL = Path(__file__).resolve().parents[2] / "reportes" / "b7" / "b7_1_verdad_B_etiquetas.csv"


def _clip(nombre, grupo, ini, fin, cfg="yolox_nano@abc", dur=None):
    modelo, h = cfg.split("@")
    r = {"kind": "clip", "clip": nombre, "modelo": modelo, "cfg": h, "grupo": grupo,
         "inicio": ini, "fin": fin}  # fmt: skip
    if dur is not None:
        r["dur_video_s"] = dur
    return r


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
        _cruce(B, "2026-10-07T18:37:40.000-05:00", sentido="entrada", track=3, t_clip=13.0),  # E3: resta
        _clip(C2, 2, "2026-10-08T06:31:20-05:00", "2026-10-08T06:32:10-05:00"),
        _cruce(C2, "2026-10-08T06:31:25.000-05:00", track=1, t_clip=5.0),  # antes de "desde": solo con clips
        _cruce(C2, "2026-10-08T06:31:40.000-05:00", track=2, t_clip=20.0),
        _cruce(C2, "2026-10-08T06:31:50.000-05:00", track=3, t_clip=30.0),  # falso: real = 1
    ]


def test_solape_un_solo_clip_y_metricas(tmp_path):
    datos = evaluar.leer_cruces([_escribir(tmp_path / "c.jsonl", registros())])
    grupos = evaluar.leer_etiquetas(FIXTURES / "etiquetas_sinteticas.csv", evaluar.ZoneInfo("America/Bogota"))
    res = evaluar.evaluar(datos, grupos)["yolox_nano@abc"]
    # grupo 1: 2 salidas (la del solape una vez) − 1 entrada = 1; grupo 2: las 3 salidas del clip
    assert [(r.n, r.real, r.salidas, r.entradas, r.det, r.tp, r.fn, r.fp) for r in res] == [
        (1, 2, 2, 1, 1, 1, 1, 0),
        (2, 1, 3, 0, 3, 1, 0, 2),
    ]
    m = evaluar._metricas(res)
    assert (m["tp"], m["real"], m["fp"], m["sensibilidad"]) == (2, 3, 2, 0.667)
    md = evaluar._metricas(res, usar_dahua=True)
    assert (md["tp"], md["sensibilidad"]) == (2, 0.667)
    # nivel 2: grupo 1 debía alertar (2 > 1) y no alerta (1); grupo 2 no debía y alerta (3 > 1)
    assert (m["alerta_vp"], m["alerta_fp"], m["alerta_fn"], m["alerta_vn"]) == (0, 1, 1, 0)
    # con la ventana de la planilla, la salida previa a "desde" del grupo 2 queda afuera
    res_p = evaluar.evaluar(datos, grupos, "planilla")["yolox_nano@abc"]
    assert [(r.n, r.salidas, r.entradas, r.det) for r in res_p] == [(1, 2, 1, 1), (2, 2, 0, 2)]


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


TZ = evaluar.ZoneInfo("America/Bogota")
N15_A = "n15_2026-10-08_06.34.37-06.34.55.mp4"
N15_B = "n15_2026-10-08_06.34.50-06.35.08.mp4"


def _grupo(n, desde, hasta, real, marcaron=1, dahua=1, no_medibles=0):
    return evaluar.Grupo(n, desde, hasta, marcaron, dahua, real, "s", "off", no_medibles=no_medibles)


def _evaluar(tmp_path, regs, grupos, ventana="clips"):
    datos = evaluar.leer_cruces([_escribir(tmp_path / "c.jsonl", regs)])
    return evaluar.evaluar(datos, grupos, ventana)["yolox_nano@abc"]


def test_e1_solape_hasta_el_final_real_del_video_n15(tmp_path):
    """Caso n15: el clip A dura 18,52 s; la salida de 06:34:55.040 está en los dos clips."""
    regs = [
        _clip(N15_A, 15, "2026-10-08T06:34:37-05:00", "2026-10-08T06:34:55-05:00", dur=18.52),
        _clip(N15_B, 15, "2026-10-08T06:34:50-05:00", "2026-10-08T06:35:08-05:00", dur=18.4),
        _cruce(N15_A, "2026-10-08T06:34:42.000-05:00", track=1, t_clip=5.0),
        _cruce(N15_A, "2026-10-08T06:34:55.040-05:00", track=2, t_clip=18.04),
        _cruce(N15_B, "2026-10-08T06:34:55.040-05:00", track=1, t_clip=5.04),
    ]
    # Grupo y aserción sin los campos nuevos: el mismo test corre (y falla con det = 3) contra la versión anterior
    g = evaluar.Grupo(15, datetime(2026, 10, 8, 6, 34, 37, tzinfo=TZ), datetime(2026, 10, 8, 6, 35, 8, tzinfo=TZ),
                      1, 2, 2, "s", "off")  # fmt: skip
    datos = evaluar.leer_cruces([_escribir(tmp_path / "c.jsonl", regs)])
    (r,) = evaluar.evaluar(datos, [g])["yolox_nano@abc"]
    assert r.det == 2  # con el fin del nombre la salida de 55.040 contaba dos veces: det = 3
    assert (r.salidas, r.fp) == (2, 0)


def test_e1_sin_duracion_usa_el_fin_del_nombre(tmp_path):
    """Sin ``dur_video_s`` el solape termina en el fin del nombre (comportamiento anterior)."""
    regs = [
        _clip(N15_A, 15, "2026-10-08T06:34:37-05:00", "2026-10-08T06:34:55-05:00"),
        _clip(N15_B, 15, "2026-10-08T06:34:50-05:00", "2026-10-08T06:35:08-05:00"),
        _cruce(N15_A, "2026-10-08T06:34:52.000-05:00", track=1, t_clip=15.0),
        _cruce(N15_B, "2026-10-08T06:34:52.100-05:00", track=1, t_clip=2.1),
    ]
    datos = evaluar.leer_cruces([_escribir(tmp_path / "c.jsonl", regs)])
    a = datos.clips["yolox_nano@abc"][N15_A]
    assert a.dur_video_s is None and a.fin_efectivo == a.fin
    g = _grupo(15, datetime(2026, 10, 8, 6, 34, 37, tzinfo=TZ), datetime(2026, 10, 8, 6, 35, 8, tzinfo=TZ), 1)
    (r,) = evaluar.evaluar(datos, [g])["yolox_nano@abc"]
    assert (r.salidas, r.det) == (1, 1)


def test_e1_solape_con_entradas(tmp_path):
    """En el solape gana el clip con más cruces contando los dos sentidos; la entrada cuenta una vez."""
    regs = [
        _clip(N15_A, 15, "2026-10-08T06:34:37-05:00", "2026-10-08T06:34:55-05:00", dur=18.52),
        _clip(N15_B, 15, "2026-10-08T06:34:50-05:00", "2026-10-08T06:35:08-05:00", dur=18.4),
        _cruce(N15_A, "2026-10-08T06:34:40.000-05:00", track=1, t_clip=3.0),
        # solape [06:34:50, 06:34:55.52]: A ve 1 salida; B ve esa salida y una entrada ⇒ gana B
        _cruce(N15_A, "2026-10-08T06:34:51.000-05:00", track=2, t_clip=14.0),
        _cruce(N15_B, "2026-10-08T06:34:51.100-05:00", track=1, t_clip=1.1),
        _cruce(N15_B, "2026-10-08T06:34:54.000-05:00", sentido="entrada", track=2, t_clip=4.0),
        # la misma entrada vista por A a las 55.3 s (dentro del solape real, fuera del nombre)
        _cruce(N15_A, "2026-10-08T06:34:55.300-05:00", sentido="entrada", track=3, t_clip=18.3),
    ]
    g = _grupo(15, datetime(2026, 10, 8, 6, 34, 37, tzinfo=TZ), datetime(2026, 10, 8, 6, 35, 8, tzinfo=TZ), 1)
    (r,) = _evaluar(tmp_path, regs, [g])
    assert (r.salidas, r.entradas, r.det) == (2, 1, 1)


def test_e2_ventana_clips_contra_planilla(tmp_path):
    """Una salida real antes de ``desde`` cuenta con ``clips`` y no con ``planilla``."""
    clip = "n28_2026-10-08_06.36.10-06.36.50.mp4"
    regs = [
        _clip(clip, 28, "2026-10-08T06:36:10-05:00", "2026-10-08T06:36:50-05:00", dur=40.6),
        _cruce(clip, "2026-10-08T06:36:14.600-05:00", track=1, t_clip=4.6),
        _cruce(clip, "2026-10-08T06:36:40.000-05:00", track=2, t_clip=30.0),
    ]
    g = _grupo(28, datetime(2026, 10, 8, 6, 36, 34, tzinfo=TZ), datetime(2026, 10, 8, 6, 36, 50, tzinfo=TZ), 2)
    (rc,) = _evaluar(tmp_path, regs, [g], "clips")
    (rp,) = _evaluar(tmp_path, regs, [g], "planilla")
    assert (rc.det, rc.tp, rc.fn) == (2, 2, 0)
    assert (rp.det, rp.tp, rp.fn) == (1, 1, 1)
    out = tmp_path / "out"
    args = [str(tmp_path / "c.jsonl"), "--etiquetas", str(FIXTURES / "etiquetas_sinteticas.csv"), "--salida", str(out)]
    with contextlib.redirect_stdout(io.StringIO()):
        assert evaluar.main([*args, "--ventana", "planilla"]) == 0
    assert "Ventana: `planilla`" in (out / "evaluacion.md").read_text()
    with pytest.raises(ValueError):
        evaluar.evaluar(evaluar.leer_cruces([tmp_path / "c.jsonl"]), [g], "otra")


def test_e3_sale_entra_sale_es_una_persona(tmp_path):
    clip = "n02_2026-10-08_06.31.20-06.32.10.mp4"
    regs = [
        _clip(clip, 2, "2026-10-08T06:31:20-05:00", "2026-10-08T06:32:10-05:00", dur=50.4),
        _cruce(clip, "2026-10-08T06:31:30.000-05:00", track=1, t_clip=10.0),
        _cruce(clip, "2026-10-08T06:31:38.000-05:00", sentido="entrada", track=1, t_clip=18.0),
        _cruce(clip, "2026-10-08T06:31:55.000-05:00", track=1, t_clip=35.0),
    ]
    g = _grupo(2, datetime(2026, 10, 8, 6, 31, 27, tzinfo=TZ), datetime(2026, 10, 8, 6, 32, 3, tzinfo=TZ), 1)
    (r,) = _evaluar(tmp_path, regs, [g])
    assert (r.salidas, r.entradas, r.det, r.tp, r.fp) == (2, 1, 1, 1, 0)
    # solo entradas: det no baja de 0
    (r0,) = _evaluar(tmp_path, [regs[0], regs[2]], [g])
    assert (r0.salidas, r0.entradas, r0.det, r0.fn) == (0, 1, 0, 1)


def test_e4_no_medibles_ausente_vacio_y_uno(tmp_path):
    cab = "n,conjunto,fecha,desde,hasta,marcaron_checkout,cruces_dahua,personas_reales,juntas_separadas,luz,nota"
    fila = "1,B4,2026-10-07,18:37:04,18:37:45,1,2,2,s,on,dos salen"
    (tmp_path / "ausente.csv").write_text(f"{cab}\n{fila}\n", encoding="utf-8")
    (tmp_path / "vacio.csv").write_text(f"{cab},no_medibles\n{fila},\n", encoding="utf-8")
    (tmp_path / "uno.csv").write_text(f"{cab},no_medibles\n{fila},1\n", encoding="utf-8")
    regs = [
        _clip(A, 1, "2026-10-07T18:37:00-05:00", "2026-10-07T18:37:30-05:00", dur=30.2),
        _cruce(A, "2026-10-07T18:37:16.000-05:00", track=1, t_clip=16.0),
    ]
    jsonl = _escribir(tmp_path / "c.jsonl", regs)
    for nombre, nm, real_med, tp, fn, debia in (("ausente", 0, 2, 1, 1, 1), ("vacio", 0, 2, 1, 1, 1),
                                                 ("uno", 1, 1, 1, 0, 0)):  # fmt: skip
        out = tmp_path / nombre
        with contextlib.redirect_stdout(io.StringIO()):
            assert evaluar.main([str(jsonl), "--etiquetas", str(tmp_path / f"{nombre}.csv"), "--salida", str(out)]) == 0
        with (out / "evaluacion.csv").open(encoding="utf-8") as fh:
            (f,) = list(csv.DictReader(fh))
        assert (f["real"], f["no_medibles"], f["real_medible"]) == ("2", str(nm), str(real_med)), nombre
        assert (f["det"], f["tp"], f["fn"], f["debia_alertar"]) == ("1", str(tp), str(fn), str(debia)), nombre
        md = (out / "evaluacion.md").read_text(encoding="utf-8")
        assert f"No medibles `yolox_nano@abc`: {nm} de 2 personas reales" in md, nombre
    # la Dahua (2 cruces) también se mide contra real_medible: con 1 no medible, TP 1 y FP 1
    g = evaluar.leer_etiquetas(tmp_path / "uno.csv", TZ)
    (r,) = evaluar.evaluar(evaluar.leer_cruces([jsonl]), g)["yolox_nano@abc"]
    md = evaluar._metricas([r], usar_dahua=True)
    assert (md["real"], md["tp"], md["fp"], md["no_medibles"]) == (1, 1, 1, 1)


def test_dedup_entre_corridas_y_salida_md_csv(tmp_path):
    p1 = _escribir(tmp_path / "r1.jsonl", registros())
    p2 = _escribir(tmp_path / "r2.jsonl", registros())  # el mismo análisis repetido
    rc = evaluar.main([str(p1), str(p2), "--etiquetas", str(FIXTURES / "etiquetas_sinteticas.csv"),
                       "--salida", str(tmp_path / "out")])  # fmt: skip
    assert rc == 0
    md = (tmp_path / "out" / "evaluacion.md").read_text()
    assert "| yolox_nano@abc | 2 | 3 | 2 | 1 | 2 | 66.7% |" in md
    assert "Casos difíciles" in md and "juntas_separadas" in md and "Ventana: `clips`" in md
    csv_txt = (tmp_path / "out" / "evaluacion.csv").read_text().splitlines()
    assert csv_txt[0].startswith("config,n,real,dahua,det,tp,fn,fp,salidas,entradas,no_medibles,real_medible")
    assert len(csv_txt) == 3


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
    # stdout fuera de la captura de pytest y aserciones sin valores: nada del CSV real en la salida
    with contextlib.redirect_stdout(io.StringIO()):
        rc = evaluar.main([str(_escribir(tmp_path / "c.jsonl", regs)), "--etiquetas", str(CSV_REAL),
                           "--salida", str(tmp_path / "out")])  # fmt: skip
    assert rc == 0, "evaluar.py falló contra el CSV real"
    filas = (tmp_path / "out" / "evaluacion.csv").read_text().splitlines()
    ok = len(filas) > 1 and filas[1].startswith("yolox_nano@abc,25,2,1,2,2,0,0")
    assert ok, "la fila del grupo 25 no coincide con lo esperado"
