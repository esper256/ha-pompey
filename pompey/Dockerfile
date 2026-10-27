# syntax=docker/dockerfile:1
# Supervisor builds this image locally. The complete pinned bundle ships in it.

FROM ghcr.io/home-assistant/base@sha256:93ef607824e3f27e868f11b10938283a98bf880ed57bcf8eaa81c6c2d521f6f5

ARG BUILD_ARCH=amd64

RUN apk add --no-cache \
    bind-tools \
    ca-certificates \
    curl \
    icu-data-full \
    icu-libs \
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
    wireguard-go \
    wireguard-tools \
    xz \
    zlib

# Build-only tools never enter the final runtime filesystem.
RUN test "${BUILD_ARCH}" = amd64 \
 && apk add --no-cache --virtual .vendor-tools git \
 && curl -fsSL "https://github.com/google/go-containerregistry/releases/download/v0.22.0/go-containerregistry_Linux_x86_64.tar.gz" \
    | tar -xz -C /usr/local/bin crane \
 && chmod +x /usr/local/bin/crane

COPY rootfs/usr/share/pompey/engines.json /usr/share/pompey/engines.json
COPY vendor_bundle.py /tmp/vendor_bundle.py
RUN python3 /tmp/vendor_bundle.py \
 && rm /tmp/vendor_bundle.py /usr/local/bin/crane \
 && apk del .vendor-tools

COPY rootfs/ /

ARG BUILD_DATE=""
ARG BUILD_DESCRIPTION="Search for a movie or show; VPN WireGuard for all internet"
ARG BUILD_NAME="Pompey"
ARG BUILD_REF=""
ARG BUILD_REPOSITORY="esper256/ha-pompey"
ARG BUILD_VERSION="0.5.0"


ENV BUILD_VERSION="${BUILD_VERSION}" \
    BUILD_REF="${BUILD_REF}" \
    S6_SERVICES_GRACETIME=30000 \
    S6_KILL_GRACETIME=3000

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
