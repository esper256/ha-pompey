ARG POMPEY_IMAGE=localhost/pompey:foundation
FROM ghcr.io/home-assistant/base@sha256:93ef607824e3f27e868f11b10938283a98bf880ed57bcf8eaa81c6c2d521f6f5 AS tools
RUN apk add --no-cache openssl py3-yaml shellcheck iputils strace
FROM ${POMPEY_IMAGE}
# Tools use their own loader/libraries. No shipped runtime library is upgraded.
COPY --from=tools / /test-tools
COPY tests /tests
ENTRYPOINT ["python3", "-u", "/tests/journey/bootstrap.py"]
