from __future__ import annotations

import json
import logging
from dataclasses import fields
from pathlib import Path

import pytest
import yaml

from pv_vision import ADDON_VERSION
from pv_vision.config import RETENCION_MAX_DIAS, Config
from pv_vision.main import load_config, startup
from tests.conftest import CAMERA_PASSWORD, CAMERA_USER, make_config

ROOT = Path(__file__).resolve().parent.parent


def _yaml() -> dict:
    return yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))


def test_config_yaml_tiene_las_mismas_opciones_que_config():
    y = _yaml()
    nombres = [f.name for f in fields(Config)]
    assert list(y["options"]) == nombres
    assert list(y["schema"]) == nombres


def test_defaults_del_yaml_iguales_a_los_de_config():
    y = _yaml()
    assert Config.from_dict(y["options"]) == Config()
    assert Config().ventanas == ("06:20-06:50", "11:50-12:20", "18:00-18:50")
    assert Config().dias == (1, 2, 3, 4, 5)
    assert (Config().stream, Config().segment_seconds, Config().retencion_dias) == ("main", 300, 30)
    assert (Config().min_free_gb, Config().tz) == (10, "America/Bogota")


def test_version_coincide_en_yaml_y_paquete():
    assert _yaml()["version"] == ADDON_VERSION == "0.2.0-alpha"


def test_yaml_slug_arch_mapas_y_api():
    y = _yaml()
    assert y["slug"] == "pv_vision"
    assert y["arch"] == ["aarch64"]
    assert y["boot"] == "auto"
    assert y["hassio_api"] is True
    assert "media:rw" in y["map"] and "addon_config:rw" in y["map"]
    assert y["schema"]["camera_password"] == "password"


def test_schema_del_supervisor_topea_la_retencion_en_30():
    assert _yaml()["schema"]["retencion_dias"] == f"int(1,{RETENCION_MAX_DIAS})"
    assert RETENCION_MAX_DIAS == 30


def test_config_valida_por_defecto_con_credenciales():
    assert make_config().validate() == []


@pytest.mark.parametrize("dias", [31, 60, 365, 0, -1])
def test_retencion_fuera_de_1_30_se_rechaza(dias):
    errores = make_config(retencion_dias=dias).validate()
    assert any("retencion_dias" in e for e in errores)


@pytest.mark.parametrize("dias", [1, 7, 30])
def test_retencion_dentro_del_tope_se_acepta(dias):
    assert make_config(retencion_dias=dias).validate() == []


@pytest.mark.parametrize(
    ("ventanas", "fragmento"),
    [
        (["22:00-02:00"], "medianoche"),
        (["06:20-06:20"], "cero"),
        (["6:20-06:50"], "formato"),
        (["06:20-24:00"], "rango"),
        (["06:00-07:00", "06:30-08:00"], "solapan"),
        ([], "vacía"),
    ],
)
def test_ventanas_invalidas(ventanas, fragmento):
    errores = make_config(ventanas=ventanas).validate()
    assert any(fragmento in e for e in errores), errores


@pytest.mark.parametrize(("dias", "fragmento"), [([], "vacía"), ([0], "1-7"), ([8], "1-7"), ([1, 1], "repetidos")])
def test_dias_invalidos(dias, fragmento):
    errores = make_config(dias=dias).validate()
    assert any(fragmento in e for e in errores), errores


@pytest.mark.parametrize(
    ("campo", "valor", "fragmento"),
    [
        ("camera_host", "", "camera_host"),
        ("camera_host", "rtsp://192.0.2.21", "camera_host"),
        ("camera_host", "192.0.2.21:554", "camera_host"),
        ("camera_user", "", "camera_user"),
        ("camera_password", "", "camera_password"),
        ("stream", "tercero", "stream"),
        ("tz", "Marte/Olympus", "tz"),
        ("segment_seconds", 10, "segment_seconds"),
        ("min_free_gb", 0, "min_free_gb"),
        ("log_level", "trace", "log_level"),
    ],
)
def test_otros_campos_invalidos(campo, valor, fragmento):
    errores = make_config(**{campo: valor}).validate()
    assert any(fragmento in e for e in errores), errores


def test_describe_y_repr_no_muestran_credenciales():
    cfg = make_config()
    d = cfg.describe()
    assert d["camera_password"] == "configurado"
    assert d["camera_user"] == "configurado"
    assert make_config(camera_password="").describe()["camera_password"] == "vacío"
    texto = repr(cfg) + json.dumps(d, ensure_ascii=False)
    assert CAMERA_PASSWORD not in texto


def test_startup_loguea_parametros_sin_secretos(caplog):
    caplog.set_level(logging.INFO)
    startup(make_config())
    texto = caplog.text
    assert "camera_password = configurado" in texto
    assert "retencion_dias = 30" in texto
    assert CAMERA_PASSWORD not in texto and CAMERA_USER not in texto


def test_startup_sale_con_retencion_mayor_a_30(caplog):
    with pytest.raises(SystemExit) as exc:
        startup(make_config(retencion_dias=31))
    assert exc.value.code == 1
    assert "retencion_dias" in caplog.text


def test_load_config_desde_options_json(tmp_path):
    p = tmp_path / "options.json"
    opts = dict(_yaml()["options"], camera_host="192.0.2.21", dias=[1, 3], ventanas=["07:00-07:30"])
    p.write_text(json.dumps(opts), encoding="utf-8")
    cfg = load_config(str(p))
    assert cfg.dias == (1, 3) and cfg.ventanas == ("07:00-07:30",)


def test_load_config_ilegible_no_vuelca_valores(tmp_path, caplog):
    p = tmp_path / "options.json"
    p.write_text('{"camera_password": "' + CAMERA_PASSWORD + '", "segment_seconds": "x"}', encoding="utf-8")
    with pytest.raises(SystemExit):
        load_config(str(p))
    assert CAMERA_PASSWORD not in caplog.text


# ----------------------------------------------------------------------------- análisis


def test_defaults_del_analisis():
    c = Config()
    assert c.analisis_activo is False and c.analisis_dir == "/media/pv_vision/conjunto_b"
    assert c.analisis_modelos == ("yolox_nano",) and c.analisis_cada_n == 3 and c.analisis_hilos == 3
    assert c.roi == "960:720:500:360" and c.linea == "500,467,1113,1080" and c.punto_referencia == "pie"
    assert (c.histeresis_px, c.cuadros_confirmacion, c.conf_alta, c.conf_baja) == (12, 2, 0.5, 0.1)
    assert (c.track_buffer_s, c.min_hits, c.referencia_desde, c.depuracion_clips) == (1.5, 3, "", ())
    assert make_config().validate_analisis() == []


@pytest.mark.parametrize(
    ("campo", "valor", "fragmento"),
    [
        ("analisis_dir", "/config", "fuera de /media/pv_vision"),
        ("analisis_dir", "../../etc", "fuera de /media/pv_vision"),
        ("analisis_modelos", [], "vacía"),
        ("analisis_modelos", ["yolov8n"], "desconocido"),
        ("analisis_modelos", ["yolox_nano", "yolox_nano"], "repetidos"),
        ("analisis_cada_n", 0, "analisis_cada_n"),
        ("analisis_hilos", 4, "analisis_hilos"),
        ("roi", "960:720:1000:400", "se sale"),
        ("linea", "0,0,10,0", "no atraviesa"),
        ("linea", "1,2,3", "formato"),
        ("sentido_salida", "arriba", "sentido_salida"),
        ("punto_referencia", "cabeza", "punto_referencia"),
        ("histeresis_px", 500, "histeresis_px"),
        ("cuadros_confirmacion", 0, "cuadros_confirmacion"),
        ("conf_baja", 0.7, "umbrales"),
        ("track_buffer_s", 0.0, "track_buffer_s"),
        ("min_hits", 0, "min_hits"),
        ("referencia_desde", "/etc/passwd", "fuera de /media/pv_vision"),
        ("depuracion_clips", ["../x.mp4"], "nombre de archivo"),
        ("depuracion_clips", ["clip.avi"], "nombre de archivo"),
    ],
)
def test_opciones_de_analisis_invalidas(campo, valor, fragmento):
    cfg = make_config(**{campo: valor})
    errores = cfg.validate_analisis()
    assert any(fragmento in e for e in errores), errores
    assert cfg.validate() == []  # un error del análisis nunca frena al grabador
    assert CAMERA_PASSWORD not in " ".join(errores)


def test_bool_estricto():
    with pytest.raises(ValueError):
        Config.from_dict({"analisis_activo": "true"})


def test_rutas_relativas_aceptadas():
    assert make_config(analisis_dir="conjunto_b", referencia_desde="conjunto_b/n25.mp4").validate_analisis() == []
    assert make_config(analisis_dir="/media/pv_vision").validate_analisis() == []


def test_startup_con_analisis_invalido_no_sale(caplog):
    caplog.set_level(logging.INFO)
    startup(make_config(analisis_activo=True, analisis_hilos=9))
    assert "Análisis desactivado, configuración inválida: analisis_hilos" in caplog.text
    assert CAMERA_PASSWORD not in caplog.text
