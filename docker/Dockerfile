# Run-time image for one KDF node of kdf-swaplab. Nothing is compiled here and no binary is
# baked in: the binary under test (KDF Reloaded, Komodo KDF 2.x, GLEEC KDF 3.x) is bind-mounted
# read-only at /usr/local/bin/kdf by the generated compose file, and its SHA-256 is recorded
# in every run's manifest. Adapted from rincoin-testlab images/kdf (2026-09-22).
#   docker build -t swaplab/kdf-runtime:debian12 -f docker/Dockerfile docker/
FROM debian:12-slim
ENV DEBIAN_FRONTEND=noninteractive
# libudev1: linked by the KDF binaries (hardware wallet transport)
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates libudev1 \
 && rm -rf /var/lib/apt/lists/*
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod 755 /usr/local/bin/entrypoint.sh && mkdir -p /kdf && chmod 777 /kdf
WORKDIR /kdf
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
