"""Regenerate `coordinator_rows.json`: the rows Exact integer kernels' own coordinator writes.

Drives the real coordinator store of an `exact-integer-kernels` checkout through a few entries
against a scratch database, with stage reports shaped like `eik`'s real ones (the compare report
is build/prod-compare.json's shape, the checks the release calibration's), then dumps the tables
the API reads. Not part of the suite; run by hand when the coordinator's output changes:

    python tests/fixtures/eik_schema/generate_rows.py \\
        --eik /path/to/exact-integer-kernels \\
        --url postgresql+psycopg://owner:pw@127.0.0.1:PORT/postgres

`--url` must be a throwaway cluster: the script creates a database there and drops it. Run it
with a Python that has the coordinator's requirements (SQLAlchemy, psycopg).
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import sys
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "coordinator_rows.json"
MEASUREMENT = "8fbabb6eb5834b6559a8789b91c9d645ca9f9f39b2fbed6c20cc557947308f2c"
VERIFIER = "7d914501e64707b88dd6d959b78aa5c3e6eaa49897fde3b1e34612666d25b191"
FP = {"measurement": MEASUREMENT, "sandbox": "container", "verifier": VERIFIER}
FUNCTIONS = ("exp", "sigmoid", "tanh")
# What the API may read, in insert order. Leases and the leader token stay behind.
TABLES = {
    "registrations": "id,uid,ss58_hot,ss58_cold,block,block_date",
    "sources": "sha256,content",
    "submissions": "id,hotkey,registration_id,intake_mode,source_sha256,submitted_at,state,"
    "verification_identity,proof_report,normalised_sha256,stage,reason,finished_at",
    "board_progress": "board,activation_boundary,last_submission_id,last_submitted_at",
    "evaluations": "id,submission_id,board,generation,config_hash,reference_promotion_id,"
    "reference_hash,plan,state,decision,created_at,completed_at",
    "benchmark_jobs": "id,request_hash,request,candidate_hash,reference_hash,environment,"
    "lease_seconds,retry_seconds,state,due_at,attempts,started_at,completed_at,created_at,result,"
    "error",
    "evaluation_jobs": "evaluation_id,job_id,environment,seed",
    "promotions": "id,board,evaluation_id,promoted_at,disqualified_at,disqualification_reason",
    "coordinator_state": "id,config_hash,config_evidence,publication,publication_hash",
}

ENTRIES = {
    # name: (gate, compare ratios or "rejected", check verdict)
    "first-champion": ("accepted", {"rv32im": 0.5, "thumbv6m": 1.2, "overall": 0.8}, "passed"),
    "gate-rejected": ("rejected", None, None),
    "compare-rejected": ("accepted", "rejected", None),
    "check-rejected": ("accepted", {"rv32im": 0.4, "thumbv6m": 0.9, "overall": 0.6}, "rejected"),
    "second-champion": ("accepted", {"rv32im": 0.9, "thumbv6m": 0.95, "overall": 0.9}, "passed"),
    "in-progress": ("accepted", None, None),
}


def source_of(name):
    return (
        f"/* {name} */\n#include <stdint.h>\n"
        + "".join(
            f"uint32_t eik_{fn}(uint32_t x) {{ return x ^ {len(name)}u; }}\n" for fn in FUNCTIONS
        )
    ).encode()


def gate(source, *, verdict):
    rejected = verdict == "rejected"
    return {
        "schema": "eik-report-1",
        "kind": "gate",
        "competition": "exact-integer-kernels",
        "sha256": hashlib.sha256(source).hexdigest(),
        "verdict": verdict,
        "stage": "ast" if rejected else None,
        "reasons": [
            "FloatingLiteral is not allowed (in eik_sigmoid)",
            "a call to an undeclared function: expf",
        ]
        if rejected
        else [],
        "normalised_sha256": hashlib.sha256(b"normalised:" + source).hexdigest(),
        "bytes": {"rv32im": 16600, "thumbv6m": 15476, "thumbv7em": 13480},
        "source_bytes": len(source),
        "fingerprint": FP,
    }


def measure(sha, seed, scale, *, targets=("rv32im", "thumbv6m", "thumbv7em"), sampled=True):
    """A passed measure report, build/prod-compare.json's numbers times `scale`."""
    base = {
        "rv32im": (12326.18, 16600, 1.058),
        "thumbv6m": (17990.92, 15476, 1.054),
        "thumbv7em": (6830.42, 13480, 1.048),
    }
    instr = {"exp": 38988.79, "sigmoid": 6755.84, "tanh": 5758.31}
    scores = {}
    for t in targets:
        s, size, factor = base[t]
        scores[t] = {
            "S": s * scale,
            "bytes": size,
            "size_factor": factor,
            "functions": {
                fn: {"geo_mean_instr": v * scale, "tail_factor": 1.0 if fn == "exp" else 1.02}
                for fn, v in instr.items()
            },
        }
        if t == "thumbv7em":
            scores[t]["reported_only"] = True
    scores["overall"] = {"S": (scores["rv32im"]["S"] * scores["thumbv6m"]["S"]) ** 0.5}
    return {
        "schema": "eik-report-1",
        "kind": "measure",
        "competition": "exact-integer-kernels",
        "sha256": sha,
        "seed": seed,
        "sampled": sampled,
        "inputs_sha256": hashlib.sha256(f"inputs:{seed}".encode()).hexdigest(),
        "hard_pool": {
            "source": "hardscan",
            "samples": 67108864,
            "threshold_log2_ulps": -18,
            "sizes": {"exp": 457, "sigmoid": 564, "tanh": 193},
        },
        "images": {t: hashlib.sha256(f"image:{sha}:{t}".encode()).hexdigest() for t in targets},
        "bytes": {t: base[t][1] for t in targets},
        "fingerprint": FP,
        "scores": scores,
        "verdict": "passed",
        "stage": None,
        "reasons": [],
    }


def compare(request, ratios):
    sha, seed = request["candidate_hash"], request["seed"]
    champions = {}
    for spec in request["comparisons"].values():
        champions.setdefault(
            spec["reference_hash"],
            measure(
                spec["reference_hash"], seed, 2.0, targets=("rv32im", "thumbv6m"), sampled=False
            ),
        )
    out = {
        "schema": "eik-report-1",
        "kind": "compare",
        "competition": "exact-integer-kernels",
        "sha256": sha,
        "seed": seed,
        "champions": champions,
        "fingerprint": FP,
    }
    if ratios == "rejected":
        rep = measure(sha, seed, 1.0)
        del rep["scores"]
        rep.update(
            verdict="rejected",
            stage="outputs:rv32im",
            reasons=["wrong output on a scored input"],
            examples=[{"input": "3f800000", "got": "402df855", "want": "402df854"}],
        )
        out["measure"] = rep
        return out
    entry = measure(sha, seed, 1.0)
    boards = {}
    for board, ratio in ratios.items():
        ref = request["comparisons"][board]["reference_hash"]
        e_s = entry["scores"][board]["S"]
        boards[board] = {
            "champion_sha256": ref,
            "entry_S": e_s,
            "champion_S": e_s / ratio,
            "ratio": ratio,
            "beats": ratio <= 0.97,
        }
    out["measure"] = entry
    out["decision"] = {
        "schema": "eik-report-1",
        "kind": "decision",
        "competition": "exact-integer-kernels",
        "sha256": sha,
        "seed": seed,
        "takeover_ratio": 0.97,
        "boards": boards,
        "candidate_boards": [b for b, v in boards.items() if v["beats"]],
        "needs_fullcheck": any(v["beats"] for v in boards.values()),
        "fingerprint": FP,
    }
    return out


def check(request, verdict):
    lo, hi = request["chunks"]
    rep = {
        "schema": "eik-report-1",
        "kind": request["kind"],
        "competition": "exact-integer-kernels",
        "sha256": request["candidate_hash"],
        "function": request["function"],
        "chunk_lo": lo,
        "chunk_hi": hi,
        "quick": False,
        "exit": 0,
        "fingerprint": FP,
        "budget_seconds": 15360,
        "seconds": round(2189.0 * (hi - lo) / 2048, 1),
        "words_checked": (hi - lo) << 20,
        "mismatches": 0,
        "examples": [],
        "range_complete": True,
        "complete": False,
        "verdict": "passed",
        "stage": None,
        "reasons": [],
    }
    if request["kind"] == "targetcheck":
        rep.update(
            target=request["target"],
            jobs=8,
            image_sha256=hashlib.sha256(f"checker:{request['target']}".encode()).hexdigest(),
        )
    if verdict == "rejected":
        rep.update(
            verdict="rejected",
            stage="fullcheck:outputs",
            reasons=[f"1 wrong outputs of {request['function']}"],
            mismatches=1,
            words_checked=1 << 20,
            seconds=20.4,
            range_complete=False,
            exit=1,
            examples=[{"input": "bf317218", "got": "3f000001", "want": "3f000000"}],
        )
    return rep


def plain(value):
    if isinstance(value, (bytes, memoryview)):
        return {"base64": base64.b64encode(bytes(value)).decode()}
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    return value


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--eik", required=True, type=Path, help="an exact-integer-kernels checkout")
    p.add_argument("--url", required=True, help="a throwaway cluster's owner URL")
    a = p.parse_args()
    sys.path.insert(0, str(a.eik.resolve()))
    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import make_url

    from coordinator import db, migrate
    from coordinator.config import CHECK_ENV, COMPARE_ENV, Config
    from coordinator.coordinator import merge
    from coordinator.store import Store
    from coordinator.worker import WorkerDb
    from eik import config as K

    # A stand-in for the shipped incumbent: its real source is not this fixture's to publish.
    tmp = tempfile.TemporaryDirectory()
    incumbent = Path(tmp.name) / "incumbent.c"
    incumbent.write_bytes(source_of("incumbent"))
    incumbent_sha = hashlib.sha256(incumbent.read_bytes()).hexdigest()
    name = "eik_rows_" + uuid.uuid4().hex[:8]
    admin = create_engine(make_url(a.url), isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        c.execute(text(f'CREATE DATABASE "{name}"'))
        for role in ("eik_rows_worker", "platform_api"):
            if not c.scalar(text("SELECT 1 FROM pg_roles WHERE rolname=:r"), {"r": role}):
                c.execute(text(f"CREATE ROLE {role} LOGIN PASSWORD 'w'"))
    url = make_url(a.url).set(database=name)
    try:
        migrate.migrate(url.render_as_string(hide_password=False))
        owner = db.engine(url.render_as_string(hide_password=False))
        with owner.begin() as c:
            migrate.run_script(
                c, (Path(migrate.__file__).with_name("migrations") / "platform_api.sql").read_text()
            )
            c.execute(
                text("INSERT INTO worker_logins(login,kind) VALUES ('eik_rows_worker','benchmark')")
            )
            for env in (COMPARE_ENV, CHECK_ENV):
                c.execute(
                    text("INSERT INTO worker_environments VALUES ('eik_rows_worker',:e)"),
                    {"e": env},
                )
            c.execute(text("GRANT eik_worker_access TO eik_rows_worker"))
        worker = WorkerDb(
            db.sessions(
                db.engine(
                    url.set(username="eik_rows_worker", password="w").render_as_string(
                        hide_password=False
                    )
                )
            )
        )
        boards = "".join(
            f'[boards.{b}]\nreference = "{incumbent}"\nreference_sha256 = "{incumbent_sha}"\n'
            for b in K.BOARDS
        )
        toml = Path(tmp.name) / "competition.toml"
        toml.write_text(f"""[competition]
lease_seconds = 900
retry_seconds = 60
[identities]
verifier = "{VERIFIER}"
measurement = "{MEASUREMENT}"
[fullcheck]
shards_per_function = 2
require_targets = ["rv32im"]
targetcheck_shards = 1
{boards}
[rewards]
rank_shares = [1.0, 0.0]
paused_policy = "treasury"
[rewards.board_weights]
overall = 0.5
rv32im = 0.25
thumbv6m = 0.25
""")
        config = Config.load(toml)
        store = Store(owner)
        store.set_leader(uuid.uuid4().hex)
        store.reconcile(config)
        seeds = iter(range(20261118, 20261218))

        def tick():
            store.plan(config, seed=lambda: next(seeds))
            store.aggregate(config, merge)
            store.publish(config)

        def work(env, report):
            identity = MEASUREMENT if env == COMPARE_ENV else VERIFIER
            while (job := worker.claim(env, identity, "eik-rows")) is not None:
                worker.complete(job, report(job.request))

        for uid, (entry, (verdict, ratios, checked)) in enumerate(ENTRIES.items(), 1):
            source = source_of(entry)
            sha = hashlib.sha256(source).hexdigest()
            hotkey = "5" + hashlib.sha256(entry.encode()).hexdigest()[:47]
            with owner.begin() as c:
                rid = c.scalar(
                    text(
                        "INSERT INTO registrations(uid,ss58_hot,ss58_cold,block,block_date) "
                        "VALUES (:u,:h,'5Cold',:b,now()) RETURNING id"
                    ),
                    {"u": uid, "h": hotkey, "b": 1000 + uid},
                )
                c.execute(
                    text("INSERT INTO sources(sha256,content) VALUES (:s,:c)"),
                    {"s": sha, "c": source},
                )
                c.execute(
                    text(
                        "INSERT INTO submissions(hotkey,registration_id,source_sha256,intake_mode) "
                        "VALUES (:h,:r,:s,'subnet')"
                    ),
                    {"h": hotkey, "r": rid, "s": sha},
                )
            sub = store.claim_gate("eik-rows", 900)
            store.finish_gate(
                sub.id,
                sub.token,
                config,
                gate(sub.content, verdict=verdict),
                0 if verdict == "accepted" else 1,
            )
            if verdict != "accepted":
                continue
            tick()
            if ratios is None:
                continue  # left with its compare job queued
            work(COMPARE_ENV, lambda r: compare(r, ratios))
            tick()
            if checked == "rejected":
                job = worker.claim(CHECK_ENV, VERIFIER, "eik-rows")
                worker.complete(job, check(job.request, "rejected"))
            else:
                work(CHECK_ENV, lambda r: check(r, checked))
            tick()

        dump = {}
        with owner.connect() as c:
            for table, columns in TABLES.items():
                result = c.execute(text(f"SELECT {columns} FROM {table} ORDER BY 1"))
                dump[table] = [{k: plain(v) for k, v in row.items()} for row in result.mappings()]
        dump = {
            "generated_from": f"exact-integer-kernels coordinator, publication "
            f"{dump['coordinator_state'][0]['publication']['version']}",
            **dump,
        }
        OUT.write_text(json.dumps(dump, indent=1, sort_keys=True) + "\n")
        print(f"wrote {OUT}: " + ", ".join(f"{t} {len(dump[t])}" for t in TABLES))
    finally:
        with admin.connect() as c:
            c.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
            c.execute(text("DROP ROLE IF EXISTS eik_rows_worker"))
        admin.dispose()
        tmp.cleanup()


if __name__ == "__main__":
    main()
