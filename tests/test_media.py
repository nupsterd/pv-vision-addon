from __future__ import annotations

import shutil

import pytest

from pv_vision.media import InfoVideo, _fraccion, motivo_descarte, probar


def test_fraccion():
    assert _fraccion("25/1") == 25.0
    assert _fraccion("0/0") is None and _fraccion(None) is None and _fraccion("x") is None


def test_motivo_descarte():
    assert motivo_descarte(None) == "sin video válido"
    assert motivo_descarte(InfoVideo(0.32, 1920, 1080, "hevc")) == "dura 0.32 s (< 1 s)"
    assert motivo_descarte(InfoVideo(1.0, 1920, 1080, "hevc")) is None


@pytest.mark.skipif(not shutil.which("ffprobe"), reason="requiere ffprobe")
def test_probar_mp4_de_28_bytes_es_invalido(tmp_path):
    p = tmp_path / "x.mp4"
    p.write_bytes(b"\x00\x00\x00\x1cftypisom\x00\x00\x02\x00isomiso2mp41")  # 28 bytes: solo ftyp
    assert len(p.read_bytes()) == 28
    assert probar(p) is None
    assert probar(tmp_path / "no-existe.mp4") is None
