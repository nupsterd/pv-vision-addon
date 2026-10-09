"""Integración con ffmpeg y un servidor RTSP real (mediamtx) que emite H.265 con usuario y clave.

Corren dentro de la imagen del add-on (ver README, "Tests"); se saltean si faltan
``ffmpeg`` (con ``libx265`` para la fuente) o ``mediamtx`` (``PV_VISION_MEDIAMTX`` o PATH).
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import signal
import socket
import subprocess
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

import pytest

from pv_vision.audit import AuditWriter
from pv_vision.recorder import Recorder
from pv_vision.segments import list_segments
from tests.conftest import BOGOTA, CAMERA_USER

MEDIAMTX = os.environ.get("PV_VISION_MEDIAMTX") or shutil.which("mediamtx")
FFMPEG = shutil.which("ffmpeg")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not (MEDIAMTX and FFMPEG), reason="requiere ffmpeg y mediamtx"),
]

# mediamtx solo admite A-Z 0-9 ! $ ( ) * + . ; < = > [ ] ^ _ - , @ # & en credenciales: la clave
# de integración usa @ # & + (cambian al URL-codificar). La de los tests unitarios suma : / '.
CAMERA_PASSWORD = "Cl@ve#no&loguear+77"
ENC = quote(CAMERA_PASSWORD, safe="")
PATH = "cam/realmonitor"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class RtspServer:
    def __init__(self, workdir: Path) -> None:
        self.port = _free_port()
        self.cfg = workdir / "mediamtx.yml"
        # JSON es YAML válido: la clave de prueba queda bien citada.
        self.cfg.write_text(
            json.dumps(
                {
                    "logLevel": "error",
                    "rtspAddress": f"127.0.0.1:{self.port}",
                    "protocols": ["tcp"],
                    "rtmp": False,
                    "hls": False,
                    "webrtc": False,
                    "srt": False,
                    "authInternalUsers": [
                        {"user": "any", "pass": "", "ips": ["127.0.0.1"], "permissions": [{"action": "publish"}]},
                        {"user": CAMERA_USER, "pass": CAMERA_PASSWORD, "permissions": [{"action": "read"}]},
                    ],
                    "paths": {"all_others": {}},
                }
            )
        )
        self.server: subprocess.Popen | None = None
        self.publisher: subprocess.Popen | None = None

    def start(self) -> None:
        self.server = subprocess.Popen([MEDIAMTX, str(self.cfg)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(0.7)
        # Fuente H.265 25 fps con GOP 50 (2 s), como la Dahua (P5).
        self.publisher = subprocess.Popen(
            [FFMPEG, "-hide_banner", "-loglevel", "quiet", "-re", "-f", "lavfi", "-i",
             "testsrc2=size=640x360:rate=25", "-c:v", "libx265", "-x265-params", "log-level=none:keyint=50",
             "-b:v", "500k", "-f", "rtsp", "-rtsp_transport", "tcp", f"rtsp://127.0.0.1:{self.port}/{PATH}"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )  # fmt: skip
        time.sleep(2.5)

    def stop(self) -> None:
        for p in (self.publisher, self.server):
            if p is not None and p.poll() is None:
                p.kill()
                p.wait()


@pytest.fixture
def server(tmp_path):
    srv = RtspServer(tmp_path)
    srv.start()
    yield srv
    srv.stop()


def _recorder(tmp_path: Path, srv: RtspServer, password: str = CAMERA_PASSWORD, **kw) -> Recorder:
    return Recorder(
        host="127.0.0.1",
        user=CAMERA_USER,
        password=password,
        stream="main",
        segment_seconds=kw.pop("segment_seconds", 4),
        tz="America/Bogota",
        out_dir=tmp_path / "media",
        tmp_dir=tmp_path / "tmp",
        audit=AuditWriter(tmp_path / "audit"),
        port=srv.port,
        **kw,
    )


def _run(rec: Recorder, seconds: float, until=None) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        rec.tick(True)
        if until is not None and until():
            return
        time.sleep(0.25)


def _ffprobe(path: Path) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_name,codec_tag_string:format=duration",
         "-of", "json", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout  # fmt: skip
    return json.loads(out)


def _ffmpeg_pids() -> list[int]:
    pids = []
    for d in Path("/proc").iterdir():
        if d.name.isdigit():
            try:
                if (d / "comm").read_text().strip() == "ffmpeg":
                    pids.append(int(d.name))
            except OSError:
                continue
    return pids


def _audit_lines(tmp_path: Path) -> list[dict]:
    return [
        json.loads(line) for f in sorted((tmp_path / "audit").glob("*.jsonl")) for line in f.read_text().splitlines()
    ]


def test_graba_hevc_hvc1_en_segmentos_con_hora_local_y_sin_secretos(server, tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    rec = _recorder(tmp_path, server)
    inicio = datetime.now(BOGOTA)
    rec.tick(True)
    time.sleep(3)
    # Mientras graba: ni la clave ni el usuario en la línea de comandos ni en el entorno de ffmpeg.
    for pid in _ffmpeg_pids():
        cmdline = Path(f"/proc/{pid}/cmdline").read_bytes().decode(errors="replace")
        environ = Path(f"/proc/{pid}/environ").read_bytes().decode(errors="replace")
        assert CAMERA_PASSWORD not in cmdline and ENC not in cmdline
        assert CAMERA_PASSWORD not in environ and ENC not in environ
    concat = list((tmp_path / "tmp").glob("*.ffconcat"))
    assert len(concat) == 1 and concat[0].stat().st_mode & 0o777 == 0o600
    _run(rec, 9)
    rec.tick(False)
    segs = list_segments(tmp_path / "media", BOGOTA, "main")
    assert len(segs) >= 2
    for i, seg in enumerate(segs):
        info = _ffprobe(seg.path)
        assert info["streams"][0]["codec_name"] == "hevc"
        assert info["streams"][0]["codec_tag_string"] == "hvc1"
        # El último lo corta el SIGINT de fin de ventana: puede durar una fracción de segundo si la
        # parada cae justo después de un corte alineado al reloj (más frecuente con ffmpeg 7.1, que
        # tarda ~0,4 s más en abrir el stream).
        assert float(info["format"]["duration"]) > (0 if i == len(segs) - 1 else 1)
    # Nombre en hora local de Bogotá (no UTC).
    assert abs((segs[0].start - inicio).total_seconds()) < 6
    assert list((tmp_path / "tmp").glob("*.ffconcat")) == []
    creados = [r["file"] for r in _audit_lines(tmp_path) if r["kind"] == "segment_created"]
    assert creados == [s.path.name for s in segs]
    assert CAMERA_PASSWORD not in caplog.text and ENC not in caplog.text


def test_corte_abrupto_deja_un_segmento_reproducible(server, tmp_path):
    rec = _recorder(tmp_path, server, segment_seconds=60)
    rec.tick(True)
    time.sleep(7)
    for pid in _ffmpeg_pids():
        cmd = Path(f"/proc/{pid}/cmdline").read_bytes()
        if b"concat" in cmd:
            os.kill(pid, signal.SIGKILL)
    time.sleep(0.5)
    (seg,) = list_segments(tmp_path / "media", BOGOTA, "main")
    assert float(_ffprobe(seg.path)["format"]["duration"]) > 3
    dec = subprocess.run(["ffmpeg", "-v", "error", "-i", str(seg.path), "-f", "null", "-"], capture_output=True)
    assert dec.returncode == 0
    rec.tick(False)


def test_caida_de_la_camara_reintenta_y_retoma(server, tmp_path, caplog):
    caplog.set_level(logging.INFO)
    rec = _recorder(tmp_path, server, backoff=(1, 2), stall_seconds=15)
    _run(rec, 5)
    assert rec.recording
    server.stop()
    _run(rec, 20, until=lambda: rec.counters.reconnects >= 1)
    assert rec.counters.reconnects >= 1
    server.start()
    antes = {s.path.name for s in list_segments(tmp_path / "media", BOGOTA)}
    _run(
        rec,
        20,
        until=lambda: rec.recording and {s.path.name for s in list_segments(tmp_path / "media", BOGOTA)} - antes,
    )
    _run(rec, 3)
    rec.tick(False)
    nuevos = {s.path.name for s in list_segments(tmp_path / "media", BOGOTA)} - antes
    assert nuevos, "no retomó la grabación después de volver la cámara"
    assert any(r["kind"] == "reconnect" for r in _audit_lines(tmp_path))
    assert "Caída de la grabación" in caplog.text


def test_clave_incorrecta_falla_sin_mostrar_la_clave(server, tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    mala = "Otra@clave#mala+99"
    rec = _recorder(tmp_path, server, password=mala, backoff=(30,))
    _run(rec, 12, until=lambda: rec.counters.reconnects >= 1)
    rec.tick(False)
    assert rec.counters.reconnects == 1
    assert "401" in caplog.text
    texto = caplog.text + json.dumps(_audit_lines(tmp_path))
    assert mala not in texto and quote(mala, safe="") not in texto
    assert "rtsp://***@127.0.0.1" in texto


def test_parada_justo_en_un_corte_no_deja_segmentos_rotos(server, tmp_path):
    """Hallazgo del PR A: si la parada cae en el instante de un corte, ffmpeg puede dejar un MP4 de
    28 bytes (sin moov) o un segmento de una fracción de segundo. Se descartan (segment_discarded)."""
    from pv_vision.media import motivo_descarte, probar

    for intento in range(3):
        sub = tmp_path / f"i{intento}"
        rec = _recorder(sub, server)
        rec.tick(True)
        deadline = time.monotonic() + 20
        while len(list_segments(sub / "media", BOGOTA)) < 3 and time.monotonic() < deadline:
            rec.tick(True)
            time.sleep(0.01)
        rec.tick(False)  # justo al aparecer el 3.er segmento
        for seg in list_segments(sub / "media", BOGOTA):
            assert motivo_descarte(probar(seg.path)) is None, f"quedó {seg.path.name} ({seg.size} bytes)"
        lineas = _audit_lines(sub)
        assert all(r["kind"] in ("segment_created", "segment_discarded") for r in lineas)
        assert any(r["kind"] == "segment_discarded" for r in lineas) or len(lineas) == 3
