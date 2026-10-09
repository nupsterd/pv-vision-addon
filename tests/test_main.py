from __future__ import annotations

import logging
from datetime import datetime, timedelta

import pytest

from pv_vision.main import RETENTION_INTERVAL, SUMMARY_INTERVAL, Controller
from pv_vision.recorder import Counters
from pv_vision.segments import GB
from tests.conftest import CAMERA_PASSWORD, FakeAudit, FakeMono, bogota, make_config


class SpyRecorder:
    def __init__(self) -> None:
        self.calls: list[tuple[bool, str]] = []
        self.counters = Counters()
        self.recording = False

    def tick(self, should_record: bool, reason_off: str = "fin de ventana") -> None:
        self.calls.append((should_record, reason_off))
        self.recording = should_record


class Clock:
    def __init__(self, start: datetime, mono: FakeMono) -> None:
        self.now = start
        self.mono = mono

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)
        self.mono.advance(seconds)


@pytest.fixture
def ctl_env(tmp_path):
    mono = FakeMono()
    clock = Clock(bogota(2026, 10, 5, 6, 19, 0), mono)  # lunes, 1 min antes de la ventana
    rec = SpyRecorder()
    audit = FakeAudit()
    state = {"sync": (True, ""), "free": 50 * GB, "sync_calls": 0}

    def sync():
        state["sync_calls"] += 1
        return state["sync"]

    ctl = Controller(
        make_config(),
        rec,  # type: ignore[arg-type]
        audit,  # type: ignore[arg-type]
        sync,
        recordings_dir=tmp_path,
        now=clock,
        monotonic=mono,
        free=lambda _p: state["free"],
    )
    return ctl, clock, rec, audit, state, tmp_path


def test_graba_solo_dentro_de_la_ventana(ctl_env):
    ctl, clock, rec, *_ = ctl_env
    ctl.step()
    clock.advance(60)
    ctl.step()
    clock.advance(30 * 60)
    ctl.step()
    assert [c[0] for c in rec.calls] == [False, True, False]


def test_arranque_a_mitad_de_ventana_graba_sin_minimo(ctl_env):
    ctl, clock, rec, *_ = ctl_env
    clock.now = bogota(2026, 10, 5, 6, 49, 59)
    ctl.step()
    assert rec.calls == [(True, "fin de ventana")]


def test_reloj_sin_sincronizar_no_graba_y_avisa(ctl_env, caplog):
    ctl, clock, rec, _audit, state, _ = ctl_env
    state["sync"] = (False, "dt_synchronized = false")
    clock.now = bogota(2026, 10, 5, 6, 30)
    ctl.step()
    assert rec.calls[-1] == (False, "reloj sin sincronizar")
    assert "hora de la Pi sin confirmar" in caplog.text
    clock.advance(10)
    ctl.step()
    assert state["sync_calls"] == 1  # reconsulta cada 30 s, no en cada vuelta
    state["sync"] = (True, "")
    clock.advance(25)
    ctl.step()
    assert rec.calls[-1][0] is True
    clock.advance(30)
    ctl.step()
    assert state["sync_calls"] == 2  # una vez sincronizado no se vuelve a consultar


def test_espacio_bajo_no_graba_avisa_y_audita_una_vez(ctl_env, caplog):
    ctl, clock, rec, audit, state, _ = ctl_env
    clock.now = bogota(2026, 10, 5, 6, 30)
    state["free"] = 9 * GB
    caplog.set_level(logging.INFO)
    ctl.step()
    clock.advance(1)
    ctl.step()
    assert rec.calls[-1] == (False, "espacio libre bajo el mínimo de 10 GB")
    assert audit.kinds().count("disk_low") == 1
    assert caplog.text.count("NO se graba: espacio libre") == 1
    state["free"] = 20 * GB
    clock.advance(1)
    ctl.step()
    assert rec.calls[-1][0] is True
    assert "Espacio libre recuperado" in caplog.text


def test_espacio_no_se_mide_fuera_de_ventana(ctl_env):
    ctl, _clock, rec, audit, state, _ = ctl_env
    state["free"] = 1
    ctl.step()
    assert rec.calls == [(False, "fin de ventana")] and "disk_low" not in audit.kinds()


def test_retencion_al_arrancar_y_cada_hora(ctl_env, caplog):
    ctl, clock, _rec, audit, _state, d = ctl_env
    caplog.set_level(logging.INFO)
    (d / "2026-09-01_06-20-00_main.mp4").write_bytes(b"\0" * 42)
    (d / "conjunto_b").mkdir()
    (d / "conjunto_b" / "2026-01-01_00-00-00_main.mp4").write_bytes(b"x")
    ctl.step()
    assert "Retención: 1 segmento(s) de más de 30 días borrados" in caplog.text
    assert audit.records[0] == {
        "kind": "segment_deleted",
        "file": "2026-09-01_06-20-00_main.mp4",
        "bytes": 42,
        "stream": "main",
    }
    assert (d / "conjunto_b" / "2026-01-01_00-00-00_main.mp4").exists()
    (d / "2026-09-02_06-20-00_main.mp4").write_bytes(b"\0")
    clock.advance(RETENTION_INTERVAL - 1)
    ctl.step()
    assert (d / "2026-09-02_06-20-00_main.mp4").exists()
    clock.advance(1)
    ctl.step()
    assert not (d / "2026-09-02_06-20-00_main.mp4").exists()
    assert caplog.text.count("Retención:") == 2


def test_resumen_cada_15_min(ctl_env, caplog):
    ctl, clock, rec, _audit, _state, _ = ctl_env
    caplog.set_level(logging.INFO)
    rec.counters.segments, rec.counters.bytes, rec.counters.reconnects = 3, 9000, 1
    ctl.step()
    assert "Resumen 15 min" not in caplog.text
    clock.advance(SUMMARY_INTERVAL)
    ctl.step()
    assert "Resumen 15 min: segmentos=3 bytes=9000 reconexiones=1 libre=50.0 GB grabando=sí" in caplog.text
    assert "próxima_ventana=2026-10-05T11:50-05:00" in caplog.text
    assert rec.counters.segments == 0  # contadores del período
    assert CAMERA_PASSWORD not in caplog.text
