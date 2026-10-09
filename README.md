# Visión (Portería Virtual) — Home Assistant Add-on

Add-on de Home Assistant OS para la Pi de un sitio (Bloque 7). Dos partes:

1. **Grabador por ventanas:** dentro de ventanas horarias de los días configurados, copia el
   stream RTSP de una cámara Dahua (probado con `DH-IPC-HFW5242HN-ZHE-MF`) **sin
   recodificar**, en segmentos MP4 fragmentados en `/media/pv_vision/`, visibles en
   *Medios → Medios locales* de HA.
2. **Modo de análisis de archivos** (apagado por defecto): sobre clips ya grabados, detecta
   personas (YOLOX o RF-DETR en ONNX Runtime, CPU), las sigue (ByteTrack mínimo) y cuenta
   los cruces de una línea propia. Escribe cruces y métricas en JSON Lines; `tools/evaluar.py`
   los compara con la verdad etiquetada.

**Modo sombra:** no habla con `pv-backend`, no genera alertas y **el video no sale
de la Pi** (ver [DOCS.md](DOCS.md), Ley 1581). Del análisis solo salen los `cruces.jsonl`
(horas, sentidos, ids de pista; sin imágenes).

```
 ┌──────────────┐  RTSP/TCP H.265   ┌────────────────────────────────┐
 │ Cámara Dahua │ ────────────────▶ │ este add-on (Pi)               │
 │ :554         │  -c copy          │ ffmpeg → /media/pv_vision/*.mp4│
 └──────────────┘                   │ retención ≤ 30 d · auditoría   │
                                    └────────────────────────────────┘
```

## Cómo funciona

- Cada segundo decide **"¿grabo ahora?"**: día ISO en `dias`, hora local dentro de una
  ventana (`inicio` incluido, `fin` excluido), **reloj de la Pi sincronizado**
  (`GET /host/info` → `dt_synchronized`, por eso `hassio_api: true`) y espacio libre
  ≥ `min_free_gb`.
- Dentro de una ventana lanza `ffmpeg -c copy` con el muxer `segment`: segmentos de
  `segment_seconds` alineados al reloj, cortados en cuadro clave, nombre
  `YYYY-MM-DD_HH-MM-SS_<stream>.mp4` en hora local (`tz`, pasada explícita al proceso).
  MP4 fragmentado (`frag_keyframe+empty_moov+default_base_moof`, etiqueta `hvc1`):
  un corte abrupto deja el archivo reproducible hasta el último fragmento.
- Al terminar la ventana: `SIGINT` (ffmpeg cierra el archivo y sale con 255, que es lo
  normal) y `SIGKILL` si no salió en 10 s.
- **Toda salida de ffmpeg dentro de una ventana es una caída** (sale con 0 si la cámara
  corta o queda muda): reintento a los 5/10/20/40/60 s, que vuelve a 5 s después de una
  sesión sana de al menos 60 s. **Vigía:** si el segmento en curso no crece en 30 s, se
  mata ffmpeg y cuenta como caída.
- Al arrancar (o reiniciarse la Pi) a mitad de una ventana, graba en seguida, sin mínimo.
- **Retención:** al arrancar y cada hora borra los segmentos con más de `retencion_dias`
  según la fecha **del nombre**. Solo el primer nivel de `/media/pv_vision/`, solo archivos
  regulares con el patrón exacto, sin seguir symlinks: **`conjunto_b/` y cualquier otra
  subcarpeta o archivo ajeno no se tocan.**

## Credenciales (regla §0: ningún secreto en argumentos ni logs)

- La URL RTSP con usuario y clave (URL-codificados) va **solo** en un archivo `ffconcat`
  de `/tmp` del contenedor, modo 600, creado con umask 077 y borrado al terminar cada
  sesión de ffmpeg. ffmpeg lo abre con el demuxer `concat`; en su línea de comandos y
  su entorno no hay URL ni clave (verificado en los tests de integración vía `/proc`).
- ffmpeg imprime la URL completa en sus errores aunque se use `-loglevel error`. Por eso
  **cada línea de su stderr pasa por una máscara**: `rtsp://usuario:clave@` → `rtsp://***@`
  y la clave literal y URL-codificada → `***`.
- Se recomienda un **usuario de la cámara solo de visualización** exclusivo para este
  add-on; el código no asume ningún usuario.

## Auditoría

`/config/pv_vision_audit_YYYYMMDD.jsonl` en el contenedor =
`/app_configs/<slug>/…` desde `core_ssh`. Un JSON por línea, sin cuadros de video ni
secretos; retención 30 días.

| `kind` | Campos |
|---|---|
| `segment_created` | `file`, `bytes`, `stream` (al cerrarse el segmento) |
| `segment_deleted` | `file`, `bytes`, `stream` (retención) |
| `reconnect` | `motivo` (enmascarado), `retry_seconds`, `stream` |
| `segment_discarded` | `file`, `bytes`, `motivo`, `stream` (segmento propio sin video o < 1 s, borrado al cerrar) |
| `disk_low` | `free_bytes`, `min_free_gb` (al pasar bajo el umbral) |
| `derived_deleted` | `file`, `bytes` (retención de `depuracion/` y `referencia/`, 7 días) |

## Modo de análisis de archivos

Proceso aparte del grabador (`python3 -m pv_vision.analisis`, `nice 10`, ORT con
`analisis_hilos` hilos y sin espera activa). Si muere, el grabador lo relanza a los 60 s /
5 min / 15 min; **nunca frena al grabador**. Con `analisis_solo_fuera_de_ventanas` (default)
no empieza clips dentro de una ventana: termina el que está y espera.

```
clip.mp4 ─ ffmpeg -threads 2: crop ROI · select 1/N · showinfo (pts) · scale+pad ─▶ rawvideo
        ─▶ ONNX Runtime (YOLOX: grilla+NMS · RF-DETR: sigmoide) solo "persona"
        ─▶ ByteTrack mínimo (2 etapas, IoU) ─▶ línea con histéresis ─▶ cruces.jsonl
```

- **Modelos** (`/opt/modelos`, sha256 verificado en el build): `yolox_nano` (416),
  `yolox_tiny` (416), `yolox_s` (640), `rfdetr_nano` (384), `rfdetr_nano_int8` (384),
  `rfdetr_small` (512). YOLOX: letterbox gris 114, BGR 0-255. RF-DETR: estirado a S×S, RGB
  normalizado ImageNet (como su propio `predict`). Licencias en [NOTICE](NOTICE).
- **Clips:** primer nivel de `analisis_dir`, nombres `nNN_AAAA-MM-DD_hh.mm.ss-hh.mm.ss.mp4`
  (conjunto B) o `AAAA-MM-DD_HH-MM-SS_main|sub.mp4` (conjunto C). Hora absoluta = hora del
  nombre + `pts`. Se saltean inválidos o de menos de 1 s y archivos modificados hace < 60 s.
  Otra resolución (p. ej. secundario 704×576): ROI y línea se escalan.
- **Conteo:** punto de referencia (`pie` o `centro` de la caja) contra la línea dirigida;
  banda `±histeresis_px` que conserva el lado estable; un cruce cuenta de lado estable a
  lado estable tras `cuadros_confirmacion` cuadros. Quien se detiene sobre la línea no
  cuenta; quien vuelve atrás suma una salida y una entrada.
- **Salidas** en `/config/analisis/<run_id>/` (`/app_configs/<slug>/analisis/…`):
  `cruces.jsonl` (`kind=cruce` por cruce; `kind=clip` por clip con `fps_proc`,
  `cuadros_por_s_video`, `x_tiempo_real`, `ms_decod`, `ms_detector`, `ms_tracker`,
  `cpu_proceso_pct` (% de un núcleo, análisis + ffmpeg), `cpu_total_pct`, `rss_mb`,
  `temp_max_c`, `freq_min_mhz`) y `resumen.json`. Idempotente: `/config/analisis/hechos.json`
  (`nombre|bytes|modelo|hash de parámetros`).
- **Cuadro de referencia** (`referencia_desde`) y **video de depuración**
  (`depuracion_clips`): en `/media/pv_vision/referencia/` y `/media/pv_vision/depuracion/`,
  borrados a los 7 días.

## Logs

Al arrancar: cada opción, con `camera_user`/`camera_password` como "configurado"/"vacío"
y la fuente como `rtsp://***@host:554/…`. Cada 15 min:
`Resumen 15 min: segmentos=… bytes=… reconexiones=… libre=… grabando=… próxima_ventana=… descartados=…`.
Cada hora: `Retención: N segmento(s) … borrados`.

## Límites conocidos

- La reproducción en el navegador depende de su soporte **H.265/HEVC** (HA no
  transcodifica): Safari sí; Chrome/Edge con decodificador por hardware; Firefox parcial.
- El nombre es la hora de la **Pi** al abrir el segmento (primer cuadro clave), no la del
  OSD de la cámara: difiere en el desfase NTP + la latencia RTSP (≈ 1 s).
- Con el GOP de la Dahua (50 a 25 fps = 2 s) cada segmento dura `segment_seconds` ± 2 s.
- La retención solo corre con el add-on en marcha (también al arrancar). Desinstalar
  **no** borra `/media/pv_vision/`.
- `dias` no conoce festivos.

## Desarrollo / tests

```bash
uv venv -p 3.13 .venv && uv pip install -p .venv -r requirements-dev.txt
.venv/bin/ruff check . && .venv/bin/ruff format --check .
.venv/bin/python -m pytest -q          # los de integración se saltean sin ffmpeg/mediamtx
```

**Dentro de la imagen real** (`amd64-base-debian:trixie`, como en la Pi pero amd64; con el python del venv
`/opt/venv`, el mismo del `CMD`), incluidos los de
integración contra un servidor RTSP real (mediamtx v1.9.3, binario no versionado):

```bash
docker build --build-arg BUILD_FROM=ghcr.io/home-assistant/amd64-base-debian:trixie -t pv-vision:test-base .
printf 'FROM pv-vision:test-base\nRUN /opt/venv/bin/pip install --no-cache-dir pytest==8.4.2 pyyaml==6.0.3\nCOPY tests/ /app/tests/\nCOPY tools/ /app/tools/\nCOPY config.yaml pyproject.toml /app/\n' \
  | docker build -t pv-vision:test -f - .
# vtest.avi: video público de opencv/opencv (samples/data, Apache-2.0), bajado en la corrida, no versionado.
curl -sSL -o "$VID_DIR/vtest.avi" https://raw.githubusercontent.com/opencv/opencv/4.x/samples/data/vtest.avi
docker run --rm --entrypoint sh -v "$MTX_DIR":/mtx:ro -v "$VID_DIR":/video:ro -e PV_VISION_MEDIAMTX=/mtx/mediamtx \
  -e PV_VISION_VIDEO_PUBLICO=/video/vtest.avi -w /app pv-vision:test -c '/opt/venv/bin/python3 -m pytest -q -p no:cacheprovider'
```

Mientras la release `modelos-v1` no exista, el build local puede servir los assets con
`python3 -m http.server` y `--network host --build-arg MODELOS_URL=http://127.0.0.1:<puerto>`
(el sha256 se verifica igual).

Módulos (`pv_vision/`): `config`, `schedule`, `segments`, `ffmpeg`, `recorder`, `media`,
`clock`, `audit`, `main`; análisis en `pv_vision/analisis/`: `modelos`, `geometria`,
`decodificar`, `detectores`, `tracker`, `conteo`, `clips`, `metricas`, `corrida`,
`referencia`, `depuracion`, `dibujo`, `proceso`, `__main__`. Evaluación: `tools/evaluar.py`.
