# Changelog

Formato basado en [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
versionado siguiendo [SemVer](https://semver.org/lang/es/).

## [Unreleased]

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
