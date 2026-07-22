FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive \
    ELAN_HOME=/opt/fc-verifier/.elan \
    PATH=/opt/fc-verifier/.elan/bin:/usr/local/go/bin:/usr/local/bin:/usr/bin:/bin

ARG ENABLE_NANODA=0

RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates curl git build-essential zstd python3 python3-pip python3-venv \
      golang-go cargo pkg-config libssl-dev jq tini \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/fc-verifier
COPY . .
RUN ./scripts/pin_dependencies.sh \
    && mkdir -p .tools \
    && curl -fsSL https://raw.githubusercontent.com/leanprover/elan/464c9d28395000a2a0128e07081e4956d50eced2/elan-init.sh -o .tools/elan-init.sh \
    && sh .tools/elan-init.sh -y --default-toolchain leanprover/lean4:v4.27.0 \
    && python3 -m venv .venv \
    && .venv/bin/pip install --no-cache-dir -e '.[dev]' \
    && ./scripts/build_trusted_cache.sh \
    && git config --system --add safe.directory '*' \
    && useradd --create-home --uid 10001 verifier \
    && mkdir -p .work \
    && chown verifier:verifier .work

USER verifier
ENV PATH=/opt/fc-verifier/.venv/bin:/opt/fc-verifier/.elan/bin:/usr/local/bin:/usr/bin:/bin
ENTRYPOINT ["/usr/bin/tini", "--", "python", "-m", "verifier"]
CMD ["doctor"]
