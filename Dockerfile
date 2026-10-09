ARG BUILD_FROM
FROM $BUILD_FROM

LABEL maintainer="nupsterd"
LABEL description="Grabador por ventanas de una cámara RTSP para Home Assistant (Portería Virtual)"

# Zona por defecto del container. El nombre de cada segmento lo pone ffmpeg con la
# opción `tz`, que el add-on pasa explícita en el entorno del proceso hijo (s6-overlay
# no pasa el ENV del container al CMD).
ENV TZ=America/Bogota

# ffmpeg 6.1 de Alpine 3.21: demuxer RTSP y muxer segment/mp4; se copia sin recodificar.
RUN apk add --no-cache \
    python3 \
    ffmpeg \
    tzdata && \
    cp /usr/share/zoneinfo/$TZ /etc/localtime && \
    echo $TZ > /etc/timezone

WORKDIR /app
COPY pv_vision/ /app/pv_vision/

CMD ["python3", "-m", "pv_vision.main"]
