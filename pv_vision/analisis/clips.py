"""Clips a analizar y su hora absoluta, a partir del nombre.

- Conjunto B: ``nNN_AAAA-MM-DD_hh.mm.ss-hh.mm.ss.mp4`` (inicio y fin del clip en hora local de la
  cámara; la hora sobreimpresa coincide al segundo).
- Conjunto C (grabador): ``AAAA-MM-DD_HH-MM-SS_main|sub.mp4`` (inicio en hora local de la Pi).

Hora absoluta de un cuadro = inicio del nombre + ``pts`` del cuadro.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, tzinfo
from pathlib import Path

_B_RE = re.compile(r"^n(\d+)_(\d{4}-\d{2}-\d{2})_(\d{2}\.\d{2}\.\d{2})-(\d{2}\.\d{2}\.\d{2})\.mp4$")
_C_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2})_(main|sub)\.mp4$")


@dataclass(frozen=True)
class Clip:
    path: Path
    inicio: datetime
    fin: datetime | None  # solo conjunto B
    grupo: int | None  # nNN del conjunto B
    conjunto: str  # "B" | "C"

    @property
    def nombre(self) -> str:
        return self.path.name

    def hora_abs(self, pts: float) -> datetime:
        return self.inicio + timedelta(seconds=pts)


def parse_nombre(path: Path, tz: tzinfo) -> Clip | None:
    nombre = path.name
    m = _B_RE.match(nombre)
    if m:
        try:
            ini = datetime.strptime(f"{m.group(2)} {m.group(3)}", "%Y-%m-%d %H.%M.%S").replace(tzinfo=tz)
            fin = datetime.strptime(f"{m.group(2)} {m.group(4)}", "%Y-%m-%d %H.%M.%S").replace(tzinfo=tz)
        except ValueError:
            return None
        if fin < ini:  # clip que cruza la medianoche
            fin += timedelta(days=1)
        return Clip(path, ini, fin, int(m.group(1)), "B")
    m = _C_RE.match(nombre)
    if m:
        try:
            ini = datetime.strptime(m.group(1), "%Y-%m-%d_%H-%M-%S").replace(tzinfo=tz)
        except ValueError:
            return None
        return Clip(path, ini, None, None, "C")
    return None


def listar(directorio: str | Path, tz: tzinfo) -> list[Clip]:
    """Clips del PRIMER nivel de ``directorio`` con nombre válido, sin symlinks, por hora de inicio."""
    out: list[Clip] = []
    try:
        entradas = list(os.scandir(directorio))
    except (FileNotFoundError, NotADirectoryError):
        return out
    for e in entradas:
        if e.is_symlink() or not e.is_file(follow_symlinks=False):
            continue
        clip = parse_nombre(Path(e.path), tz)
        if clip is not None:
            out.append(clip)
    out.sort(key=lambda c: (c.inicio, c.nombre))
    return out
