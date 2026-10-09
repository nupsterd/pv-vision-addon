"""Corrida del análisis con decodificador y detector falsos (sin ffmpeg, onnxruntime ni pesos)."""

from __future__ import annotations

import json
import os
from datetime import timedelta
from pathlib import Path

import numpy as np
import pytest

from pv_vision.analisis import corrida as corrida_mod
from pv_vision.analisis.corrida import Corrida, hash_parametros
from pv_vision.analisis.decodificar import Cuadro
from pv_vision.analisis.geometria import Mapeo
from pv_vision.media import InfoVideo
from tests.conftest import bogota, make_config

# ROI 960x720 en (100,300); línea vertical x=600 dirigida hacia arriba ⇒ izq_a_der = de x<600 a x>600.
OPCIONES = {"roi": "960:720:100:300", "linea": "600,1000,600,300", "analisis_cada_n": 3}
RECORRIDO = list(range(450, 760, 25))  # una persona que cruza hacia la derecha (salida)


class FakeDecodificador:
    instancias: list[FakeDecodificador] = []

    def __init__(self, clip: Path, mapeo: Mapeo, cada_n: int, pix_fmt: str) -> None:
        self.clip, self.mapeo, self.cada_n, self.pix_fmt = clip, mapeo, cada_n, pix_fmt
        self.espera_s = 0.0
        FakeDecodificador.instancias.append(self)

    def __iter__(self):
        for i in range(len(RECORRIDO)):
            yield Cuadro(i, round(i * 0.12, 3), np.zeros((self.mapeo.size, self.mapeo.size, 3), np.uint8))


class FakeDetector:
    """Devuelve la caja de la persona del RECORRIDO en coordenadas de ENTRADA del modelo."""

    def __init__(self, mapeo: Mapeo) -> None:
        self.mapeo, self.i = mapeo, 0

    def __call__(self, _img, _conf):
        x = RECORRIDO[min(self.i, len(RECORRIDO) - 1)]
        self.i += 1
        x1, y1 = self.mapeo.a_entrada(x - 30.0, 700.0)
        x2, y2 = self.mapeo.a_entrada(x + 30.0, 900.0)
        return np.array([[x1, y1, x2, y2]], np.float32), np.array([0.9], np.float32)


@pytest.fixture
def entorno(tmp_path, monkeypatch):
    FakeDecodificador.instancias = []
    monkeypatch.setattr(corrida_mod, "Decodificador", FakeDecodificador)
    media = tmp_path / "media"
    (media / "conjunto_b").mkdir(parents=True)
    viejo = os.path.getmtime(media) - 3600

    def nuevo_clip(nombre: str, contenido: bytes = b"video") -> Path:
        p = media / "conjunto_b" / nombre
        p.write_bytes(contenido)
        os.utime(p, (viejo, viejo))
        return p

    def factory(modelo, _hilos):
        from pv_vision.analisis.geometria import parse_roi

        return FakeDetector(Mapeo(parse_roi(OPCIONES["roi"]), modelo.size, modelo.preproc))

    def probe(path: Path):
        return None if path.stat().st_size < 4 else InfoVideo(30.0, 1920, 1080, "hevc", 25.0)

    def hacer(**over) -> Corrida:
        cfg = make_config(**{**OPCIONES, **over})
        return Corrida(
            cfg,
            base=tmp_path / "analisis",
            media=media,
            detector_factory=factory,
            probe=probe,
            ahora=lambda: bogota(2026, 10, 10, 9, 0, 0),
        )

    return hacer, nuevo_clip, tmp_path / "analisis", media


def _registros(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text().splitlines()]


def test_cruce_con_hora_absoluta_y_resumen_de_clip(entorno):
    hacer, nuevo_clip, base, _media = entorno
    nuevo_clip("n25_2026-10-07_18.37.04-18.37.45.mp4")
    c = hacer()
    assert c.pasada() == 1
    regs = _registros(base / c.run_id / "cruces.jsonl")
    cruces = [r for r in regs if r["kind"] == "cruce"]
    clip = [r for r in regs if r["kind"] == "clip"]
    assert len(cruces) == 1 and cruces[0]["sentido"] == "salida" and cruces[0]["modelo"] == "yolox_nano"
    esperado = bogota(2026, 10, 7, 18, 37, 4) + timedelta(seconds=cruces[0]["t_clip"])
    assert cruces[0]["t_abs"] == esperado.isoformat(timespec="milliseconds")
    assert cruces[0]["cfg"] == hash_parametros(c.cfg, "yolox_nano")
    r = clip[0]
    assert r["grupo"] == 25 and r["salidas"] == 1 and r["entradas"] == 0 and r["cuadros"] == len(RECORRIDO)
    for k in ("fps_proc", "cuadros_por_s_video", "ms_decod", "ms_detector", "ms_tracker", "cpu_proceso_pct",
              "cpu_total_pct", "rss_mb", "temp_max_c", "freq_min_mhz"):  # fmt: skip
        assert k in r
    resumen = json.loads((base / c.run_id / "resumen.json").read_text())
    assert resumen["clips"] == 1 and resumen["salidas"] == 1


def test_idempotente_y_reanaliza_si_cambian_parametros(entorno):
    hacer, nuevo_clip, _base, _media = entorno
    nuevo_clip("n01_2026-09-30_16.43.42-16.44.07.mp4")
    assert hacer().pasada() == 1
    assert hacer().pasada() == 0  # mismo clip, mismos parámetros
    assert hacer(histeresis_px=20).pasada() == 1  # cambia el hash ⇒ se reanaliza
    assert hacer(analisis_modelos=["yolox_nano", "yolox_tiny"], histeresis_px=20).pasada() == 1  # solo el nuevo


def test_clip_invalido_se_saltea_y_queda_marcado(entorno):
    hacer, nuevo_clip, base, _media = entorno
    nuevo_clip("n02_2026-10-01_06.34.38-06.35.12.mp4", b"x")
    c = hacer()
    assert c.pasada() == 0 and c.totales["salteados"] == 1
    hechos = json.loads((base / "hechos.json").read_text())
    assert list(hechos.values())[0]["estado"] == "salteado"
    assert hacer().pasada() == 0 and FakeDecodificador.instancias == []


def test_archivo_recien_modificado_se_deja_para_despues(entorno):
    hacer, nuevo_clip, _base, _media = entorno
    p = nuevo_clip("n03_2026-10-02_06.38.16-06.38.47.mp4")
    os.utime(p, None)  # mtime = ahora
    assert hacer().pasada() == 0


def test_pausa_entre_clips(entorno):
    hacer, nuevo_clip, _base, _media = entorno
    nuevo_clip("n01_2026-09-30_16.43.42-16.44.07.mp4")
    nuevo_clip("n02_2026-10-01_06.34.38-06.35.12.mp4")
    llamadas = []

    def pausar():
        llamadas.append(1)
        return len(llamadas) > 1  # deja pasar el primer clip y pausa antes del segundo

    assert hacer().pasada(pausar=pausar) == 1
    assert hacer().pasada() == 1  # retoma: solo queda el segundo


def test_interrumpido_no_se_marca(entorno):
    hacer, nuevo_clip, _base, _media = entorno
    nuevo_clip("n01_2026-09-30_16.43.42-16.44.07.mp4")
    c = hacer()
    original = c.analizar_clip

    def con_sigterm(*a, **k):
        c.detener = True
        return original(*a, **k)

    c.analizar_clip = con_sigterm
    assert c.pasada() == 0
    assert hacer().pasada() == 1


def test_pedir_depuracion_reanaliza_ese_clip(entorno, monkeypatch):
    hacer, nuevo_clip, _base, media = entorno
    nombre = "n01_2026-09-30_16.43.42-16.44.07.mp4"
    nuevo_clip(nombre)
    assert hacer().pasada() == 1
    escritos = []

    class FakeVideo:
        def __init__(self, destino, *a, **k):
            self.destino = destino
            escritos.append(destino)

        def cuadro(self, *a):
            pass

        def cerrar(self):
            return True

    monkeypatch.setattr(corrida_mod, "VideoDepuracion", FakeVideo)
    assert hacer(depuracion_clips=[nombre]).pasada() == 1
    assert escritos == [
        media / "depuracion" / "20261010-090000" / "n01_2026-09-30_16.43.42-16.44.07_yolox_nano_dbg.mp4"
    ]


def test_analisis_dir_symlink_fuera_de_media_se_rechaza(entorno, tmp_path):
    hacer, _nuevo_clip, _base, media = entorno
    afuera = tmp_path / "afuera"
    afuera.mkdir()
    (afuera / "n01_2026-09-30_16.43.42-16.44.07.mp4").write_bytes(b"video")
    (media / "trampa").symlink_to(afuera)
    assert hacer(analisis_dir="/media/pv_vision/trampa").pendientes() == []
