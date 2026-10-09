"""Ventanas horarias y días de grabación: parseo, validación y "¿grabo ahora?".

Una ventana ``"HH:MM-HH:MM"`` incluye el inicio y excluye el fin, en hora local.
No se admiten ventanas que crucen la medianoche (P7) ni ventanas solapadas.
Los días son ISO 8601: 1 = lunes … 7 = domingo.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, time, timedelta

_WINDOW_RE = re.compile(r"^(\d{2}):(\d{2})-(\d{2}):(\d{2})$")


@dataclass(frozen=True, order=True)
class Window:
    start: time
    end: time

    def __str__(self) -> str:
        return f"{self.start:%H:%M}-{self.end:%H:%M}"

    def contains(self, t: time) -> bool:
        return self.start <= t < self.end


def parse_window(text: str) -> Window:
    """``"06:20-06:50"`` → ``Window``. ``ValueError`` con el motivo si no es válida."""
    m = _WINDOW_RE.match(text.strip())
    if not m:
        raise ValueError(f"ventana {text!r}: formato esperado HH:MM-HH:MM")
    h1, m1, h2, m2 = (int(g) for g in m.groups())
    for h, mi in ((h1, m1), (h2, m2)):
        if h > 23 or mi > 59:
            raise ValueError(f"ventana {text!r}: hora fuera de rango")
    start, end = time(h1, m1), time(h2, m2)
    if end == start:
        raise ValueError(f"ventana {text!r}: dura cero minutos")
    if end < start:
        raise ValueError(f"ventana {text!r}: cruza la medianoche (no admitido)")
    return Window(start, end)


def parse_windows(texts: Iterable[str]) -> list[Window]:
    """Parsea, ordena y rechaza solapes. ``ValueError`` con el primer problema."""
    windows = sorted(parse_window(t) for t in texts)
    if not windows:
        raise ValueError("ventanas está vacía")
    for prev, cur in zip(windows, windows[1:], strict=False):
        if cur.start < prev.end:
            raise ValueError(f"ventanas {prev} y {cur} se solapan")
    return windows


def validate_days(days: Iterable[int]) -> list[int]:
    out = list(days)
    if not out:
        raise ValueError("dias está vacía")
    for d in out:
        if not isinstance(d, int) or isinstance(d, bool) or not 1 <= d <= 7:
            raise ValueError(f"dia {d!r} fuera de 1-7 (ISO: 1 = lunes)")
    if len(set(out)) != len(out):
        raise ValueError("dias tiene valores repetidos")
    return sorted(out)


def active_window(now: datetime, windows: Sequence[Window], days: Iterable[int]) -> Window | None:
    """La ventana que contiene ``now`` (hora local) en un día habilitado, o ``None``."""
    if now.isoweekday() not in set(days):
        return None
    t = now.time().replace(tzinfo=None)
    for w in windows:
        if w.contains(t):
            return w
    return None


def window_end(now: datetime, window: Window) -> datetime:
    """Fin de ``window`` el mismo día de ``now`` (con la zona de ``now``)."""
    return now.replace(hour=window.end.hour, minute=window.end.minute, second=0, microsecond=0)


def next_window_start(now: datetime, windows: Sequence[Window], days: Iterable[int]) -> datetime | None:
    """Inicio de la próxima ventana estrictamente posterior a ``now`` (hasta 8 días)."""
    allowed = set(days)
    base = now.replace(hour=0, minute=0, second=0, microsecond=0)
    for offset in range(0, 9):
        day = base + timedelta(days=offset)
        if day.isoweekday() not in allowed:
            continue
        for w in windows:
            start = day.replace(hour=w.start.hour, minute=w.start.minute)
            if start > now:
                return start
    return None
