# Mathematical fidelity review

Reviewer model: `claude-fable-5-2`. Fresh-context subagent of the authoring session; it received only the brief below and the files it names.

## Brief 1 (2026-11-15T21:56:05.868Z)

```text
You are an independent mathematical reviewer (real algebraic geometry and qualitative theory of planar ODEs). A platform that pays bounties for Lean proofs or refutations of open problems wants to publish two formal statements labelled as open cases of Hilbert's sixteenth problem. Review them for mathematical fidelity to the literature and to Hilbert's text, and for whether the labelling is honest. Web access is available and encouraged for checking sources; today's date is 2026-11-15.

Read these two Lean files (read-only; do not modify anything):
- <scratch>/h16/src/HilbertDrafts/Hilbert16.lean  (definitions)
- <scratch>/h16/src/HilbertDrafts/Hilbert16Open.lean  (the two candidate targets `QuadraticFourCycles` and `QuinticSurfaceComponents`, with docstrings)

Candidate A — `QuadraticFourCycles`: for every pair of real polynomials P, Q in two variables of total degree ≤ 2, the system x' = P, y' = Q has at most 4 limit cycles, where a limit cycle is the image of a non-constant periodic solution that has an open neighbourhood containing no other periodic-orbit image.
Candidate B — `QuinticSurfaceComponents`: for every nonzero real homogeneous polynomial p of degree 5 in 4 variables with no singular point in complex projective 3-space, the real zero locus in RP^3 has at most 23 connected components.

Questions to answer, each with reasons and sources:

1. Hilbert's own text. Quote (from the 1902 Newson English translation of "Mathematical Problems", problem 16, e.g. at https://mathcs.clarku.edu/~djoyce/hilbert/problems.html or another reliable copy) the sentences on (a) the number, form and position of the sheets of an algebraic surface in space and the maximum number of sheets of a quartic, and (b) the maximum number and position of Poincaré's boundary cycles (cycles limites) for a first-order first-degree differential equation of degree n. Is it accurate to describe A as "the degree-two case of the second part" and B as "the degree-five case of the question about the maximal number of sheets of a surface, first part"? Note anything in Hilbert's wording that A or B fails to respect (e.g. Hilbert's equation is dy/dx = Y/X with X, Y of degree n — is 'degree ≤ 2 for both components' the standard reading of n = 2? Does Hilbert's 'sheets' mean connected components of the real projective surface? Affine vs projective?).

2. Conventions for A. Is "limit cycle = periodic orbit isolated among periodic orbits" the standard definition used when people state H(2) = 4 (e.g. in Ilyashenko's "Centennial history of Hilbert's 16th problem", Bull. AMS 2002; Jibin Li's survey "Hilbert's 16th problem and bifurcations of planar polynomial vector fields", 2003; Ye Yanqian's or Zhang Zhifen's books)? Are quadratic systems with degenerate features (common factors of P and Q giving curves of singular points, systems of lower degree, systems with centers) included in the standard definition of H(2), and does including them change anything? Is the count over the whole plane? Is there any standard-convention difference (e.g. counting multiplicity, counting limit cycles "at infinity", or counting on the Poincaré sphere) under which the true value of "the maximum number of limit cycles of a quadratic system" would differ from the count used in A?

3. Conventions for B. Confirm the standard setting of the question "maximal number of connected components of a real nonsingular quintic surface in RP^3" (Itenberg–Kharlamov; Bihan; Orevkov's "Real quintic surface with 23 components", 2001; Kharlamov–Itenberg and Viro surveys). Is "nonsingular" there the smoothness of the complexification (which is what B uses) or only of the real locus, and does the distinction affect the maximum? Is the known range {23, 24, 25} stated for exactly this setting? Is "at most 23" a reasonable way to pose the open case as a prove-or-refute target, given that the truth could be 23, 24 or 25 — is there a prevailing expectation in the literature, and is there any reason to think one side is already known?

4. Is either statement known to be TRUE or FALSE already (a theorem or a counterexample in the literature, including recent preprints)? In particular for A: has any quadratic system with five limit cycles been published; has any accepted proof of H(2) = 4 appeared. For B: any quintic with 24 or 25 components, or any proof of an upper bound below 25.

5. Labelling. Suggest accurate one-line public titles and a 2–3 sentence public scope note for each, making clear that these are specific open cases and not the whole of Hilbert's sixteenth problem, and (for A) how it relates to a separately claimed general finiteness/uniform-bound result (a September 2026 OpenAI manuscript claims that for each degree d some finite bound B(d) exists; it claims no exact values).

6. Any mathematical objection to publishing either as a bounty target, or any change to the formal statement you would require first.

Report format: for each of A and B give a verdict — ACCEPT, ACCEPT WITH CHANGES (list them), or REJECT — followed by numbered answers to the six questions with source URLs. Distinguish facts you verified by opening a source from recollection. Do not write any files.
```

## Report 1 (2026-11-15T22:14:22.152Z)

## Verdicts

- **A (`QuadraticFourCycles`): ACCEPT WITH CHANGES.** The formal statement is a faithful rendering of "H(2) ≤ 4" and needs no change. The required changes are to labelling and disclosure only (listed under 6).
- **B (`QuinticSurfaceComponents`): ACCEPT WITH CHANGES.** The formal statement matches the literature's setting exactly and needs no change. The required changes are a neutral title and one docstring correction (listed under 6).

Evidence tags: [V] opened and read this session; [S] search-result snippet only; [R] recollection; [O] my own argument. I read both Lean files and `Hilbert16OpenChecks.lean`. No repository files were written; temporary downloads in the scratchpad were deleted.

## 1. Hilbert's text

Quotes [V], Newson translation, https://mathcs.clarku.edu/~djoyce/hilbert/problems.html#prob16:

(a) "A thorough investigation of the relative position of the separate branches when their number is the maximum seems to me to be of very great interest, and not less so the corresponding investigation as to the number, form, and position of the sheets of an algebraic surface in space. Till now, indeed, it is not even known what is the maximum number of sheets which a surface of the 4-th order in three dimensional space can really have." The footnote cites Rohn, "Flächen vierter Ordnung", 1886.

(b) "This is the question as to the maximum number and position of Poincaré's boundary cycles (cycles limites) for a differential equation of the first order and degree of the form [dy/dx = Y/X] where X and Y are rational integral functions of the n-th degree in x and y. Written homogeneously, this is [X(y dz/dt − z dy/dt) + Y(z dx/dt − x dz/dt) + Z(x dy/dt − y dx/dt) = 0] where X, Y, and Z are rational integral homogeneous functions of the n-th degree in x, y, z…" The two formulas are images on that page; I took their text from https://en.wikipedia.org/wiki/Hilbert%27s_sixteenth_problem [V].

**A.** "Degree-two case of the second part" is accurate under the standard reading, with three qualifications:
- **Number only, upper half only.** Hilbert asks for "maximum number and position". A addresses only the number, and only the upper bound; the lower bound of 4 is known but not part of the target.
- **Degree convention.** "Both components of degree ≤ 2" is the standard reading. Buzzi–Novaes define H(n) := sup{π(P,Q): deg P, deg Q ≤ n} [V, https://arxiv.org/abs/2411.09594]. For n = 2, "≤ 2" versus "= 2" is immaterial, since linear fields have no limit cycles (Ilyashenko: "H(1) = 0" [V]).
- **Homogeneous form.** Hilbert's homogeneous equation with a general Z describes every degree-n foliation of RP². In the chart z = 1 it reads dy/dx = (Y − yZ)/(X − xZ), which has affine degree n + 1 in general; the affine equation is the case Z = 0 [O]. So the literal homogeneous class is larger and is counted on RP². The literature uses the affine reading and A follows it; the public note should say "affine plane". `Hilbert16.lean` already keeps the projective reading as a separate task.

**B.** A fair description, with one caveat: Hilbert singles out the maximum number of sheets only for degree 4 and never mentions degree 5. "The first degree in which the maximal number is unknown" is accurate. The docstring phrase "Hilbert's text asks for the maximum number of sheets of a surface" overstates this.
- **Sheets** are connected components of the real point set, and the universal modern reading is nonsingular surfaces in RP³. Itenberg: the classification "of nonsingular surfaces of a given degree in RP³ was also included by Hilbert in the first part of the 16-th problem" [V, https://webusers.imj-prg.fr/~ilia.itenberg/papers/constr.pdf]. Degtyarev–Kharlamov say the same of quartics [V, https://arxiv.org/abs/math/0004134].
- Hilbert himself writes neither "projective" nor "nonsingular". An affine count or a singular class would be a different problem [O].

## 2. Conventions for A

- **Definition.** Ilyashenko: "(The limit cycle is an isolated closed orbit of a vector field.)"; H(n) is the bound in his Problem 2; "It is still unknown whether or not H(2) exists" [V; Bull. AMS 39 (2002), https://www.ams.org/journals/bull/2002-39-03/S0273-0979-02-00946-1/, read through a web.archive.org copy of the PDF]. Buzzi–Novaes: "a (non-stationary) periodic orbit that is isolated from other periodic orbits" [V]. Gasull–Santana: "limit cycles (i.e. isolated periodic orbits)", H(2) ≥ 4, "it is not known if H(2) < ∞" [V, https://arxiv.org/abs/2407.13465].
- **Match with the OpenAI manuscript.** Its definition is the same as A's: "It is a limit cycle if some open neighborhood of that image contains no other periodic orbit. We count geometric images once, without multiplicity" [V].
- **"Contained in U" versus "meets U".** The Lean isolation clause excludes other periodic orbits contained in U. In the plane this is equivalent to excluding orbits that meet a smaller neighbourhood: a periodic orbit crosses a transversal once, so one passing near C closes after one return and stays near C [O].
- **Degenerate systems.** They are included in the standard supremum and contribute nothing for n = 2 [O]. A common linear factor gives a reparametrised linear field, whose periodic orbits come in continua. A common quadratic factor gives a constant direction. Orbits in period annuli are not isolated and are not counted.
- **Whole plane.** Yes: "in the whole plane" (OpenAI Theorem 1.1 [V]).
- **Other conventions.** None changes the value for n = 2:
  - Multiplicity is not counted. Gasull–Santana show H(n) is realised with hyperbolic cycles [V, abstract].
  - On the Poincaré sphere the count at least doubles (Ilyashenko [V]); nobody defines H(n) that way.
  - For even n the equator always carries singular points, so there is no "limit cycle at infinity" [O/R].
  - Polycycles are not limit cycles.
- **Not opened.** Jibin Li's survey and the books of Ye and Zhang are paywalled. From recollection they use "isolated closed orbit" and H(2) ≥ 4 [R].

## 3. Conventions for B

- **Setting.** Orevkov: "There exists a real algebraic surface in RP³ of degree 5 which has b₀ = 23 connected components: 22 spheres and RP² with two handles"; existence with "b₀ = 24, 25 … is still open (it is known that b₀ ≤ 25…)" [V, https://www.math.univ-toulouse.fr/~orevkov/q23.pdf]. Itenberg: "b₀(RX) ≤ 25 for a nonsingular surface X of degree 5 in RP³. No nonsingular surface of degree 5 in RP³ with 24 or 25 connected components is known" [V]. Degtyarev–Kharlamov Prop. 3.5.6 gives 22 ≤ max β₀ ≤ 25, written before Orevkov [V].
- **Meaning of "nonsingular".** Itenberg defines it by absence of real critical points; Degtyarev–Kharlamov and the Smith–Thom and Comessatti bounds use a nonsingular complexification. The maximum is the same either way: real-nonsingular polynomials form open chambers of constant isotopy type, and the complex discriminant is nowhere dense in each [O]. B's complex-nonsingular hypothesis loses nothing.
- **Range.** {23, 24, 25} is stated for exactly this setting.
- **Expectation.** I found no published conjecture either way. Twenty-five components would force an M-quintic of type 24S ⊔ V₅ or 23S ⊔ S₁ ⊔ V₃ (Degtyarev–Kharlamov [V]). Orevkov's Remark 2 shows his scheme cannot exceed 23 [V]. "At most 23" is a legitimate prove-or-refute target, but the title must not suggest 23 is the expected answer.

## 4. Already known?

**A: not settled.**
- **No five-cycle example.** Searches found no quadratic system with five limit cycles. The lower bound is Chen–Wang 1979 and Shi 1980 [V via https://arxiv.org/abs/1002.1055]; Shi's system has exactly four (Galias–Tucker 2022 [S]).
- **No accepted proof.** Claimed proofs exist and none is accepted:

| Claim | Status |
|---|---|
| Petrovskii–Landis, H(2) = 3 | disproved (Ilyashenko [V]) |
| Qin Yuanxun, "at most four" | Shi's 1988 counterexample to two of its lemmas [S, https://zendy.io/title/10.1112/blms/20.6.597] |
| Gaiko, arXiv math/0611142 | unrefereed claim [V, abstract] |
| Llibre–Pedregal, arXiv 1411.6814 and 1904.01292 | both withdrawn [V] |
| da Silva–Vieira–Leonel, Entropy 2024 | formula gives 4 at n = 2 [V, https://pmc.ncbi.nlm.nih.gov/articles/PMC11431191/]; rebutted by Buzzi–Novaes [V] |
| Pedregal, arXiv 2103.07193 v4, 3 Nov 2026 | live; abstract says "For quadratic systems, we find H(2)=4" [V] |

- **Pedregal v4.** This is the one that matters for disclosure. It is unrefereed and v2 was withdrawn. On a quick read, not a refereeing, its general-degree formula uses M ≤ 0 components of the divergence curve for n = 3, which cannot be right; the n = 2 corollary does not depend on that.
- **OpenAI manuscript.** "Uniform bounds for planar polynomial limit cycles", 24 Sept 2026, claims only that some B(d) exists: "It does not furnish an effective formula for B(d)" [V, entry 143 of https://github.com/openai/math]. Its Lean artifact covers only the quintic Liénard companion [V]. A third-party tracker lists it as unreviewed [V, https://vibemathed.com/problem/hilbert-16-uniform-bound-limit-cycles].

**B: not settled.** No quintic with 24 or 25 components and no upper bound below 25. The latest source, March 2026, says "we still don't know if there exist projective quintics with 24 o[r] 25 connected components" [V, https://arxiv.org/abs/2603.08094]. The OpenAI release contains nothing on this [V, grep of `CONTENTS.md`].

## 5. Labelling

**A.**
- Title: "Quadratic vector fields have at most four limit cycles (H(2) ≤ 4): an open case of Hilbert's 16th problem, part two".
- Scope note: "Asserts that every planar system x′ = P, y′ = Q with deg P, deg Q ≤ 2 has at most four limit cycles (isolated periodic orbits, counted in the whole affine plane, without multiplicity). With the classical four-cycle examples this would give H(2) = 4. It is one degree of the 'maximum number' question only, and says nothing about positions or higher degrees. A separately claimed and unreviewed September 2026 OpenAI manuscript asserts only that some finite bound B(d) exists for each degree; it gives no values and does not decide this statement. Unrefereed claims of H(2) = 4 exist (most recently arXiv:2103.07193v4); none is accepted."

**B.**
- Title: "Is 23 the maximal number of components of a nonsingular real quintic surface in RP³? An open case of Hilbert's 16th problem, part one".
- Scope note: "Asserts that a real quintic surface in RP³ with nonsingular complexification has at most 23 connected components. The maximum is known to be 23, 24 or 25 (Orevkov 2001; Kharlamov, Itenberg–Kharlamov), so the statement may be true or false; a quintic with 24 or 25 components would refute it. Hilbert asked about the number, form and position of sheets and named only the quartic maximum (10, Kharlamov 1972); this target covers only the number in degree five."

## 6. Objections and required changes

There is no mathematical objection to either formal statement. Neither is vacuous or cheaply decidable.

**A, required:**
1. Reword the header "two closed open cases" to "two specific open cases".
2. Disclose the unrefereed H(2) = 4 claims, especially Pedregal v4, and state that the lower bound of 4 is known but not formalised.
3. State "affine plane, without multiplicity" in the public text.

**A, optional:** a remark or lemma on the equivalence of the two isolation notions, and a quadratic rather than cubic non-vacuity witness in `Hilbert16OpenChecks.lean`.

**B, required:**
1. Use a neutral title and state that the truth value is unknown in both directions.
2. Correct the docstring sentence about Hilbert's wording, as in 1.
3. State that Orevkov's lower bound is not formalised, so the target is "max ≤ 23".

**B, optional:** note that the real-nonsingular and complex-nonsingular conventions give the same maximum.

Files reviewed:
- <scratch>/h16/src/HilbertDrafts/Hilbert16.lean
- <scratch>/h16/src/HilbertDrafts/Hilbert16Open.lean
- <scratch>/h16/src/HilbertDrafts/Hilbert16OpenChecks.lean
