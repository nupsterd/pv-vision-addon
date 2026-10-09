from __future__ import annotations

import json
from datetime import timedelta

from pv_vision.audit import AuditWriter
from pv_vision.clock import parse_host_info, supervisor_token
from tests.conftest import bogota


def test_registro_json_por_linea_y_rotacion_diaria(tmp_path):
    now = [bogota(2026, 10, 5, 23, 59, 59)]
    a = AuditWriter(tmp_path, now=lambda: now[0])
    a.record("segment_created", file="2026-10-05_18-45-00_main.mp4", bytes=10, stream="main")
    now[0] += timedelta(seconds=2)
    a.record("reconnect", motivo="x", retry_seconds=5, stream="main")
    a.close()
    d1 = (tmp_path / "pv_vision_audit_20261005.jsonl").read_text().splitlines()
    d2 = (tmp_path / "pv_vision_audit_20261006.jsonl").read_text().splitlines()
    assert json.loads(d1[0]) == {
        "ts": "2026-10-05T23:59:59-05:00",
        "kind": "segment_created",
        "file": "2026-10-05_18-45-00_main.mp4",
        "bytes": 10,
        "stream": "main",
    }
    assert json.loads(d2[0])["kind"] == "reconnect"


def test_retencion_de_auditoria_30_dias(tmp_path):
    for name in ("pv_vision_audit_20260901.jsonl", "pv_vision_audit_20260906.jsonl", "otro_20200101.jsonl"):
        (tmp_path / name).write_text("{}\n")
    a = AuditWriter(tmp_path, now=lambda: bogota(2026, 10, 6, 8, 0))
    borrados = a.purge()
    assert [p.name for p in borrados] == ["pv_vision_audit_20260901.jsonl"]
    assert (tmp_path / "pv_vision_audit_20260906.jsonl").exists()
    assert (tmp_path / "otro_20200101.jsonl").exists()


def test_error_de_disco_no_corta(tmp_path, caplog):
    archivo = tmp_path / "no-es-carpeta"
    archivo.write_text("")
    AuditWriter(archivo).record("segment_created", file="x")
    assert "Falló la escritura de la auditoría" in caplog.text


def test_supervisor_token_desde_s6(tmp_path, monkeypatch):
    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
    assert supervisor_token(str(tmp_path)) == ""
    (tmp_path / "SUPERVISOR_TOKEN").write_text("tok\n")
    assert supervisor_token(str(tmp_path)) == "tok"
    monkeypatch.setenv("SUPERVISOR_TOKEN", "env")
    assert supervisor_token(str(tmp_path)) == "env"


def test_parse_host_info():
    assert parse_host_info(b'{"result":"ok","data":{"dt_synchronized":true}}') == (True, "")
    assert parse_host_info(b'{"data":{"dt_synchronized":false}}') == (False, "dt_synchronized = false")
    assert parse_host_info(b"basura")[0] is False
