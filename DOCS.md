# Visión (Portería Virtual)

Grabador por ventanas de una cámara RTSP (Dahua) para **evaluar un detector de
acompañamiento**. Modo sombra: no envía nada fuera de la Pi.

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
