from __future__ import annotations

import logging
import signal
from pathlib import Path
from urllib.parse import quote

import pytest

from pv_vision.media import InfoVideo
from pv_vision.recorder import Recorder
from tests.conftest import CAMERA_HOST, CAMERA_PASSWORD, CAMERA_USER, FakeAudit, FakeMono, FakePopen

ENC = quote(CAMERA_PASSWORD, safe="")


@pytest.fixture
def env(tmp_path):
    """Recorder con ffmpeg falso. ``probe`` da por válido todo archivo de >= 100 bytes (ffprobe real
    en los tests de integración); los más chicos son "sin video" (como el MP4 de 28 bytes)."""
    mono = FakeMono()
    audit = FakeAudit()
    popen = FakePopen()
    out = tmp_path / "media"
    rec = Recorder(
        host=CAMERA_HOST,
        user=CAMERA_USER,
        password=CAMERA_PASSWORD,
        stream="main",
        segment_seconds=300,
        tz="America/Bogota",
        out_dir=out,
        tmp_dir=tmp_path / "tmp",
        audit=audit,
        popen=popen,
        monotonic=mono,
        probe=fake_probe,
    )
    return rec, mono, audit, popen, out, tmp_path / "tmp"


def fake_probe(path: Path) -> InfoVideo | None:
    size = path.stat().st_size
    if size < 100:
        return None
    # 100 bytes ⇒ 0,5 s: permite probar el descarte por duración.
    return InfoVideo(0.5 if size == 100 else 300.0, 1920, 1080, "hevc")


def _grow(out: Path, name: str, size: int) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / name).write_bytes(b"\0" * size)


def test_arranca_con_ffconcat_600_y_sin_secretos_en_argumentos(env):
    rec, _mono, _audit, popen, _out, tmp = env
    rec.tick(True)
    assert rec.recording
    texto, modo = popen.concat_seen[0]
    assert modo == 0o600
    assert f"file 'rtsp://{CAMERA_USER}:{ENC}@{CAMERA_HOST}:554/cam/realmonitor?channel=1&subtype=0'" in texto
    args = " ".join(popen.last.args)
    assert CAMERA_PASSWORD not in args and ENC not in args and "rtsp://" not in args
    assert popen.last.kwargs["env"]["TZ"] == "America/Bogota"
    assert len(list(tmp.glob("*.ffconcat"))) == 1


def test_fin_de_ventana_sigint_255_es_normal_y_borra_el_ffconcat(env):
    rec, mono, audit, popen, out, tmp = env
    rec.tick(True)
    _grow(out, "2026-10-05_06-20-00_main.mp4", 1000)
    mono.advance(1)
    rec.tick(True)
    _grow(out, "2026-10-05_06-25-00_main.mp4", 500)
    mono.advance(1)
    rec.tick(True)
    rec.tick(False)
    assert popen.last.signals == [signal.SIGINT]
    assert not popen.last.killed
    assert not rec.recording
    assert list(tmp.glob("*.ffconcat")) == []
    assert audit.kinds() == ["segment_created", "segment_created"]  # sin "reconnect"
    assert [r["file"] for r in audit.records] == ["2026-10-05_06-20-00_main.mp4", "2026-10-05_06-25-00_main.mp4"]
    assert rec.counters.segments == 2 and rec.counters.bytes == 1500 and rec.counters.reconnects == 0


def test_sigkill_si_no_termina_con_sigint(env, tmp_path):
    rec, _mono, _audit, popen, _out, _tmp = env
    popen.honors_sigint = False
    rec._grace = 0
    rec.tick(True)
    rec.tick(False)
    assert popen.last.signals == [signal.SIGINT] and popen.last.killed


@pytest.mark.parametrize("rc", [0, 1, 255])
def test_toda_salida_dentro_de_la_ventana_es_caida(env, rc):
    rec, mono, audit, popen, _out, tmp = env
    rec.tick(True)
    popen.last.exit(rc)
    rec.tick(True)
    assert not rec.recording
    assert audit.records[-1]["kind"] == "reconnect"
    assert f"código {rc}" in audit.records[-1]["motivo"]
    assert audit.records[-1]["retry_seconds"] == 5
    assert list(tmp.glob("*.ffconcat")) == []


def test_backoff_5_10_20_40_60_sin_bucle_cerrado(env):
    rec, mono, audit, popen, _out, _tmp = env
    esperas = []
    for _ in range(7):
        rec.tick(True)
        popen.last.exit(0)
        rec.tick(True)
        esperas.append(audit.records[-1]["retry_seconds"])
        lanzados = len(popen.procs)
        mono.advance(esperas[-1] - 0.5)
        rec.tick(True)
        assert len(popen.procs) == lanzados  # no relanza antes de la espera
        mono.advance(0.5)
    assert esperas == [5, 10, 20, 40, 60, 60, 60]
    assert rec.counters.reconnects == 7


def test_backoff_vuelve_a_5_tras_sesion_sana(env):
    rec, mono, audit, popen, out, _tmp = env
    for _ in range(3):
        rec.tick(True)
        popen.last.exit(0)
        rec.tick(True)
        mono.advance(60)
    assert [r["retry_seconds"] for r in audit.records] == [5, 10, 20]
    rec.tick(True)  # sesión que crece 70 s
    for i in range(8):
        _grow(out, "2026-10-05_06-20-00_main.mp4", 100 * (i + 1))
        mono.advance(10)
        rec.tick(True)
    popen.last.exit(0)
    rec.tick(True)
    assert audit.records[-1]["kind"] == "reconnect" and audit.records[-1]["retry_seconds"] == 5


def test_vigia_de_crecimiento_30_s(env):
    rec, mono, audit, popen, out, _tmp = env
    rec.tick(True)
    _grow(out, "2026-10-05_06-20-00_main.mp4", 100)
    mono.advance(1)
    rec.tick(True)
    mono.advance(30)
    rec.tick(True)
    assert rec.recording  # 30 s justos: todavía no
    mono.advance(1)
    rec.tick(True)
    assert popen.last.killed and not rec.recording
    assert "vigía" in audit.records[-1]["motivo"]


def test_vigia_sin_ningun_segmento(env):
    rec, mono, audit, popen, _out, _tmp = env
    rec.tick(True)
    mono.advance(31)
    rec.tick(True)
    assert popen.last.killed
    assert audit.kinds() == ["reconnect"]


def test_segmentos_previos_a_la_sesion_no_se_auditan(env):
    rec, mono, audit, popen, out, _tmp = env
    _grow(out, "2026-10-05_06-00-00_main.mp4", 10)
    _grow(out, "2026-10-05_06-00-00_sub.mp4", 10)
    rec.tick(True)
    _grow(out, "2026-10-05_06-20-00_main.mp4", 10)
    rec.tick(False)
    assert [r["file"] for r in audit.records] == ["2026-10-05_06-20-00_main.mp4"]


def test_stderr_de_ffmpeg_enmascarado_en_logs_y_auditoria(env, caplog):
    rec, mono, audit, popen, _out, _tmp = env
    popen.stderr_text = (
        f"[concat @ 0x1] Impossible to open 'rtsp://{CAMERA_USER}:{ENC}@192.0.2.21:554/cam/realmonitor'\n"
        f"Error opening input file rtsp://{CAMERA_USER}:{CAMERA_PASSWORD}@192.0.2.21:554/cam.\n"
    )
    caplog.set_level(logging.DEBUG)
    rec.tick(True)
    popen.last.exit(0)
    rec.tick(True)
    texto = caplog.text + repr(audit.records)
    assert "rtsp://***@192.0.2.21" in texto
    assert CAMERA_PASSWORD not in texto and ENC not in texto and CAMERA_USER not in texto


def test_falla_al_lanzar_ffmpeg_reintenta(env):
    rec, mono, audit, _popen, _out, tmp = env

    def boom(*_a, **_k):
        raise FileNotFoundError(2, "No such file or directory")

    rec._popen = boom
    rec.tick(True)
    assert not rec.recording
    assert audit.records[-1]["kind"] == "reconnect"
    assert list(tmp.glob("*.ffconcat")) == []


def test_ventana_nueva_arranca_sin_espera(env):
    rec, mono, audit, popen, _out, _tmp = env
    rec.tick(True)
    popen.last.exit(0)
    rec.tick(True)
    assert rec.seconds_to_retry() == 5
    rec.tick(False)
    rec.tick(True)
    assert rec.recording and len(popen.procs) == 2


def test_motivo_de_parada_se_loguea(env, caplog):
    rec, *_ = env
    caplog.set_level(logging.INFO)
    rec.tick(True)
    rec.tick(False, "espacio libre bajo el mínimo de 10 GB")
    assert "Grabación detenida (espacio libre bajo el mínimo de 10 GB)" in caplog.text


def test_segmento_de_28_bytes_al_cerrar_se_descarta(env):
    """Hallazgo del PR A: si la parada cae justo en un corte, ffmpeg deja un MP4 de 28 bytes sin moov."""
    rec, mono, audit, popen, out, _tmp = env
    rec.tick(True)
    _grow(out, "2026-10-05_06-45-00_main.mp4", 1000)
    mono.advance(1)
    rec.tick(True)
    _grow(out, "2026-10-05_06-50-00_main.mp4", 28)
    rec.tick(False)
    assert audit.kinds() == ["segment_created", "segment_discarded"]
    desc = audit.records[-1]
    assert desc["file"] == "2026-10-05_06-50-00_main.mp4" and desc["bytes"] == 28
    assert desc["motivo"] == "sin video válido"
    assert not (out / "2026-10-05_06-50-00_main.mp4").exists()
    assert (out / "2026-10-05_06-45-00_main.mp4").exists()
    assert rec.counters.segments == 1 and rec.counters.discarded == 1


def test_segmento_de_menos_de_1_s_se_descarta(env):
    rec, _mono, audit, _popen, out, _tmp = env
    rec.tick(True)
    _grow(out, "2026-10-05_06-50-00_main.mp4", 100)  # fake_probe ⇒ 0,5 s
    rec.tick(False)
    assert audit.records[-1]["kind"] == "segment_discarded"
    assert "0.50 s" in audit.records[-1]["motivo"]


def test_descarte_no_toca_segmentos_previos_ni_ajenos(env):
    rec, _mono, audit, _popen, out, _tmp = env
    _grow(out, "2026-10-05_06-00-00_main.mp4", 28)  # de otra sesión: no se toca aunque sea inválido
    _grow(out, "basura.mp4", 5)
    (out / "conjunto_b").mkdir()
    rec.tick(True)
    rec.tick(False)
    assert audit.records == []
    assert (out / "2026-10-05_06-00-00_main.mp4").exists() and (out / "basura.mp4").exists()


def test_descarte_tambien_tras_una_caida(env):
    rec, _mono, audit, popen, out, _tmp = env
    rec.tick(True)
    _grow(out, "2026-10-05_06-20-00_main.mp4", 28)
    popen.last.exit(0)
    rec.tick(True)
    assert audit.kinds() == ["segment_discarded", "reconnect"]
