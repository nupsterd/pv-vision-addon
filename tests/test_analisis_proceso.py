from __future__ import annotations

import logging

from pv_vision.analisis.proceso import ProcesoAnalisis
from tests.conftest import FakeMono


class Proc:
    def __init__(self):
        self.rc = None
        self.pid = 4321
        self.terminado = False

    def poll(self):
        return self.rc

    def terminate(self):
        self.terminado = True
        self.rc = -15

    def wait(self, timeout=None):
        return self.rc

    def kill(self):
        self.rc = -9


def test_lanza_y_relanza_con_espera_creciente(caplog):
    mono = FakeMono()
    procs: list[Proc] = []

    def popen(cmd, **kw):
        procs.append(Proc())
        return procs[-1]

    p = ProcesoAnalisis(cmd=["x"], popen=popen, monotonic=mono)
    p.tick()
    assert len(procs) == 1
    procs[-1].rc = 1
    p.tick()  # detecta la caída: espera 60 s
    p.tick()
    assert len(procs) == 1
    mono.advance(60)
    p.tick()
    assert len(procs) == 2
    procs[-1].rc = 1
    p.tick()
    mono.advance(299)
    p.tick()
    assert len(procs) == 2  # segunda espera: 300 s
    mono.advance(1)
    p.tick()
    assert len(procs) == 3
    assert "El grabador sigue" in caplog.text


def test_config_invalida_desactiva_sin_relanzar(caplog):
    procs: list[Proc] = []

    def popen(cmd, **kw):
        procs.append(Proc())
        return procs[-1]

    p = ProcesoAnalisis(cmd=["x"], popen=popen, monotonic=FakeMono())
    p.tick()
    procs[-1].rc = 2
    caplog.set_level(logging.ERROR)
    for _ in range(3):
        p.tick()
    assert len(procs) == 1 and p.desactivado
    assert "configuración inválida" in caplog.text


def test_detener_manda_sigterm():
    proc = Proc()
    p = ProcesoAnalisis(cmd=["x"], popen=lambda *a, **k: proc, monotonic=FakeMono())
    p.tick()
    p.detener()
    assert proc.terminado


def test_fallo_al_lanzar_no_propaga():
    def popen(*a, **k):
        raise OSError("sin python")

    p = ProcesoAnalisis(cmd=["x"], popen=popen, monotonic=FakeMono())
    p.tick()
    assert p.proc is None and p.fallos == 1
