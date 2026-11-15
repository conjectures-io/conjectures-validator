# Literature status: limit cycles of quadratic vector fields

Reviewer model: `claude-fable-5-2`. Fresh-context subagent of the authoring session; it received only the brief below and the files it names.

## Brief 1 (2026-11-15T21:51:19.587Z)

```text
Research task (web search required; today's date is 2026-11-15). I need the CURRENT research status of one precise mathematical statement, so that a prize platform can decide whether it is still an open problem. Be skeptical and cite sources with URLs and dates.

Statement S: "Every real planar polynomial vector field of degree at most 2 (a quadratic system x' = P(x,y), y' = Q(x,y)) has at most 4 limit cycles", i.e. the Hilbert number H(2) ≤ 4 (with the known lower bound this would give H(2) = 4). Here a limit cycle is a non-constant periodic orbit that is isolated among periodic orbits.

Please establish, with sources:
1. The standard statement and attribution of the conjecture H(2) = 4, and the known lower bound (Shi Songling 1980 / Chen Lansun–Wang Mingshu 1979, the (3,1) configuration). Confirm whether any quadratic system with 5 or more limit cycles has ever been published or claimed.
2. Known partial results (e.g. Bamón 1986 finiteness for each quadratic field; Zhang Pingguang's results on (3,1)/(2,2) distributions and at most two limit cycles around one of two foci; any others) — just enough to show what is and isn't proved.
3. Most important: any claimed full proof or disproof of H(2) ≤ 4 or H(2) = 4, in journals or arXiv or elsewhere, through November 2026. Search specifically for 2024, 2025 and 2026 claims. I am aware of an OpenAI manuscript dated September 24, 2026, "Uniform bounds for planar polynomial limit cycles" (in the GitHub repository openai/math under preprints/), which claims existence of a finite bound B(d) for every degree d; check (by reading its abstract/introduction if you can fetch it — try https://github.com/openai/math/tree/main/preprints/uniform-bounds-for-planar-polynomial-limit-cycles-September-24-2026 and the raw PDF there) whether it claims any explicit value or explicit numerical bound for degree 2, and in particular whether it claims or implies H(2) ≤ 4. Also check the follow-up arXiv preprints 2610.12218 (Qin and Sun, lower bound) and 2610.33114 (Chen, Xiao, Zhang) for any statement about H(2).
4. Any AI-generated or formal (Lean/Coq/Isabelle) proof claims about quadratic systems' limit cycles in 2025–2026.

Report format: a short verdict line ("OPEN as of <date>" / "CLAIMED RESOLVED by ..." / "UNCERTAIN because ..."), then numbered findings each with source URL, date, and one-sentence relevance. Clearly separate what you verified by actually opening a source from what you only saw in search snippets. If a source could not be fetched, say so rather than guessing. Do not write any files.
```

## Brief 2 (2026-11-15T22:10:38.007Z)

```text
The coordinator sent a message while you were working:
Follow-up on the Pedregal preprint arXiv:2103.07193v4 (3 Nov 2026) that you already read: I need a closer, factual assessment of whether it can be treated as an established proof of H(2) ≤ 4.
Please answer from the PDF you have (re-open it; quote theorem statements verbatim with page/section numbers):
1. The exact statement of the main theorem(s) and of Corollary 1.5. In the bound you reported, H(n) ≤ 1 + (n−1)²(M+N), what exactly are M and N — constants, or quantities depending on n or on the system? What explicit function of n does the paper claim as an upper bound for H(n) (give the formula and its growth order in n)?
2. Compare that claimed upper bound with the established lower bounds for H(n): Christopher–Lloyd 1995 (H(n) ≥ c·n² log n), Li–Chan–Chung / Han–Li 2012 ((n+2)² ln(n+2)/(2 ln 2) type bounds), and the specific small-degree records (e.g. H(3) ≥ 13, H(4) ≥ 28 or whatever the current verified records are — check and cite). Does the paper's general bound contradict any established lower bound for some specific n (give the smallest n where the claimed upper bound is below a published lower bound, with the numbers), or is it consistent with them? If it contradicts, say plainly that the paper's main theorem is false as stated and explain whether the n = 2 corollary depends on the same argument.
3. How Corollary 1.5 (H(2) = 4) is derived: is it a direct specialization of the general bound at n = 2, or does it use a separate argument?
4. What changed between v3 (Aug 2024) and v4 (Nov 2026) per the arXiv comments/abstract, the stated reason for the v2 withdrawal, and whether the paper is published or cited as established anywhere (zbMATH/MathSciNet/Google Scholar citing works that accept or dispute it).
5. Your "parity reversed" observation about Harnack's theorem: state precisely where it occurs and what it affects.
Report facts and verbatim quotes first, your own inferences clearly labelled second. Keep it concise. Do not write any files.

Address this before completing your current task.
```

## Report 1 (2026-11-15T22:08:58.364Z)

**Verdict: OPEN as of 2026-11-15.** No accepted or independently verified proof or disproof of H(2) ≤ 4 exists. One live, unrefereed preprint does claim exactly statement S (Pedregal, arXiv:2103.07193v4, 3 Nov 2026), so the platform should treat that as an unverified prior claim. The OpenAI manuscript does not claim or imply H(2) ≤ 4.

## A. Verified by opening the source

**1. Statement, lower bound, and 5+ cycle claims**

1. Yu–Zeng, arXiv:2002.09987 (23 Feb 2020), https://arxiv.org/abs/2002.09987 — states that Shi and Chen–Wang examples give H(2) ≥ 4 and "the question whether H(2) = 4 is still open."
2. zbMATH records, https://zbmath.org (queried 2026-11-15) — confirm the citations: Chen Lansun and Wang Mingshu, Acta Math. Sin. 22, 751–758 (1979); Shi Songling, Sci. Sin. 23, 153–158 (1980).
3. Zegeling slides, AQTDE 2023 (Feb 2023), https://www.gsd.uab.cat/aqtde2023/data/uploads/slides/3-zegeling.pdf — "Currently: H(2) ≥ 4"; the first examples had a (p ≥ 3, q ≥ 1) distribution, and (3,1) is the configuration found.
4. Galias–Tucker, Appl. Math. Comput. 415 (2022), https://research.monash.edu/en/publications/the-songling-system-has-exactly-four-limit-cycles/ — computer-assisted proof that Shi's system has exactly four limit cycles.
5. Gasull–Santana, arXiv:2407.13465v2 (1 Oct 2024), https://arxiv.org/abs/2407.13465 — "the best lower bounds so far are" H(2) ≥ 4, and "Even for the quadratic case, it is not known if H(2)<∞."
6. Chicone's zbMATH review of Żołądek (2000), https://zbmath.org/?q=an:1512051 — opens with "Is there a quadratic system with more than four limit cycles?"; the paper finds evidence the bound is four and produces no five-cycle example.
7. No quadratic system with 5 or more limit cycles turned up in arXiv API queries, zbMATH queries or web searches. The zbMATH hits for "five limit cycles" were systems in R³, cubic perturbations, or upper bounds.
8. Shi's 1988 paper (Bull. LMS 20, 597–599), zbMATH abstract https://zbmath.org/?q=an:4052045 — an earlier claimed proof of "at most 4" by Qin Yuanxun was refuted: "we give a counterexample to two results used by Qin Yuanxun in his published proof."
9. Wikipedia, last edited 7 Oct 2026, https://en.wikipedia.org/wiki/Hilbert%27s_sixteenth_problem — the bound is unsolved for n > 1; quadratic fields with four cycles are known; Petrovsky–Landis claim was shown wrong.

**2. Partial results**

10. Bamón, Publ. Math. IHÉS 64 (1986) 111–142, http://www.numdam.org/item/PMIHES_1986__64__111_0/ — each quadratic field has finitely many limit cycles; this gives no uniform bound.
11. Zhang Pingguang, Acta Math. Sin. 44 (2001) and Qual. Theory Dyn. Syst. 3 (2002) 437–463, https://portal.mardi4nfdi.de/wiki/Item:Q1431480 plus zbMATH summary — "at most one limit cycle around one of the two foci", so distributions are (0,i) or (1,i). This excludes (2,2). Your brief said "at most two"; the sources say one.
12. Zegeling, Adv. Nonlinear Anal. 2024 (DOI 10.1515/anona-2024-0012), zbMATH review https://zbmath.org/?q=an:7888720 — re-proves the (n,0)/(n,1) property, aiming "to simplify and fill gaps" in Zhang's proof.
13. Zegeling's slides (item 3) reduce the problem to H(2) = max{p1 + 1, p2}; what is unproved is the bound on cycles within a single nest.
14. zbMATH records for single-nest results: Zhang 1999 (at most one cycle around a second-order weak focus) and Li Chengzhi 1986 (none around a third-order weak focus).
15. Dumortier–Roussarie–Rousseau, J. Differ. Equations 110 (1994), zbMATH review — the problem "remains open even for" quadratic fields; their program targets existence of a uniform bound, not the value 4.

**3. Claimed proofs or disproofs**

16. OpenAI, "Uniform bounds for planar polynomial limit cycles" (dated 24 Sep 2026; sole commit 2026-10-06), https://github.com/openai/math/tree/main/preprints/uniform-bounds-for-planar-polynomial-limit-cycles-September-24-2026 — I extracted the full 160-page PDF text.
    - Theorem 1.1 asserts only that a finite B(d) exists for each d.
    - The introduction says: "It does not furnish an effective formula for B(d)."
    - The only quadratic mention is Bautin's local bound of three; there is no H(2), B(2) or numeric bound anywhere.
    - If correct it gives H(2) < ∞ and nothing more.
    - There is no withdrawal or fix entry for it in the repo's history.md (latest entry 29 Oct 2026).
17. Pedregal, arXiv:2103.07193v4 (3 Nov 2026), https://arxiv.org/abs/2103.07193 — PDF read; this is the one live claim of S.
    - Corollary 1.5 states: "At any rate, H(2) = 4", derived from a claimed general bound H(n) ≤ 1 + (n−1)²(M+N).
    - It has no journal reference and I found no independent check or discussion.
    - Version history: v1 12 Mar 2021, v2 withdrawn 29 Aug 2024, v3 30 Aug 2024, v4 3 Nov 2026.
    - Predecessors: arXiv:1411.6814 was withdrawn "due to a mistake in the way we counted limit cycles"; arXiv:1904.01292 was withdrawn in 2020.
    - My own reading, not a referee report: the general-degree bound applies Harnack's theorem with the parity reversed relative to the paper's own statement. This does not touch the n = 2 corollary.
18. Gaiko, arXiv:math/0611142 (6 Nov 2006), https://arxiv.org/abs/math/0611142 — claims the maximum is four with only a (3:1) distribution. The zbMATH review of his 2003 book says the program "is presented mostly in the form of suggestions and a more solid proof of its efficiency is required."
19. da Silva–Vieira–Leonel, Entropy 26(9):745 (30 Aug 2024), https://pmc.ncbi.nlm.nih.gov/articles/PMC11431191/ — claims a maximum of 2(n−1)(4(n−1)−2), which is 4 at n = 2.
20. Buzzi–Novaes, arXiv:2411.09594 (14 Nov 2024), https://arxiv.org/abs/2411.09594 — refutes the Entropy formula for large degree. The authors defended it in arXiv:2412.01916 (2 Dec 2024).
21. Qin–Sun, arXiv:2610.12218v1 (8 Oct 2026), https://arxiv.org/abs/2610.12218 — PDF read: "The exact values of H(d) remain unknown, even for H(2)"; its table lists degree 2 with lower bound 4 [Shi80].
22. Chen–Xiao–Zhang, arXiv:2610.33114v1 (23 Oct 2026), https://arxiv.org/abs/2610.33114 — PDF read: a Rayleigh–Liénard uniqueness paper with no statement about H(2) or quadratic systems. It says the OpenAI preprint's "arguments call for careful independent verification."
23. arXiv API queries (run 2026-11-15) for "H(2)", "at most four limit cycles", "Hilbert number" and quadratic limit-cycle titles since mid-2024 returned no other claim.

**4. AI-generated or formal claims, 2025–2026**

24. OpenAI Lean scope note, https://github.com/openai/math/blob/main/lean/docs/143.md — Lean covers only the quintic Liénard result (at most two cycles); the uniform bound is not formalized and nothing concerns quadratic systems.
25. Qin–Sun (item 21) discloses GPT-5.6 Sol and Lean 4 certification, but only for an asymptotic lower bound Ω(d² ln² d).
26. Haibo Lu, arXiv:2607.13785v3 (26 Aug 2026), https://arxiv.org/abs/2607.13785 — Codex-assisted; proves local finite cyclicity of one quadratic graphic, "existential and is not claimed to be sharp"; not a bound on H(2).
27. VibeMathed entry (added 7 Oct 2026), https://vibemathed.com/problem/hilbert-16-uniform-bound-limit-cycles — lists the OpenAI result as "Unreviewed" and says it "says nothing about the value of H(2)."
28. ProofAtlas (last checked 13 Aug 2026), https://www.proofatlas.ai/collaboration/hilberts-sixteenth-problem/ — found no proof-assistant formalization of the classical problem.
29. I found no Lean, Coq or Isabelle proof of any upper bound on H(2).

## B. Seen only in search snippets

- Petrovskii–Landis (1955) specifically claimed "at most 3"; Wikipedia confirmed only that their claim was wrong.
- Chengzhi Li's 2025 survey in Mathematical Theory and Applications 45(2) exists; its treatment of H(2) is unconfirmed.
- No named-expert assessment of the OpenAI limit-cycle manuscript surfaced.

## C. Could not fetch

- MDPI Entropy page (403; PMC mirror used).
- De Gruyter article for Zegeling 2024 and DOAJ (blocked; zbMATH review used).
- Springer page for Zhang 2002 (login redirect).
- ScienceDirect page for Gaiko 2008 (403).
- Wiley page for Shi 1988 (403).
- Li's survey page (404).
- Cambridge PDF of Llibre–Schlomiuk (DNS failure), so I have no opened source for the exact wording or attribution of the "H(2) = 4" conjecture beyond items 1 and 18.

No project files were written. PDFs and extracted text are only in `<scratch>`.

## Report 2 (2026-11-15T22:17:51.898Z)

**Bottom line:** the Pedregal preprint cannot be treated as an established proof of H(2) ≤ 4, but it is not refuted either. Its general bound is consistent with every published lower bound, and the H(2) = 4 corollary rests entirely on the unverified main theorem.

All quotes are from the v4 PDF (https://arxiv.org/abs/2103.07193v4); PDF pages match printed pages.

## Facts and verbatim quotes

**1. Statements**

- **Theorem 1.1 (§1, pp. 2–3):** "Consider the polynomial differential system (1.1) of degree n > 1. Assume that: (1) P and Q have no common non-trivial factor. (2) All of the connected components of the algebraic curve Px + Qy = 0 are homeomorphic to a straight line or to an oval (i.e. such curve has no singular points), and that there are M of such components. (3) The polynomial system (1.2) P(Pxx + Qyx) + Q(Pxy + Qyy) = 0, Px + Qy = 0, only has N simple solutions (i.e. N simple contact points of the vector field (P, Q) with the curve Px + Qy = 0). Then an upper bound for the number H(n) of limit cycles that such a differential system (1.1) may have is (1.3) H(n) ≤ 1 + (n − 1)²(M + N)."
- **M and N** are properties of the individual system, not constants: the number of components of the divergence curve and the number of contact points (restated in §5, p. 26, as Theorem 5.1).
- **Theorem 1.2 (p. 3):** "H(n) ≤ (5/2)n⁴ − (23/2)n³ + (43/2)n² − (37/2)n + 7 if n is even, and H(n) ≤ (5/2)n⁴ − (23/2)n³ + (41/2)n² − (33/2)n + 6 if n is odd." Growth is quartic, about 2.5·n⁴. It is obtained from (1.3) with N ≤ 2(n−1)² (Bezout) and a Harnack bound on M (p. 4).
- **Corollary 1.5 (§1.1, p. 5):** "If the divergence of a quadratic polynomial differential system (1.1) is constant or zero, then it has no limit cycles. Otherwise if the straight line Div = 0 of a quadratic polynomial differential system (1.1) has: (a) either one or two contact points, then it cannot have more than 4 limit cycles. (b) no contact points, then it has no limit cycles. At any rate, H(2) = 4."

**2. Comparison with lower bounds: consistent, no contradiction at any n**

| n | Claimed upper bound | Published lower bound |
|---|---|---|
| 2 | 4 | 4 (Shi 1980) |
| 3 | 33 | 13 (Li–Liu–Yang 2009) |
| 4 | 181 | 28 (Prohens–Torregrosa 2019) |
| 5 | 561 | 37 |
| 6 | 1426 | 53 |
| 7 | 2953 | 74 |
| 8 | 5587 | 96 |
| 10 | 15472 | 142 |

- The records for n = 2–8 are from the Qin–Sun table (arXiv:2610.12218, 8 Oct 2026); n = 3, 4 and 10 are confirmed by zbMATH reviews.
- Asymptotic lower bounds, per zbMATH reviews: Christopher–Lloyd 1995 gives order n² log n; Han–Li 2012 gives H(m) ≥ m² for m ≥ 3 (m ≠ 6, 14) and growth at least (m+2)² ln(m+2)/(2 ln 2).
- I checked numerically that the quartic exceeds the Han–Li expression for every n from 3 to 200,000, and the degree-(2^k − 1) sequence quoted by Buzzi–Novaes for k up to 199.
- The paper itself cites Christopher–Lloyd and Han–Li (refs [12], [22]).

**3. Derivation of Corollary 1.5**

It is a direct specialization of (1.3), with no separate argument. Proof, p. 5: "So using (1.3), we have n = 2, M = 1, N ∈ {0, 1, 2}. If N = 2, contact points are simple, and then 1 + (n − 1)²(M + N) = 1 + 1 + 2 = 4. If N = 1, the unique contact point has multiplicity two, and the upper bound is the same." The N = 0 and constant-divergence cases use Bendixson; the lower bound cites Chen–Wang and Shi.

**4. Version history and status**

- **v2 (29 Aug 2024, 1 KB):** marked "(withdrawn)", "This paper has been withdrawn by Pablo Pedregal". The comment reads: "Given that the proof largely comes from tools that are not typical in Dynamical Systems, a special effort must be made at transparency". No error is admitted.
- **v3 (30 Aug 2024, 69 pp.):** titled "A variational approach to Hilbert's 16th problem within the framework of global analysis".
- **v4 (3 Nov 2026, 46 pp.):** retitled "Hilbert's 16th problem through Morse theory", with no comment field. The abstract swaps "global analysis" for "Morse theory" and adds the OpenAI sentence; p. 44 adds "No AI tools whatsoever have been used in this work."
- **v3 versus v4:** Theorems 1.1, 1.2, 1.4 and Corollary 1.5 are identically worded in both. I did not diff the proofs.
- **Publication:** no journal reference or DOI on any version. zbMATH indexes it only as an arXiv preprint and lists the predecessors (arXiv:1411.6814, 1904.01292, 1904.01300) as WITHDRAWN.
- **Citations:** Semantic Scholar shows 0 citations. It is not cited in the OpenAI manuscript, Qin–Sun, Chen–Xiao–Zhang, Gasull–Santana, Buzzi–Novaes, or the two Haibo Lu preprints. I found no work accepting or disputing it. MathSciNet and Google Scholar were not accessible, and OpenAlex was rate-limited.

**5. The Harnack parity point (§1.1, p. 4)**

- Theorem 1.4 as stated: "The maximum number of connected components of an algebraic curve of degree k is (a) 1 + (k − 1)(k − 2)/2 if k is even, (b) (k − 1)(k − 2)/2 if k is odd."
- The next proof then says: "M ≤ (1/2)(n−2)(n−3)+1, if n is even, and M ≤ (1/2)(n − 2)(n − 3), if n is odd. Note that k = n − 1."
- Since k = n − 1 has the opposite parity to n, the "+1" is applied to the opposite case from the paper's own Theorem 1.4.
- Theorem 1.4 as stated also gives 0 components for a line (k = 1), while Corollary 1.5 uses M = 1.

## My inferences

- **What the Harnack slip affects:** only the explicit polynomial in Theorem 1.2 for odd n. For n = 3 the divergence curve is a conic, so M can be 1 (ellipse) or 2 affine branches (hyperbola), giving 37 or 41 from (1.3) rather than the stated 33. It does not affect Theorem 1.1, Corollary 1.5, or the comparison in the table.
- **A second error, in the proof of the main theorem (§5, p. 27):** the proof counts ovals of Div = 0 as contributing "at most Σ yj different limit behaviors" (zero if no contact points). This conflicts with Theorem 4.4(b.3) on p. 20, "the limit behavior is the whole oval without contact points". The paper then remarks: "Note that in fact, the upper bound can be slightly improved to 1 + (n − 1)²(L + N) where L is the number of connected components of the curve Div = 0 homeomorphic to a line."
- **That remark is false.** Take x' = −y + x f(r²), y' = x + y f(r²) with f(s) = (s−1)(s−2), degree 5. It satisfies hypotheses (1)–(3), its divergence curve is two circles with no contact points (L = 0, N = 0), and it has exactly two limit cycles (r = 1 and r = √2). The "improved" bound gives 1. I checked this by hand and numerically.
- **What the counterexample does not do:** the stated inequality (1.3) still holds for it (33 ≥ 2), and for n = 2 the divergence curve is a line with no ovals. So Theorem 1.1 and the n = 2 corollary are not refuted.
- **Overall:** for n = 2, hypothesis-level facts force M = 1 and N ≤ 2, so Theorem 1.1 at n = 2 is essentially statement S itself. The corollary therefore has no support beyond the full Morse-theory argument (Theorems 4.4, 4.5, 5.1 and the §5.1 perturbation step), which is unrefereed, uncited, and contains at least the two checkable errors above.

No files were written to the project.
