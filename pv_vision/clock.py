"""¿Está sincronizado el reloj de la Pi? (``GET /host/info`` → ``dt_synchronized``).

La Pi 5 sin batería de RTC arranca tras un apagón con la hora vieja hasta que NTP
sincroniza. Un segmento con fecha falsa rompe la retención por nombre, así que no se
graba hasta confirmar la hora (P4). Requiere ``hassio_api: true``. Mismo patrón que
``nut-ups-addon`` (D10), con ``urllib`` de la stdlib.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from collections.abc import Callable

SUPERVISOR_HOST_INFO = "http://supervisor/host/info"
# s6-overlay v3 (imagen base de HA) NO pasa el entorno del contenedor al CMD: el token que
# inyecta el Supervisor queda solo en este archivo. Verificado en amd64-base:3.21 (nut-ups).
S6_ENV_DIR = "/run/s6/container_environment"
SUPERVISOR_TIMEOUT = 3.0

# Devuelve (sincronizado, motivo si no lo está).
SyncCheck = Callable[[], tuple[bool, str]]


def supervisor_token(s6_env_dir: str | None = None) -> str:
    """``SUPERVISOR_TOKEN`` del entorno o, si no está, del entorno guardado por s6-overlay."""
    token = os.environ.get("SUPERVISOR_TOKEN", "").strip()
    if token:
        return token
    try:
        with open(os.path.join(s6_env_dir or S6_ENV_DIR, "SUPERVISOR_TOKEN"), encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return ""


def parse_host_info(body: bytes) -> tuple[bool, str]:
    try:
        synced = json.loads(body)["data"]["dt_synchronized"]
    except (ValueError, KeyError, TypeError):
        return False, "respuesta de /host/info sin data.dt_synchronized"
    if synced is True:
        return True, ""
    return False, "dt_synchronized = false"


def supervisor_sync_check(token: str | None = None, url: str = SUPERVISOR_HOST_INFO) -> SyncCheck:
    token = token if token is not None else supervisor_token()

    def check() -> tuple[bool, str]:
        if not token:
            return False, "sin SUPERVISOR_TOKEN (¿falta hassio_api: true?)"
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
        try:
            with urllib.request.urlopen(req, timeout=SUPERVISOR_TIMEOUT) as resp:
                return parse_host_info(resp.read())
        except urllib.error.HTTPError as exc:
            return False, f"Supervisor respondió HTTP {exc.code}"
        except (urllib.error.URLError, OSError) as exc:
            return False, f"Supervisor no responde ({type(exc).__name__})"

    return check
