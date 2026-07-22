FROM ubuntu:24.04@sha256:4fbb8e6a8395de5a7550b33509421a2bafbc0aab6c06ba2cef9ebffbc7092d90

ENV DEBIAN_FRONTEND=noninteractive \
    ELAN_HOME=/opt/fc-verifier/.elan \
    PATH=/opt/fc-verifier/.elan/bin:/usr/local/go/bin:/usr/local/bin:/usr/bin:/bin

ARG ENABLE_NANODA=0
ARG LEAN_BUILD_THREADS=2
ENV LEAN_NUM_THREADS=${LEAN_BUILD_THREADS}

RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates curl git build-essential zstd python3 \
      golang-go cargo pkg-config libssl-dev jq tini \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/fc-verifier

# Expensive, immutable inputs are isolated from verifier-source edits so a
# security patch does not invalidate the full Formal Conjectures build.
COPY pins.lock.json lean-toolchain lakefile.toml lake-manifest.json ./
COPY scripts/pin_dependencies.sh ./scripts/pin_dependencies.sh
RUN ./scripts/pin_dependencies.sh \
    && mkdir -p .tools \
    && architecture="$(dpkg --print-architecture)" \
    && case "$architecture" in \
         amd64) platform=x86_64-unknown-linux-gnu ;; \
         arm64) platform=aarch64-unknown-linux-gnu ;; \
         *) echo "unsupported architecture: $architecture" >&2; exit 2 ;; \
       esac \
    && version="$(jq -r '.elan.version' pins.lock.json)" \
    && digest="$(jq -r --arg platform "$platform" '.elan.assets[$platform]' pins.lock.json)" \
    && curl -fsSL "https://github.com/leanprover/elan/releases/download/v$version/elan-$platform.tar.gz" -o .tools/elan.tar.gz \
    && echo "$digest  .tools/elan.tar.gz" | sha256sum --check --strict \
    && tar -xzf .tools/elan.tar.gz -C .tools elan-init \
    && .tools/elan-init -y --default-toolchain leanprover/lean4:v4.27.0 \
    && cd vendor/formal-conjectures \
    && lake exe cache get \
    && lake build FormalConjecturesAnswerPostpone extract_names \
    && cd /opt/fc-verifier/vendor/lean4export \
    && lake build lean4export \
    && cd /opt/fc-verifier/vendor/comparator \
    && lake build comparator \
    && mkdir -p /opt/fc-verifier/vendor/landrun/bin \
    && cd /opt/fc-verifier/vendor/landrun \
    && go build -trimpath -o bin/landrun ./cmd/landrun \
    && if [ "$ENABLE_NANODA" = 1 ]; then \
         cd /opt/fc-verifier/vendor/nanoda && cargo build --release --locked; \
       fi \
    && cd /opt/fc-verifier \
    && rm -rf /root/.cache/mathlib \
    && rm -f .tools/elan.tar.gz .tools/elan-init

COPY . .
RUN mkdir -p .lake/packages \
    && for package in vendor/formal-conjectures/.lake/packages/*; do \
         ln -s "../../$package" ".lake/packages/$(basename "$package")"; \
       done \
    && lake exe cache get \
    && lake build VerifierLean TaskSupport TestFixtures catalog_extractor task_inspector \
    && cc -O2 -Wall -Wextra -Werror -o .tools/seccomp-launcher security/seccomp-launcher.c \
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
