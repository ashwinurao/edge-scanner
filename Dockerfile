# Edge Scanner on a home server (see "Running on a home server" in the README).

# The dashboard is built once here; the scanner serves the built files at /v2.
FROM node:22-slim AS dashboard
WORKDIR /build
COPY dashboard-v2/package.json dashboard-v2/package-lock.json ./
RUN npm ci
COPY dashboard-v2 ./
RUN npm run build

FROM python:3.13-slim

# HOME under data/ so schwabdev's ~/.schwabdev/tokens.db lands in the mounted volume.
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HOME=/app/data/home

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY scanner scanner
COPY scripts scripts
# The OV picks route reads the module's files through the shared contract alone (ticket #21);
# the rest of ov_picks/ (samples, schema notes) is for development and stays out.
COPY ov_picks/__init__.py ov_picks/contract.py ov_picks/
COPY start_scanner.sh .
COPY --from=dashboard /build/dist dashboard-v2/dist
RUN mkdir -p data/home && chown -R 99:100 /app

# Unraid's nobody:users; override with `user:` in compose for other hosts.
USER 99:100
EXPOSE 7777
# 0.0.0.0 inside the container only; compose publishes the port on the LAN address alone.
CMD ["sh", "start_scanner.sh", "--host", "0.0.0.0"]
