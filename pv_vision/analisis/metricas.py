"""Métricas de rendimiento de un clip: CPU del proceso y total, RSS, temperatura y frecuencia.

Lee ``/proc`` y ``/sys`` (rutas inyectables para los tests). Lo que no existe (p. ej. sin
sensor térmico en un contenedor amd64) queda en ``None``.
"""

from __future__ import annotations

import glob
import os
import resource
import time
from dataclasses import dataclass, field


def _leer(path: str) -> str | None:
    try:
        with open(path, encoding="ascii") as fh:
            return fh.read()
    except OSError:
        return None


@dataclass
class Rutas:
    proc_stat: str = "/proc/stat"
    termicas: str = "/sys/class/thermal/thermal_zone*/temp"
    frecuencias: str = "/sys/devices/system/cpu/cpu[0-9]*/cpufreq/scaling_cur_freq"


def cpu_total(rutas: Rutas) -> tuple[float, float] | None:
    """(ocupado, total) en jiffies de la línea ``cpu`` de /proc/stat."""
    txt = _leer(rutas.proc_stat)
    if not txt:
        return None
    campos = [float(v) for v in txt.splitlines()[0].split()[1:]]
    ocioso = campos[3] + (campos[4] if len(campos) > 4 else 0.0)
    return sum(campos) - ocioso, sum(campos)


def temperatura_c(rutas: Rutas) -> float | None:
    vals = []
    for p in glob.glob(rutas.termicas):
        txt = _leer(p)
        if txt and txt.strip().lstrip("-").isdigit():
            vals.append(int(txt) / 1000.0)
    return max(vals) if vals else None


def frecuencia_min_mhz(rutas: Rutas) -> float | None:
    vals = []
    for p in glob.glob(rutas.frecuencias):
        txt = _leer(p)
        if txt and txt.strip().isdigit():
            vals.append(int(txt) / 1000.0)
    return min(vals) if vals else None


def cpu_proceso_s() -> float:
    """CPU (usuario + sistema) de este proceso y de sus hijos terminados (ffmpeg)."""
    a = resource.getrusage(resource.RUSAGE_SELF)
    b = resource.getrusage(resource.RUSAGE_CHILDREN)
    return a.ru_utime + a.ru_stime + b.ru_utime + b.ru_stime


@dataclass
class Medidor:
    """Se crea al empezar un clip; ``muestrear()`` cada ~1 s; ``resultado()`` al terminar."""

    rutas: Rutas = field(default_factory=Rutas)

    def __post_init__(self) -> None:
        self._t0 = time.monotonic()
        self._cpu0 = cpu_proceso_s()
        self._tot0 = cpu_total(self.rutas)
        self._ultima = 0.0
        self.temp_max: float | None = None
        self.freq_min: float | None = None
        self.muestrear(forzar=True)

    def muestrear(self, forzar: bool = False) -> None:
        ahora = time.monotonic()
        if not forzar and ahora - self._ultima < 1.0:
            return
        self._ultima = ahora
        t = temperatura_c(self.rutas)
        if t is not None:
            self.temp_max = t if self.temp_max is None else max(self.temp_max, t)
        f = frecuencia_min_mhz(self.rutas)
        if f is not None:
            self.freq_min = f if self.freq_min is None else min(self.freq_min, f)

    def resultado(self) -> dict[str, float | None]:
        self.muestrear(forzar=True)
        dur = max(time.monotonic() - self._t0, 1e-6)
        tot1 = cpu_total(self.rutas)
        cpu_total_pct = None
        if self._tot0 and tot1 and tot1[1] > self._tot0[1]:
            cpu_total_pct = round(100 * (tot1[0] - self._tot0[0]) / (tot1[1] - self._tot0[1]), 1)
        rss_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return {
            "duracion_s": round(dur, 2),
            # % de UN núcleo (puede pasar de 100): análisis + ffmpeg hijo.
            "cpu_proceso_pct": round(100 * (cpu_proceso_s() - self._cpu0) / dur, 1),
            # % de TODA la máquina (0-100): incluye HA y los demás add-ons.
            "cpu_total_pct": cpu_total_pct,
            "nucleos": os.cpu_count(),
            "rss_mb": round(rss_kb / 1024, 1),
            "temp_max_c": self.temp_max,
            "freq_min_mhz": self.freq_min,
        }
