from __future__ import annotations

from urllib.parse import quote

import pytest

from pv_vision.ffmpeg import (
    Redactor,
    build_command,
    child_env,
    ffconcat_text,
    masked_url,
    rtsp_url,
    write_ffconcat,
)
from tests.conftest import CAMERA_HOST, CAMERA_PASSWORD, CAMERA_USER

ENC = quote(CAMERA_PASSWORD, safe="")


def test_rtsp_url_principal_y_secundario_con_credenciales_codificadas():
    main = rtsp_url(CAMERA_HOST, CAMERA_USER, CAMERA_PASSWORD, "main")
    assert main == f"rtsp://visor-pv:{ENC}@192.0.2.21:554/cam/realmonitor?channel=1&subtype=0"
    assert rtsp_url(CAMERA_HOST, CAMERA_USER, CAMERA_PASSWORD, "sub").endswith("channel=1&subtype=1")
    # @ : / # ' de la clave quedan codificados: no rompen la URL ni el ffconcat.
    assert ENC == "Cl%40ve%3Ano%2Floguear%2377%27x"


def test_masked_url_sin_credenciales():
    m = masked_url(CAMERA_HOST, "main")
    assert m == "rtsp://***@192.0.2.21:554/cam/realmonitor?channel=1&subtype=0"
    assert CAMERA_USER not in m


def test_comando_sin_url_ni_credenciales(tmp_path):
    cmd = build_command(tmp_path / "x.ffconcat", "/media/pv_vision", "main", 300)
    texto = " ".join(cmd)
    assert CAMERA_PASSWORD not in texto and ENC not in texto and CAMERA_USER not in texto
    assert "rtsp://" not in texto
    assert cmd[0] == "ffmpeg"
    assert cmd[cmd.index("-f") + 1] == "concat"
    assert cmd[cmd.index("-i") + 1] == str(tmp_path / "x.ffconcat")
    for par in (["-c", "copy"], ["-map", "0:v"], ["-tag:v", "hvc1"], ["-segment_time", "300"], ["-strftime", "1"]):
        i = cmd.index(par[0])
        assert cmd[i : i + 2] == par
    assert "movflags=+frag_keyframe+empty_moov+default_base_moof" in cmd
    assert cmd[-1] == "/media/pv_vision/%Y-%m-%d_%H-%M-%S_main.mp4"


def test_ffconcat_con_opciones_rtsp():
    url = rtsp_url(CAMERA_HOST, CAMERA_USER, CAMERA_PASSWORD, "main")
    txt = ffconcat_text(url)
    assert txt.splitlines() == [
        "ffconcat version 1.0",
        f"file '{url}'",
        "option rtsp_transport tcp",
        "option timeout 5000000",
    ]
    with pytest.raises(ValueError):
        ffconcat_text("rtsp://a'b@h/x")


def test_write_ffconcat_modo_600(tmp_path):
    p = write_ffconcat(tmp_path / "a.ffconcat", "rtsp://u:p@192.0.2.21:554/x")
    assert p.stat().st_mode & 0o777 == 0o600


def test_child_env_fija_tz():
    env = child_env("America/Bogota", base={"PATH": "/bin", "TZ": "UTC"})
    assert env == {"PATH": "/bin", "TZ": "America/Bogota"}


# Líneas reales de stderr de ffmpeg 6.1.2 capturadas en el Phase 0 (clave cambiada por la de prueba).
def _lineas_reales(user: str, pw: str) -> list[str]:
    return [
        f"Error opening input file rtsp://{user}:{pw}@127.0.0.1:5540/cam.",
        f"[concat @ 0x7f69e48aa600] Impossible to open 'rtsp://{user}:{pw}@127.0.0.1:8554/cam'",
        "[rtsp @ 0x7f69e459a800] method DESCRIBE failed: 401 Unauthorized",
        "[in#0 @ 0x7f69e03eb940] Error opening input: Server returned 401 Unauthorized (authorization failed)",
        "[tcp @ 0x7d9eebcefec0] Connection to tcp://127.0.0.1:5540?timeout=2000000 failed: Connection refused",
        "[in#0/rtsp @ 0x737dcdd49940] Error during demuxing: Operation timed out",
    ]


@pytest.mark.parametrize("forma", [CAMERA_PASSWORD, ENC])
def test_mascara_sobre_lineas_reales_de_error(forma):
    r = Redactor([CAMERA_PASSWORD, CAMERA_USER])
    for linea in _lineas_reales(CAMERA_USER, forma):
        out = r.mask(linea)
        assert CAMERA_PASSWORD not in out and ENC not in out, out
        if "rtsp://" in linea and "@" in linea:
            assert "rtsp://***@127.0.0.1" in out


def test_mascara_clave_suelta_y_codificada_fuera_de_una_url():
    r = Redactor([CAMERA_PASSWORD])
    assert r.mask(f"algo {CAMERA_PASSWORD} y {ENC} y {quote(CAMERA_PASSWORD)}") == "algo *** y *** y ***"


def test_mascara_userinfo_sin_conocer_la_clave():
    assert Redactor([]).mask("open rtsp://otro:xyz@10.0.0.1/x failed") == "open rtsp://***@10.0.0.1/x failed"


def test_mascara_no_toca_lineas_inocuas():
    linea = "[segment @ 0x1] Opening '/media/pv_vision/2026-10-05_06-20-03_main.mp4' for writing"
    assert Redactor([CAMERA_PASSWORD]).mask(linea) == linea
