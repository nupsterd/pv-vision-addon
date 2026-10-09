# Visión (Portería Virtual) — Home Assistant Add-on

Add-on de Home Assistant OS para la Pi de un sitio. **Fase (a) del Bloque 7: solo
graba.** Dentro de ventanas horarias de los días configurados, copia el stream RTSP
de una cámara Dahua (probado con el formato de `DH-IPC-HFW5242HN-ZHE-MF`) **sin
recodificar**, en segmentos MP4 fragmentados en `/media/pv_vision/`, visibles en
*Medios → Medios locales* de HA.

**Modo sombra:** no habla con `pv-backend`, no genera alertas y **el video no sale
de la Pi** (ver [DOCS.md](DOCS.md), Ley 1581). El detector de personas vendrá en una
fase posterior, en este mismo add-on.

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
| `disk_low` | `free_bytes`, `min_free_gb` (al pasar bajo el umbral) |

## Logs

Al arrancar: cada opción, con `camera_user`/`camera_password` como "configurado"/"vacío"
y la fuente como `rtsp://***@host:554/…`. Cada 15 min:
`Resumen 15 min: segmentos=… bytes=… reconexiones=… libre=… grabando=… próxima_ventana=…`.
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
printf 'FROM pv-vision:test-base\nRUN apk add --no-cache py3-pytest py3-yaml\nCOPY tests/ /app/tests/\nCOPY config.yaml pyproject.toml /app/\n' \
  | docker build -t pv-vision:test -f - .
docker run --rm --entrypoint sh -v "$MTX_DIR":/mtx:ro -e PV_VISION_MEDIAMTX=/mtx/mediamtx -w /app pv-vision:test \
  -c '/opt/venv/bin/python3 -m pytest -q -p no:cacheprovider'
```

Módulos (`pv_vision/`): `config`, `schedule`, `segments`, `ffmpeg`, `recorder`, `clock`,
`audit`, `main`.
