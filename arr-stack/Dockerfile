# syntax=docker/dockerfile:1
# Arr Stack — Gluetun + nginx skeleton. *arr binaries are added in a later
# stage by copying /app/<name> from pinned linuxserver images (see docs/DESIGN.md).

ARG GLUETUN_VERSION=v3

FROM qmcgaw/gluetun:${GLUETUN_VERSION} AS gluetun

FROM ghcr.io/home-assistant/base:latest

ARG BUILD_ARCH
ARG BUILD_DATE
ARG BUILD_DESCRIPTION="Arr stack behind Proton VPN"
ARG BUILD_NAME="Arr Stack"
ARG BUILD_REF
ARG BUILD_REPOSITORY="esper256/ha-arr-addons"
ARG BUILD_VERSION="0.1.0"

RUN apk add --no-cache \
    bind-tools \
    curl \
    iptables \
    iptables-legacy \
    iproute2 \
    jq \
    nginx \
    openvpn \
    wireguard-tools

COPY --from=gluetun /gluetun-entrypoint /usr/local/bin/gluetun
COPY rootfs/ /

ENV BUILD_VERSION="${BUILD_VERSION}"

RUN rm -f /etc/nginx/http.d/default.conf \
    && chmod +x \
      /etc/cont-init.d/*.sh \
      /etc/services.d/*/run \
      /etc/services.d/*/finish \
      /usr/local/bin/wait-for-vpn \
      /usr/local/bin/render-launcher

LABEL \
    io.hass.name="${BUILD_NAME}" \
    io.hass.description="${BUILD_DESCRIPTION}" \
    io.hass.arch="${BUILD_ARCH}" \
    io.hass.type="addon" \
    io.hass.version="${BUILD_VERSION}" \
    org.opencontainers.image.title="${BUILD_NAME}" \
    org.opencontainers.image.description="${BUILD_DESCRIPTION}" \
    org.opencontainers.image.source="https://github.com/${BUILD_REPOSITORY}" \
    org.opencontainers.image.authors="Eric Friesen" \
    org.opencontainers.image.licenses="MIT" \
    org.opencontainers.image.created="${BUILD_DATE}" \
    org.opencontainers.image.revision="${BUILD_REF}" \
    org.opencontainers.image.version="${BUILD_VERSION}"
