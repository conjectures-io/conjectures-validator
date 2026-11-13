# Hilbert 16: classification output and cycle bound

The primary target is `Hilbert16Classification.ClassificationOutput`, a type
whose inhabitant supplies actual catalogues, realizations, exhaustion,
irredundancy and sharp maxima. It is not a proposition saying merely that some
classification program exists. Fable accepted this interpretation in round 2.

`Hilbert16.UniformBound` is the core closed Part 2 conjecture. Pure cycle
classification outputs separately retain the requested counts and positions.
`EffectiveHilbert16` is an optional strengthening with terminating programs;
its computability requirements do not define the historical question.

## Geometry conventions

- Real projective space is the actual projectivization with quotient topology.
- Homogeneous polynomials are nonzero and have the specified actual degree.
- Primary curves and surfaces are complex-nonsingular. The reusable Boolean
  switch also defines unrestricted loci, but that mode is outside the primary
  output: singular surface equations can encode spatial graphs and knots.
- Position means ambient isotopy. Both maps and inverses vary continuously,
  starting at the identity. A whole family moves by one common isotopy, so
  nesting and mutual placement are preserved.
- Codes are finite unions of convex hulls of rational vertices, projected in
  the projective case. Realizability excludes invalid codes from a catalogue.
- Component counts bijectively enumerate actual connected components; infinite
  cardinalities cannot be totalized to zero.

## Dynamics conventions

Affine fields have degree at most d. Projective fields are homogeneous of
exact degree d; these are separate tasks, with no degree-equivalence theorem
assumed. A projective trajectory has a nonzero lift satisfying
`γ' = a V(γ) + b γ`, with continuous nonvanishing a and continuous b. Only the
projected orbit must be periodic; it must be nonconstant. Isolation excludes
other entire periodic orbits in a neighbourhood, not trajectories entering it.
One-sided projective cycles and periodic invariant lines at infinity count.

Arrangements are unoriented families. Stability, multiplicity, flow direction,
and placement relative to singular points are not recorded. This is an explicit
interpretation of Hilbert's word “position”. No unproved equivalence between
classification output and the uniform-bound proposition is used.

## Known benchmarks and status

`HarnackSharpBound` specifies the bound `(d−1)(d−2)/2+1` and its attainment for
nonsingular degree-d curves. `QuarticSurfaceMaximum` specifies the upper bound
ten and an attaining nonsingular quartic surface. These are known theorem
statements, not new conjectures or proofs supplied here. The quartic statement
is confirmed by Kharlamov–Itenberg, *Towards the Maximal Number of Components
of a Nonsingular Surface of Degree 5 in RP³*, introduction:
https://www.math.ucdavis.edu/~deloera/MISC/LA-BIBLIO/trunk/Itenberg/Itenberg2.pdf

Smooth plane curve classification is computable in principle through real
algebraic geometry. That does not produce usable catalogues for every degree.
The output type remains a legitimate specification of what must be supplied.
The packet does not transcribe Hilbert's erroneous sextic list or certify any
recent claim resolving the general cycle bound.
