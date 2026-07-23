ARG PYTHON_IMAGE=python:3.12-slim-bookworm@sha256:d50fb7611f86d04a3b0471b46d7557818d88983fc3136726336b2a4c657aa30b

FROM ${PYTHON_IMAGE} AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /src
COPY pyproject.toml README.md requirements-subnet.lock ./
COPY verifier ./verifier
COPY frontier_subnet ./frontier_subnet

RUN python -m venv /opt/frontier-venv \
    && /opt/frontier-venv/bin/python -m pip install \
        --constraint requirements-subnet.lock \
        ".[subnet]"

FROM ${PYTHON_IMAGE}

LABEL org.opencontainers.image.title="Frontier Math submission miner" \
      org.opencontainers.image.description="Bittensor transport for operator-imported Lean submissions" \
      org.opencontainers.image.licenses="Apache-2.0"

ENV HOME=/home/miner \
    PATH=/opt/frontier-venv/bin:/usr/local/bin:/usr/bin:/bin \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONHASHSEED=0 \
    PYTHONNOUSERSITE=1 \
    PYTHONUNBUFFERED=1 \
    XDG_STATE_HOME=/state

COPY --from=builder /opt/frontier-venv /opt/frontier-venv

RUN mkdir -p /home/miner /state /wallets \
    && chown -R 10001:10001 /home/miner /state /wallets

USER 10001:10001
WORKDIR /home/miner

EXPOSE 8091
STOPSIGNAL SIGTERM
ENTRYPOINT ["/opt/frontier-venv/bin/frontier-miner"]
CMD ["serve", "--help"]
