"""Integración del análisis: ffmpeg + ONNX Runtime + YOLOX Nano reales sobre un video PÚBLICO.

Ningún cuadro de la oficina. El video es ``vtest.avi`` de ``opencv/opencv`` (``samples/data``,
Apache-2.0: peatones en una plaza), que se baja en la corrida (no se versiona) y se pasa por
``PV_VISION_VIDEO_PUBLICO``. Corre dentro de la imagen del add-on (modelos en /opt/modelos).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from datetime import timedelta
from pathlib import Path

import pytest

from pv_vision.analisis.corrida import Corrida
from pv_vision.analisis.modelos import MODELOS
from tests.conftest import BOGOTA, bogota, make_config

VIDEO = os.environ.get("PV_VISION_VIDEO_PUBLICO", "")
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not (shutil.which("ffmpeg") and VIDEO and Path(VIDEO).exists() and Path(MODELOS["yolox_nano"].ruta).exists()),
        reason="requiere ffmpeg, /opt/modelos y PV_VISION_VIDEO_PUBLICO",
    ),
]

NOMBRE = "n01_2026-10-08_12.00.00-12.00.20.mp4"


@pytest.fixture(scope="module")
def clip_publico(tmp_path_factory) -> Path:
    """vtest.avi → H.265 1080p25 de 20 s con nombre del conjunto B (como los clips de la Dahua)."""
    d = tmp_path_factory.mktemp("media") / "conjunto_b"
    d.mkdir()
    out = d / NOMBRE
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", VIDEO, "-t", "20",
         "-vf", "fps=25,scale=1920:1080", "-c:v", "libx265", "-preset", "ultrafast",
         "-x265-params", "log-level=none:keyint=50:bframes=0", "-tag:v", "hvc1", str(out)],
        check=True,
    )  # fmt: skip
    viejo = out.stat().st_mtime - 3600
    os.utime(out, (viejo, viejo))
    return out


def _probe(path: Path) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_name,width,height:format=duration",
         "-of", "json", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout  # fmt: skip
    return json.loads(out)


def test_yolox_nano_de_punta_a_punta_con_depuracion(clip_publico, tmp_path):
    media = clip_publico.parent.parent
    cfg = make_config(
        analisis_activo=True, roi="1920:1080:0:0", linea="960,1080,960,0", analisis_hilos=2,
        depuracion_clips=[NOMBRE],
    )  # fmt: skip
    c = Corrida(cfg, base=tmp_path / "analisis", media=media, ahora=lambda: bogota(2026, 10, 10, 21, 0, 0))
    assert c.pasada() == 1
    regs = [json.loads(x) for x in (c.dir / "cruces.jsonl").read_text().splitlines()]
    clip = next(r for r in regs if r["kind"] == "clip")
    cruces = [r for r in regs if r["kind"] == "cruce"]
    assert 160 <= clip["cuadros"] <= 170  # 500 cuadros, 1 de cada 3
    assert clip["fps_proc"] > 0 and clip["ms_detector"] > 0 and clip["cpu_proceso_pct"] > 0
    assert clip["resolucion"] == "1920x1080" and clip["entrada_modelo"] == 416
    assert len(cruces) >= 1, "en 20 s de vtest.avi cruzan peatones la línea del medio"
    inicio = bogota(2026, 10, 8, 12, 0, 0)
    for r in cruces:
        assert r["t_abs"] == (inicio + timedelta(seconds=r["t_clip"])).isoformat(timespec="milliseconds")
        assert r["sentido"] in ("salida", "entrada")
    dbg = media / "depuracion" / c.run_id / f"{clip_publico.stem}_yolox_nano_dbg.mp4"
    info = _probe(dbg)
    assert info["streams"][0]["codec_name"] == "h264" and info["streams"][0]["width"] == 416
    assert 18 < float(info["format"]["duration"]) < 22
    # idempotente: la segunda pasada no repite
    c2 = Corrida(cfg, base=tmp_path / "analisis", media=media, ahora=lambda: bogota(2026, 10, 10, 21, 5, 0))
    assert c2.pasada() == 0


def test_cuadro_de_referencia_png(clip_publico):
    from pv_vision.analisis.__main__ import generar_referencia

    media = clip_publico.parent.parent
    cfg = make_config(referencia_desde=f"conjunto_b/{NOMBRE}")
    png = generar_referencia(cfg, media)
    assert png is not None and png.parent == media / "referencia" and png.name.startswith("ref_n01_")
    info = _probe(png)
    assert info["streams"][0]["codec_name"] == "png"
    assert (info["streams"][0]["width"], info["streams"][0]["height"]) == (1920, 1080)
    assert generar_referencia(cfg, media) == png  # idempotente


def test_hora_de_los_nombres_en_bogota():
    assert BOGOTA.key == "America/Bogota"
