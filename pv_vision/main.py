"""Entry point del add-on: ``python3 -m pv_vision.main``.

Bucle de 1 s: retención (al arrancar y cada hora), "¿grabo ahora?" (ventana + día +
reloj sincronizado + espacio libre), ``Recorder.tick`` y resumen cada 15 min.
"""

from __future__ import annotations

import logging
import signal
import sys
import threading
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from pv_vision import ADDON_VERSION
from pv_vision.analisis.proceso import ProcesoAnalisis
from pv_vision.audit import AUDIT_DIR, AuditWriter
from pv_vision.clock import SyncCheck, supervisor_sync_check, supervisor_token
from pv_vision.config import OPTIONS_PATH, Config
from pv_vision.recorder import Recorder
from pv_vision.schedule import active_window, next_window_start
from pv_vision.segments import (
    DERIVADOS_RETENCION_DIAS,
    RECORDINGS_DIR,
    fmt_gb,
    free_bytes,
    has_enough_space,
    purge_derivados,
    purge_expired,
)

log = logging.getLogger("pv_vision")

TMP_DIR = "/tmp"
RETENTION_INTERVAL = 3600
SUMMARY_INTERVAL = 900
CLOCK_RECHECK = 30
WARN_INTERVAL = 600


def setup_logging(level: str) -> None:
    logging.basicConfig(
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    logging.getLogger().setLevel(level.upper())


def load_config(path: str = OPTIONS_PATH) -> Config:
    try:
        return Config.from_options_json(path)
    except FileNotFoundError:
        logging.basicConfig(level="INFO")
        log.error("%s no existe. ¿Está corriendo dentro del add-on?", path)
        sys.exit(1)
    except (KeyError, TypeError, ValueError) as exc:
        # Sin el detalle: el mensaje de un json/cast podría incluir un valor de la config.
        logging.basicConfig(level="INFO")
        log.error("Configuración ilegible en %s (%s).", path, type(exc).__name__)
        sys.exit(1)


def startup(cfg: Config) -> None:
    """Valida y loguea la config. Sale con código 1 si es inválida."""
    setup_logging(cfg.log_level)
    errores = cfg.validate()
    for err in errores:
        log.error("Configuración inválida: %s", err)
    if errores:
        sys.exit(1)
    log.info("Visión (Portería Virtual) %s — grabador por ventanas, modo sombra", ADDON_VERSION)
    for key, value in cfg.describe().items():
        log.info("  %s = %s", key, value)
    if cfg.analisis_activo:
        for err in cfg.validate_analisis():
            log.error("Análisis desactivado, configuración inválida: %s (el grabador sigue).", err)


def analisis_habilitado(cfg: Config) -> bool:
    return cfg.analisis_activo and not cfg.validate_analisis()


class Controller:
    def __init__(
        self,
        cfg: Config,
        recorder: Recorder,
        audit: AuditWriter,
        sync_check: SyncCheck,
        *,
        recordings_dir: str | Path = RECORDINGS_DIR,
        now: Callable[[], datetime] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        free: Callable[[str | Path], int] = free_bytes,
        analisis: ProcesoAnalisis | None = None,
    ) -> None:
        self.cfg = cfg
        self.recorder = recorder
        self.audit = audit
        self._sync_check = sync_check
        self._dir = Path(recordings_dir)
        self._tz = cfg.zone
        self._windows = cfg.windows
        self._now = now or (lambda: datetime.now(self._tz))
        self._mono = monotonic
        self._free = free
        self.analisis = analisis
        start = self._mono()
        self._next_retention = start
        self._next_summary = start + SUMMARY_INTERVAL
        self._clock_ok = False
        self._next_clock_check = start
        self._last_clock_warn = -1e18
        self._low_space = False
        self._last_space_warn = -1e18

    # ------------------------------------------------------------------ pasos

    def step(self) -> None:
        now = self._now()
        mono = self._mono()
        if mono >= self._next_retention:
            self.run_retention(now)
            self._next_retention = mono + RETENTION_INTERVAL
        window = active_window(now, self._windows, self.cfg.dias)
        want, reason = window is not None, "fin de ventana"
        if want and not self._clock_synced(mono):
            want, reason = False, "reloj sin sincronizar"
        if want and not self._space_ok(mono, now):
            want, reason = False, f"espacio libre bajo el mínimo de {self.cfg.min_free_gb} GB"
        self.recorder.tick(want, reason)
        if self.analisis is not None:
            try:
                self.analisis.tick()
            except Exception:  # noqa: BLE001 — el análisis nunca frena al grabador
                log.exception("Error al supervisar el proceso de análisis.")
        if mono >= self._next_summary:
            self.summary(now)
            self._next_summary = mono + SUMMARY_INTERVAL

    def _clock_synced(self, mono: float) -> bool:
        if self._clock_ok:
            return True
        if mono < self._next_clock_check:
            return False
        self._next_clock_check = mono + CLOCK_RECHECK
        ok, why = self._sync_check()
        if ok:
            self._clock_ok = True
            log.info("Reloj de la Pi sincronizado (dt_synchronized): se puede grabar.")
            return True
        if mono - self._last_clock_warn >= WARN_INTERVAL:
            self._last_clock_warn = mono
            log.warning("Dentro de una ventana pero NO se graba: hora de la Pi sin confirmar (%s).", why)
        return False

    def _space_ok(self, mono: float, now: datetime) -> bool:
        try:
            free = self._free(self._dir)
        except OSError as exc:
            log.error("No se pudo medir el espacio libre de %s: %s", self._dir, exc)
            return False
        ok = has_enough_space(free, self.cfg.min_free_gb)
        if not ok:
            if not self._low_space:
                self.audit.record("disk_low", free_bytes=free, min_free_gb=self.cfg.min_free_gb)
            if not self._low_space or mono - self._last_space_warn >= WARN_INTERVAL:
                self._last_space_warn = mono
                log.warning(
                    "NO se graba: espacio libre %s por debajo de min_free_gb = %d GB.",
                    fmt_gb(free),
                    self.cfg.min_free_gb,
                )
        elif self._low_space:
            log.info("Espacio libre recuperado (%s): se puede grabar.", fmt_gb(free))
        self._low_space = not ok
        return ok

    def run_retention(self, now: datetime) -> int:
        borrados = purge_expired(self._dir, now, self.cfg.retencion_dias)
        total = 0
        for seg in borrados:
            total += seg.size
            self.audit.record("segment_deleted", file=seg.path.name, bytes=seg.size, stream=seg.stream)
        log.info(
            "Retención: %d segmento(s) de más de %d días borrados (%s).",
            len(borrados),
            self.cfg.retencion_dias,
            fmt_gb(total),
        )
        derivados = purge_derivados(self._dir, now.timestamp())
        for path, size in derivados:
            self.audit.record("derived_deleted", file=str(path.relative_to(self._dir)), bytes=size)
        if derivados:
            log.info(
                "Retención: %d archivo(s) de depuración/referencia de más de %d días borrados.",
                len(derivados),
                DERIVADOS_RETENCION_DIAS,
            )
        return len(borrados)

    def summary(self, now: datetime) -> None:
        c = self.recorder.counters.take()
        try:
            libre = fmt_gb(self._free(self._dir))
        except OSError:
            libre = "?"
        nxt = next_window_start(now, self._windows, self.cfg.dias)
        log.info(
            "Resumen 15 min: segmentos=%d bytes=%d reconexiones=%d libre=%s grabando=%s próxima_ventana=%s "
            "descartados=%d",
            c.segments,
            c.bytes,
            c.reconnects,
            libre,
            "sí" if self.recorder.recording else "no",
            nxt.isoformat(timespec="minutes") if nxt else "-",
            c.discarded,
        )


def build(cfg: Config) -> Controller:
    audit = AuditWriter(AUDIT_DIR)
    audit.purge()
    if not supervisor_token():
        log.error("Sin SUPERVISOR_TOKEN: no se puede confirmar la hora y NO se grabará (¿falta hassio_api: true?).")
    recorder = Recorder(
        host=cfg.camera_host.strip(),
        user=cfg.camera_user,
        password=cfg.camera_password,
        stream=cfg.stream,
        segment_seconds=cfg.segment_seconds,
        tz=cfg.tz,
        out_dir=RECORDINGS_DIR,
        tmp_dir=TMP_DIR,
        audit=audit,
    )
    log.info("Fuente: %s · grabaciones en %s · auditoría en %s", recorder.masked_source, RECORDINGS_DIR, AUDIT_DIR)
    analisis = ProcesoAnalisis() if analisis_habilitado(cfg) else None
    if analisis is not None:
        log.info("Modo de análisis de archivos ACTIVO (proceso aparte, nice 10).")
    return Controller(cfg, recorder, audit, supervisor_sync_check(), analisis=analisis)


def main() -> None:
    cfg = load_config()
    startup(cfg)
    stop = threading.Event()

    def on_signal(signum: int, _frame: object) -> None:
        log.info("Señal %s recibida: deteniendo la grabación y saliendo.", signum)
        stop.set()

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)

    ctl = build(cfg)
    try:
        while not stop.is_set():
            try:
                ctl.step()
            except Exception:  # noqa: BLE001 — un error inesperado no debe matar el grabador
                log.exception("Error inesperado en el bucle principal; se sigue en 5 s.")
                stop.wait(5)
            stop.wait(1)
    finally:
        if ctl.analisis is not None:
            ctl.analisis.detener()
        ctl.recorder.shutdown()
        ctl.audit.close()


if __name__ == "__main__":
    main()
