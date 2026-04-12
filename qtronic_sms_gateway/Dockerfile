ARG BUILD_FROM=ghcr.io/home-assistant/base:latest
FROM $BUILD_FROM

ARG BUILD_ARCH
ARG BUILD_VERSION

LABEL \
  io.hass.version="${BUILD_VERSION}" \
  io.hass.type="app" \
  io.hass.arch="${BUILD_ARCH}" \
  org.opencontainers.image.title="Q-Tronic SMS Gateway" \
  org.opencontainers.image.description="ESPHome-based GSM gateway add-on with REST and MQTT" \
  org.opencontainers.image.vendor="Q-Tronic"

RUN apk add --no-cache \
    python3 \
    py3-pip \
    bash

COPY app /app
COPY run.sh /run.sh

RUN pip3 install --no-cache-dir \
    aioesphomeapi==44.13.3 \
    aiomqtt>=2,<3 \
    fastapi>=0.115,<1 \
    uvicorn>=0.30,<1

RUN chmod a+x /run.sh

CMD ["/run.sh"]
