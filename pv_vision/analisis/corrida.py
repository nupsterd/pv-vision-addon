"""Una corrida del modo de análisis de archivos (detector en sombra, D1).

Por cada clip de ``analisis_dir`` y cada modelo de ``analisis_modelos``: decodifica (1 de N),
detecta personas, sigue y cuenta cruces de la línea. Escribe en ``/config/analisis/<run_id>/``:

- ``cruces.jsonl``: un registro ``kind="cruce"`` por cruce (hora absoluta, sentido, pista) y
  un ``kind="clip"`` por clip analizado (rendimiento), así la evaluación sabe qué clips se
  analizaron aunque no tengan cruces;
- ``resumen.json``: totales de la corrida.

Ningún cuadro ni video se guarda acá. Idempotente: ``/config/analisis/hechos.json`` guarda
``nombre|bytes|modelo|hash de parámetros``; lo hecho no se repite y se puede interrumpir.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from pv_vision import ADDON_VERSION
from pv_vision.analisis import clips as clips_mod
from pv_vision.analisis.conteo import ContadorLinea
from pv_vision.analisis.decodificar import Decodificador
from pv_vision.analisis.depuracion import VideoDepuracion
from pv_vision.analisis.geometria import REF_H, REF_W, Mapeo, Roi, dahua_linea_px
from pv_vision.analisis.metricas import Medidor
from pv_vision.analisis.modelos import MODELOS, Modelo
from pv_vision.analisis.tracker import ByteTrackMinimo
from pv_vision.config import MEDIA_BASE, Config, ruta_relativa_a
from pv_vision.media import InfoVideo, motivo_descarte, probar

log = logging.getLogger("pv_vision.analisis")

ANALISIS_DIR = Path("/config/analisis")
FPS_POR_DEFECTO = 25.0
# Un archivo modificado hace menos de esto puede estar escribiéndose (grabador): se deja para después.
EDAD_MINIMA_S = 60


def hash_parametros(cfg: Config, modelo: str) -> str:
    """Hash corto de todo lo que cambia el resultado del análisis de un clip."""
    p = {
        "modelo": modelo,
        "roi": cfg.roi,
        "linea": cfg.linea,
        "sentido": cfg.sentido_salida,
        "punto": cfg.punto_referencia,
        "hist": cfg.histeresis_px,
        "conf": cfg.cuadros_confirmacion,
        "alta": cfg.conf_alta,
        "baja": cfg.conf_baja,
        "buffer": cfg.track_buffer_s,
        "hits": cfg.min_hits,
        "cada_n": cfg.analisis_cada_n,
    }
    return hashlib.sha256(json.dumps(p, sort_keys=True).encode()).hexdigest()[:10]


def roi_en_clip(roi: Roi, ancho: int, alto: int) -> Roi:
    """ROI de 1920×1080 escalado a la resolución del clip y recortado al cuadro."""
    r = roi.scaled(ancho / REF_W, alto / REF_H)
    w, h = min(r.w, ancho), min(r.h, alto)
    return Roi(w, h, min(r.x, ancho - w), min(r.y, alto - h))


class Hechos:
    def __init__(self, path: Path) -> None:
        self.path = path
        try:
            self._d: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self._d = {}

    def __contains__(self, clave: str) -> bool:
        return clave in self._d

    def marcar(self, clave: str, valor: dict[str, Any]) -> None:
        self._d[clave] = valor
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._d, ensure_ascii=False, indent=0), encoding="utf-8")
        os.replace(tmp, self.path)


class Corrida:
    def __init__(
        self,
        cfg: Config,
        *,
        base: Path = ANALISIS_DIR,
        media: Path = Path(MEDIA_BASE),
        detector_factory: Callable[[Modelo, int], Callable] | None = None,
        probe: Callable[[Path], InfoVideo | None] = probar,
        ahora: Callable[[], datetime] | None = None,
        edad_minima_s: float = EDAD_MINIMA_S,
    ) -> None:
        self.cfg = cfg
        self.tz = cfg.zone
        self._ahora = ahora or (lambda: datetime.now(self.tz))
        self.run_id = self._ahora().strftime("%Y%m%d-%H%M%S")
        self.base = base
        self.media = media
        self.dir = base / self.run_id
        self.hechos = Hechos(base / "hechos.json")
        self._probe = probe
        self._edad_min = edad_minima_s
        self._detectores: dict[str, Callable] = {}
        self._factory = detector_factory or _detector_real
        self.totales: dict[str, Any] = {"clips": 0, "salteados": 0, "cruces": 0, "salidas": 0, "entradas": 0}
        self.detener = False  # lo pone el manejador de SIGTERM

    # ------------------------------------------------------------------ utilidades

    def _detector(self, modelo: Modelo) -> Callable:
        if modelo.nombre not in self._detectores:
            self._detectores[modelo.nombre] = self._factory(modelo, self.cfg.analisis_hilos)
        return self._detectores[modelo.nombre]

    def _escribir(self, registro: dict[str, Any]) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        with (self.dir / "cruces.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(registro, ensure_ascii=False, separators=(",", ":")) + "\n")

    def escribir_resumen(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        r = {
            "run_id": self.run_id,
            "version": ADDON_VERSION,
            "modelos": list(self.cfg.analisis_modelos),
            "dir": self.cfg.analisis_dir,
            **self.totales,
        }
        (self.dir / "resumen.json").write_text(json.dumps(r, ensure_ascii=False, indent=1), encoding="utf-8")

    # ------------------------------------------------------------------ pasada

    def pendientes(self) -> list[clips_mod.Clip]:
        d = Path(ruta_relativa_a(self.cfg.analisis_dir, self.media))
        real = Path(os.path.realpath(d))
        base_real = Path(os.path.realpath(self.media))
        if real != base_real and base_real not in real.parents:
            log.error("analisis_dir %s apunta fuera de %s (symlink): no se analiza.", d, self.media)
            return []
        return clips_mod.listar(d, self.tz)

    def pasada(self, pausar: Callable[[], bool] = lambda: False) -> int:
        """Analiza lo pendiente. Devuelve cuántos (clip, modelo) analizó. Se corta entre clips si
        ``pausar()`` es verdadero (empezó una ventana) o si llegó SIGTERM."""
        hechos_ahora = 0
        for clip in self.pendientes():
            for nombre in self.cfg.analisis_modelos:
                if self.detener or pausar():
                    return hechos_ahora
                modelo = MODELOS[nombre]
                try:
                    st = clip.path.stat()
                except FileNotFoundError:
                    continue
                clave = f"{clip.nombre}|{st.st_size}|{nombre}|{hash_parametros(self.cfg, nombre)}"
                if clip.nombre in self.cfg.depuracion_clips:
                    clave += "|dbg"  # pedir el video de depuración vuelve a analizar ese clip
                if clave in self.hechos:
                    continue
                if time.time() - st.st_mtime < self._edad_min:
                    continue
                info = self._probe(clip.path)
                motivo = motivo_descarte(info)
                if motivo is not None:
                    log.info("Clip salteado %s: %s.", clip.nombre, motivo)
                    self.totales["salteados"] += 1
                    self.hechos.marcar(clave, {"run": self.run_id, "estado": "salteado", "motivo": motivo})
                    continue
                assert info is not None
                resultado = self.analizar_clip(clip, info, modelo)
                if resultado is None:  # interrumpido: no se marca
                    return hechos_ahora
                self.hechos.marcar(clave, {"run": self.run_id, "estado": "analizado", "cruces": resultado})
                hechos_ahora += 1
                self.escribir_resumen()
        return hechos_ahora

    # ------------------------------------------------------------------ un clip

    def analizar_clip(self, clip: clips_mod.Clip, info: InfoVideo, modelo: Modelo) -> int | None:
        cfg = self.cfg
        sx, sy = info.ancho / REF_W, info.alto / REF_H
        roi = roi_en_clip(cfg.roi_px, info.ancho, info.alto)
        lin = cfg.linea_px.scaled(sx, sy)
        mapeo = Mapeo(roi, modelo.size, modelo.preproc)
        fps_clip = info.fps or FPS_POR_DEFECTO
        fps_ana = fps_clip / cfg.analisis_cada_n
        tracker = ByteTrackMinimo(
            conf_alta=cfg.conf_alta,
            conf_baja=cfg.conf_baja,
            buffer_cuadros=max(1, math.ceil(cfg.track_buffer_s * fps_ana)),
            min_hits=cfg.min_hits,
        )
        contador = ContadorLinea(
            lin, cfg.sentido_salida, cfg.histeresis_px * (sx + sy) / 2, cfg.cuadros_confirmacion, cfg.punto_referencia
        )
        detector = self._detector(modelo)
        dec = Decodificador(clip.path, mapeo, cfg.analisis_cada_n, modelo.pix_fmt)
        dbg = None
        if clip.nombre in cfg.depuracion_clips:
            destino = self.media / "depuracion" / self.run_id / f"{clip.path.stem}_{modelo.nombre}_dbg.mp4"
            dbg = VideoDepuracion(
                destino,
                mapeo,
                fps_ana,
                lin,
                dahua_linea_px().scaled(sx, sy),
                cfg.histeresis_px * (sx + sy) / 2,
                bgr=modelo.pix_fmt == "bgr24",
            )
        med = Medidor()
        cfg_hash = hash_parametros(cfg, modelo.nombre)
        t_por_cuadro: list[float] = []
        t_det = t_trk = 0.0
        n = 0
        interrumpido = False
        for cuadro in dec:
            if self.detener:
                interrumpido = True
                break
            t_por_cuadro.append(cuadro.t_clip)
            t0 = time.perf_counter()
            cajas, puntajes = detector(cuadro.imagen, cfg.conf_baja)
            if len(cajas):
                xs, ys = mapeo.a_completo(cajas[:, [0, 2]], cajas[:, [1, 3]])
                cajas = cajas.copy()
                cajas[:, [0, 2]], cajas[:, [1, 3]] = xs, ys
            t1 = time.perf_counter()
            confirmadas = tracker.actualizar(cajas, puntajes)
            nuevos = contador.actualizar(confirmadas, cuadro.indice)
            t2 = time.perf_counter()
            t_det += t1 - t0
            t_trk += t2 - t1
            for c in nuevos:
                t_clip = t_por_cuadro[c.cuadro]
                self._escribir(
                    {
                        "kind": "cruce",
                        "clip": clip.nombre,
                        "modelo": modelo.nombre,
                        "cfg": cfg_hash,
                        "t_clip": t_clip,
                        "t_abs": clip.hora_abs(t_clip).isoformat(timespec="milliseconds"),
                        "sentido": c.sentido,
                        "track": c.track,
                        "punto": list(c.punto),
                        "conf_media": c.conf_media,
                    }
                )
            if dbg is not None:
                dbg.cuadro(cuadro.imagen, confirmadas, [contador.punto(p.caja) for p in confirmadas], bool(nuevos))
            med.muestrear()
            n += 1
        if dbg is not None and not dbg.cerrar():
            log.warning("No se pudo cerrar el video de depuración %s.", dbg.destino)
        if interrumpido:
            log.info("Análisis de %s interrumpido; se retoma en la próxima corrida.", clip.nombre)
            return None
        m = med.resultado()
        dur = m["duracion_s"] or 1e-6
        salidas = sum(1 for c in contador.cruces if c.sentido == "salida")
        entradas = len(contador.cruces) - salidas
        registro = {
            "kind": "clip",
            "clip": clip.nombre,
            "modelo": modelo.nombre,
            "cfg": cfg_hash,
            "conjunto": clip.conjunto,
            "grupo": clip.grupo,
            "inicio": clip.inicio.isoformat(),
            "fin": clip.fin.isoformat() if clip.fin else None,
            "dur_video_s": round(info.duracion, 2),
            "resolucion": f"{info.ancho}x{info.alto}",
            "roi": str(roi),
            "linea": str(lin),
            "entrada_modelo": modelo.size,
            "cada_n": cfg.analisis_cada_n,
            "hilos": cfg.analisis_hilos,
            "cuadros": n,
            "salidas": salidas,
            "entradas": entradas,
            "fps_proc": round(n / dur, 2),
            "cuadros_por_s_video": round(n / info.duracion, 2) if info.duracion else None,
            "x_tiempo_real": round(info.duracion / dur, 2),
            "ms_decod": round(1000 * dec.espera_s / max(n, 1), 1),
            "ms_detector": round(1000 * t_det / max(n, 1), 1),
            "ms_tracker": round(1000 * t_trk / max(n, 1), 2),
            **m,
        }
        self._escribir(registro)
        self.totales["clips"] += 1
        self.totales["cruces"] += len(contador.cruces)
        self.totales["salidas"] += salidas
        self.totales["entradas"] += entradas
        log.info(
            "%s · %s: %d cuadros, %d salidas, %d entradas, %.1f fps, %.2fx tiempo real, CPU %s%%, %s °C.",
            clip.nombre,
            modelo.nombre,
            n,
            salidas,
            entradas,
            registro["fps_proc"],
            registro["x_tiempo_real"],
            m["cpu_proceso_pct"],
            m["temp_max_c"],
        )
        return len(contador.cruces)


def _detector_real(modelo: Modelo, hilos: int) -> Callable:
    from pv_vision.analisis.detectores import Detector

    return Detector(modelo, hilos)
