"""Auditoría local en JSON Lines, un archivo por día, con retención de 30 días.

``<audit_dir>/pv_vision_audit_YYYYMMDD.jsonl`` (fecha local). Registra segmentos
creados y borrados, reconexiones y grabaciones omitidas por falta de espacio:
nombres de archivo, tamaños y motivos. **Nunca** un cuadro de video ni un secreto.
Los archivos con más de ``retention_days`` días se borran al arrancar y en cada
cambio de día.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from collections.abc import Callable
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, TextIO

log = logging.getLogger("pv_vision.audit")

AUDIT_DIR = "/config"
AUDIT_RETENTION_DAYS = 30
PREFIX = "pv_vision_audit_"
_NAME_RE = re.compile(r"^pv_vision_audit_(\d{8})\.jsonl$")


class AuditWriter:
    def __init__(
        self,
        directory: str | Path,
        retention_days: int = AUDIT_RETENTION_DAYS,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._dir = Path(directory)
        self._retention = retention_days
        self._now = now or (lambda: datetime.now().astimezone())
        self._lock = threading.Lock()
        self._fh: TextIO | None = None
        self._day: date | None = None

    def path_for(self, day: date) -> Path:
        return self._dir / f"{PREFIX}{day:%Y%m%d}.jsonl"

    def record(self, kind: str, **fields: Any) -> None:
        """Agrega ``{"ts", "kind", ...campos}``. Un error de disco se loguea y NO corta el flujo."""
        now = self._now()
        line = json.dumps(
            {"ts": now.isoformat(timespec="seconds"), "kind": kind, **fields},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        with self._lock:
            try:
                today = now.date()
                if self._fh is None or today != self._day:
                    self._rotate(today)
                assert self._fh is not None
                self._fh.write(line + "\n")
                self._fh.flush()
            except OSError as exc:
                log.error("Falló la escritura de la auditoría en %s: %s", self._dir, exc)

    def _rotate(self, today: date) -> None:
        if self._fh is not None:
            self._fh.close()
            self._fh = None
        self._dir.mkdir(parents=True, exist_ok=True)
        self._fh = self.path_for(today).open("a", encoding="utf-8")
        self._day = today
        self._purge(today)

    def purge(self) -> list[Path]:
        with self._lock:
            return self._purge(self._now().date())

    def _purge(self, today: date) -> list[Path]:
        limit = today - timedelta(days=self._retention)
        borrados: list[Path] = []
        if not self._dir.is_dir():
            return borrados
        for p in sorted(self._dir.iterdir()):
            m = _NAME_RE.match(p.name)
            if not m:
                continue
            try:
                day = datetime.strptime(m.group(1), "%Y%m%d").date()
            except ValueError:
                continue
            if day < limit:
                try:
                    p.unlink()
                    borrados.append(p)
                except OSError as exc:
                    log.error("No se pudo borrar la auditoría vieja %s: %s", p, exc)
        if borrados:
            log.info(
                "Retención de auditoría: %d archivo(s) de más de %d días borrados.",
                len(borrados),
                self._retention,
            )
        return borrados

    def close(self) -> None:
        with self._lock:
            if self._fh is not None:
                self._fh.close()
                self._fh = None
