"""Proceso ffmpeg de una ventana: arranque, vigía, reintentos y detención limpia.

``Recorder.tick()`` se llama cada ~1 s desde el bucle principal con "¿grabo ahora?".
Reglas (Phase 0, aprobadas):

- **Toda salida de ffmpeg dentro de una ventana es una caída**, con cualquier código:
  ffmpeg sale con 0 cuando la cámara corta o se queda muda (timeout de E/S).
- **Vigía de crecimiento:** si el segmento en curso no crece en ``stall_seconds`` (o no
  aparece ninguno), se mata el proceso y cuenta como caída.
- **Reintento** con espera 5/10/20/40/60 s; vuelve a 5 s después de una sesión que
  grabó sana al menos ``healthy_seconds``. Nunca en bucle cerrado.
- **Fin de ventana:** ``SIGINT`` (ffmpeg cierra el archivo y sale con 255, que es lo
  normal) y ``SIGKILL`` si no salió en ``stop_grace`` s.
- El ffconcat con la URL se crea al arrancar y se borra siempre que el proceso termina.
- Cada segmento cerrado de la sesión pasa por ``ffprobe``: si no tiene video válido o dura
  menos de 1 s (p. ej. el MP4 de 28 bytes que queda si la parada cae justo en un corte), se
  borra y se audita como ``segment_discarded``. Solo se miran los segmentos nuevos de la
  propia sesión, nunca los de otra ni archivos ajenos.
- Cada línea de stderr pasa por la máscara antes de llegar al log.
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Any
from zoneinfo import ZoneInfo

from pv_vision.audit import AuditWriter
from pv_vision.ffmpeg import (
    RTSP_PORT,
    Redactor,
    build_command,
    child_env,
    masked_url,
    remove_quietly,
    rtsp_url,
    write_ffconcat,
)
from pv_vision.media import InfoVideo, motivo_descarte, probar
from pv_vision.segments import list_segments

log = logging.getLogger("pv_vision.recorder")

BACKOFF_SECONDS = (5, 10, 20, 40, 60)
STALL_SECONDS = 30
HEALTHY_SECONDS = 60
STOP_GRACE_SECONDS = 10
# Por sesión de ffmpeg: las primeras N líneas de stderr van en WARNING, el resto en DEBUG.
STDERR_WARN_LINES = 20


@dataclass
class Counters:
    """Totales desde el último resumen (los lee y reinicia el bucle principal)."""

    segments: int = 0
    bytes: int = 0
    reconnects: int = 0
    discarded: int = 0

    def take(self) -> Counters:
        snap = Counters(self.segments, self.bytes, self.reconnects, self.discarded)
        self.segments = self.bytes = self.reconnects = self.discarded = 0
        return snap


@dataclass
class _Session:
    proc: Any
    concat_path: Path
    started: float
    baseline: set[str]
    last_growth: float
    last_key: tuple[str, int] | None = None
    audited: set[str] = field(default_factory=set)
    stderr_thread: threading.Thread | None = None
    last_stderr: str = ""


class Recorder:
    def __init__(
        self,
        *,
        host: str,
        user: str,
        password: str,
        stream: str,
        segment_seconds: int,
        tz: str,
        out_dir: str | Path,
        tmp_dir: str | Path,
        audit: AuditWriter,
        port: int = RTSP_PORT,
        backoff: Sequence[float] = BACKOFF_SECONDS,
        stall_seconds: float = STALL_SECONDS,
        healthy_seconds: float = HEALTHY_SECONDS,
        stop_grace: float = STOP_GRACE_SECONDS,
        popen: Callable[..., Any] = subprocess.Popen,
        probe: Callable[[Path], InfoVideo | None] = probar,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._host = host
        self._port = port
        self._user = user
        self._password = password
        self.stream = stream
        self._segment_seconds = segment_seconds
        self._tz_name = tz
        self._tz = ZoneInfo(tz)
        self.out_dir = Path(out_dir)
        self._tmp_dir = Path(tmp_dir)
        self._audit = audit
        self._backoff = tuple(backoff)
        self._stall = stall_seconds
        self._healthy = healthy_seconds
        self._grace = stop_grace
        self._popen = popen
        self._probe = probe
        self._mono = monotonic
        self.redactor = Redactor([password, user])
        self.counters = Counters()
        self._session: _Session | None = None
        self._failures = 0
        self._next_attempt = 0.0
        self._attempt_no = 0

    # ------------------------------------------------------------------ estado

    @property
    def recording(self) -> bool:
        return self._session is not None

    @property
    def masked_source(self) -> str:
        return masked_url(self._host, self.stream, self._port)

    def seconds_to_retry(self) -> float:
        return max(0.0, self._next_attempt - self._mono())

    # ------------------------------------------------------------------ bucle

    def tick(self, should_record: bool, reason_off: str = "fin de ventana") -> None:
        if not should_record:
            if self._session is not None:
                self.stop(reason_off)
            # Una ventana nueva arranca sin espera acumulada.
            self._failures = 0
            self._next_attempt = 0.0
            return
        s = self._session
        if s is not None:
            rc = s.proc.poll()
            if rc is not None:
                self._fail(f"ffmpeg terminó dentro de la ventana (código {rc}){self._stderr_hint(s)}")
                return
            if self._check_growth(s):
                return
            self._kill(s)
            self._fail(f"sin datos nuevos durante {self._stall:.0f} s (vigía de crecimiento)")
            return
        if self._mono() >= self._next_attempt:
            self.start()

    # ------------------------------------------------------------------ arranque

    def start(self) -> None:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self._tmp_dir.mkdir(parents=True, exist_ok=True)
        self._attempt_no += 1
        concat = self._tmp_dir / f"pv_vision_{os.getpid()}_{self._attempt_no}.ffconcat"
        write_ffconcat(concat, rtsp_url(self._host, self._user, self._password, self.stream, self._port))
        cmd = build_command(concat, self.out_dir, self.stream, self._segment_seconds)
        baseline = {seg.path.name for seg in list_segments(self.out_dir, self._tz, self.stream)}
        try:
            proc = self._popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                env=child_env(self._tz_name),
                text=True,
                errors="replace",
            )
        except OSError as exc:
            remove_quietly(concat)
            self._session = None
            self._fail(f"no se pudo lanzar ffmpeg: {exc.strerror or exc}")
            return
        now = self._mono()
        s = _Session(proc=proc, concat_path=concat, started=now, baseline=baseline, last_growth=now)
        if proc.stderr is not None:
            s.stderr_thread = threading.Thread(
                target=self._pump_stderr, args=(s, proc.stderr), name="ffmpeg-stderr", daemon=True
            )
            s.stderr_thread.start()
        self._session = s
        log.info(
            "Grabando %s (%s) en %s, segmentos de %d s.",
            self.masked_source,
            self.stream,
            self.out_dir,
            self._segment_seconds,
        )

    def _pump_stderr(self, s: _Session, pipe: IO[str]) -> None:
        n = 0
        for raw in pipe:
            line = self.redactor.mask(raw.rstrip())
            if not line:
                continue
            s.last_stderr = line
            n += 1
            if n <= STDERR_WARN_LINES:
                log.warning("ffmpeg: %s", line)
            else:
                log.debug("ffmpeg: %s", line)
        if n > STDERR_WARN_LINES:
            log.info("ffmpeg: %d líneas más de stderr en DEBUG.", n - STDERR_WARN_LINES)

    # ------------------------------------------------------------------ segmentos

    def _new_segments(self, s: _Session) -> list[Any]:
        return [seg for seg in list_segments(self.out_dir, self._tz, self.stream) if seg.path.name not in s.baseline]

    def _check_growth(self, s: _Session) -> bool:
        """Audita los segmentos cerrados y devuelve False si el vigía venció."""
        segs = self._new_segments(s)
        for seg in segs[:-1]:
            self._audit_segment(s, seg.path, seg.size)
        now = self._mono()
        if segs:
            key = (segs[-1].path.name, segs[-1].size)
            if key != s.last_key:
                s.last_key = key
                s.last_growth = now
        return now - s.last_growth <= self._stall

    def _audit_segment(self, s: _Session, path: Path, size: int) -> None:
        name = path.name
        if name in s.audited:
            return
        s.audited.add(name)
        motivo = motivo_descarte(self._probe(path))
        if motivo is not None:
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            except OSError as exc:
                log.error("No se pudo borrar el segmento descartado %s: %s", name, exc)
            self.counters.discarded += 1
            self._audit.record("segment_discarded", file=name, bytes=size, motivo=motivo, stream=self.stream)
            log.info("Segmento descartado: %s (%d bytes, %s).", name, size, motivo)
            return
        self.counters.segments += 1
        self.counters.bytes += size
        self._audit.record("segment_created", file=name, bytes=size, stream=self.stream)
        log.debug("Segmento cerrado: %s (%d bytes).", name, size)

    def _close_session(self, s: _Session) -> None:
        """Proceso ya terminado: espera stderr, audita lo que quedó y borra el ffconcat."""
        if s.stderr_thread is not None:
            s.stderr_thread.join(timeout=2)
        for seg in self._new_segments(s):
            self._audit_segment(s, seg.path, seg.size)
        remove_quietly(s.concat_path)
        self._session = None

    # ------------------------------------------------------------------ fin / caída

    def stop(self, reason: str) -> None:
        s = self._session
        if s is None:
            return
        if s.proc.poll() is None:
            try:
                s.proc.send_signal(signal.SIGINT)
            except ProcessLookupError:
                pass
            try:
                s.proc.wait(timeout=self._grace)
            except subprocess.TimeoutExpired:
                log.warning("ffmpeg no terminó %.0f s después de SIGINT: SIGKILL.", self._grace)
                self._kill(s)
        # 255 tras SIGINT es lo normal en ffmpeg: no es error.
        self._close_session(s)
        log.info("Grabación detenida (%s).", reason)

    def _kill(self, s: _Session) -> None:
        try:
            s.proc.kill()
        except ProcessLookupError:
            pass
        try:
            s.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            log.error("ffmpeg no terminó tras SIGKILL.")

    def _stderr_hint(self, s: _Session) -> str:
        if s.stderr_thread is not None:
            s.stderr_thread.join(timeout=1)
        return f": {s.last_stderr}" if s.last_stderr else ""

    def _fail(self, motivo: str) -> None:
        s = self._session
        healthy = False
        if s is not None:
            healthy = (s.last_growth - s.started) >= self._healthy
            self._close_session(s)
        if healthy:
            self._failures = 0
        delay = self._backoff[min(self._failures, len(self._backoff) - 1)]
        self._failures += 1
        self._next_attempt = self._mono() + delay
        self.counters.reconnects += 1
        motivo = self.redactor.mask(motivo)
        self._audit.record("reconnect", motivo=motivo, retry_seconds=delay, stream=self.stream)
        log.warning("Caída de la grabación: %s. Reintento en %d s.", motivo, delay)

    def shutdown(self, grace: float = 5) -> None:
        """Salida del add-on: detiene ffmpeg con una espera corta (el Supervisor da ~10 s)."""
        self._grace = min(self._grace, grace)
        self.stop("el add-on se detiene")
