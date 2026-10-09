"""Configuración del add-on, leída de ``/data/options.json`` (la escribe el Supervisor).

Los nombres y defaults de ``Config`` son los mismos que ``options`` de ``config.yaml``
(un test lo verifica cargando el YAML). ``validate()`` repite las reglas del ``schema``
del Supervisor y agrega las que el schema no puede expresar (tope de retención,
ventanas que cruzan la medianoche o se solapan, zona horaria).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, fields
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pv_vision.schedule import Window, parse_windows, validate_days

OPTIONS_PATH = "/data/options.json"
LOG_LEVELS = ("debug", "info", "warning")
STREAMS = ("main", "sub")
# Ley 1581 (§5.9.719): la retención nunca supera 30 días, aunque la UI lo permitiera.
RETENCION_MAX_DIAS = 30
# Host o IP sin esquema, puerto, ruta ni comillas (va dentro de la URL y del archivo ffconcat).
_HOST_RE = re.compile(r"^[A-Za-z0-9.-]+$")


@dataclass(frozen=True)
class Config:
    camera_host: str = ""
    camera_user: str = ""
    # repr=False: un log de la config o un traceback nunca imprime la clave.
    camera_password: str = field(default="", repr=False)
    stream: str = "main"
    ventanas: tuple[str, ...] = ("06:20-06:50", "11:50-12:20", "18:00-18:50")
    dias: tuple[int, ...] = (1, 2, 3, 4, 5)
    segment_seconds: int = 300
    retencion_dias: int = 30
    min_free_gb: int = 10
    tz: str = "America/Bogota"
    log_level: str = "info"

    @classmethod
    def from_dict(cls, opts: dict[str, Any]) -> Config:
        kwargs: dict[str, Any] = {}
        for f in fields(cls):
            if f.name not in opts or opts[f.name] is None:
                continue
            value = opts[f.name]
            if f.name == "ventanas":
                kwargs[f.name] = tuple(str(v) for v in value)
            elif f.name == "dias":
                kwargs[f.name] = tuple(int(v) for v in value)
            elif f.type == "int":
                kwargs[f.name] = int(value)
            else:
                kwargs[f.name] = str(value)
        return cls(**kwargs)

    @classmethod
    def from_options_json(cls, path: str = OPTIONS_PATH) -> Config:
        with open(path, encoding="utf-8") as fh:
            return cls.from_dict(json.load(fh))

    @property
    def windows(self) -> list[Window]:
        return parse_windows(self.ventanas)

    @property
    def zone(self) -> ZoneInfo:
        return ZoneInfo(self.tz)

    def validate(self) -> list[str]:
        """Errores de configuración que impiden arrancar (lista vacía = OK)."""
        errores: list[str] = []
        if not self.camera_host.strip():
            errores.append("camera_host está vacío")
        elif not _HOST_RE.match(self.camera_host.strip()):
            errores.append("camera_host debe ser solo un host o una IP (sin esquema, puerto ni ruta)")
        if not self.camera_user:
            errores.append("camera_user está vacío")
        if not self.camera_password:
            errores.append("camera_password está vacía")
        if self.stream not in STREAMS:
            errores.append(f"stream inválido: {self.stream!r} (main|sub)")
        try:
            parse_windows(self.ventanas)
        except ValueError as exc:
            errores.append(str(exc))
        try:
            validate_days(self.dias)
        except ValueError as exc:
            errores.append(str(exc))
        if not 30 <= self.segment_seconds <= 3600:
            errores.append(f"segment_seconds ({self.segment_seconds}) fuera de 30-3600")
        if not 1 <= self.retencion_dias <= RETENCION_MAX_DIAS:
            errores.append(
                f"retencion_dias ({self.retencion_dias}) fuera de 1-{RETENCION_MAX_DIAS}: "
                f"el tope de {RETENCION_MAX_DIAS} días es legal (Ley 1581) y no se puede superar"
            )
        if self.min_free_gb < 1:
            errores.append(f"min_free_gb ({self.min_free_gb}) debe ser >= 1")
        try:
            ZoneInfo(self.tz)
        except (ZoneInfoNotFoundError, ValueError):
            errores.append(f"tz desconocida: {self.tz!r}")
        if self.log_level not in LOG_LEVELS:
            errores.append(f"log_level inválido: {self.log_level!r}")
        return errores

    def describe(self) -> dict[str, Any]:
        """Config para el log de arranque: los secretos solo como configurado/vacío."""
        out: dict[str, Any] = {}
        for f in fields(self):
            value = getattr(self, f.name)
            if f.name in ("camera_user", "camera_password"):
                value = "configurado" if value else "vacío"
            elif isinstance(value, tuple):
                value = ", ".join(str(v) for v in value)
            out[f.name] = value
        return out
