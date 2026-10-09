"""Nombres de segmento, selección de segmentos a borrar (retención) y umbral de espacio.

El nombre es ``YYYY-MM-DD_HH-MM-SS_<stream>.mp4`` en hora local; lo pone ffmpeg
(``-strftime 1``) al abrir cada segmento. La retención decide SOLO por la fecha y
hora del nombre (no por ``mtime``), mira SOLO el primer nivel de la carpeta de
grabaciones, nunca sigue symlinks y nunca toca un archivo que no cumpla el patrón
exacto: subcarpetas como ``conjunto_b/`` y archivos ajenos quedan fuera.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timedelta, tzinfo
from pathlib import Path

log = logging.getLogger("pv_vision.segments")

RECORDINGS_DIR = "/media/pv_vision"
FILENAME_STRFTIME = "%Y-%m-%d_%H-%M-%S"
_SEGMENT_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2})_(main|sub)\.mp4$")
GB = 1024**3


def segment_template(stream: str) -> str:
    """Plantilla de salida para ffmpeg (``-strftime 1``)."""
    return f"{FILENAME_STRFTIME}_{stream}.mp4"


def segment_name(start: datetime, stream: str) -> str:
    """Nombre del segmento que empieza en ``start`` (hora local)."""
    return start.strftime(segment_template(stream))


def parse_segment_name(name: str, tz: tzinfo) -> tuple[datetime, str] | None:
    """``(inicio local, stream)`` si ``name`` cumple el patrón exacto; si no, ``None``."""
    m = _SEGMENT_RE.match(name)
    if not m:
        return None
    try:
        start = datetime.strptime(m.group(1), FILENAME_STRFTIME).replace(tzinfo=tz)
    except ValueError:
        return None
    return start, m.group(2)


@dataclass(frozen=True)
class SegmentFile:
    path: Path
    start: datetime
    stream: str
    size: int


def list_segments(directory: str | Path, tz: tzinfo, stream: str | None = None) -> list[SegmentFile]:
    """Segmentos del PRIMER nivel de ``directory``, ordenados por inicio.

    Sin recursión, sin seguir symlinks y solo archivos regulares con el patrón exacto.
    """
    out: list[SegmentFile] = []
    try:
        entries = list(os.scandir(directory))
    except FileNotFoundError:
        return out
    for entry in entries:
        parsed = parse_segment_name(entry.name, tz)
        if parsed is None:
            continue
        if entry.is_symlink() or not entry.is_file(follow_symlinks=False):
            continue
        start, st = parsed
        if stream is not None and st != stream:
            continue
        try:
            size = entry.stat(follow_symlinks=False).st_size
        except OSError:
            continue
        out.append(SegmentFile(Path(entry.path), start, st, size))
    out.sort(key=lambda s: (s.start, s.stream))
    return out


def select_expired(segments: list[SegmentFile], now: datetime, retention_days: int) -> list[SegmentFile]:
    """Segmentos cuyo inicio (por el nombre) es más viejo que ``retention_days`` respecto de ``now``."""
    limit = now - timedelta(days=retention_days)
    return [s for s in segments if s.start < limit]


def purge_expired(directory: str | Path, now: datetime, retention_days: int) -> list[SegmentFile]:
    """Borra los segmentos vencidos de ``directory``. Devuelve los borrados."""
    tz = now.tzinfo
    assert tz is not None, "now debe tener zona horaria"
    borrados: list[SegmentFile] = []
    for seg in select_expired(list_segments(directory, tz), now, retention_days):
        try:
            seg.path.unlink()
            borrados.append(seg)
        except FileNotFoundError:
            continue
        except OSError as exc:
            log.error("No se pudo borrar el segmento vencido %s: %s", seg.path.name, exc)
    return borrados


DERIVADOS = ("depuracion", "referencia")
DERIVADOS_RETENCION_DIAS = 7  # Q5; nunca más de 30 (Ley 1581)


def purge_derivados(
    media_dir: str | Path, ahora_ts: float, dias: int = DERIVADOS_RETENCION_DIAS
) -> list[tuple[Path, int]]:
    """Borra lo que tenga más de ``dias`` (por mtime) dentro de ``depuracion/`` y ``referencia/``.

    Son carpetas que solo escribe el análisis. No sigue symlinks, no toca ``conjunto_b/`` ni el
    primer nivel de grabaciones, y quita las subcarpetas de corrida que queden vacías."""
    limite = ahora_ts - min(dias, 30) * 86400
    borrados: list[tuple[Path, int]] = []
    for nombre in DERIVADOS:
        raiz = Path(media_dir) / nombre
        if raiz.is_symlink() or not raiz.is_dir():
            continue
        for dirpath, _dirnames, filenames in os.walk(raiz, topdown=False, followlinks=False):
            d = Path(dirpath)
            for f in filenames:
                p = d / f
                try:
                    st = p.lstat()
                except OSError:
                    continue
                if st.st_mtime < limite:
                    try:
                        p.unlink()
                        borrados.append((p, st.st_size))
                    except OSError as exc:
                        log.error("No se pudo borrar %s: %s", p, exc)
            if d != raiz:
                try:
                    d.rmdir()  # solo si quedó vacía
                except OSError:
                    pass
    return borrados


def free_bytes(path: str | Path) -> int:
    """Bytes libres del filesystem de ``path`` (sube a la carpeta existente más cercana)."""
    p = Path(path)
    while not p.exists() and p != p.parent:
        p = p.parent
    return shutil.disk_usage(p).free


def has_enough_space(free: int, min_free_gb: int) -> bool:
    return free >= min_free_gb * GB


def fmt_gb(n: int) -> str:
    return f"{n / GB:.1f} GB"
