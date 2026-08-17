# syntax=docker/dockerfile:1
# Arr Stack — native Proton WireGuard + nginx skeleton. *arr binaries are
# added later by copying /app/<name> from pinned linuxserver images.

FROM ghcr.io/home-assistant/base:latest

ARG BUILD_ARCH
ARG BUILD_DATE
ARG BUILD_DESCRIPTION="Arr stack behind Proton VPN"
ARG BUILD_NAME="Arr Stack"
ARG BUILD_REF
ARG BUILD_REPOSITORY="esper256/ha-arr-addons"
ARG BUILD_VERSION="0.1.1"

RUN apk add --no-cache \
    bind-tools \
    curl \
    iptables \
    iptables-legacy \
    iproute2 \
    jq \
    libnatpmp \
    nginx \
    wireguard-go \
    wireguard-tools

COPY rootfs/ /

ENV BUILD_VERSION="${BUILD_VERSION}"

RUN rm -f /etc/nginx/http.d/default.conf \
    && chmod +x \
      /etc/cont-init.d/*.sh \
      /etc/services.d/*/run \
      /etc/services.d/*/finish \
      /usr/local/bin/wait-for-vpn \
      /usr/local/bin/render-launcher \
      /usr/local/bin/vpn-killswitch

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
