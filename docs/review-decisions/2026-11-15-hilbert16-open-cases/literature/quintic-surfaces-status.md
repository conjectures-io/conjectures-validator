# Literature status: components of real quintic surfaces

Reviewer model: `claude-fable-5-2`. Fresh-context subagent of the authoring session; it received only the brief below and the files it names.

## Brief 1 (2026-11-15T21:51:29.692Z)

```text
Research task (web search required; today's date is 2026-11-15). I need the CURRENT research status of one precise mathematical question, so that a prize platform can decide whether it is still an open problem. Be skeptical and cite sources with URLs and dates.

Question Q: What is the maximal number of connected components of the real point set of a nonsingular real algebraic surface of degree 5 in real projective 3-space RP^3 (the surface is the zero set of a real homogeneous quintic in 4 variables whose complex projective zero set is smooth)?

My recollection, which you must verify or correct: the maximum is known only to lie in {23, 24, 25}; the lower bound 23 is due to S. Yu. Orevkov, "Real quintic surface with 23 components", C. R. Acad. Sci. Paris Sér. I Math. 333 (2001); earlier constructions gave 21 (Itenberg–Kharlamov 1996) and 22 (Bihan 1999/2001); the upper bound 25 follows from the Smith–Thom / Petrovskii–Oleinik / Comessatti-type inequalities. This is the degree-5 case of the question in the first part of Hilbert's 16th problem (Hilbert asked for the maximal number of sheets of a quartic surface, which is 10, proved by Kharlamov).

Please establish, with sources:
1. The exact current best lower and upper bounds, with attribution, and where the upper bound 25 comes from. Confirm the bound is for the number of connected components of a NONSINGULAR quintic in RP^3.
2. Most important: whether anyone has since constructed a nonsingular real quintic surface with 24 or 25 components, or proved an upper bound of 23 or 24 — search through November 2026, including arXiv, surveys by Itenberg, Kharlamov, Orevkov, Viro, Brugallé, Renaudineau, Shustin, Bihan, and any 2024–2026 papers or AI-generated claims. 
3. A recent (ideally 2015 or later) survey or paper that explicitly states the question is open, quoted with URL.
4. Separately and briefly: confirm that the corresponding maximum for degree 4 is 10 (Kharlamov) and that Harnack's bound for plane curves is (d-1)(d-2)/2 + 1, just as sanity checks on conventions.

Report format: a short verdict line ("OPEN as of <date>: maximum in {..}" / "RESOLVED: ..." / "UNCERTAIN because ..."), then numbered findings each with source URL, date, and one-sentence relevance. Clearly separate what you verified by actually opening a source from what you only saw in search snippets. If a source could not be fetched, say so rather than guessing. Do not write any files.
```

## Report 1 (2026-11-15T22:04:04.717Z)

**OPEN as of 2026-11-15: maximum in {23, 24, 25}.** The latest explicit expert statement I opened is dated 2026-03-09. For March–November 2026 the verdict rests on absence of evidence: I found no construction with 24 or 25 components and no upper bound below 25.

Your recollection needs three attribution corrections:
- **21 components:** Kharlamov (1981), not Itenberg–Kharlamov.
- **22 components:** Kharlamov–Itenberg (1996), not Bihan.
- **Bihan (1999):** reached 23, but only for a real surface that deforms, as a complex surface, to a smooth quintic and is not embeddable in RP^3.

Orevkov (2001) is the first, and still the record, 23-component quintic in RP^3.

## A. Verified by opening the source

1. **Orevkov, "Real quintic surface with 23 components", C. R. Acad. Sci. Paris Sér. I 333 (2001), 115–118.** https://www.math.univ-toulouse.fr/~orevkov/q23.pdf
   - Theorem: a real algebraic surface of degree 5 in RP^3 with b0 = 23, namely 22 spheres and RP^2 with two handles.
   - Introduction: 21 components by Kharlamov [1981]; 22 by Kharlamov and Itenberg [1996]; Bihan [1999] got b0 = 23 on a surface "not embeddable into RP^3".
   - It states: "The question of existence of quintic surfaces in RP^3 with b0 = 24, 25 ... is still open (it is known that b0 ≤ 25 ...)".
   - Quotes are restored from a lossy PDF text extraction (odd font encoding), so check the PDF before quoting publicly.
   - Relevance: primary source for the lower bound 23 and the construction history.

2. **Degtyarev–Kharlamov survey, arXiv:math/0004134 (20 Apr 2000; Russian Math. Surveys 55).** https://arxiv.org/abs/math/0004134
   - Origin of 25: for a nonsingular quintic, h^{1,1} = 45, total Betti number 55, signature −35, and "the upper bounds follow from the Smith and Comessatti inequalities ... and the extremal congruence".
   - My arithmetic from those inequalities: 2·b0 + b1 ≤ 55 and 2·b0 − b1 ≤ 45, so b0 ≤ 25.
   - "In degrees ≥5 even the maximal number of connected components of the real part of a surface is not known. (Note that the maximum can always be realized by a nonsingular surface.)"
   - A 25-component quintic would have to be an M-surface of type 24S ⊔ V_5 or 23S ⊔ S_1 ⊔ V_3; two other types are prohibited (Viro; Kharlamov–Itenberg), and existence is "still open".
   - Relevance: establishes where 25 comes from and that restricting to nonsingular surfaces does not change the maximum.

3. **Itenberg, "Construction of real algebraic varieties" (26 Feb 2002).** https://webusers.imj-prg.fr/~ilia.itenberg/papers/constr.pdf
   - The generalized Harnack and Comessatti inequalities give b0 ≤ 5m^3/12 − 3m^2/2 + 25m/12 for a nonsingular surface of degree m in RP^3.
   - "In particular, b0(RX) ≤ 25 for a nonsingular surface X of degree 5 in RP^3. No nonsingular surface of degree 5 in RP^3 with 24 or 25 connected components is known."
   - Relevance: confirms the bound is for connected components of a nonsingular quintic in RP^3.

4. **Kharlamov, "Overview of topological properties of real algebraic surfaces", arXiv:math/0502127 (2005).** https://arxiv.org/abs/math/0502127
   - "We only know that the maximal number of connected components is somewhere in between 23 and 25".
   - Relevance: status statement by the author of the upper bound.

5. **Gabard, "Notes on Hilbert's 16th", arXiv:1310.1865 (Oct 2013).** https://arxiv.org/abs/1310.1865
   - Attributes b0 ≤ 25 and the 21-component surface to Kharlamov 1981, and says "the cases b0 = 24, 25 are still wide open".
   - Calls this "the direct avatar of Hilbert's question for quartics settled as b0(max) = 10 in Kharlamov 1972".
   - Relevance: independent confirmation of attributions and of the Hilbert 16 framing.

6. **Itenberg, "Topology of real algebraic varieties and tropical homology" (notes dated 10 Aug 2018; published in Singularities – Kagoshima 2017, 2020).** https://www.imj-prg.fr/~ilia.itenberg/papers/Tropical_homology_notes.pdf
   - Defines non-singular as no critical points in C^{n+1}∖{0}, which matches your convention.
   - "For non-singular surfaces of degree 5 in RP^3, even the maximal possible number of connected components of the real part is not known (this maximal number is at least 23 and at most 25...)".
   - Relevance: post-2015 survey stating the question is open, in exactly your convention.

7. **Renaudineau–Shaw, arXiv:1805.02030 (v2 12 Nov 2019; Ann. Sci. ENS 2023, DOI 10.24033/asens.2547).** https://arxiv.org/abs/1805.02030
   - "It is known that the maximal number of connected components of a non-singular real algebraic quintic surface is either 23, 24, or 25".
   - Relevance: peer-reviewed post-2015 statement of the exact set.

8. **Fanelli–Mangolte, arXiv:2507.04012 (5 Jul 2025).** https://arxiv.org/abs/2507.04012
   - "For example, this problem is still open for smooth real quintic surfaces in P^3, see [IK96, Or01]."
   - Relevance: open as of mid-2025.

9. **Akyar–Shkolnikov, "Introduction to non-Abelian Patchworking", arXiv:2603.08094 (9 Mar 2026).** https://arxiv.org/html/2603.08094v1
   - "the known best upper bound on the number of connected components of a real quintic surface in RP^3 is 25, but the highest number of component construction was done by Orevkov a quarter of a century ago giving an example with 23 components. In other words, we still don't know if there exist projective quintics with 24 on 25 connected components."
   - Relevance: the most recent explicit statement found.

10. **Search for anything newer (checked 2026-11-15) — nothing found.**
    - **arXiv API:** about 15 title/abstract queries (quintic, real, surface, components, Betti, M-surface, patchworking) returned no paper claiming 24, 25, or a lower upper bound. This covers metadata only, not full text.
    - **Citation indexes:** OpenAlex lists 8 works citing Orevkov's paper and Semantic Scholar 10; the newest is item 9, and none is an improvement.
    - **Orevkov's homepage:** lists papers through 2026 and has no new quintic-surface item. https://www.math.univ-toulouse.fr/~orevkov/
    - **OpenAI's October 2026 release (721 unrefereed manuscripts, 373 result families):** the community index has no item on real quintic surfaces or the first part of Hilbert 16. Its only Hilbert 16 item is #143, on limit cycles, which is the second part and does not bear on Q. https://mathcommunity.github.io/oai-math-revised/
    - **Epoch FrontierMath Open Problems and ProofAtlas's Hilbert 16 page:** nothing on this question. https://epoch.ai/frontiermath/open-problems and https://www.proofatlas.ai/collaboration/hilberts-sixteenth-problem/

11. **Sanity checks on conventions.**
    - **Degree 4:** the maximum is 10 (Kharlamov, Funct. Anal. Appl. 6 (1972)), per item 5; item 4 lists the extremal quartic type as a genus-2 surface plus 9 spheres. The general formula in item 3 gives 11 for m = 4, so the 10 needs Kharlamov's extra argument.
    - **Harnack:** (d−1)(d−2)/2 + 1, confirmed in items 3, 6 and 9 and on Wikipedia (last edited 7 Oct 2026). https://en.wikipedia.org/wiki/Hilbert%27s_sixteenth_problem

## B. Seen only in metadata or snippets

- Itenberg, "Viro Method in Topology of Real Algebraic Varieties" (Springer chapter, DOI 10.1007/978-3-031-99571-2_2, July 2025): OpenAlex shows it cites Orevkov's paper, but the page redirected to a login and I did not read it.
- arXiv:2610.17090, a commentary on the OpenAI release: seen as a search snippet only.

## C. Could not fetch or did not check

- ScienceDirect pages for Orevkov 2001 and Bihan 1999, and zbMATH: HTTP 403.
- MathOverflow: blocked to the search tool, not checked.
- MathSciNet: not accessible.
- Originals of Kharlamov 1972, Kharlamov 1981, Itenberg–Kharlamov 1996 and Bihan 1999: not opened; their content is taken from items 1, 2, 3 and 5.

No files were written to the repository.
