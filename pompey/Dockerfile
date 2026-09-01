# syntax=docker/dockerfile:1
# Pompey — thin local image: supervisor, nginx, WireGuard.
# Engines and Seerr are fetched at runtime after the tunnel is up.

FROM ghcr.io/home-assistant/base:latest

ARG BUILD_ARCH
ARG BUILD_DATE
ARG BUILD_DESCRIPTION="Search for a movie or show; Proton WireGuard for all internet"
ARG BUILD_NAME="Pompey"
ARG BUILD_REF
ARG BUILD_REPOSITORY="esper256/ha-pompey"
ARG BUILD_VERSION="0.2.38"

RUN apk add --no-cache \
    bind-tools \
    ca-certificates \
    curl \
    git \
    icu-data-full \
    icu-libs \
    iptables \
    iptables-legacy \
    nftables \
    iproute2 \
    jq \
    krb5-libs \
    libintl \
    libnatpmp \
    libstdc++ \
    nginx \
    python3 \
    sqlite \
    tar \
    tzdata \
    unzip \
    wireguard-go \
    wireguard-tools \
    xz \
    zlib

# crane unpacks Seerr's published image at runtime. Not an engine.
RUN case "${BUILD_ARCH}" in \
      aarch64) crane_arch="Linux_arm64" ;; \
      *) crane_arch="Linux_x86_64" ;; \
    esac \
 && curl -fsSL "https://github.com/google/go-containerregistry/releases/download/v0.22.0/go-containerregistry_${crane_arch}.tar.gz" \
    | tar -xz -C /usr/local/bin crane \
 && chmod +x /usr/local/bin/crane

COPY rootfs/ /

ENV BUILD_VERSION="${BUILD_VERSION}"

RUN rm -f /etc/nginx/http.d/default.conf \
    && chmod +x \
      /etc/cont-init.d/*.sh \
      /etc/services.d/*/run \
      /etc/services.d/*/finish \
      /usr/local/bin/* \
      /usr/local/bin/pompey-wg/*

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
