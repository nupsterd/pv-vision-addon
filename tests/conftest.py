"""Fixtures compartidas. Ninguna credencial es real: la IP es de documentación (RFC 5737)."""

from __future__ import annotations

import io
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from pv_vision.config import Config

BOGOTA = ZoneInfo("America/Bogota")
CAMERA_HOST = "192.0.2.21"
CAMERA_USER = "visor-pv"
# Con caracteres que cambian al URL-codificar, para probar las dos formas en la máscara.
CAMERA_PASSWORD = "Cl@ve:no/loguear#77'x"


def make_config(**overrides: object) -> Config:
    base: dict[str, object] = {
        "camera_host": CAMERA_HOST,
        "camera_user": CAMERA_USER,
        "camera_password": CAMERA_PASSWORD,
    }
    base.update(overrides)
    return Config.from_dict(base)


def bogota(y: int, mo: int, d: int, h: int = 0, mi: int = 0, s: int = 0) -> datetime:
    return datetime(y, mo, d, h, mi, s, tzinfo=BOGOTA)


class FakeMono:
    def __init__(self, start: float = 1000.0) -> None:
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


class FakeAudit:
    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []

    def record(self, kind: str, **fields: Any) -> None:
        self.records.append({"kind": kind, **fields})

    def kinds(self) -> list[str]:
        return [r["kind"] for r in self.records]

    def close(self) -> None:
        pass


class FakeProc:
    """Proceso ffmpeg simulado. ``exit(rc)`` lo termina; SIGINT lo termina con 255."""

    def __init__(self, args: list[str], stderr_text: str = "", honors_sigint: bool = True, **kwargs: Any) -> None:
        self.args = args
        self.kwargs = kwargs
        self.rc: int | None = None
        self.signals: list[int] = []
        self.killed = False
        self.honors_sigint = honors_sigint
        self.stderr = io.StringIO(stderr_text) if kwargs.get("stderr") == subprocess.PIPE else None

    def poll(self) -> int | None:
        return self.rc

    def exit(self, rc: int) -> None:
        self.rc = rc

    def send_signal(self, sig: int) -> None:
        self.signals.append(sig)
        if self.honors_sigint:
            self.rc = 255

    def kill(self) -> None:
        self.killed = True
        self.rc = -9

    def wait(self, timeout: float | None = None) -> int:
        if self.rc is None:
            raise subprocess.TimeoutExpired(self.args, timeout or 0)
        return self.rc


class FakePopen:
    """Fábrica de ``FakeProc`` que guarda cada lanzamiento y el ffconcat que vio."""

    def __init__(self, stderr_text: str = "", honors_sigint: bool = True) -> None:
        self.procs: list[FakeProc] = []
        self.concat_seen: list[tuple[str, int]] = []
        self.stderr_text = stderr_text
        self.honors_sigint = honors_sigint

    def __call__(self, args: list[str], **kwargs: Any) -> FakeProc:
        concat = Path(args[args.index("-i") + 1])
        self.concat_seen.append((concat.read_text(encoding="utf-8"), concat.stat().st_mode & 0o777))
        proc = FakeProc(args, stderr_text=self.stderr_text, honors_sigint=self.honors_sigint, **kwargs)
        self.procs.append(proc)
        return proc

    @property
    def last(self) -> FakeProc:
        return self.procs[-1]
