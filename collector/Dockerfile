# Build-Kontext ist das Repository-Hauptverzeichnis (siehe docker-compose.yml)
# Basis-Image und uv sind per Digest festgelegt; Dependabot (docker) hebt sie an.
FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b AS lock
COPY --from=ghcr.io/astral-sh/uv:0.11.8@sha256:3b7b60a81d3c57ef471703e5c83fd4aaa33abcd403596fb22ab07db85ae91347 /uv /usr/local/bin/uv
WORKDIR /lock
COPY collector/pyproject.toml collector/uv.lock ./
# Genau die Versionen und Hashes aus uv.lock, also das, was die CI getestet hat.
# Die Gruppe "build" bringt das Build-Backend (hatchling) ebenfalls aus dem Lock.
RUN uv export --frozen --no-dev --group build --no-emit-project -o requirements.txt

FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b

LABEL org.opencontainers.image.source=https://github.com/netmon24/PRTG-Grafana \
      org.opencontainers.image.description="netmon24 PRTG-Grafana: Collector, Portal und Einrichtung"

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
# Sicherheitsaktualisierungen des Betriebssystems, die im Basis-Image noch fehlen (Trivy-Prüfung im Release)
RUN apt-get update && apt-get -y --no-install-recommends upgrade \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY --from=lock /lock/requirements.txt /tmp/requirements.txt
RUN pip install --require-hashes -r /tmp/requirements.txt && rm /tmp/requirements.txt
COPY collector/pyproject.toml /app/pyproject.toml
COPY collector/src /app/src
RUN pip install --no-deps --no-build-isolation . && useradd --system --uid 10001 --no-create-home app \
    && mkdir -p /data /certs /tokens && chown app /data /certs /tokens
COPY grafana/dashboards /app/dashboards

USER app
CMD ["python", "-m", "prtg_collector"]
