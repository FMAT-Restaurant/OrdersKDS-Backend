FROM grafana/k6:2.3.0

COPY --chown=12345:12345 tests/k6 /scripts

USER 12345
WORKDIR /scripts
ENTRYPOINT ["k6"]
