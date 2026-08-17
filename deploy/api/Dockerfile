# The network-facing submission API.
#
# A SEPARATE IMAGE FROM THE VERIFIER, DELIBERATELY. submission_api/__init__.py states the rule:
# this package "must never share a process or container with that verifier, and payment
# credentials and validator wallet keys must never be reachable from it". So this image has no
# Lean, no Elan, no Comparator, no Landrun, and no build toolchain in its final layer — nothing
# that could compile or execute a submitted proof. The repository's ../../Dockerfile is the
# verifier and stays that way.
#
# Same pinned Ubuntu base as the verifier image, by digest. One base image in the repository
# means one thing to patch, and pinning by digest means a rebuild does not silently move it.

FROM ubuntu:24.04@sha256:4fbb8e6a8395de5a7550b33509421a2bafbc0aab6c06ba2cef9ebffbc7092d90 AS builder

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

# Compilers live in the builder only. Several pinned dependencies (ckzg, bitarray, cytoolz)
# build from source when no wheel matches the platform, and the runtime stage must not carry a
# toolchain that could be used to build something else.
RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential ca-certificates python3 python3-dev python3-venv \
    && rm -rf /var/lib/apt/lists/*

RUN python3 -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH

WORKDIR /src

# The lockfile alone first, so a code edit does not invalidate the dependency layer.
#
# The direct dependencies are named here and their versions come from the constraint file, which
# is the same pinned set `pyproject.toml`'s `service` and `db` extras resolve to. Listed
# explicitly rather than installed via `.[service]` because that would need the project source
# in this layer, and then every code edit would reinstall bittensor.
COPY requirements-service.lock ./
RUN pip install --constraint requirements-service.lock \
      fastapi \
      uvicorn \
      httpx \
      google-auth \
      pydantic \
      SQLAlchemy \
      greenlet \
      "psycopg[binary]" \
      bittensor

# Then the project itself. `--no-deps`: every dependency is already installed at its pinned
# version above, and resolving again could pull something the lockfile did not sanction.
COPY pyproject.toml README.md ./
COPY verifier ./verifier
COPY conjectures_subnet ./conjectures_subnet
COPY submission_api ./submission_api
RUN pip install --no-deps .


# --- Runtime -------------------------------------------------------------------------------

FROM ubuntu:24.04@sha256:4fbb8e6a8395de5a7550b33509421a2bafbc0aab6c06ba2cef9ebffbc7092d90

ENV DEBIAN_FRONTEND=noninteractive \
    PATH=/opt/venv/bin:/usr/local/bin:/usr/bin:/bin \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# `tini` so uvicorn gets signals directly and a rolling restart drains rather than being
# killed. No compilers, no git, no curl.
# `/usr/sbin/useradd` by absolute path: the base image ships it but does not put /usr/sbin on
# PATH, and the verifier Dockerfile calls it the same way for the same reason. A real passwd
# entry matters because libraries in the dependency set resolve the home directory through
# `getpwuid`, which fails outright for a uid with no entry.
#
# uid 10002, not the verifier's 10001. Different trust domains should not share a uid: if a
# volume were ever mounted into both by mistake, the file ownership would say so.
RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates python3 tini \
    && rm -rf /var/lib/apt/lists/* \
    && /usr/sbin/useradd --create-home --shell /usr/sbin/nologin --uid 10002 conjectures

COPY --from=builder /opt/venv /opt/venv

# The audited task pool and the pin lock are read at startup. Mounted read-only by compose
# rather than copied in, so a pin rotation is a restart against new bytes and not a rebuild.
WORKDIR /opt/conjectures

USER 10002:10002

EXPOSE 8000

# No `--proxy-headers`. uvicorn's forwarded-header handling would rewrite `scope["client"]`
# itself, and the API already resolves the client address from `TRUSTED_PROXY_HOPS` — see
# `submission_api/middleware.py:client_address`. Two layers deciding would mean neither is
# authoritative, and the rate limiter would key on whichever won.
#
# One worker per container. The rate limiter and its key table are per process, so scaling by
# replica keeps `RATE_LIMIT_REQUESTS` meaning "per replica" as documented, instead of silently
# multiplying by a worker count set here.
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["uvicorn", "submission_api.asgi:app", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--workers", "1", \
     "--no-server-header", \
     "--timeout-keep-alive", "15"]
