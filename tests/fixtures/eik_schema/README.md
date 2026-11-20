Exact integer kernels API contract fixture. `0001_schema.sql`, `0002_source_embargoes.sql` and
`platform_api.sql` are copied unchanged from the `exact-integer-kernels` repository
(`coordinator/migrations/`, commit 100f143),
which owns them; `sha256.json` pins them. This fixture is applied only to an explicitly declared
private test database, never by the central API deployment.

`coordinator_rows.json` is what that repository's coordinator writes for six entries (two
takeovers, a gate rejection, a compare rejection, a check rejection and one in progress), dumped
from the tables the API reads. `generate_rows.py` regenerates it; see its docstring.
