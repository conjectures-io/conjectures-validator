# Candidate review and task publication: 50 task targets

**Review status:** Passed after revision; task release published; validator release branch prepared<br>
**Review date:** 2026-08-24 UTC<br>
**Release shape:** 50 theorem targets, each in `formalized` and `counterexample` mode (100 bundles)<br>
**Task baseline:** `conjectures-tasks@c1829b7c28bd54a59a9f1f2dcb9834b1cab53cfd`<br>
**Published task release:** `conjectures-tasks@8dd81458db1cdcd2ec4fd3e6f866aa1907006858`<br>
**Task release PR:** [conjectures-tasks#11](https://github.com/conjectures-io/conjectures-tasks/pull/11), merged as `4bd1d01dd6193eec6b48eb6176ebdae9aa76a384`<br>
**Validator used for staging:** `conjectures-validator@a8d559db1d4d6ccdd2f7cc07e7d7dd5d45a8afb2`<br>
**Pinned Formal Conjectures source:** `379fc0298dc146df549e7061c3ede0353a5bb51f`<br>
**Formal Conjectures main checked:** `1bd0e70d325bcae22edb8d77e946da79c4d0d378`<br>
**Erdős Problems tracker checked:** `3eb045e8f27ef5d45fff7835388520f49ddc9313`

## Decision

The **initial** 50-target slate failed the adversarial review. Five targets were removed and replaced
before this report was marked passed:

| Removed initial target | Final replacement | Why the initial target did not survive |
| --- | --- | --- |
| `Erdos889.erdos_889.variants.V1_eq_1_finite` | `Erdos32.erdos_32` | Directly implied by the already-active general variant. |
| `Erdos1212.erdos_1212` | `Erdos600.erdos_600.parts.i` | Exact target remains open, but extensive prior public Lean work makes it poor fresh benchmark material. |
| `Erdos1145.erdos_1145` | `Erdos885.erdos_885` | Directly implies the already-active Problem 28 target by taking `A = B`. |
| `Erdos566.erdos_566` | `Erdos891.erdos_891` | Lean states size Ramsey where the source asks about ordinary Ramsey; it repeats a paid retirement defect. |
| `Erdos75.erdos_75` | `Erdos1192.erdos_1192` | An exact public proof claim predates this review and has received a substantive mathematical check. |

The corrected 50 targets below passed the pre-publication review gate. After explicit approval,
they were added in task release `8dd81458…` as exactly 50 target rows and 100 new bundle directories
while preserving every existing bundle byte-for-byte. Pull request #11 published the commit with a
merge commit, so the validator's exact task pin remains reachable from the task repository's main
branch. No production activation was performed during task publication.

This review treats a "problem" as one exact Lean theorem target, and the corrected slate spans 50
distinct Erdős problem numbers. Seven targets are independent parts or variants in source files
already represented in the pool; the other 43 introduce new canonical source paths. The resulting
totals are 209 targets, 418 bundles, 189 Erdős targets, 20 Green targets, and 182 distinct source
paths.

## Review funnel

The pinned catalog contains 3,267 declarations. At the pre-publication baseline, 353 unselected,
non-retired declarations in the Erdős and Green source families passed the mechanical production
policy. The current Erdős tracker removed 28 candidates whose live status was proved, disproved, or
otherwise solved.
Direct page, source-drift, PR/issue, collision, semantic, and reward-overlap review was then applied
to a larger-than-50 candidate set before settling this slate.

Every accepted target satisfies all of these gates:

- exact `DIRECT_PROP`, `research open`, theorem/`Prop`, and both production modes supported;
- the declaration depends on its own admitted proof body, but has no `sorry` in its type, answer
  metadata, formal-proof metadata, or matching proved type elsewhere in the catalog;
- after excluding the intentional `answer(sorry)` placeholder, no helper or type-level definition
  used by the target depends on a sorried existence proof (`Nat.find`/`Classical.choose` included);
- absent from the current targets and retirement ledgers, with no selected, retired, intra-slate,
  or proved-declaration type-hash collision;
- current authoritative tracker status still eligible;
- zero live proof claims for the exact problem, zero listed mathematical workers, and zero listed
  formalization workers;
- no active PR that resolves or corrects the selected theorem;
- Lean type compared with the informal statement, including domains, quantifier order, side
  conditions, asymptotic filters, inequalities, distinctness/nonemptiness, and helper definitions;
- degenerate values and witnesses considered where relevant; and
- no direct implication/equivalence that would duplicate an existing reward target.

The direct problem pages are an important freshness signal, not a proof that the literature is
complete. Their own disclaimer says unrecorded literature may exist. Status was therefore combined
with exact problem-number/statement searches, source references, the live Formal Conjectures issue
and PR queues, and current tracker data. In the corrected slate, 48 pages show `open` and two
(Problems 873 and 1004) show only partial activity; all 50 show zero claimed proofs and empty
mathematical/formalization worker lists.

## Accepted slate

`tracker/page` reports the tracker state followed by the direct page's comment-activity widget.
Every row has zero proof claims and empty mathematical/formalization worker lists. `drift none`
means the pinned source file is byte-identical to current main. `drift neutral` is limited to an
unused-import/scope change or a change to an unrelated sibling; `drift equivalent` means a helper
was replaced by its definitionally identical Mathlib version. In both cases the selected type and
helper semantics are unchanged.

| # | Problem / exact theorem | Pinned type hash | Live evidence | Semantic and overlap review |
| ---: | --- | --- | --- | --- |
| 1 | [32](https://www.erdosproblems.com/32) / `Erdos32.erdos_32` | `sha256:5517f12e37ad44b6b8a2049475cde72352adbc6775884434d5e8bfec0b8761ba` | open/open; PR —; drift neutral | Additive complement to the primes with counting function `o((log N)^2)`; the known `O((log N)^2)` result and almost-all-integers refinements do not settle it. |
| 2 | [936](https://www.erdosproblems.com/936) / `Erdos936.erdos_936.variants.two_pow_sub_one` | `sha256:f18a0302acc299b87cbc04e7dcf8a9843fc5e7d415637beaa6622fe2cf1ad1d2` | open/open; PR —; drift none | Mersenne (`2^n−1`) eventual non-powerfulness; independent of selected Fermat-number sibling. |
| 3 | [950](https://www.erdosproblems.com/950) / `Erdos950.erdos_950.parts.ii` | `sha256:b7fd4d41bfd0eb1f9be01a7f164b0ae1816049103ee6e82efb66295e0ddc9c7a` | open/open; PR —; drift none | `limsup f(n)=∞`; independent of selected `liminf=1` part. |
| 4 | [323](https://www.erdosproblems.com/323) / `Erdos323.erdos_323.variants.k_gt_2` | `sha256:6999fdcf13250f347167761e85107cc258e7b4dd6475cfce32b2ca8fed30726e` | open/open; PR —; drift none | All `k>2`; exact little-o target and quantifier order. |
| 5 | [155](https://www.erdosproblems.com/155) / `Erdos155.erdos_155` | `sha256:e200e0171f6b5e78d0cc6b256d346b3e6b66f39f97ab4ab49de7bd5b80badb55` | open/open; PR —; drift none | All `k≥1`, eventually in `N`; shift and `+1` bound preserved. |
| 6 | [244](https://www.erdosproblems.com/244) / `Erdos244.erdos_244` | `sha256:0f3326bc9f6fac3ba29ddbe12782a09b6d17d9ae33984b569eb2dd7dc1e3ff12` | open/open; PR —; drift none | All real `C>1`; prime/power representation and positive lower density preserved. |
| 7 | [893](https://www.erdosproblems.com/893) / `Erdos893.erdos_893` | `sha256:200751647459d0c8ae82a267d3c6d3a380afb15b41e19fe688dacf27f0d842ac` | open/open; PR —; drift none | Ratio `f(2n)/f(n)→∞`; denominator is eventually positive by the helper definition. |
| 8 | [873](https://www.erdosproblems.com/873) / `Erdos873.erdos_873` | `sha256:f65bfe32e4ca49963d561bbe51dc02312f9b260863cd7537acd49d813914419c` | open/partial; PR —; drift none | Positive strictly monotone sequence, all `ε>0`, chosen `k`; partial results only, no solution claim. |
| 9 | [200](https://www.erdosproblems.com/200) / `Erdos200.erdos_200` | `sha256:d43fa2325088a551f87016acbf0fea01b86760180eef57029b6670de0c56bea0` | open/open; PR —; drift none | Longest prime AP is little-o of `log N`; domain and asymptotic filter match. |
| 10 | [354](https://www.erdosproblems.com/354) / `Erdos354.erdos_354.parts.i` | `sha256:173f55ac95b251a0388db86fbd8b1d5d27babb8fa0769821d95c57ac2587f15b` | open/open; PR —; drift none | Positive `α,β`, irrational ratio, base `2`; index interleave preserves duplicates. The malformed part ii was rejected. |
| 11 | [412](https://www.erdosproblems.com/412) / `Erdos412.erdos_412` | `sha256:76232ceb7eac301b3c9a58bdf2e5f7fe16e3d9a81a9f299b0f56f2c493266557` | open/open; PR —; drift none | Both inputs `≥2`; independent iterate counts `i,j`; divisor-sum iterate is exact. |
| 12 | [890](https://www.erdosproblems.com/890) / `Erdos890.erdos_890.parts.a` | `sha256:3e332fb3ae679532aff86de00ec4490a65925f13321c5f5de0d166dfa59adcf4` | open/open; PR —; drift none | Counts distinct prime factors strictly `>k`; `k≥1`; exact liminf inequality. |
| 13 | [680](https://www.erdosproblems.com/680) / `Erdos680.erdos_680.parts.ii` | `sha256:d0068db7ed3750f57e52cb8465a3662197735d0284cf00ed1d3549169ce44777` | open/open; PR —; drift none | All `ε>0`, positive `Cε`; negated eventual statement and `k≠0` match. |
| 14 | [458](https://www.erdosproblems.com/458) / `Erdos458.erdos_458` | `sha256:909d60f631d5bf196c4bbe998c7ccc755c1f67be8a90e99926311a249344db99` | falsifiable/open; PR —; drift none | Zero-based Lean prime index matches the informal one-based indexing; LCM inequality exact. |
| 15 | [943](https://www.erdosproblems.com/943) / `Erdos943.erdos_943` | `sha256:c5a8f359522f97e397057bba602208dc7826505e2e60b9c4647e60fef5bb1171` | open/open; PR —; drift none | Single `o(n)=o(1)` witness and eventual convolution bound; no degenerate witness. |
| 16 | [50](https://www.erdosproblems.com/50) / `Erdos50.erdos_50` | `sha256:49437b186485c7a34248bc088d114225dcdd8ff9bc800f6a45ff2325bb55bea1` | open/open; PR —; drift none | Distribution-function predicate plus positive within-derivative on `[0,1]`; endpoints handled explicitly. |
| 17 | [126](https://www.erdosproblems.com/126) / `Erdos126.erdos_126` | `sha256:baceba31bace2d3c4aefa7abde6d6d4ad6e742acfe0dfdae6302fcf177f793d0` | open/open; PR —; drift none | Maximal-factor-count predicate and `f(n)/log n→∞`; selected sibling is a different weaker asymptotic. |
| 18 | [247](https://www.erdosproblems.com/247) / `Erdos247.erdos_247` | `sha256:fc1a51eefa55e36b0beb64cfb799fcc1ef092cbfc9068a64bc3712d971af2479` | open/open; PR —; drift none | Strict sequence, limsup growth, infinite sum, and transcendence domains preserved. |
| 19 | [463](https://www.erdosproblems.com/463) / `Erdos463.erdos_463` | `sha256:20c3316fde211c926c0f24a4be33346603ee27b460f46825d43f1552d0d26465` | open/open; PR —; drift none | `f→∞`, eventual `n`, composite witness `m`, strict inequalities, and least prime factor preserved. |
| 20 | [145](https://www.erdosproblems.com/145) / `Erdos145.erdos_145` | `sha256:f176a0bfb88442d3e2af7d9fd0574cf9f3c2a8fbf2509c1c12c8e4aa57fd694f` | open/open; PR —; drift none | All real `α≥0`; existence of the normalized gap-moment limit. |
| 21 | [701](https://www.erdosproblems.com/701) / `Erdos701.erdos_701` | `sha256:b2ab9e4fd6ef1d1f746255e4d533ab72a28d93fb7b74382a536f0ffee2cb1dce` | open/open; PR —; drift none | Finite nonempty ground type, hereditary family, every intersecting subfamily, exact cardinal comparison. |
| 22 | [428](https://www.erdosproblems.com/428) / `Erdos428.erdos_428` | `sha256:2b3c9b455f9a0af251361349f272481934f62a120bd713409e7d90e0cfb5f626` | open/open; PR —; drift none | Infinitely many prime-translate witnesses plus positive liminf relative to prime counting. |
| 23 | [600](https://www.erdosproblems.com/600) / `Erdos600.erdos_600.parts.i` | `sha256:7aeac9f2ec0e2514bbb4d975e0b2724ff741607674a3104ad6c50fab022e7aee` | open/open; PR —; drift equivalent | For every fixed `r≥2`, the difference `e(n,r+1)-e(n,r)` tends to infinity; `sInf` is total and the current triangle helper is definitionally identical. |
| 24 | [931](https://www.erdosproblems.com/931) / `Erdos931.erdos_931` | `sha256:4db34befecc68b1ff513e49fce7f436c4f4c709505c0108d9a0cf0c117531dec` | open/open; PR —; drift none | `k₂≥3`, `k₂≤k₁`, separated intervals, equality of prime-factor sets, finiteness exact. |
| 25 | [695](https://www.erdosproblems.com/695) / `Erdos695.erdos_695.variants.upperBound` | `sha256:5d3b0dcca310b5be635b9260e38a0b7930d25a971b364e4543feb1a67468077a` | open/open; PR —; drift none | Strict prime chain, congruence `qᵢ₊₁≡1 mod qᵢ`, explicit error-term upper bound; independent of the selected lower-growth target. |
| 26 | [942](https://www.erdosproblems.com/942) / `Erdos942.erdos_942` | `sha256:32a13035c76f2ccb7f9417c54eba68efd9280000b308bcf1aac4618cfe86b99b` | open/open; PR —; drift none | Positive exponent `c`, one little-o error, eventual upper bound and infinite lower excursions. |
| 27 | [885](https://www.erdosproblems.com/885) / `Erdos885.erdos_885` | `sha256:cff674d6b05471491f148f4a647447aea3e317313380710734e78f08c452c900` | open/open; PR #5055; drift none | All `k≥1`; the public Lean work proves only the already-known `k=2,3` variants, leaving the all-`k` target untouched. |
| 28 | [1073](https://www.erdosproblems.com/1073) / `Erdos1073.erdos_1073` | `sha256:ce379578bcf74e9c9f0f26f3097db5efb58de820117e694203a10aba079b3ce4` | open/open; PR #4688,#4198; drift none | One little-o exponent witness; early boundary values do not affect the asymptotic target. |
| 29 | [1106](https://www.erdosproblems.com/1106) / `Erdos1106.erdos_1106.parts.ii` | `sha256:3a79424001452961961cc10b1ab7e9653b853758d2edcf306248b0b9a775c8f8` | open/open; PR #4688,#4198,#4003; drift none | Product over `1..n`; counts distinct prime factors and requires `>n` eventually. |
| 30 | [1085](https://www.erdosproblems.com/1085) / `Erdos1085.erdos_1085.variants.upper_d3` | `sha256:572861df5ecc3996e045308319c5cc289f7ec47f97227c17e29d6eda14608822` | open/open; PR #4688,#4198; drift none | Dimension exactly `3`; Big-O of `n^(4/3) log log n`; coercions and exponents checked. |
| 31 | [1004](https://www.erdosproblems.com/1004) / `Erdos1004.erdos_1004` | `sha256:9e8759cd8569a0f823d624ca196c909b12dd34eba5c6b823d6e5886b031be601` | open/partial; PR #4688,#4198; drift none | All `c>0`, eventual `x`, witness `n≤x`, and exact run length `floor((log x)^c)`; only partial activity. |
| 32 | [1057](https://www.erdosproblems.com/1057) / `Erdos1057.erdos_1057` | `sha256:d9d4e2c4c121fc065a35e95ed514ddd67f7ce3df8821ac1e9b832792b75983bf` | open/open; PR #4688,#4198,#3422; drift none | `log C(x)/log x→1`, the standard exact encoding of `C(x)=x^(1-o(1))`. |
| 33 | [1137](https://www.erdosproblems.com/1137) / `Erdos1137.erdos_1137` | `sha256:96d9d56fb0a38908f5fdde11eab68df2bad467b53ddce71bed55150b40dda9a1` | open/open; PR #4688,#4198; drift none | Finite maxima over `n<x`, adjacent prime gaps, and squared normalization; the early truncated index is irrelevant. |
| 34 | [1072](https://www.erdosproblems.com/1072) / `Erdos1072.erdos_1072.parts.ii` | `sha256:261dceb9bf5ba4c679a32f43a20b26f91cc07c812631e4db38e04705fffbd1dc` | open/open; PR #4688,#4198; drift none | Density-one subset relative to primes and restricted-filter convergence of `f(p)/p` to zero. |
| 35 | [1068](https://www.erdosproblems.com/1068) / `Erdos1068.erdos_1068` | `sha256:18aadc80443418fe2cdc6e8658361f772dfac497a2279d7ec9048f0a57804b76` | open/open; PR #4688,#4198; drift none | Chromatic cardinal `ℵ₁`, countable induced subgraph, and infinite connectivity all explicit. |
| 36 | [951](https://www.erdosproblems.com/951) / `Erdos951.erdos_951` | `sha256:e7f6d00b13790afff65a409d922be549ab1de8761aa833bdb01447a188c4c265` | open/open; PR #4004; drift none | `a₀>1`, strict monotonicity, source predicate, eventual counting bound against `π(floor x)`. |
| 37 | [517](https://www.erdosproblems.com/517) / `Erdos517.erdos_517` | `sha256:d2a9309904fa5956c223f09218938410def4426ce0caba1a365efe9d0132da58` | open/open; PR #4004; drift none | Fabry gaps, nonzero coefficients, entire-series equality, and infinite preimage for every complex value. |
| 38 | [891](https://www.erdosproblems.com/891) / `Erdos891.erdos_891` | `sha256:84dd238bc29290f58b85c308b55eef43271bdcec150368672368bccd4e73f352` | open/open; PR —; drift neutral | `range k` and zero-based `Nat.nth Prime` give exactly the first `k` primes; `Ico` has the intended length and `ω` counts distinct prime factors. |
| 39 | [108](https://www.erdosproblems.com/108) / `Erdos108.erdos_108` | `sha256:3bbbe11684218da1653d0fe0c24de6480477a26459659c3dfe0e846659461119` | open/open; PR #4688,#4198; drift none | `r≥4`, `k≥2`, finite threshold, nonempty graph, subgraph girth and chromatic lower bounds explicit. |
| 40 | [564](https://www.erdosproblems.com/564) / `Erdos564.erdos_564` | `sha256:4f45446140ae17cb1c0fac3316b084440d1b8d0bd65fd94fa5a8230266c87c6f` | open/open; PR —; drift none | Positive constant and eventual double-exponential lower bound for 3-uniform Ramsey numbers. |
| 41 | [789](https://www.erdosproblems.com/789) / `Erdos789.erdos_789.variants.sq` | `sha256:5f0475c69d1d1d8fe299a0db410a528e1ee8017f96eaf19609441e9bc257c04d` | open/open; PR #4003; drift none | Direct `Θ(√n)` variant; `sSup` threshold is nonempty/bounded. Issue #4923 affects only the answer-slot main. |
| 42 | [1192](https://www.erdosproblems.com/1192) / `Erdos1192.erdos_1192` | `sha256:82dfbe82fe1b5bbca9d16f555655d8e5b3e26fc1b3dfe8a931db7f67770fc9b5` | open/open; PR —; drift neutral | Ordered `r`-tuple representation count, eventual basis condition, and `O(x)` second moment are exact; the known `r=2` theorem and recent partial work do not settle all `r≥2`. |
| 43 | [978](https://www.erdosproblems.com/978) / `Erdos978.erdos_978.parts.ii` | `sha256:2ce98f554e4b76bc888ebfc4734728e9556132798b2850ef1e4e31ed5f1f04f0` | open/open; PR #4004; drift none | Irreducible polynomial, degree `>3` not a power of two, positive leading coefficient, and exact exponents. |
| 44 | [70](https://www.erdosproblems.com/70) / `Erdos70.erdos_70.variants.omega_times_two_four` | `sha256:8212d9ec59d89064b56f940bf51a9af36699a95555f245f9a8360c26a3efbd1f` | open/open; PR —; drift none | Permutation-invariant triple coloring; exact continuum, ordinal `ω·2`, and blue cardinal `4` first-open case. |
| 45 | [975](https://www.erdosproblems.com/975) / `Erdos975.erdos_975` | `sha256:81dcf2ec44832ea4834f84aea071e5869a7003db2972096758f2237b9101bdca` | open/open; PR #5077,#4003; drift none | Nonconstant irreducible integer polynomial, eventual positivity, per-polynomial `c>0`; floor/early terms do not change the limit. |
| 46 | [829](https://www.erdosproblems.com/829) / `Erdos829.erdos_829` | `sha256:1d8e233d78fa8ff827ddb91213ee8268e2b4df495b4cf6d7750f2a1aeba0461b` | open/open; PR #4194; drift none | Ordered sums by natural cubes; including zero changes at most two representations; natural `C` captures polylog `O(1)`. |
| 47 | [812](https://www.erdosproblems.com/812) / `Erdos812.erdos_812.parts.i` | `sha256:e27111c9f2a0896182ff6489ff6a82cf045c41f102ec676f15d45633245816bc` | open/open; PR #3588; drift none | Positive uniform ratio eventually; the diagonal 2-uniform Ramsey helper is exact. PR #3588 uses an equivalent helper. |
| 48 | [85](https://www.erdosproblems.com/85) / `Erdos85.erdos_85` | `sha256:da2437c05a6a570fd6224ce38798081c0d3208e49357c6b7c8bab76b9095e787` | open/open; PR —; drift neutral | Threshold `sInf` is nonempty (`k=n` makes the premise impossible); small `n<4` cases vanish under `atTop`. |
| 49 | [156](https://www.erdosproblems.com/156) / `Erdos156.erdos_156` | `sha256:69fff1f4ac55d0ed271ae93c03d155a308965a4a265c87207817f12eecb3687a` | open/open; PR —; drift neutral | Minimum maximal-Sidon cardinal has nonempty bounded `sInf`; Big-O minimum matches the stated asymptotic existence. |
| 50 | [14](https://www.erdosproblems.com/14) / `Erdos14.erdos_14.parts.ii` | `sha256:92676249a3598e8a654641b6d8078ed91bb3b00db116143e5a586cd63e1e6b20` | open/open; PR —; drift neutral | `allUniqueSums` is unordered uniqueness up to swap; fixed `A`, finite interval count, and little-o of `√N` are exact. |

## PR classification

No accepted target has an active resolving or correcting PR. The PRs shown above classify as:

- `#4688`, `#4198`, and `#3422`: broad repository/module work, not a resolution of the selected target;
- `#4003` and `#4004`: reference/docstring normalization only;
- `#5077`: a linter adjustment involving a solved sibling in Problem 975, leaving the selected main theorem unchanged;
- `#4194`: explicitly discharges test/textbook scaffolding in Problem 829, not the open target;
- `#3588`: changes Problem 812 to a mathematically equivalent diagonal Ramsey helper, without proving or changing the target; and
- [`#5055`](https://github.com/google-deepmind/formal-conjectures/pull/5055): proves the already-known `k=2,3`
  variants of Problem 885, not the selected all-`k` statement.

## Important rejections

The review rejected candidates even when they passed the machine policy:

| Candidate | Reason |
| --- | --- |
| `Erdos75.erdos_75` | Exact [public proof claim](https://www.erdosproblems.com/forum/thread/75) posted 2026-04-12 via a Specker-graph argument. A subsequent check found only a minor issue and obtained the stronger `α(H) ≫ n/log n`; the page's `open` badge is therefore not a safe novelty signal. |
| `Erdos889.erdos_889.variants.V1_eq_1_finite` | Directly implied by active `Erdos889.erdos_889.variants.general`: `v(n,k) ≤ V(n,k)`, so `v₁(n) → ∞` forces only finitely many `V₁(n)=1`. |
| `Erdos1145.erdos_1145` | Directly implies active `Erdos28.erdos_28` by taking `A = B`; the ratio hypothesis becomes `1` and the remaining premises/limsup conclusion specialize exactly. |
| `Erdos566.erdos_566` | [Problem 566](https://www.erdosproblems.com/566) asks about ordinary Ramsey `R(G,H)`, but Lean states `SimpleGraph.sizeRamsey`. Specializing to `Q₃` reproduces retired [Problem 567(i)](https://www.erdosproblems.com/567), retired and paid for this exact exploitable mismatch. |
| `Erdos1212.erdos_1212` | Exact target still appears open, but merged [PR #4218](https://github.com/google-deepmind/formal-conjectures/pull/4218) adds the target plus six proved structural lemmas and a [separate Lean project](https://github.com/ephraimduncan/erdos-1212) develops it further. Quarantined for benchmark/reward freshness. |
| `Erdos10.erdos_10` | The main target remains open, but the page now carries a [partial proof claim](https://www.erdosproblems.com/forum/thread/10/proof-claims) for the unselected Grechuk sibling. Replaced to preserve the slate's strict zero-claim freshness rule. |
| `Erdos295.erdos_295` | Hidden `TYPE_DEPENDS_ON_SORRY` defect: target data uses `Nat.find (exists_k N)`, while `exists_k` is admitted. Mechanical bundle generation alone does not detect this choice-from-sorry dependency. |
| `Erdos855.erdos_855` | Nested `∀ᶠ x, ∀ᶠ y` lets the `y` threshold depend on `x`, weakening the informal joint “for all sufficiently large `x` and `y`” statement. |
| `Erdos968.erdos_968` | Material pinned-to-current semantic drift: the source moved from `HasPosDensity` to positivity of `lowerDensity`. Requires a fresh fidelity decision before use. |
| `Erdos1093.erdos_1093.parts.i` | Shared `deficiency` uses `Nat.smoothNumbers k` (prime divisors `< k`) while [Problem 1093](https://www.erdosproblems.com/1093) defines `k`-smooth using prime divisors `≤ k`; sibling part ii is already retired for this source mismatch. |
| `Erdos410.erdos_410`, `Erdos930.erdos_930` | Current mathematical workers are listed on their live pages, so they fail the zero-worker freshness gate. |
| `Erdos1002.erdos_1002` | A July 2026 exact public proof claim is under review; not suitable for a fresh release slate. |
| `Erdos945.erdos_945.variants.constant` | Equivalent to the already-selected main target; the equivalence now has a claimed formal proof. |
| `Erdos252.erdos_252.variants.k_ge_five` | Directly weaker than the selected all-`k≥1` target. |
| `Erdos325.erdos_325.variants.weaker` | Directly implied by the selected parent. |
| `Erdos324.erdos_324` | Direct existential consequence of the selected quintic witness. |
| `Erdos208.erdos_208.parts.i` | Directly implied by the selected logarithmic-bound variant. |
| `Erdos385.erdos_385.parts.ii` | Implies the selected part i immediately. |
| `Erdos535.erdos_535.variants.sunflower_strong` | Explicitly intended to imply two selected sunflower targets. |
| `Erdos1003.erdos_1003.variants.Icc` | Its `k=1` instance implies the selected main target. |
| `Erdos306.erdos_306`, `Erdos1133.erdos_1133` | Live page carries a solution-claim signal. |
| `Erdos828.erdos_828`, `Erdos495.erdos_495` | A current formalization worker is listed. |
| `Erdos944.erdos_944` | Active PR contains machine-checked progress on the `k=4,r=1` core. |
| `Erdos354.erdos_354.parts.ii` | The existential `γ` is unused while the sequence hardcodes base `2`. |
| `Erdos653.erdos_653` | Open dependency/formalization-defect issue. |
| `Erdos655.erdos_655.variants.general_position` | Live formal-repository solved/open mismatch. |
| `Erdos681.erdos_681`, `Erdos288.erdos_288.variants.i2_card_eq_1` | Current mathematical workers are listed. |

Separately, 28 machine-eligible pinned declarations were removed because the current tracker now
marks their underlying problems proved, disproved, or solved.

## Adversarial solve and formalization review

The review did not stop at status labels. It tried to discharge or refute every corrected target
with 42 isolated Lean attacks in each mode: `native_decide`, simplification, constructors,
arithmetic, contradiction/vacuity, explicit witnesses, `aesop`, and search tactics. Across 100
bundles this compiled 4,200 attempts:

- 4,053 failed to elaborate;
- 147 search probes compiled only by reusing the imported admitted source theorem or inserting a
  new `sorry`; their axiom closure includes `sorryAx`, so none is admissible; and
- zero admissible proof or counterexample was found, with zero unmapped errors and zero duplicate
  target types.

The exact-structure confirmation on `Erdos32.erdos_32` reported
`[propext, sorryAx, Classical.choice, Quot.sound]` for the apparent search hits; the permitted set
excludes `sorryAx`. The review run wrote detailed per-attempt results and the axiom confirmation to
temporary files under `/tmp/add-50-attack-audit/`; the aggregate results and decisive axiom closure
are preserved in this report.

Two additional mathematical smoke tests were used where finite exploration could expose an easy
failure (but could not prove the conjecture):

- Problem 458: checked 664,578 consecutive-prime gaps below 10,000,000; no counterexample was found.
- Problem 412: iterated the divisor-sum map for starting values 2 through 200 for 15 steps; no
  trivial universal merge or short collapse was found.

The elaborated-type dependency audit enumerated every constant used by all 50 target types and ran
`Lean.collectAxioms` on the helpers after excluding the explicit answer placeholder. No accepted
helper depends on `sorryAx`. This check is what rejected reserve Problem 295 even though its bundles
pass the ordinary mechanical policy.

The online pass checked the direct pages and discussions, exact problem-number/statement searches,
current source references, and the Formal Conjectures issue/PR queues. Partial results were treated
as evidence, not as full solutions: Problems 873 and 1004 remain watch items, while the exact public
claim for Problem 75 and the substantial pre-existing formal development for Problem 1212 caused
removal. The accepted-but-watch list is Problems 428, 517, 701, 873, 885, 890, 931, 942, 943, 951,
978, 1004, 1137, and 1192: none has an exact solution, but each has public near-target activity,
heuristic evidence, or partial formal work and should receive a same-day freshness recheck before
any later publication.

## Temporary generation and compilation

Staging used a detached clean validator checkout at `a8d559d…`, not the `prod/tmp` working tree.
The latter was 114 commits behind that compatible baseline and contained a user-owned
`.env.example` edit. No pool metadata or bundle directory was touched during the candidate gate.

The staged check for each target and mode performed production-policy validation, challenge
generation, Lean compilation, `TaskInspector`, bundle reload, target/source hash comparison, and
manifest/digest validation. The initial 100 bundles all passed; after the five review failures were
found, both modes for each of the five replacements were generated and compiled through the same
pipeline.

**Corrected result: passed.** A final aggregate sweep selected the 90 surviving initial bundles and
the ten replacement bundles, then loaded them alongside all 318 active bundles. It established:

- exactly 50 complete `{formalized, counterexample}` pairs for 50 distinct problem numbers;
- 100 unique task IDs, 100 unique bundle digests, 100 unique generated target hashes, and 50 unique
  source hashes;
- zero theorem-name, source-hash, task-ID, bundle-digest, or generated-target-hash intersections
  with the active pool, and zero theorem/source-hash intersections with the retirement ledger;
- production eligibility, empty known-proof-collision lists, source identity, bundle reload, and
  formalized/negated target relations all valid for every bundle; and
- exactly seven regular files per bundle (700 total), with no symlinks.

The review run wrote its original-batch summaries under `/tmp/add-50-candidate-staging/` and its
replacement bundles under `/tmp/add-50-candidate-replacements/`. Those were temporary staging
artifacts, not published pool contents; the committed task release and the invariants recorded here
are the durable output. `Erdos564.erdos_564` was also regenerated in an earlier independent pilot
and produced the same two bundle digests.

## Task publication outcome

The approved publication was performed incrementally and fail closed:

1. added exactly the 50 audited target and selection-audit rows;
2. loaded and revalidated all 318 existing bundles without regenerating or renaming them;
3. added only the 100 approved new bundles;
4. built one canonical allowlist from all 418 loaded bundle objects;
5. proved that all 159 old source rows, 318 old task rows, and 2,226 old pool-file hashes are
   unchanged, with a target-set difference of exactly this slate;
6. added exactly 700 pool files in 100 new directories, with zero modified, deleted, or renamed old
   pool files and exactly seven regular files per new directory;
7. validated all 100 new bundles with the compatible validator checkout and reloaded all 418
   bundles through the registry;
8. committed the task release on `codex/add-50-reviewed-problems` as `8dd81458…`; and
9. updated the validator's task pin and policy/documentation counts to the new release.

### Post-publication validation

- The release-critical pool, API, loader, generator, and worker suite passed **82/82** tests.
- In the clean `a8d559d…` release checkout with every dependency at its locked revision, the full
  non-integration suite passed **1,198 tests with 8 deselected and zero failures** in 254.56 seconds.
- The integration runner successfully rebuilt the full 3,267-declaration catalog, then passed
  **7/7 end-to-end cases with 1,199 deselected and zero failures** in 661.78 seconds. This covered
  ten audited live production challenges, both modes of an answer-placeholder target, Comparator
  acceptance and direct rejection paths, numeric answers, and counterexample/refutation handling.
- A fresh PostgreSQL 17 test database applied and validated all migrations V001 through V029. The
  migration-built schema and SQLAlchemy mirror match across **768 objects** (327 columns, 241
  constraints, 108 indexes, 2 domains, 72 enum labels, 9 triggers, and 9 functions).
- `verifier doctor` found every repository, toolchain, executable, and source pin exact and clean,
  and reported the verifier ready. Its host-only production-isolation flag is expectedly false
  when invoked as root; the immutable release-image preflight supplies the required non-root probe.

No release-critical test failed, errored, or timed out. The catalog generated during the
integration run was test evidence rather than a task-source change, so its nondeterministic timing
and local build-cache rendering were not included in the validator release diff.

The selection-audit header retains its earlier global provenance because the 159 retained targets
were not all re-reviewed on 2026-08-24. The 50 new rows carry the current candidate-review evidence,
and `POOL.md` documents that audit boundary. Advancing the global header would require refreshing
PR-touch metadata for every retained row.

The existing rebuild script has a Green-directory naming regression, so the release used the
incremental load/generate/rebuild path. The task commit is published and fetchable from the
configured upstream. Validator publication, queue draining, atomic activation, and production
deployment are tracked as the subsequent release stages.
