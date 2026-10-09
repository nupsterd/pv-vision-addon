"""Add-on de Home Assistant: grabador por ventanas de una cámara RTSP (Bloque 7, fase a).

Dentro de cada ventana horaria de los días configurados, ffmpeg copia el stream de
la cámara SIN recodificar en segmentos MP4 fragmentados bajo ``/media/pv_vision/``.
Retención por la fecha del nombre (tope 30 días), auditoría local sin video y
modo sombra: no habla con ``pv-backend`` ni saca el video de la Pi.
"""

ADDON_VERSION = "0.1.1-alpha"
