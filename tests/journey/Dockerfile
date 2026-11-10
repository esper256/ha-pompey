ARG POMPEY_IMAGE=localhost/pompey:foundation
FROM ghcr.io/home-assistant/base@sha256:58422480a299b16b6ea376c2167463206d430619ed02b40cd687c8bd6875519e AS tools
RUN apk add --no-cache openssl py3-yaml shellcheck iputils
FROM ${POMPEY_IMAGE}
# Tools use their own loader/libraries. No shipped runtime library is upgraded.
COPY --from=tools / /test-tools
COPY tests /tests
COPY tools /tools
ENTRYPOINT ["python3", "-u", "/tests/journey/bootstrap.py"]
