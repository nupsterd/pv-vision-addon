"""Configuración del add-on, leída de ``/data/options.json`` (la escribe el Supervisor).

Los nombres y defaults de ``Config`` son los mismos que ``options`` de ``config.yaml``
(un test lo verifica cargando el YAML). ``validate()`` repite las reglas del ``schema``
del Supervisor y agrega las que el schema no puede expresar (tope de retención,
ventanas que cruzan la medianoche o se solapan, zona horaria, rutas del análisis dentro de
``/media/pv_vision/``, ROI y línea). Los mensajes de error nunca incluyen secretos.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field, fields
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pv_vision.analisis.geometria import Linea, Roi, linea_cruza_roi, parse_linea, parse_roi
from pv_vision.analisis.modelos import MODELOS
from pv_vision.schedule import Window, parse_windows, validate_days

OPTIONS_PATH = "/data/options.json"
LOG_LEVELS = ("debug", "info", "warning")
STREAMS = ("main", "sub")
# Ley 1581 (§5.9.719): la retención nunca supera 30 días, aunque la UI lo permitiera.
RETENCION_MAX_DIAS = 30
# Host o IP sin esquema, puerto, ruta ni comillas (va dentro de la URL y del archivo ffconcat).
_HOST_RE = re.compile(r"^[A-Za-z0-9.-]+$")
# Todo lo que lee o escribe el análisis vive dentro de esta carpeta.
MEDIA_BASE = "/media/pv_vision"
ANALISIS_HILOS_MAX = 3
SENTIDOS = ("izq_a_der", "der_a_izq")
PUNTOS = ("pie", "centro")
# Línea de la Dahua extendida a los bordes del ROI por defecto (geometria.linea_por_defecto).
LINEA_POR_DEFECTO = "500,467,1113,1080"
_CLIP_NOMBRE_RE = re.compile(r"^[^/\\\x00]+\.mp4$")


def ruta_en_media(texto: str, base: str = MEDIA_BASE) -> str:
    """Ruta absoluta normalizada dentro de ``base`` (acepta relativa a ``base``); ``ValueError`` si sale."""
    if not texto or "\x00" in texto:
        raise ValueError("ruta vacía")
    ruta = os.path.normpath(texto if os.path.isabs(texto) else os.path.join(base, texto))
    if ruta != base and not ruta.startswith(base + os.sep):
        raise ValueError(f"{texto!r} está fuera de {base}/")
    return ruta


def ruta_relativa_a(texto: str, media: str | os.PathLike[str]) -> str:
    """Ruta de la config (dentro de ``/media/pv_vision``) llevada a otra raíz ``media`` (tests)."""
    rel = os.path.relpath(ruta_en_media(texto), MEDIA_BASE)
    return os.path.normpath(os.path.join(os.fspath(media), rel))


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
    # --- Análisis de archivos (detector en sombra, B7.1). Todo apagado por defecto.
    analisis_activo: bool = False
    analisis_dir: str = "/media/pv_vision/conjunto_b"
    analisis_modelos: tuple[str, ...] = ("yolox_nano",)
    analisis_cada_n: int = 3
    analisis_hilos: int = 3
    analisis_solo_fuera_de_ventanas: bool = True
    roi: str = "960:720:500:360"
    linea: str = LINEA_POR_DEFECTO
    sentido_salida: str = "izq_a_der"
    punto_referencia: str = "pie"
    histeresis_px: int = 12
    cuadros_confirmacion: int = 2
    conf_alta: float = 0.5
    conf_baja: float = 0.1
    track_buffer_s: float = 1.5
    min_hits: int = 3
    referencia_desde: str = ""
    depuracion_clips: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, opts: dict[str, Any]) -> Config:
        kwargs: dict[str, Any] = {}
        for f in fields(cls):
            if f.name not in opts or opts[f.name] is None:
                continue
            value = opts[f.name]
            if f.type == "tuple[str, ...]":
                kwargs[f.name] = tuple(str(v) for v in value)
            elif f.type == "tuple[int, ...]":
                kwargs[f.name] = tuple(int(v) for v in value)
            elif f.type == "int":
                kwargs[f.name] = int(value)
            elif f.type == "float":
                kwargs[f.name] = float(value)
            elif f.type == "bool":
                if not isinstance(value, bool):
                    raise ValueError(f"{f.name} debe ser true o false")
                kwargs[f.name] = value
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

    @property
    def roi_px(self) -> Roi:
        return parse_roi(self.roi)

    @property
    def linea_px(self) -> Linea:
        return parse_linea(self.linea)

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

    def validate_analisis(self) -> list[str]:
        """Reglas de la sección de análisis. Un error acá desactiva el análisis pero NUNCA frena al
        grabador (el add-on arranca igual y lo informa en el log)."""
        errores: list[str] = []
        try:
            ruta_en_media(self.analisis_dir)
        except ValueError as exc:
            errores.append(f"analisis_dir: {exc}")
        if not self.analisis_modelos:
            errores.append("analisis_modelos está vacía")
        for m in self.analisis_modelos:
            if m not in MODELOS:
                errores.append(f"analisis_modelos: {m!r} desconocido ({'|'.join(MODELOS)})")
        if len(set(self.analisis_modelos)) != len(self.analisis_modelos):
            errores.append("analisis_modelos tiene valores repetidos")
        if not 1 <= self.analisis_cada_n <= 25:
            errores.append(f"analisis_cada_n ({self.analisis_cada_n}) fuera de 1-25")
        if not 1 <= self.analisis_hilos <= ANALISIS_HILOS_MAX:
            errores.append(f"analisis_hilos ({self.analisis_hilos}) fuera de 1-{ANALISIS_HILOS_MAX}")
        roi: Roi | None = None
        try:
            roi = parse_roi(self.roi)
        except ValueError as exc:
            errores.append(str(exc))
        try:
            lin = parse_linea(self.linea)
            if roi is not None and not linea_cruza_roi(lin, roi):
                errores.append(f"linea {self.linea!r} no atraviesa el roi {self.roi!r}")
        except ValueError as exc:
            errores.append(str(exc))
        if self.sentido_salida not in SENTIDOS:
            errores.append(f"sentido_salida inválido: {self.sentido_salida!r} ({'|'.join(SENTIDOS)})")
        if self.punto_referencia not in PUNTOS:
            errores.append(f"punto_referencia inválido: {self.punto_referencia!r} ({'|'.join(PUNTOS)})")
        if not 0 <= self.histeresis_px <= 200:
            errores.append(f"histeresis_px ({self.histeresis_px}) fuera de 0-200")
        if not 1 <= self.cuadros_confirmacion <= 10:
            errores.append(f"cuadros_confirmacion ({self.cuadros_confirmacion}) fuera de 1-10")
        if not 0 < self.conf_baja <= self.conf_alta <= 1:
            errores.append(
                f"umbrales: se requiere 0 < conf_baja ({self.conf_baja}) <= conf_alta ({self.conf_alta}) <= 1"
            )
        if not 0.1 <= self.track_buffer_s <= 10:
            errores.append(f"track_buffer_s ({self.track_buffer_s}) fuera de 0.1-10")
        if not 1 <= self.min_hits <= 10:
            errores.append(f"min_hits ({self.min_hits}) fuera de 1-10")
        if self.referencia_desde:
            try:
                ruta_en_media(self.referencia_desde)
            except ValueError as exc:
                errores.append(f"referencia_desde: {exc}")
        for c in self.depuracion_clips:
            if not _CLIP_NOMBRE_RE.match(c):
                errores.append(f"depuracion_clips: {c!r} debe ser solo un nombre de archivo .mp4")
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
