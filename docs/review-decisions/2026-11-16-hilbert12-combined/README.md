# Hilbert 12

The published statement is `Hilbert12.Hilbert12Combined`. For each number field embedded in the complex numbers, it asks for one analytic recipe whose special values generate the ray class field at every modulus and transform under its Artin map. The arithmetic classification and both classical benchmarks use those same fields. [Complete Lean statement](Hilbert12Combined.lean)

The arithmetic classification includes exact finite catalogues by degree, abelian Galois group and relative discriminant. Over the rationals, marking the real place gives roots of unity and omitting it gives their real traces. Over imaginary quadratic fields, the benchmark uses the j-invariant and Weber values at nonzero points annihilated by the modulus ideal.

## Verification and proof status

The proposition remains open. The source type-checks on Lean `v4.35.0-rc2` with Mathlib `065356127b1dc0016f66b7283ce0ce2c4055aa55`. The checked implication `Hilbert12.Hilbert12Combined.toKroneckerType` recovers the general analytic target. The axiom audit of the declaration and this implication reports `propext`, `Classical.choice` and `Quot.sound`.

Run `lake env lean -DwarningAsError=true Hilbert12Combined.lean` in a Mathlib checkout at that revision. The source SHA-256 is `a6326a4a459d998d61511b43c818ba57269e1ca27cb78b2ba48699aee88f6f4d`.

A proposed construction still requires mathematical review of how it defines the special points. The point language permits unbounded arithmetic quantification, so a proof would have to be examined for indirect encoding of the generators.
