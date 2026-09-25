# Competition gates

One entry per competition this deployment runs, naming the checkout on the gate host and
the exact revision it must be at.

    slug           the competition, as it appears in /v1/competitions/{slug}
    root           the checkout on the gate host
    commit         the reviewed revision; the worker refuses to start on anything else
    pins_sha256    sha256 of <root>/validator/verifier/PINS.json
    timeout_seconds  the whole gate, every stage (default 2700)

`commit` and `pins_sha256` are what stand in for the container digest the Lean verifier is
pinned by. The gate cannot be containerized -- it needs bubblewrap, elan/Lean,
Charon/Aeneas and cargo on the host -- so instead the worker checks at startup that the
checkout is at that commit, that the tree is clean, and that `PINS.json` still hashes to
what is recorded here. `PINS.json` pins the gate's own files; this pins `PINS.json`.

The values above are placeholders and will be refused. To fill them in:

    cd /srv/gates/lz77
    git rev-parse HEAD
    sha256sum validator/verifier/PINS.json

Bumping a gate to a new revision is a reviewed change to this file, deliberately: it is the
record of which code decided a verdict, and a gate that could update itself between
submissions would make that unanswerable.
