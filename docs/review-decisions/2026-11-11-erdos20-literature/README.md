# Literature review of the proposed twenty Erdős additions

Reviewed November 11, 2026, America/New_York (retrieval timestamps are in UTC).
This is an intake literature review, not a binding reward decision or an independent proof verification.

**The batch must remain held: #173 has a full solution matching the selected theorem accepted by erdosproblems.ai.**
The other 19 have no full unconditional solution identified in the sources checked.
Several have solved special cases or conditional results. Calling all 20 “unsolved with no solves” would be incorrect.

This review supersedes the eligibility conclusion for #173 in
[the earlier batch review](../2026-11-11-erdos20/README.md) and its selection record.
It also supplements the other 19 scope notes. Historical records are preserved for audit.
The current twenty-target manifests still contain #173; keeping the validator PR in draft
does not itself remove that target. Exclusion/replacement, new manifests and release checks
are required before activation. No replacement is selected by this review.

For every number, I read its erdosproblems.ai problem and wiki pages, its repository claim
records and public discussion feed, and Bloom's problem, forum and proof-claim pages.
There were 117 successful catalog/wiki/claim-page fetches, plus 20 public AI discussion
responses. The latter contained 17 repository claim records, no user-submitted claims or
comments, and no further cursor. These counts describe this retrieval, not the completeness
of the literature. Number searches were supplemented with topic searches and examination of
the linked primary papers or author claims. Individual reading depth is recorded below.

The AI claim records were served from a corpus captured October 28 at
`17c86a0d4ebe1487a087ceb8c14952a10ec2f2bd`; a fresh page retrieval does not make every
underlying assessment current. In particular, #462's latest paper revision was checked
directly. Catalog labels and AI-authored working notes were treated as leads and status
reports, with the relevant hypotheses and quantifiers checked against our selected
statements. The “no full solution found” findings are bounded negative search results,
not certificates of mathematical openness. MathSciNet, subscription-only reviews, private
work and exhaustive citation-graph searches were not covered.

[review.json](review.json) binds every conclusion to its theorem, exact production type and
hash. [review.csv](review.csv) is the compact table.
[source-manifest.json](source-manifest.json) records retrieval URLs, timestamps, hashes and
access failures. Raw HTML, JSON, downloaded PDFs and saved search results are held locally at
`/root/.codex/worktrees/erdos-next-20/current/output/erdos20-literature-20261111`;
they are not all reproduced in Git. The primary #295 claimant PDF was unavailable (404).
The #389 publisher endpoint was unavailable (403), but the author's paper text was accessible.

| Problem | Topic | Finding |
| --- | --- | --- |
| [#151](https://erdosproblems.ai/problems/151) | Clique transversals | No full solution found; solved graph classes and a finite-order claim. |
| [#159](https://erdosproblems.ai/problems/159) | Four-cycle versus clique Ramsey numbers | No full solution found; the fixed power saving is still missing. |
| [#173](https://erdosproblems.ai/problems/173) | Monochromatic triangles in the plane | EXCLUDE: full matching formal solution accepted by erdosproblems.ai. |
| [#295](https://erdosproblems.ai/problems/295) | Shortest Egyptian-fraction representations | No full solution found; denominator-restricted claims only. |
| [#311](https://erdosproblems.ai/problems/311) | Approximation to one by unit fractions | No full solution found; improved bounds do not give the exponential rate. |
| [#322](https://erdosproblems.ai/problems/322) | Many representations by kth powers | No full solution found; the cubic case is already solved. |
| [#345](https://erdosproblems.ai/problems/345) | Descents of completeness thresholds | No full solution found; individual thresholds and bounds are known. |
| [#388](https://erdosproblems.ai/problems/388) | Equal products of disjoint consecutive blocks | No full solution found; fixed-length results do not give global finiteness. |
| [#389](https://erdosproblems.ai/problems/389) | Divisibility of adjacent products | No full solution found; particular starting values are settled. |
| [#393](https://erdosproblems.ai/problems/393) | Factorizations of factorials in short intervals | No unconditional full solution found; abc gives a conditional answer. |
| [#422](https://erdosproblems.ai/problems/422) | Values omitted by Hofstadter Q | No full solution found; recent solved recurrences are different. |
| [#423](https://erdosproblems.ai/problems/423) | Growth of the Hofstadter consecutive-sum sequence | No full solution found; infinite omissions are proved for a different target. |
| [#430](https://erdosproblems.ai/problems/430) | A composite in the prime-factor sequence | No full solution found; also checked equivalent problem #385. |
| [#461](https://erdosproblems.ai/problems/461) | Distinct smooth parts in intervals | No full solution found; restricted-height bounds and stronger-constant counterexamples. |
| [#462](https://erdosproblems.ai/problems/462) | Least prime factors in short intervals | No unconditional full solution found; current preprint remains conditional. |
| [#467](https://erdosproblems.ai/problems/467) | Two prime-residue coverings | No full solution found for the selected explicit interpretation. |
| [#472](https://erdosproblems.ai/problems/472) | An infinite greedy prime extension | No full solution found; long finite trajectories do not prove infinitude. |
| [#478](https://erdosproblems.ai/problems/478) | Distinct factorial residues | No full solution found; lower-bound improvements are partial. |
| [#483](https://erdosproblems.ai/problems/483) | An exponential upper bound for Schur numbers | No full solution found; a recent AI claim covers restricted colorings. |
| [#545](https://erdosproblems.ai/problems/545) | Ramsey maximizers for a fixed edge count | No full solution found for the eventual version; literal all-size version has counterexamples. |

**#151 — Clique transversals**

Selected target: The exact inequality \(\tau(G)\le n-H(n)\) for every finite graph.

Erdős–Gallai–Tuza bounds and the clique-coloring estimate of Joret–Micek–Reed–Smid give weaker general bounds. The latter yields a transversal bound of the form \(n-c\sqrt{n\log n}\), by taking the complement of a largest color class; this deduction does not recover the exact \(H(n)\). Tuza covers chordal graphs. Veljjanoski's September 2026 submission covers at most 39 vertices and remains a partial claim in the AI catalog.

Sources: [erdosproblems.ai](https://erdosproblems.ai/problems/151); [AI source account](https://erdosproblems.ai/wiki/problems/extremal_graph_theory/E0151); [Bloom](https://www.erdosproblems.com/151); [Joret et al., abstract](https://arxiv.org/abs/2006.11353); [Veljjanoski, claim manuscript](https://github.com/veljjanoski/erdos151/blob/b648f36b34c7ad348a813d14a29e5b521b04c06e/PROOF.md).

Reading depth: Catalog, discussion and claim scopes; Joret et al. abstract. No independent validation of Veljjanoski's proof.

**#159 — Four-cycle versus clique Ramsey numbers**

Selected target: Some fixed \(c>0\) gives \(R(C_4,K_n)=O(n^{2-c})\).

Logarithmic improvements on quadratic growth do not give a fixed power saving. Cambie–Freschi's June 2026 paper on cycle-versus-graph Ramsey numbers explicitly retains this question in its concluding remarks. Its general bound, specialized to a complete graph, is quadratic and does not settle our target.

Sources: [erdosproblems.ai](https://erdosproblems.ai/problems/159); [AI source account](https://erdosproblems.ai/wiki/problems/ramsey_theory/E0159); [Bloom](https://www.erdosproblems.com/159); [Cambie–Freschi, §3](https://arxiv.org/html/2606.11174v1).

Reading depth: Primary theorem statements and concluding remarks, alongside both catalogs.

**#173 — Monochromatic triangles in the plane**

Selected target: In every arbitrary two-coloring of the plane, any two triangles without monochromatic congruent copies are congruent.

The AI catalog accepts Plasma AI's October 19, 2026 claim as full and marks the problem solved. The pinned repository proves Erdos173.erdos_173 with the same arbitrary-coloring and triangle quantifiers as our selected target. I inspected Solution.lean, its definitions, README and comparator configuration, and read the project's replay and statement-fidelity records. Those records report successful compilation, statement comparison and kernel replay with only propext, Classical.choice and Quot.sound. I did not independently rebuild or replay it. The project reports no external referee examination. Bloom's OPEN label is therefore insufficient for eligibility. A stronger one-color-class historical formulation remaining open does not preserve eligibility of this selected theorem.

Sources: [erdosproblems.ai](https://erdosproblems.ai/problems/173); [AI source account](https://erdosproblems.ai/wiki/problems/discrete_geometry/E0173); [Bloom](https://www.erdosproblems.com/173); [Accepted full claim](https://erdosproblems.ai/wiki/problems/discrete_geometry/E0173/claims/2026_10_19_plasma_ai); [Pinned Solution.lean](https://github.com/plasma-ai/erdos-173/blob/4a368a652753fab53272f7a747755876f04bc2cc/Solution.lean); [Project replay receipt](https://erdosproblems.ai/wiki/library/discrete_geometry/plasma_ai_2026_monochromatic_triangles_two_colorings_plane/evidence/verify/replay_receipt); [Project statement-fidelity review](https://erdosproblems.ai/wiki/library/discrete_geometry/plasma_ai_2026_monochromatic_triangles_two_colorings_plane/evidence/verify/statement_fidelity).

Reading depth: Pinned source-level statement comparison and third-party verification records; no local Lean rebuild.

**#295 — Shortest Egyptian-fraction representations**

Selected target: The excess \(k(N)-(e-1)N\) tends to positive infinity for unrestricted distinct denominators at least \(N\).

The Erdős–Straus bounds leave the excess between a constant lower bound and an upper bound of order \(N/\log N\). They do not establish divergence. The September 2026 tienxion claim adds polynomial upper bounds on denominators, so it does not settle the unrestricted minimum. Finite computations of small values also do not decide the limit. The linked claimant PDF returned 404 during this review, so its scope is taken from the author's proof-claim submission, without validating its argument.

Sources: [erdosproblems.ai](https://erdosproblems.ai/problems/295); [AI source account](https://erdosproblems.ai/wiki/problems/unit_fractions/E0295); [Bloom](https://www.erdosproblems.com/295); [Erdős–Straus 1971](https://doi.org/10.2307/2317539); [Author's partial proof claim](https://www.erdosproblems.com/forum/thread/295/proof-claims); [Unavailable linked manuscript](https://github.com/tienxion/erdos/blob/main/erdos295/erdos295_reviewed_lower_bounds.pdf).

Reading depth: Catalog bibliography and author claim scope; historical paper not independently audited and claimant PDF unavailable.

**#311 — Approximation to one by unit fractions**

Selected target: For the least nonzero error \(\delta(N)\), the limit \(-\log\delta(N)/N\) exists in \((0,1)\).

Tang's January 15, 2026 note states an upper bound \(\exp[-c_0N/((\log N)^3(\log\log N)^3)]\). This does not give a fixed positive limiting exponential rate. I read the downloaded note's formulation and bound. It uses the older restriction excluding a subset summing to one; the selected source records Kovač's equivalence with the minimum-nonzero-error formulation. Neither the bound nor that reformulation resolves the target.

Sources: [erdosproblems.ai](https://erdosproblems.ai/problems/311); [AI source account](https://erdosproblems.ai/wiki/problems/unit_fractions/E0311); [Bloom](https://www.erdosproblems.com/311); [Tang's v2 note](https://github.com/QuanyuTang/erdos-problem-311-note/blob/main/On_Erd%C5%91s_Problem_311_v2.pdf); [Formulation discussion](https://www.erdosproblems.com/forum/thread/311).

Reading depth: Downloaded primary note, introduction and theorem scope; discussion and selected Lean definition.

**#322 — Many representations by kth powers**

Selected target: For every \(k\ge3\), some \(c>0\) gives more than \(n^c\) representations as a sum of \(k\) kth powers for infinitely many \(n\).

Mahler's 1936 construction gives polynomially many representations for cubes, including a bound of order \(n^{1/12}\) on twelfth powers. Bloom's exposition derives the construction and explains the unresolved higher-power cases. The selected theorem quantifies over every \(k\ge3\); a solution at \(k=3\) does not resolve it. This is a concrete reason to avoid saying that the candidates have no solved cases.

Sources: [erdosproblems.ai](https://erdosproblems.ai/problems/322); [AI source account](https://erdosproblems.ai/wiki/problems/diophantine_problems/E0322); [Bloom](https://www.erdosproblems.com/322); [Mahler, Note on Hypothesis K](https://doi.org/10.1112/jlms/s1-11.2.136); [Bloom's derivation of Mahler's construction](https://www.thomasbloom.org/notes/hypothesisk.html).

Reading depth: Author's mathematical exposition with explicit construction; catalog claim scope.

**#345 — Descents of completeness thresholds**

Selected target: There are infinitely many exponents at which the least completeness threshold for distinct kth powers decreases.

Wiener (2023) determines the threshold for eighth powers and provides lower bounds for exponents 9–16. General upper bounds and congruence lower bounds do not establish infinitely many adjacent descents. Numerical threshold conventions need care: the largest nonrepresentable integer is one less than the least threshold used in our target; this shift does not affect comparisons between adjacent exponents.

Sources: [erdosproblems.ai](https://erdosproblems.ai/problems/345); [AI source account](https://erdosproblems.ai/wiki/problems/additive_bases/E0345); [Bloom](https://www.erdosproblems.com/345); [Wiener, JIS 26 (2023), introduction and Table 1](https://cs.uwaterloo.ca/journals/JIS/VOL26/Wiener/wiener3.pdf); [Current discussion](https://www.erdosproblems.com/forum/thread/345).

Reading depth: Primary abstract, definitions and threshold table; discussion scope.

**#388 — Equal products of disjoint consecutive blocks**

Selected target: Only finitely many quadruples of starting points and block lengths greater than three have equal products.

The literature distinguishes finiteness for each fixed pair of lengths from finiteness when both lengths vary. MacLeod–Barrodale and later Beukers–Shorey–Tijdeman results cover restricted or fixed-length settings. The 2026 forum claim likewise addresses fixed lengths; discussion identifies problems with its argument and notes the existing result. Even a correct proof at each fixed pair would not establish finiteness of the union over all lengths.

Sources: [erdosproblems.ai](https://erdosproblems.ai/problems/388); [AI source account](https://erdosproblems.ai/wiki/problems/diophantine_problems/E0388); [Bloom](https://www.erdosproblems.com/388); [MacLeod–Barrodale, On equal products of consecutive integers](https://doi.org/10.4153/CMB-1970-052-8); [Claim and mathematical responses](https://www.erdosproblems.com/forum/thread/388).

Reading depth: Primary abstract and current author/reviewer discussion; fixed-versus-variable quantifier comparison.

**#389 — Divisibility of adjacent products**

Selected target: For every \(n\ge1\), some positive block length makes the first block product divide the next.

Ulas (2013), with Schinzel's appendix, gives a digit-sum criterion, infinitely many witnesses at particular small starting values, and computational witnesses for further values. The paper explicitly does not solve the all-starting-values problem. Its starting-point convention is shifted by one relative to ours. More recent forum computations extend finite ranges, which cannot establish the universal quantifier.

Sources: [erdosproblems.ai](https://erdosproblems.ai/problems/389); [AI source account](https://erdosproblems.ai/wiki/problems/diophantine_problems/E0389); [Bloom](https://www.erdosproblems.com/389); [Ulas's paper, author-hosted text](https://www.academia.edu/48555588/A_Note_on_Erd%C5%91s_Straus_and_Erd%C5%91s_Graham_Divisibility_Problems_With_an_Appendix_by_Andrzej_Schinzel_); [Publication record](https://ruj.uj.edu.pl/entities/publication/f86b128d-6237-42f6-9175-82e1a877766b); [Current computations](https://www.erdosproblems.com/forum/thread/389).

Reading depth: Primary introduction and Theorem 2.2; forum computations. Publisher endpoint returned 403; author-hosted text was accessible.

**#393 — Factorizations of factorials in short intervals**

Selected target: The least span of a factorization of \(n!\) into at least two distinct positive integers tends to infinity.

Luca (2002) proves finiteness for polynomial-factorial equations assuming abc, which implies the selected conclusion under that conjecture. Bui–Pratt–Zaharescu give unconditional power savings in counting solutions, not the required eventual absence for every bounded span. The 2026 Turturean claim also assumes abc. None removes the hypothesis from our theorem.

Sources: [erdosproblems.ai](https://erdosproblems.ai/problems/393); [AI source account](https://erdosproblems.ai/wiki/problems/factorials_binomials/E0393); [Bloom](https://www.erdosproblems.com/393); [Luca, published abstract](https://web.math.pmf.unizg.hr/glasnik/vol_37/no2_04.html); [Bui–Pratt–Zaharescu](https://arxiv.org/abs/2204.08423); [Conditional claims](https://www.erdosproblems.com/forum/thread/393/proof-claims).

Reading depth: Primary abstracts and conditional claim scopes; selected definition of the minimum span.

**#422 — Values omitted by Hofstadter Q**

Selected target: Every positive-valued sequence satisfying the selected original Hofstadter-Q predicate omits infinitely many integers.

Cloitre's July 2026 revision proves totality and asymptotics for the Mantovanelli perturbation, which adds \((-1)^n\) to the recurrence. Related 2026 papers also study perturbed or changed initial conditions. They do not settle the original Q recurrence's omitted-value question. The selected theorem is an implication for a sequence satisfying its predicate; it should not be advertised as separately proving existence or totality of the original recurrence.

Sources: [erdosproblems.ai](https://erdosproblems.ai/problems/422); [AI source account](https://erdosproblems.ai/wiki/problems/integer_sequences/E0422); [Bloom](https://www.erdosproblems.com/422); [Cloitre, v3 abstract](https://arxiv.org/abs/2604.06237v3); [Original problem and discussion](https://www.erdosproblems.com/forum/thread/422).

Reading depth: Primary latest abstract compared with selected recurrence and catalog discussion.

**#423 — Growth of the Hofstadter consecutive-sum sequence**

Selected target: The greedy sequence satisfies \(a_n=n+o(n)\).

Tang's March 2026 paper proves infinitely many omitted integers and gives \(n+\Omega(\log\log n)\le a_n\ll n^{4175/2506+o(1)}\). These statements do not determine \(a_n/n\to1\). Bolan's independent qualitative result and the associated solved formal variants also concern omissions, not the selected asymptotic. The distinct Hofstadter Q problem is #422.

Sources: [erdosproblems.ai](https://erdosproblems.ai/problems/423); [AI source account](https://erdosproblems.ai/wiki/problems/integer_sequences/E0423); [Bloom](https://www.erdosproblems.com/423); [Tang, v2 abstract](https://arxiv.org/abs/2603.09939v2); [Claims and independent result](https://www.erdosproblems.com/forum/thread/423/proof-claims).

Reading depth: Primary latest abstract and scope of both recorded claims.

**#430 — A composite in the prime-factor sequence**

Selected target: For all sufficiently large inputs, the selected prime-factor-defined sequence has a composite term.

The selected terms explicitly exceed one; including one would trivialize a literal reading of the problem. With that correction, the question is equivalent to the first part of #385. Tao's 2024 analysis identifies a sufficient bound on gaps between balanced semiprimes and the parity obstruction; it supplies no unconditional solution. Current #385 and #430 records and indexed searches yielded no matching full proof. Finite checks cannot establish the eventual assertion.

Sources: [erdosproblems.ai](https://erdosproblems.ai/problems/430); [AI source account](https://erdosproblems.ai/wiki/problems/integer_sequences/E0430); [Bloom](https://www.erdosproblems.com/430); [Tao's original analysis](https://terrytao.wordpress.com/2024/08/19/erdos-problem-385-the-parity-problem-and-siegel-zeroes/); [Equivalent question #385](https://erdosproblems.ai/wiki/problems/arithmetic_functions/E0385); [Current #430 discussion](https://www.erdosproblems.com/forum/thread/430).

Reading depth: Primary mathematical analysis, both equivalent catalog formulations, and selected terms definition.

**#461 — Distinct smooth parts in intervals**

Selected target: A single positive constant \(c\) gives \(f(n,t)\ge ct\) for all shifts and lengths.

The classical logarithmically weaker estimate and the forum's bounds with restricted interval height do not supply a uniform positive proportion. The construction challenging the constant one-half does not rule out every positive constant. The AI page's finite-period reduction allows a finite computation at each fixed length but provides no bound uniform in all lengths. No full claim was present in the checked claim feeds.

Sources: [erdosproblems.ai](https://erdosproblems.ai/problems/461); [AI source account](https://erdosproblems.ai/wiki/problems/primes/E0461); [Bloom](https://www.erdosproblems.com/461); [Authors' discussion and partial arguments](https://www.erdosproblems.com/forum/thread/461); [Erdős–Graham source bibliography](https://www.erdosproblems.com/461).

Reading depth: Current primary forum arguments and catalog source account; no independently audited new paper found.

**#462 — Least prime factors in short intervals**

Selected target: An eventual positive lower bound for the selected interval sum, which includes primes.

Zhang's arXiv v2, revised October 21, 2026, treats composite-only sums. Its Theorem 1.7 assumes a bounded-fluctuation prime-distribution hypothesis; its abstract explicitly leaves uniformity open unconditionally. A valid composite-only lower bound would imply our inclusive target, but an unproved hypothesis remains. The AI review discusses an older version: its criticism of a stronger, Maier-incompatible hypothesis must not be copied onto v2 as though the statement were unchanged. I have not independently verified the preprint's arguments.

Sources: [erdosproblems.ai](https://erdosproblems.ai/problems/462); [AI source account](https://erdosproblems.ai/wiki/problems/primes/E0462); [Bloom](https://www.erdosproblems.com/462); [Zhang v2, abstract and Theorem 1.7](https://arxiv.org/html/2608.24930v2); [Source interpretation discussion](https://www.erdosproblems.com/forum/thread/462).

Reading depth: Latest primary version, hypothesis and theorem statements; compared against older AI assessment and selected inclusive sum.

**#467 — Two prime-residue coverings**

Selected target: For every sufficiently large bound, choose residues and partition the primes into two groups, each covering every smaller positive integer.

The historical source is ambiguous about quantifiers. Our selected theorem uses the displayed existential choice of residues and prime partition, followed by coverage of all integers in range. The checked records contained no full proof or counterexample for that interpretation. Finite obstructions do not refute a statement allowing all small bounds to be exceptional. Related one-cover and multiple-cover problems do not automatically provide the required partition.

Sources: [erdosproblems.ai](https://erdosproblems.ai/problems/467); [AI source account](https://erdosproblems.ai/wiki/problems/integer_sequences/E0467); [Bloom](https://www.erdosproblems.com/467); [Source ambiguity and chosen formulation](https://www.erdosproblems.com/467); [Finite-obstruction working report](https://www.erdosproblemaday.com/report/467).

Reading depth: Selected formal quantifiers, original-source account, current discussion and author-reported finite obstruction scope.

**#472 — An infinite greedy prime extension**

Selected target: Some finite prime seed produces an infinite trajectory under the least-prime extension rule.

Syn's July 2026 research brief explicitly claims no solution. It reports a million steps from seed 7 and arbitrary finite survival with the seed depending on the requested length. The quantifiers differ: a seed for every finite length does not establish a single finite seed surviving forever. Its Lean development also leaves its analytic input explicit. No matching full claim appeared in the checked records or searches.

Sources: [erdosproblems.ai](https://erdosproblems.ai/problems/472); [AI source account](https://erdosproblems.ai/wiki/problems/integer_sequences/E0472); [Bloom](https://www.erdosproblems.com/472); [Author's technical research brief](https://maths.syn.gl/research-brief.html); [Current discussion](https://www.erdosproblems.com/forum/thread/472).

Reading depth: Primary project status and mathematical quantifiers; no recomputation of the million-step claim.

**#478 — Distinct factorial residues**

Selected target: The number of factorial residues modulo prime \(p\) is asymptotic to \((1-1/e)p\).

Grebennikov–Sagdeev–Semchankau–Vasilevskii prove a square-root lower bound with constant tending to \(\sqrt2\). Hu's July 2026 manuscript claims \(\gg p^{8/15}\), still far below the required linear asymptotic; I read its abstract and main theorem in the pinned PDF. Abramov's September 2026 revision, despite its 'Solution' title, addresses whether all residues from 2! through (p−1)! can be distinct, a different question. Its 2025 predecessor was withdrawn. Neither assertion would imply this asymptotic.

Sources: [erdosproblems.ai](https://erdosproblems.ai/problems/478); [AI source account](https://erdosproblems.ai/wiki/problems/factorials_binomials/E0478); [Bloom](https://www.erdosproblems.com/478); [Grebennikov et al.](https://arxiv.org/abs/2204.01153); [Hu, pinned manuscript](https://github.com/hxypqr/factorial-residues-mod-prime/blob/48bd9059408858b01097e3cf43774193d3151298/paper/hu_factorial_residues.pdf); [Abramov's distinctness claim, v7](https://arxiv.org/abs/2604.26429v7); [Withdrawn 2025 predecessor](https://arxiv.org/abs/2504.19392).

Reading depth: Primary abstracts and Hu's main theorem; no independent proof check of Hu or Abramov.

**#483 — An exponential upper bound for Schur numbers**

Selected target: A fixed exponential bound in the number of colors forces a monochromatic solution to \(a+b=c\), with equal summands allowed.

The October 2026 Plasma AI claim gives an exponential bound only when each color class lies in a single sum-free Bohr arc; it is cataloged as partial and unreviewed. Hegde–Lott–Petridis–Ponagandla's August 2026 bound specializes at the Schur parameter to \(3^r r!\), which is not a fixed exponential. Recent lower bounds and fixed-color computations likewise do not settle the target. Bounds for multicolor Ramsey numbers in regimes of growing clique size cannot be substituted for a fixed triangle or Schur problem.

Sources: [erdosproblems.ai](https://erdosproblems.ai/problems/483); [AI source account](https://erdosproblems.ai/wiki/problems/ramsey_theory/E0483); [Bloom](https://www.erdosproblems.com/483); [Plasma AI's restricted-coloring claim](https://erdosproblems.ai/wiki/problems/ramsey_theory/E0483/claims/2026_10_19_plasma_ai); [Hegde et al., abstract](https://arxiv.org/abs/2608.03661); [Yang–Mao, latest version](https://arxiv.org/abs/2608.01962).

Reading depth: Exact AI claim restrictions and primary 2026 abstract/theorem parameter ranges.

**#545 — Ramsey maximizers for a fixed edge count**

Selected target: For every sufficiently large edge count, the clique-plus-extra-vertex graph maximizes the diagonal Ramsey number among graphs without isolated vertices.

Small-edge counterexamples defeat an all-edge-count reading but not our eventual quantifier. The 1989 Burr–Erdős–Faudree–Schelp theorem evaluates certain clique-plus-extra-vertex Ramsey numbers; it does not compare them with every graph of equal size. Sudakov's general bound in terms of the square root of the edge count also does not identify the exact maximizer. The AI catalog rejects the small-counterexample submission as a resolution of the amended eventual statement, not as invalidating the counterexamples themselves.

Sources: [erdosproblems.ai](https://erdosproblems.ai/problems/545); [AI source account](https://erdosproblems.ai/wiki/problems/ramsey_theory/E0545); [Bloom](https://www.erdosproblems.com/545); [Burr et al., Theorem 4](https://users.renyi.hu/~p_erdos/1989-21.pdf); [Sudakov](https://arxiv.org/abs/1002.0095); [Small counterexample claim](https://www.erdosproblems.com/forum/thread/545/proof-claims).

Reading depth: Primary theorem statement and abstract, submitted counterexample scope, and selected eventual quantifier.
