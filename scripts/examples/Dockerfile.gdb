# Optional helper image, used only by the malloc_trim step at the end of seed-throttle-load.py
# when the seed is a Reloaded node: attaches gdb to the seed process to ask the allocator to
# return free memory.
#   docker build -t swaplab/gdb:1 -f scripts/examples/Dockerfile.gdb docker/
FROM swaplab/kdf-runtime:debian12
USER root
RUN apt-get update && apt-get install -y --no-install-recommends gdb && rm -rf /var/lib/apt/lists/*
