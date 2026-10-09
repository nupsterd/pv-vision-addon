"""URL RTSP, archivo ffconcat, comando de ffmpeg y máscara de secretos.

Regla del proyecto (§0): ningún secreto como argumento de línea de comandos. ffmpeg
6.1 no tiene opciones de usuario/clave para RTSP ni ``-/opción archivo``, así que la
URL con credenciales va SOLO dentro de un archivo ``ffconcat`` (modo 600, creado con
umask 077 en ``/tmp`` del contenedor y borrado al terminar) que ffmpeg abre con el
demuxer ``concat``. ffmpeg igual imprime la URL completa en sus errores (verificado
en el Phase 0), por eso TODA línea de su stderr pasa por ``Redactor.mask`` antes de
llegar al log.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable
from pathlib import Path
from urllib.parse import quote

from pv_vision.segments import segment_template

RTSP_PORT = 554
SUBTYPES = {"main": 0, "sub": 1}
# Timeout de E/S del socket RTSP (µs). Con la cámara muda y el TCP abierto, ffmpeg sale a los ~10 s.
RTSP_TIMEOUT_US = 5_000_000
MOVFLAGS = "+frag_keyframe+empty_moov+default_base_moof"

_USERINFO_RE = re.compile(r"(rtsps?://)[^\s/@'\"]*@", re.IGNORECASE)


def rtsp_path(stream: str) -> str:
    return f"/cam/realmonitor?channel=1&subtype={SUBTYPES[stream]}"


def rtsp_url(host: str, user: str, password: str, stream: str, port: int = RTSP_PORT) -> str:
    """URL con credenciales URL-codificadas (``quote(..., safe="")``). Nunca va a un log."""
    return f"rtsp://{quote(user, safe='')}:{quote(password, safe='')}@{host}:{port}{rtsp_path(stream)}"


def masked_url(host: str, stream: str, port: int = RTSP_PORT) -> str:
    """La misma URL para el log: sin usuario ni clave."""
    return f"rtsp://***@{host}:{port}{rtsp_path(stream)}"


def ffconcat_text(url: str) -> str:
    # Las credenciales van URL-codificadas y el host se valida sin comillas, así que la URL
    # no puede contener ' y no rompe la directiva file.
    if "'" in url or "\n" in url:
        raise ValueError("la URL no puede contener comillas simples ni saltos de línea")
    return f"ffconcat version 1.0\nfile '{url}'\noption rtsp_transport tcp\noption timeout {RTSP_TIMEOUT_US}\n"


def write_ffconcat(path: str | Path, url: str) -> Path:
    """Escribe el ffconcat con modo 600 (umask 077 durante la creación)."""
    p = Path(path)
    old = os.umask(0o077)
    try:
        fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(ffconcat_text(url))
    finally:
        os.umask(old)
    os.chmod(p, 0o600)
    return p


def remove_quietly(path: str | Path | None) -> None:
    if path is None:
        return
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass


def build_command(concat_path: str | Path, out_dir: str | Path, stream: str, segment_seconds: int) -> list[str]:
    """Argumentos de ffmpeg. Sin URL ni credenciales: la entrada es el archivo ffconcat."""
    return [
        "ffmpeg",
        "-hide_banner",
        "-nostdin",
        "-loglevel",
        "warning",
        "-f",
        "concat",
        "-safe",
        "0",
        "-protocol_whitelist",
        "file,rtsp,rtp,tcp,udp",
        "-i",
        str(concat_path),
        "-map",
        "0:v",
        "-c",
        "copy",
        "-tag:v",
        "hvc1",
        "-f",
        "segment",
        "-segment_time",
        str(segment_seconds),
        "-segment_atclocktime",
        "1",
        "-reset_timestamps",
        "1",
        "-strftime",
        "1",
        "-segment_format",
        "mp4",
        "-segment_format_options",
        f"movflags={MOVFLAGS}",
        str(Path(out_dir) / segment_template(stream)),
    ]


def child_env(tz: str, base: dict[str, str] | None = None) -> dict[str, str]:
    """Entorno de ffmpeg con ``TZ`` explícita: ``-strftime`` usa la zona del proceso hijo."""
    env = dict(os.environ if base is None else base)
    env["TZ"] = tz
    return env


class Redactor:
    """Enmascara credenciales en texto: ``rtsp://usuario:clave@`` → ``rtsp://***@``,
    y la clave literal y su forma URL-codificada → ``***``."""

    def __init__(self, secrets: Iterable[str]) -> None:
        variants: set[str] = set()
        for s in secrets:
            if not s:
                continue
            variants.update({s, quote(s, safe=""), quote(s)})
        # Primero las más largas: una variante no deja restos de otra.
        self._literals = sorted((v for v in variants if v), key=len, reverse=True)

    def mask(self, text: str) -> str:
        # Primero los literales: una clave sin codificar con @ o / cortaría el userinfo y
        # dejaría restos de la clave después de la máscara de la URL.
        out = text
        for lit in self._literals:
            out = out.replace(lit, "***")
        return _USERINFO_RE.sub(r"\1***@", out)
