# Changelog

Formato basado en [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
versionado siguiendo [SemVer](https://semver.org/lang/es/).

## [Unreleased]

## [0.2.0-alpha] - 2026-10-09

Modo de análisis de archivos (B7.1, PR B): detector de personas en sombra sobre clips ya
grabados. Apagado por defecto; no envía nada fuera de la Pi.

### Added
- Proceso aparte `python3 -m pv_vision.analisis` (nice 10, relanzado si muere; nunca frena
  al grabador): ffmpeg (crop ROI, 1 de N, scale+pad) → ONNX Runtime → ByteTrack mínimo propio →
  conteo de línea con histéresis y confirmación. Solo clase persona.
- Modelos en la imagen con sha256 verificado en el build: YOLOX Nano/Tiny/S (release
  0.1.1rc0 de Megvii) y RF-DETR Nano/Nano int8/Small (exportados, release `modelos-v1`).
  `NOTICE` con licencias y atribución.
- Opciones `analisis_*`, `roi`, `linea`, `sentido_salida`, `punto_referencia`,
  `histeresis_px`, `cuadros_confirmacion`, umbrales del seguimiento, `referencia_desde` y
  `depuracion_clips`, con validación estricta (un error desactiva el análisis, no el grabador).
- `cruces.jsonl` (cruces con hora absoluta y métricas por clip: fps, CPU, RSS, temperatura,
  frecuencia), `resumen.json` e idempotencia por clip/modelo/parámetros.
- Cuadro de referencia PNG (grilla, ROI, líneas, flecha de salida) y video de depuración
  H.264 bajo demanda; retención de 7 días para `depuracion/` y `referencia/`.
- `tools/evaluar.py` (stdlib): niveles 1 y 2 contra las etiquetas del conjunto B. Solape entre
  clips hasta `inicio + dur_video_s` (salidas y entradas), `--ventana clips|planilla`,
  `det = max(0, salidas − entradas)` y columna opcional `no_medibles` (B7.2).
- Dependencias del venv fijadas en `requirements.txt` (numpy 2.5.3, onnxruntime 1.30.0).

### Fixed
- Grabador: al cerrar cada sesión de ffmpeg se descartan los segmentos propios sin video
  (MP4 inválido, p. ej. de 28 bytes, o de menos de 1 s) — quedaban cuando la parada caía justo
  en un corte, típico al final de una ventana alineada a 5 min. Se auditan como
  `segment_discarded` y se cuentan en el resumen (`descartados=`).

## [0.1.1-alpha] - 2026-10-09

Cambio de imagen base (B7.1, PR A). Sin cambios de comportamiento del grabador.

### Changed
- Imagen base `aarch64-base:3.21` (Alpine) → `aarch64-base-debian:trixie` (Debian 13, glibc),
  necesaria para las wheels oficiales de ONNX Runtime del detector (fase siguiente).
  ffmpeg 6.1 → 7.1 y Python 3.12 → 3.13. Paquetes por `apt-get` sin *recommends*.
- El add-on corre con el Python de un venv en `/opt/venv` (vacío por ahora; ahí irán las
  dependencias del detector).
- CI con Python 3.13.

## [0.1.0-alpha] - 2026-10-08

Primera versión (B7.1, fase a: solo grabador). Verificada en hardware real (Pi de la oficina)
antes del tag.

### Added
- Grabación por ventanas horarias (`ventanas`, `dias`) del stream RTSP de una cámara
  Dahua (`main`/`sub`) con `ffmpeg -c copy`, en segmentos MP4 fragmentados (`hvc1`)
  `YYYY-MM-DD_HH-MM-SS_<stream>.mp4` en hora local bajo `/media/pv_vision/`.
- URL RTSP con credenciales solo en un archivo `ffconcat` 600 (nunca en argumentos) y
  máscara de credenciales sobre todo el stderr de ffmpeg.
- Robustez: toda salida de ffmpeg dentro de una ventana es caída; reintentos 5/10/20/40/60 s;
  vigía de crecimiento de 30 s; retoma sola al reiniciar el add-on o la Pi; detención
  limpia al cerrar la ventana.
- No graba hasta que el Supervisor confirma el reloj sincronizado (`dt_synchronized`) ni
  con espacio libre bajo `min_free_gb`.
- Retención por la fecha del nombre (tope duro 30 días), al arrancar y cada hora, solo
  sobre el primer nivel y el patrón exacto (no toca `conjunto_b/`).
- Auditoría JSON Lines diaria (segmentos creados/borrados, reconexiones, espacio bajo),
  retención 30 días; resumen en el log cada 15 min.
- Tests unitarios y de integración (ffmpeg + mediamtx con H.265 y autenticación), también
  dentro de la imagen real de HA; CI en GitHub Actions.
