# Frontier Math subnet: phases 1–3

This repository now contains the Bittensor v11 foundation, signed HTTP protocol, and a
submission-only reference miner. The miner is deliberately small: an operator imports an existing
Lean file from local disk, and the miner commits and reveals those exact bytes. It has no solver,
solver adapter, remote upload endpoint, task downloader, validator scoring loop, or emissions logic.
Miner operators build their solvers independently.

## Delivered phases

1. **Foundation:** Bittensor v11 and web dependencies, Python compatibility, localnet, a separate
   miner image, and runtime state outside the repository.
2. **Protocol and trust boundary:** strict versioned records, canonical task and submission
   commitments, miner-signed response envelopes, authenticated validator requests, exact verifier
   handoff, replay/rate limits, and durable immutable round state.
3. **Submission-only miner:** one local import command plus health, capabilities, commitment, and
   reveal endpoints. There are no solve, job, challenge-download, or upload routes.

## Components and trust boundaries

- `frontier_subnet/` contains strict protocol records, `btauth/1` request authentication,
  domain-separated proof commitments, chain endpoint publication, the SQLite submission store, and
  the miner API.
- `frontier-miner load` is the only submission-ingress path. It accepts one local regular `.lean`
  file and one audited gold task bundle, verifies both against `gold/allowlist.json`, and stores the
  immutable source bytes by digest.
- Gold allowlist v2 admits one exact `formalized` task per selected Formal Conjectures source file.
  It rejects transformed positive/negative pairs, answer-wrapper extraction, duplicate source
  paths, duplicate target types, and every retired v1 task.
- `frontier-miner serve` exposes unauthenticated `GET /healthz` and receiver-bound, hotkey-signed
  `GET /v1/capabilities`, `POST /v1/commitments`, and `POST /v1/reveals`.
- The miner signs commitment and reveal envelopes with its hotkey. A persisted random salt binds
  the exact chain, subnet, round, task digest, and submission digest.
- One global commitment is created per miner, task, and chain round. Every authorized validator
  receives the same immutable envelope; `request_id` is correlation metadata, not part of that
  globally reusable proof.
- The miner process never verifies a proof. Validators must pass revealed source to the existing
  one-shot, networkless verifier container with the exact published task digest.

Never mount a wallet, miner database, Docker socket, or network interface into the verifier
container. Never add the verifier's development override flags to a network service.

## Host installation

Python 3.11–3.13 is supported. Install the subnet extra into the existing development environment:

```bash
.venv/bin/pip install \
  --constraint requirements-subnet.lock \
  -e '.[dev,subnet]'
.venv/bin/frontier-miner --help
```

The runtime dependency closure is pinned in `requirements-subnet.lock`. Bittensor 11 does not
provide the old Axon, Dendrite, or Synapse server stack; this subnet uses ordinary FastAPI/httpx
traffic with `bittensor.http_auth`.

Runtime state defaults to `$XDG_STATE_HOME/frontier-math/miner.sqlite3`, or
`~/.local/state/frontier-math/miner.sqlite3` when `XDG_STATE_HOME` is unset. It is intentionally
outside the repository. The Docker deployment uses a named volume for the same reason.

## Start an immutable local chain

The localnet compose file pins Rao Foundation Subtensor commit
`89eb75f38fb3121fdd041d642331cc975dd20d94` by its multi-architecture OCI digest. It exposes RPC
only on host loopback and persists chain state in a named volume:

```bash
docker compose -f docker-compose.localnet.yml up -d
.venv/bin/btcli query is-fast-blocks --network local
BT_SUBNET_INTEGRATION=1 .venv/bin/pytest tests/test_subnet_localnet.py
```

`True` enables 250 ms development blocks. To use 12-second blocks, change the localnet command's
first argument to `False`. Removing the local development chain and its state is explicit and
destructive:

```bash
docker compose -f docker-compose.localnet.yml down -v
```

On a fresh localnet, netuids 0 and 1 already exist. The following creates disposable local-only
wallets and a custom subnet. The Alice seed is a public development seed; never use it on a public
network.

```bash
.venv/bin/btcli wallet regen-coldkey -w alice --no-password \
  --seed 0xe5be9a5092b81bca64be81d212e7f2f9eba183bb7a90954f7b76361f6edb5c0a
.venv/bin/btcli wallet create -w owner -H default --no-password
.venv/bin/btcli wallet create -w validator -H default --no-password
.venv/bin/btcli wallet create -w miner -H default --no-password

.venv/bin/btcli tx transfer --dest owner --amount-tao 2000 -w alice --network local -y
.venv/bin/btcli tx transfer --dest validator --amount-tao 100 -w alice --network local -y
.venv/bin/btcli tx transfer --dest miner --amount-tao 100 -w alice --network local -y

.venv/bin/btcli tx register-subnet -w owner --network local
```

The last command prints the assigned netuid; a fresh chain normally assigns `2`. Substitute the
actual value below:

```bash
NETUID=2
.venv/bin/btcli tx start-call --netuid "$NETUID" -w owner --network local
.venv/bin/btcli tx burned-register --netuid "$NETUID" -w validator --network local
.venv/bin/btcli tx burned-register --netuid "$NETUID" -w miner --network local
.venv/bin/btcli query metagraph --netuid "$NETUID" --network local --json
```

The production miner authorization policy admits only registered hotkeys with validator permits
and the configured minimum TAO stake. During isolated local protocol testing, an operator may pass
one or more explicit `--allow-hotkey 5...` values instead. Do not use
`--allow-any-authenticated` outside a disposable localnet.

## Prepare the miner wallet mount

The miner signs only with its hotkey. Do not expose its coldkey to the container. Create a dedicated
wallet root containing only the selected private hotkey and its public companion:

```text
/var/lib/frontier-miner-wallets/
└── miner/
    └── hotkeys/
        ├── default
        └── defaultpub.txt
```

Copy those two files from the Bittensor wallet created on the host. Do not copy `coldkey` or any
other wallet. The container runs as UID/GID 10001, so the dedicated directory must be readable by
that identity and not by unrelated users. The compose file mounts it read-only at `/wallets`.

Set the deployment inputs:

```bash
export FRONTIER_MINER_WALLET_DIR=/var/lib/frontier-miner-wallets
export FRONTIER_MINER_WALLET_NAME=miner
export FRONTIER_MINER_HOTKEY=default
export FRONTIER_NETUID=2
export BT_NETWORK=finney
```

For a miner joined to the localnet compose network, use
`BT_NETWORK=ws://subtensor-localnet:9944` and invoke Compose with both files:

```bash
docker compose \
  -f docker-compose.localnet.yml \
  -f docker-compose.subnet.yml \
  build miner

docker compose \
  -f docker-compose.localnet.yml \
  -f docker-compose.subnet.yml \
  up -d subtensor-localnet miner
```

## Import a submission

The container image does not contain repository tasks, proof files, or an allowlist. Mount exactly
one of each read-only for the one-shot import command. This loader does not require the serving
wallet or netuid environment variables:

```bash
TASK_DIR="$PWD/tasks/gold/<exact-task-directory>"
SUBMISSION_FILE="$PWD/submissions/Main.lean"

docker compose -f docker-compose.subnet.yml run --rm --no-deps \
  -v "$TASK_DIR:/inputs/task:ro" \
  -v "$SUBMISSION_FILE:/inputs/Main.lean:ro" \
  -v "$PWD/gold/allowlist.json:/inputs/allowlist.json:ro" \
  miner-load load \
    --database /state/frontier-math/miner.sqlite3 \
    --task-dir /inputs/task \
    --submission /inputs/Main.lean \
    --allowlist /inputs/allowlist.json
```

The command prints the task bundle and submission SHA-256 commitments. Importing a replacement for
the same exact task updates the local selection intentionally; it does not execute the proof.
The one-shot loader has no network and no wallet mount. There is no HTTP upload route.

## Run and publish the miner

Start the service:

```bash
docker compose -f docker-compose.subnet.yml up -d miner
curl --fail http://127.0.0.1:8091/healthz
docker compose -f docker-compose.subnet.yml logs --tail=100 miner
```

The Compose service:

- runs as UID/GID 10001 with a read-only root, all capabilities dropped, no new privileges, bounded
  CPU/memory/PIDs/files, and a small non-executable `/tmp`;
- mounts the wallet read-only and keeps SQLite state in the `miner-state` named volume;
- binds to host loopback unless `FRONTIER_MINER_LISTEN_IP` is explicitly changed;
- starts exactly one Uvicorn worker, which is required by the in-memory replay cache and rate
  limiter.

Do not scale this service above one process. A multi-process or multi-host deployment first needs a
shared atomic nonce store and shared admission controls.

Endpoint publication is a separate, explicit chain mutation. After registration and after a TLS
reverse proxy is reachable at the public address:

```bash
.venv/bin/btcli axon set \
  --netuid "$FRONTIER_NETUID" \
  --ip <public-ip> \
  --port <public-tls-port> \
  -w "$FRONTIER_MINER_WALLET_NAME" \
  -H "$FRONTIER_MINER_HOTKEY" \
  --network "$BT_NETWORK" \
  -y
```

`btauth/1` supplies identity, integrity, freshness, receiver binding, and replay protection; it
does not encrypt traffic. Terminate TLS before exposing the API publicly. Keep system time
synchronized because signed requests use a short freshness window.

## Configuration

The supplied Compose service recognizes:

| Variable | Default | Purpose |
| --- | --- | --- |
| `FRONTIER_MINER_WALLET_DIR` | required | Dedicated hotkey-only wallet root mounted read-only |
| `FRONTIER_NETUID` | required | Bittensor subnet UID |
| `BT_NETWORK` | `finney` | Network name or exact `ws://`/`wss://` endpoint |
| `FRONTIER_MINER_WALLET_NAME` | `miner` | Wallet directory name |
| `FRONTIER_MINER_HOTKEY` | `default` | Hotkey filename |
| `FRONTIER_MINER_LISTEN_IP` | `127.0.0.1` | Host interface receiving mapped port 8091 |
| `FRONTIER_MINER_PORT` | `8091` | Host-side mapped port |
| `FRONTIER_MIN_VALIDATOR_TAO` | `0` | Minimum root TAO stake for permitted validators |
| `FRONTIER_METAGRAPH_REFRESH_SECONDS` | `30` | Validator authorization cache lifetime |
| `FRONTIER_REQUESTS_PER_MINUTE` | `60` | Per-hotkey, per-route request limit |
| `FRONTIER_MAX_CONCURRENT_REQUESTS` | `16` | In-process bounded request concurrency |
| `FRONTIER_MINER_LOG_LEVEL` | `info` | Uvicorn log level |

The serving process fails closed when the required wallet directory or netuid is omitted: the
default wallet path does not exist and the default netuid is invalid. The networkless
`miner-load` service does not consume either value.

Round defaults are 360 blocks total, 120 blocks to commit, and 360 blocks until expiry. The miner
enforces those windows against the finalized chain head, never the reorgable best head. Override
them with direct `frontier-miner serve` arguments only after validators agree on the same protocol
schedule.

## Operations and recovery

- The SQLite database contains imported source, proof commitments, unrevealed salts, and signed
  reveals. Loss before reveal makes outstanding commitments unusable. Back up the named volume
  while the miner is stopped, and test restoration before relying on it.
- Hotkey rotation changes proof attribution. Register and publish the new hotkey before serving
  from it; do not copy coldkeys into the miner.
- A chain read failure fails closed. A caller whose hotkey lacks a current validator permit or
  configured stake is denied even when its HTTP signature is valid.
- Rebuild and identify the miner image by digest for deployment. Review changes to the pinned
  Bittensor/web dependencies and protocol version before upgrading.
- Keep the verifier image, miner image, wallet, database, and public TLS endpoint as separate
  operational units.

## Verification gates

Before shipping a miner image:

```bash
.venv/bin/pytest
.venv/bin/pip check
docker compose config --quiet
FRONTIER_MINER_WALLET_DIR=/tmp/frontier-wallets FRONTIER_NETUID=2 \
  docker compose -f docker-compose.subnet.yml config --quiet
docker compose -f docker-compose.localnet.yml config --quiet
docker build -f docker/miner.Dockerfile -t frontier-math-miner:local .
docker compose build verifier
docker compose run --rm verifier doctor
git diff --check
git status --short
```

The first three subnet phases do not include validator challenge selection, proof verification
queues, deterministic scoring, weight submission, novelty rewards, or mainnet launch procedures.
