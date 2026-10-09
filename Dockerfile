ARG BUILD_FROM
FROM $BUILD_FROM

LABEL maintainer="nupsterd"
LABEL description="Grabador por ventanas de una cámara RTSP para Home Assistant (Portería Virtual)"

# Zona por defecto del container. El nombre de cada segmento lo pone ffmpeg con la
# opción `tz`, que el add-on pasa explícita en el entorno del proceso hijo (s6-overlay
# no pasa el ENV del container al CMD).
ENV TZ=America/Bogota

# Base Debian trixie de HA (glibc): permite las wheels oficiales de onnxruntime para el
# detector. ffmpeg 7.1 de Debian: demuxer RTSP y muxer segment/mp4; se copia sin recodificar.
# El venv de /opt/venv queda para las dependencias de Python del detector.
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        python3 \
        python3-venv \
        ffmpeg \
        tzdata && \
    rm -rf /var/lib/apt/lists/* && \
    cp /usr/share/zoneinfo/$TZ /etc/localtime && \
    echo $TZ > /etc/timezone && \
    python3 -m venv /opt/venv

WORKDIR /app
COPY pv_vision/ /app/pv_vision/

CMD ["/opt/venv/bin/python3", "-m", "pv_vision.main"]
