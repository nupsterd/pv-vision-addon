ARG BUILD_FROM
FROM $BUILD_FROM

LABEL maintainer="nupsterd"
LABEL description="Grabador por ventanas y detector en sombra de una cámara RTSP para Home Assistant (Portería Virtual)"

# Zona por defecto del container. El nombre de cada segmento lo pone ffmpeg con la
# opción `tz`, que el add-on pasa explícita en el entorno del proceso hijo (s6-overlay
# no pasa el ENV del container al CMD).
ENV TZ=America/Bogota

# Base Debian trixie de HA (glibc): permite las wheels oficiales de onnxruntime.
# ffmpeg 7.1 de Debian: demuxer RTSP, muxer segment/mp4, libx264 (video de depuración).
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

# Dependencias del detector (versiones fijadas en requirements.txt).
COPY requirements.txt /tmp/requirements.txt
RUN /opt/venv/bin/pip install --no-cache-dir -r /tmp/requirements.txt && rm /tmp/requirements.txt

# Modelos ONNX (solo persona). El build FALLA si un sha256 no coincide.
# - YOLOX: release oficial 0.1.1rc0 de Megvii (Apache-2.0).
# - RF-DETR: exportados de los pesos COCO de Roboflow (Apache-2.0) y publicados como assets
#   de la release modelos-v1 de este repositorio. Ver NOTICE.
ARG YOLOX_URL=https://github.com/Megvii-BaseDetection/YOLOX/releases/download/0.1.1rc0
ARG MODELOS_URL=https://github.com/nupsterd/pv-vision-addon/releases/download/modelos-v1
RUN mkdir -p /opt/modelos && cd /opt/modelos && \
    for m in yolox_nano yolox_tiny yolox_s; do \
        curl -fsSL --retry 3 -o "$m.onnx" "$YOLOX_URL/$m.onnx"; \
    done && \
    for m in rfdetr_nano rfdetr_nano_int8 rfdetr_small; do \
        curl -fsSL --retry 3 -o "$m.onnx" "$MODELOS_URL/$m.onnx"; \
    done && \
    printf '%s\n' \
        "c789161ed43c8269fcd4e67c67eeeb4e80c622da2eb296a20bc6007bd18a0b7d  yolox_nano.onnx" \
        "427cc366d34e27ff7a03e2899b5e3671425c262ea2291f88bb942bc1cc70b0f7  yolox_tiny.onnx" \
        "c5c2d13e59ae883e6af3b45daea64af4833a4951c92d116ec270d9ddbe998063  yolox_s.onnx" \
        "f47f480059cbbc89de8ee715569935c63bb121632050befa93bbe8a67578c117  rfdetr_nano.onnx" \
        "ac6a26a7f3c4476e17e751aadf7633c54d9b3144d698c084b102cab65abd5a2a  rfdetr_nano_int8.onnx" \
        "d71a2a374e79a03f5071573f0a59c04fb2ea9f7f2d682ec5b05995d0ad9ca0ba  rfdetr_small.onnx" \
        > SHA256SUMS && \
    sha256sum -c SHA256SUMS

WORKDIR /app
COPY NOTICE /app/NOTICE
COPY pv_vision/ /app/pv_vision/

CMD ["/opt/venv/bin/python3", "-m", "pv_vision.main"]
