"""Proceso del modo de análisis: ``python3 -m pv_vision.analisis`` (lo lanza el grabador).

Corre aparte del grabador, con ``nice 10``. Si ``analisis_solo_fuera_de_ventanas`` (default),
no empieza clips dentro de una ventana de grabación: termina el clip en curso y espera a que
la ventana cierre (Q7). Cuando no queda nada pendiente, vuelve a mirar cada 10 min (por si
aparecen clips nuevos o cambian los parámetros). Un error acá nunca frena al grabador.
"""

from __future__ import annotations

import logging
import os
import signal
import sys
import time
from datetime import datetime
from pathlib import Path

from pv_vision.analisis import referencia
from pv_vision.analisis.corrida import Corrida, hash_parametros
from pv_vision.config import MEDIA_BASE, Config, ruta_relativa_a
from pv_vision.main import load_config, setup_logging
from pv_vision.media import probar
from pv_vision.schedule import active_window

log = logging.getLogger("pv_vision.analisis")

NICE = 10
ESPERA_VENTANA_S = 15
ESPERA_SIN_PENDIENTES_S = 600


def generar_referencia(cfg: Config, media: Path = Path(MEDIA_BASE)) -> Path | None:
    """Cuadro de referencia si ``referencia_desde`` tiene un archivo (idempotente por parámetros)."""
    if not cfg.referencia_desde:
        return None
    origen = Path(ruta_relativa_a(cfg.referencia_desde, media))
    info = probar(origen)
    if info is None:
        log.error("referencia_desde: %s no existe o no es un video válido.", origen.name)
        return None
    h = hash_parametros(cfg, "referencia")[:8]
    destino = media / "referencia" / f"ref_{origen.stem}_{h}.png"
    if destino.exists():
        log.info("Cuadro de referencia ya generado: %s", destino)
        return destino
    if referencia.generar(origen, destino, info.ancho, info.alto, cfg.roi_px, cfg.linea_px, cfg.sentido_salida):
        return destino
    return None


def main() -> None:
    cfg = load_config()
    setup_logging(cfg.log_level)
    errores = cfg.validate_analisis()
    if errores:
        for e in errores:
            log.error("Análisis desactivado, configuración inválida: %s", e)
        sys.exit(2)
    try:
        os.nice(NICE)
    except OSError:
        pass
    corrida = Corrida(cfg)

    def on_signal(signum: int, _frame: object) -> None:
        corrida.detener = True

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)
    log.info(
        "Análisis de archivos activo (run %s): dir=%s modelos=%s 1 de cada %d, %d hilos, solo fuera de ventanas=%s.",
        corrida.run_id,
        cfg.analisis_dir,
        ",".join(cfg.analisis_modelos),
        cfg.analisis_cada_n,
        cfg.analisis_hilos,
        cfg.analisis_solo_fuera_de_ventanas,
    )
    generar_referencia(cfg)
    windows, tz = cfg.windows, cfg.zone

    def en_ventana() -> bool:
        return cfg.analisis_solo_fuera_de_ventanas and active_window(datetime.now(tz), windows, cfg.dias) is not None

    pausado = False
    while not corrida.detener:
        if en_ventana():
            if not pausado:
                log.info("Ventana de grabación en curso: análisis en pausa hasta que cierre.")
                pausado = True
            time.sleep(ESPERA_VENTANA_S)
            continue
        if pausado:
            log.info("Ventana cerrada: se retoma el análisis.")
            pausado = False
        hechos = corrida.pasada(pausar=en_ventana)
        if corrida.detener or en_ventana():
            continue
        if hechos == 0:
            log.info(
                "Sin clips pendientes en %s; se vuelve a mirar en %d min.",
                cfg.analisis_dir,
                ESPERA_SIN_PENDIENTES_S // 60,
            )
            corrida.escribir_resumen()
            for _ in range(ESPERA_SIN_PENDIENTES_S):
                if corrida.detener:
                    break
                time.sleep(1)
    log.info("Análisis detenido.")


if __name__ == "__main__":
    main()
