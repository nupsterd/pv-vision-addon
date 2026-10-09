"""Supervisión del proceso de análisis desde el grabador (sin numpy: corre en el proceso principal).

Lanza ``python3 -m pv_vision.analisis`` como proceso aparte (que se pone ``nice 10`` solo),
lo relanza si muere con error (espera 60 s → 5 min → 15 min) y lo detiene con SIGTERM al
salir. Ningún fallo del análisis se propaga al grabador.
"""

from __future__ import annotations

import logging
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from typing import Any

log = logging.getLogger("pv_vision.analisis.proceso")

REINTENTOS_S = (60, 300, 900)
SALIDA_CONFIG_INVALIDA = 2


class ProcesoAnalisis:
    def __init__(
        self,
        cmd: Sequence[str] | None = None,
        popen: Callable[..., Any] = subprocess.Popen,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.cmd = list(cmd or [sys.executable, "-m", "pv_vision.analisis"])
        self._popen = popen
        self._mono = monotonic
        self.proc: Any = None
        self.fallos = 0
        self._proximo = 0.0
        self.desactivado = False

    def tick(self) -> None:
        if self.desactivado:
            return
        if self.proc is not None:
            rc = self.proc.poll()
            if rc is None:
                return
            self.proc = None
            if rc == SALIDA_CONFIG_INVALIDA:
                log.error(
                    "El análisis terminó por configuración inválida: queda desactivado hasta reiniciar el add-on."
                )
                self.desactivado = True
                return
            espera = REINTENTOS_S[min(self.fallos, len(REINTENTOS_S) - 1)]
            self.fallos += 1
            self._proximo = self._mono() + espera
            log.warning(
                "El proceso de análisis terminó (código %s); se relanza en %d s. El grabador sigue.", rc, espera
            )
            return
        if self._mono() < self._proximo:
            return
        try:
            self.proc = self._popen(self.cmd, stdin=subprocess.DEVNULL)
            log.info("Proceso de análisis lanzado (pid %s).", getattr(self.proc, "pid", "?"))
        except OSError as exc:
            espera = REINTENTOS_S[min(self.fallos, len(REINTENTOS_S) - 1)]
            self.fallos += 1
            self._proximo = self._mono() + espera
            log.error("No se pudo lanzar el análisis (%s); reintento en %d s.", exc, espera)

    def detener(self, espera_s: float = 3.0) -> None:
        """SIGTERM y espera corta: el Supervisor da ~10 s para todo el add-on al detenerlo."""
        if self.proc is None or self.proc.poll() is not None:
            return
        self.proc.terminate()
        try:
            self.proc.wait(timeout=espera_s)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(timeout=5)
