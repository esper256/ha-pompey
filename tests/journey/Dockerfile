ARG POMPEY_IMAGE=localhost/pompey:foundation
FROM ghcr.io/home-assistant/base@sha256:880016115f356afc814398e17ba68a7ee66dfc1b4eaef09755ea2c9fa18ea481 AS tools
RUN apk add --no-cache openssl py3-yaml shellcheck iputils
FROM ${POMPEY_IMAGE}
# Tools use their own loader/libraries. No shipped runtime library is upgraded.
COPY --from=tools / /test-tools
COPY tests /tests
COPY tools /tools
ENTRYPOINT ["python3", "-u", "/tests/journey/bootstrap.py"]
