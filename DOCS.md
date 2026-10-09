# Visión (Portería Virtual)

Grabador por ventanas de una cámara RTSP (Dahua) y **detector de personas en modo
sombra** sobre archivos, para **evaluar un detector de acompañamiento**. No envía nada fuera
de la Pi salvo lo que se baje a mano: los `cruces.jsonl` del análisis (sin imágenes).

## ⚠️ Aviso — Ley 1581 de 2012 (Habeas Data)

- **Finalidad única:** desarrollo y evaluación del detector del Bloque 7, con autorización
  escrita vigente del responsable del sitio y avisos de videovigilancia instalados.
- **Retención máxima: 30 días.** El add-on rechaza `retencion_dias` mayor a 30 y borra los
  segmentos vencidos cada hora.
- **El video no sale de la Pi.** Se permite *mirarlo* desde Medios de HA (también por el
  acceso remoto del sitio), pero **no descargar ni guardar copias** en otros equipos.
- **Requisito: excluir la carpeta *Media* de los backups automáticos de HA**
  (*Ajustes → Sistema → Copias de seguridad → configuración de copias automáticas →
  datos incluidos*). Si no, el video entra en los backups (y en su destino remoto, si lo
  hay), con otra retención y fuera de la Pi.
- **Al cerrar el Bloque 8 se borra todo:** `/media/pv_vision/` completo (incluida
  `conjunto_b/`) y la auditoría, y se desinstala el add-on (ver "Borrar grabaciones").

## Instalación

1. *Ajustes → Complementos (Apps) → Tienda → ⋮ → Repositorios* →
   `https://github.com/nupsterd/pv-vision-addon` (o `…#<rama>` para probar una rama antes
   del merge) → instalar **"Visión (Portería Virtual)"**. Se construye en la Pi (incluye
   ffmpeg): unos minutos.
2. **Configuración:** `camera_host`, `camera_user` y `camera_password` (la clave **solo** en
   ese campo de la UI). Recomendado: un usuario de la cámara **solo de visualización**
   exclusivo para este add-on. Guardar y verificar desde `core_ssh` sin mostrar la clave:
   `ha apps info <slug> --raw-json | jq '.data.options | .camera_password="***"'`.
3. **Información:** *Iniciar al arrancar* + *Watchdog* → Iniciar.
4. Log esperado: la lista de opciones (secretos como "configurado"), `Fuente: rtsp://***@…`,
   `Retención: …`, `Reloj de la Pi sincronizado …` y, dentro de una ventana,
   `Grabando rtsp://***@…`.

## Opciones

| Opción | Default | Descripción |
|---|---|---|
| `camera_host` | — | IP o host de la cámara (sin esquema, puerto ni ruta). Puerto RTSP 554. |
| `camera_user` | — | Usuario de la cámara (se loguea solo como "configurado"). |
| `camera_password` | — | Clave (campo `password`; nunca se loguea). |
| `stream` | `main` | `main` = principal (`subtype=0`, 1080p) · `sub` = secundario 1 (`subtype=1`, 704×576). |
| `ventanas` | `06:20-06:50`, `11:50-12:20`, `18:00-18:50` | `HH:MM-HH:MM` en hora local; inicio incluido, fin excluido. No pueden cruzar la medianoche ni solaparse. |
| `dias` | `1, 2, 3, 4, 5` | Días ISO (1 = lunes … 7 = domingo). |
| `segment_seconds` | `300` | Duración de cada segmento (30-3600). |
| `retencion_dias` | `30` | 1-30. **Tope duro de 30** (Ley 1581): un valor mayor se rechaza. |
| `min_free_gb` | `10` | Debajo de este espacio libre no se graba (WARNING). |
| `tz` | `America/Bogota` | Zona para ventanas y nombres de archivo. |
| `log_level` | `info` | `debug` \| `info` \| `warning`. |

### Opciones del análisis (todo apagado por defecto)

| Opción | Default | Descripción |
|---|---|---|
| `analisis_activo` | `false` | Enciende el modo de análisis de archivos (proceso aparte; nunca frena al grabador). |
| `analisis_dir` | `/media/pv_vision/conjunto_b` | Carpeta a analizar (solo primer nivel). Debe estar dentro de `/media/pv_vision/`. |
| `analisis_modelos` | `yolox_nano` | Lista: `yolox_nano`, `yolox_tiny`, `yolox_s`, `rfdetr_nano`, `rfdetr_nano_int8`, `rfdetr_small`. |
| `analisis_cada_n` | `3` | Analiza 1 de cada N cuadros (25 fps / 3 = 8,3 cuadros por segundo de video). |
| `analisis_hilos` | `3` | Hilos de ONNX Runtime (1-3: queda ≥ 1 núcleo para HA y los demás add-ons). |
| `analisis_solo_fuera_de_ventanas` | `true` | Dentro de una ventana termina el clip en curso y espera a que cierre. |
| `roi` | `960:720:500:360` | Recorte `W:H:X:Y` en píxeles de 1920×1080. |
| `linea` | `500,467,1113,1080` | `x1,y1,x2,y2` en píxeles de 1920×1080 (la de la Dahua extendida a los bordes del ROI). Debe atravesar el ROI. |
| `sentido_salida` | `izq_a_der` | Qué cruce es salida, respecto de la línea dirigida `x1,y1 → x2,y2` (`izq_a_der` = `LeftToRight` de la Dahua). Verificar con la flecha del cuadro de referencia. |
| `punto_referencia` | `pie` | Punto de la caja que cruza: `pie` (centro del borde inferior) o `centro`. |
| `histeresis_px` | `12` | Banda alrededor de la línea donde se conserva el último lado. |
| `cuadros_confirmacion` | `2` | Cuadros seguidos del lado nuevo para contar el cruce. |
| `conf_alta` / `conf_baja` | `0.5` / `0.1` | Umbrales del seguimiento (las bajas solo continúan pistas). |
| `track_buffer_s` | `1.5` | Tiempo que una pista sobrevive sin detección. |
| `min_hits` | `3` | Detecciones para confirmar una pista. |
| `referencia_desde` | vacío | Archivo dentro de `/media/pv_vision/` (p. ej. `conjunto_b/n25_….mp4`) para el cuadro de referencia. |
| `depuracion_clips` | vacía | Nombres de clip para generar el video de depuración (y reanalizarlos). |

Un error en estas opciones **desactiva el análisis** con un ERROR en el log; el grabador arranca igual.

Espacio aproximado por día hábil con las ventanas por defecto (110 min): `main` (2 Mbps)
≈ 1,65 GB; `sub` (512 kbps) ≈ 0,42 GB. En 30 días: ≈ 36 GB / ≈ 9 GB.

## Ver las grabaciones

*Medios → Medios locales → pv_vision*. Los archivos se llaman
`YYYY-MM-DD_HH-MM-SS_<stream>.mp4` (hora local de inicio). El video es H.265: si el
navegador no lo reproduce, probar Safari o Edge/Chrome en un equipo con decodificador
HEVC por hardware. **Solo mirar, no descargar copias.**

## Borrar grabaciones

Desde la terminal de la Pi (`core_ssh`):

```sh
ls -la /media/pv_vision/                       # segmentos + conjunto_b/
rm /media/pv_vision/2026-10-0[1-5]_*_main.mp4   # un rango puntual
rm -r /media/pv_vision/                         # TODO (cierre del Bloque 8)
S=$(ha apps list | grep -o '[0-9a-f]*_pv_vision' | head -1); rm /app_configs/$S/pv_vision_audit_*.jsonl
```

La retención automática **no** toca `conjunto_b/` ni archivos con otro nombre: se borran
a mano. Desinstalar el add-on **no** borra `/media/pv_vision/`.

## Análisis de archivos (detector en sombra)

1. **Cuadro de referencia** (para fijar ROI y línea): `referencia_desde: "conjunto_b/<clip con el
   pasillo vacío>.mp4"`, Save y reiniciar. En *Medios → pv_vision → referencia* aparece
   `ref_<clip>_<hash>.png` con grilla cada 100 px, ROI y línea propia (verde), línea de la
   Dahua (roja) y una flecha **SALIDA**. Si la flecha apunta al revés, cambiar `sentido_salida`.
   Ajustar `roi`/`linea` y repetir (cada cambio genera un PNG nuevo).
2. **Analizar:** `analisis_activo: true`, `analisis_modelos` y el resto; Save y reiniciar. Log:
   `Análisis de archivos activo (run …)` y una línea por clip con cuadros, salidas, entradas,
   fps, × tiempo real, CPU y °C. Fuera de ventanas por defecto.
3. **Video de depuración** de un clip: `depuracion_clips: ["<clip>.mp4"]` → en
   *Medios → pv_vision → depuracion/<run>/* (ROI, líneas, banda, cajas con id, destello en cada
   cruce). **Solo mirar.** Se borra a los 7 días.
4. **Resultados** en `/app_configs/<slug>/analisis/<run_id>/cruces.jsonl` y `resumen.json`.
   Lo hecho queda en `/app_configs/<slug>/analisis/hechos.json`: borrarlo fuerza a reanalizar todo.

**Traer los resultados al ThinkPad** (solo estos archivos JSON; **nunca** video ni imágenes):

```sh
# ThinkPad. Alias SSH de la Pi de la oficina (§2.4); <slug> = el de pv_vision, <run> = la corrida.
mkdir -p ~/porteria-virtual/reportes/b7/analisis/<run>
scp -o HostName=192.168.27.153 pi-laboficina:/app_configs/<slug>/analisis/<run>/cruces.jsonl \
    pi-laboficina:/app_configs/<slug>/analisis/<run>/resumen.json ~/porteria-virtual/reportes/b7/analisis/<run>/
python3 tools/evaluar.py ~/porteria-virtual/reportes/b7/analisis/*/cruces.jsonl \
    --etiquetas ~/porteria-virtual/reportes/b7/b7_1_verdad_B_etiquetas.csv --salida ~/porteria-virtual/reportes/b7/eval
```

`evaluar.py` (solo stdlib) junta los clips de cada grupo `nNN` y calcula:

- **Solape:** el tramo de cada clip es `[inicio, inicio + dur_video_s]` (el nombre trunca al
  segundo y el video dura más; sin `dur_video_s`, el fin del nombre). En los 2-4 s en que dos
  clips seguidos se solapan cuenta los cruces, salidas y entradas, de un solo clip: el que más
  tenga en ese tramo contando los dos sentidos (empate: el primero).
- **Ventana** (`--ventana`): `clips` (por defecto) evalúa todo el tramo de los clips del grupo
  e ignora `desde`/`hasta`; `planilla` solo cuenta los cruces dentro de `[desde, hasta]`, para
  comparar. El modo usado queda en el encabezado de `evaluacion.md`.
- **Unidad:** `det = max(0, salidas − entradas)` por grupo (quien sale, vuelve a marcar y sale
  otra vez es una persona).
- **No medibles:** columna opcional `no_medibles` en el CSV de etiquetas (entero; vacía o
  ausente = 0) para quien cruza en el borde del clip. La verdad es
  `real_medible = max(0, personas_reales − no_medibles)`, para el detector y para la Dahua; el
  total de no medibles por configuración siempre aparece en `evaluacion.md`.
- **Nivel 1:** `det` contra `real_medible`: TP, FN, FP y sensibilidad, con la Dahua
  (`cruces_dahua`) como referencia sobre los mismos grupos. **Nivel 2:** alertaría
  (`det > marcaron_checkout`) contra debía alertar (`real_medible > marcaron_checkout`) por
  grupo de `checkOut`. Desgloses por `juntas_separadas`, `luz` y casos difíciles.

Escribe `evaluacion.md` y `evaluacion.csv` (por grupo: `real`, `det`, TP/FN/FP, `salidas`,
`entradas`, `no_medibles`, `real_medible`, alerta y debía alertar).

## Auditoría

`/app_configs/<slug>/pv_vision_audit_YYYYMMDD.jsonl`: segmentos creados y borrados,
reconexiones y avisos de espacio. Sin video ni secretos. Retención 30 días.

```sh
F=$(ls -t /app_configs/$S/pv_vision_audit_*.jsonl | head -1)
echo "creados: $(grep -c segment_created $F) | borrados: $(grep -c segment_deleted $F) | reconexiones: $(grep -c reconnect $F)"
ha apps logs $S | grep -E 'Resumen|WARNING|ERROR' | tail -8
```

## Troubleshooting

| Síntoma en el log | Causa probable | Qué hacer |
|---|---|---|
| `NO se graba: hora de la Pi sin confirmar` | NTP de la Pi aún sin sincronizar (tras un apagón) o falta `hassio_api` | Esperar; `ha host info`. Si persiste, revisar red/DNS de la Pi. |
| `Sin SUPERVISOR_TOKEN` | El add-on no tiene acceso a la API del Supervisor | Reinstalar desde el repositorio (el `config.yaml` trae `hassio_api: true`). |
| `ffmpeg: … 401 Unauthorized` + `Caída de la grabación` | Usuario o clave incorrectos | Corregir y reiniciar. No insistir: la cámara puede bloquear la cuenta. |
| `Caída … Connection refused` / `Operation timed out` en bucle | Cámara apagada, reiniciando o sin red | Esperar: reintenta hasta cada 60 s. |
| `sin datos nuevos durante 30 s (vigía…)` | El stream abrió pero no llegan cuadros | Revisar la cámara y la red. |
| `NO se graba: espacio libre … por debajo de min_free_gb` | Disco casi lleno | Revisar backups (¿incluyen Media?) y `/media`. |
| `Segmento descartado: … (sin video válido)` al cerrar una ventana | La parada cayó justo en un corte de segmento | Normal: el archivo vacío o de < 1 s se borra solo. |
| `Análisis desactivado, configuración inválida: …` | Opción del análisis fuera de rango | Corregirla; el grabador sigue funcionando. |
| `El proceso de análisis terminó (código …); se relanza en …` | Error del análisis (p. ej. memoria) | Ver las líneas anteriores del log; se relanza solo (60 s → 5 min → 15 min). |
