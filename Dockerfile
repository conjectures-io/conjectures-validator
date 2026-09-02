FROM ubuntu:24.04@sha256:4fbb8e6a8395de5a7550b33509421a2bafbc0aab6c06ba2cef9ebffbc7092d90

ENV DEBIAN_FRONTEND=noninteractive \
    ELAN_HOME=/opt/fc-verifier/.elan \
    PATH=/opt/fc-verifier/.elan/bin:/usr/local/go/bin:/usr/local/bin:/usr/bin:/bin

ARG ENABLE_NANODA=0
ARG LEAN_BUILD_THREADS=2
ENV LEAN_NUM_THREADS=${LEAN_BUILD_THREADS}

RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates curl git build-essential zstd python3 \
      golang-go cargo pkg-config libssl-dev tini \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/fc-verifier

# Expensive, immutable inputs are isolated from verifier-source edits so a
# security patch does not invalidate the full Formal Conjectures build.
COPY pins.lock.json lean-toolchain lakefile.toml lake-manifest.json ./
COPY scripts/pin_dependencies.sh scripts/install_elan.sh scripts/build_trusted_cache.sh ./scripts/
# The audited Formal Conjectures patch lives in the pinned task repository, a sibling of this
# build context and so unreachable from a COPY. Passed in as the named context `tasks`; the
# script still accepts it only against the sha256 in pins.lock.json. Build with
# `scripts/build_image.sh`, which supplies the context.
COPY --from=tasks tiers/tier-1/formal-conjectures-audit-fixes.patch ./.build/
# The build itself lives in scripts/, not here. Two copies of the recipe is how the verifier a
# miner builds from source drifts from the one that decides their submission; `--stage` is what
# lets a single script keep the layer split this Dockerfile depends on.
RUN FC_AUDIT_PATCH=/opt/fc-verifier/.build/formal-conjectures-audit-fixes.patch \
    ./scripts/pin_dependencies.sh \
    && ./scripts/install_elan.sh \
    && ENABLE_NANODA="${ENABLE_NANODA}" ./scripts/build_trusted_cache.sh --stage vendor \
    && rm -rf /root/.cache/mathlib /opt/fc-verifier/.build

COPY . .
# `COPY` preserves the checkout's permission bits. A root-owned release cloned with umask 077
# therefore arrives as 0700 directories and 0600 files, which the non-root user below cannot read.
# Normalize only the application tree copied by the preceding instruction; the
# three pruned paths are trusted-cache layers from the vendor build and already have usable modes.
RUN find . \
      \( -path './vendor' -o -path './.elan' -o -path './.lake' \) -prune \
      -o -exec chmod a+rX {} + \
    && ./scripts/build_trusted_cache.sh --stage root \
    && rm -rf /root/.cache/mathlib \
    && for directory in formal-conjectures comparator lean4export landrun nanoda; do \
         git config --system --add safe.directory "/opt/fc-verifier/vendor/$directory"; \
       done \
    && for package_root in \
         vendor/formal-conjectures/.lake/packages \
         vendor/comparator/.lake/packages \
         .lake/packages; do \
         for package in "$package_root"/*; do \
           [ -e "$package" ] || continue; \
           git config --system --add safe.directory "/opt/fc-verifier/$package"; \
         done; \
       done \
    && /usr/sbin/useradd --create-home --shell /usr/sbin/nologin --uid 10001 verifier \
    && mkdir -p .work \
    && chown verifier:verifier .work

USER verifier
ENV PATH=/opt/fc-verifier/.elan/bin:/usr/local/bin:/usr/bin:/bin \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONHASHSEED=0 \
    PYTHONNOUSERSITE=1
ENTRYPOINT ["/usr/bin/tini", "--", "python3", "-m", "verifier"]
CMD ["doctor"]
