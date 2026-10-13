# P34/P35 admission

The owner approved the exact 34- and 35-circle unit-square targets on October 12, 2026.
`packing-status.json` supersedes the two prior hold decisions, retaining their research
qualifications and all disk exclusions. `prior-packing-status.json` preserves the original audit.

`Candidate11.P34` asks for the universal radius upper bound
`1/(4 + 2*sqrt(2) + 2*sqrt(6))`; `Candidate11.P35` asks for
`1/(8 + sqrt(2) + sqrt(6))`. The frozen square-packing predicate requires positive radius,
containment in the unit square and pairwise center separation of at least twice the radius.
The known constructions are certified by `PackingFixtures.lean`; the global upper bounds
remain the bounty targets. No proof of global optimality is asserted by this packet.

The packet is copied byte-for-byte from the earlier reviewed preparation and committed by
`packet-sha256.json`. Compilation uses the pinned local image
`sha256:60ffdc444e7ec3f707fca465173dadcdf7652c67ed9da810fa23f3edf63a10a8`
with networking disabled, `lean --trust=0`, and a disposable build directory. Compile modules
in order: QuadCertificate, FrozenSquareData, FrozenCircleData, Candidates, PackingFixtures.
The circle module is an import dependency only; it does not readmit any disk target.

Admission permits preparing these targets for release at `linear-age-v3-chat-logs`, with
reviewed logs required. Production activation still needs generated task bundles, registry,
source/catalog pins and a matching verifier-image release, as described in the rollout guide.
