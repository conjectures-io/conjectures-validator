import Lean
import Lean.Util.CollectAxioms
import FormalConjectures.ErdosProblems.«107»
import FormalConjectures.ErdosProblems.«398»
import FormalConjectures.GreensOpenProblems.«72»
import FormalConjectures.Millennium.RiemannHypothesis
import FormalConjectures.Wikipedia.ArtinPrimitiveRootsConjecture
import FormalConjectures.Wikipedia.BorsukConjecture
import FormalConjectures.Wikipedia.BrocardConjecture
import FormalConjectures.Wikipedia.Bunyakovsky
import FormalConjectures.Wikipedia.CarmichaelTotient
import FormalConjectures.Wikipedia.ClassNumberProblem
import FormalConjectures.Wikipedia.Dickson
import FormalConjectures.Wikipedia.EulerBrick
import FormalConjectures.Wikipedia.Fermat
import FormalConjectures.Wikipedia.GaussCircleProblem
import FormalConjectures.Wikipedia.GoldbachConjecture
import FormalConjectures.Wikipedia.Goormaghtigh
import FormalConjectures.Wikipedia.Hadamard
import FormalConjectures.Wikipedia.HardyLittlewood
import FormalConjectures.Wikipedia.IdonealCompleteness
import FormalConjectures.Wikipedia.InscribedSquare
import FormalConjectures.Wikipedia.InverseGalois
import FormalConjectures.Wikipedia.Irrational
import FormalConjectures.Wikipedia.Koethe
import FormalConjectures.Wikipedia.KummerVandiver
import FormalConjectures.Wikipedia.LegendreConjecture
import FormalConjectures.Wikipedia.LehmerMahlerMeasureProblem
import FormalConjectures.Wikipedia.LehmerTotient
import FormalConjectures.Wikipedia.Lemoine
import FormalConjectures.Wikipedia.Mersenne
import FormalConjectures.Wikipedia.NormalityOfPi
import FormalConjectures.Wikipedia.Oppermann
import FormalConjectures.Wikipedia.PerfectNumbers
import FormalConjectures.Wikipedia.PollocksConjecture
import FormalConjectures.Wikipedia.PrimesAndPerfectSquares
import FormalConjectures.Wikipedia.RegularPrimes
import FormalConjectures.Wikipedia.ScholzConjecture
import FormalConjectures.Wikipedia.TwinPrimes

open Lean Meta Elab Command
set_option maxHeartbeats 0
set_option maxRecDepth 100000

def inspectTarget (name : Name) : MetaM Json := do
  let info ← getConstInfo name
  let mut typeAxioms : Array Name := #[]
  for c in info.type.getUsedConstants do
    for ax in (← collectAxioms c) do
      if !typeAxioms.contains ax then typeAxioms := typeAxioms.push ax
  let pretty ← ppExpr info.type
  let canonical ← withOptions (fun o => o.setBool `pp.all true |>.setBool `pp.proofs true |>.setBool `pp.universes true) (ppExpr info.type)
  let bodyAxioms ← collectAxioms name
  return Json.mkObj [
    ("theorem", toJson name.toString),
    ("type_pretty", toJson pretty.pretty),
    ("type_canonical", toJson canonical.pretty),
    ("type_has_sorry", toJson info.type.hasSorry),
    ("type_dependency_axioms", toJson (typeAxioms.map Name.toString)),
    ("declaration_axioms", toJson (bodyAxioms.map Name.toString))]

run_meta do
  let mut rows : Array Json := #[]
  rows := rows.push (← inspectTarget `PerfectNumbers.odd_perfect_number_conjecture)
  rows := rows.push (← inspectTarget `Mersenne.infinitely_many_mersenne_primes)
  rows := rows.push (← inspectTarget `Fermat.infinite_fermat_primes)
  rows := rows.push (← inspectTarget `EulerBrick.perfect_euler_brick_existence)
  rows := rows.push (← inspectTarget `Irrational.irrational_eulerMascheroniConstant)
  rows := rows.push (← inspectTarget `GoldbachConjecture.goldbach)
  rows := rows.push (← inspectTarget `Idoneal.idoneal_numbers_completeness)
  rows := rows.push (← inspectTarget `LegendreConjecture.legendre_conjecture)
  rows := rows.push (← inspectTarget `ClassNumberProblem.class_number_problem)
  rows := rows.push (← inspectTarget `TwinPrimes.twin_primes)
  rows := rows.push (← inspectTarget `KummerVandiver.kummer_vandiver)
  rows := rows.push (← inspectTarget `RegularPrimes.regularprime_conjecture)
  rows := rows.push (← inspectTarget `PollocksConjecture.pollock_tetrahedral)
  rows := rows.push (← inspectTarget `Bunyakovsky.bunyakovsky_conjecture)
  rows := rows.push (← inspectTarget `RiemannHypothesis.riemannHypothesis)
  rows := rows.push (← inspectTarget `Irrational.irrational_catalanConstant)
  rows := rows.push (← inspectTarget `Erdos398.erdos_398)
  rows := rows.push (← inspectTarget `Mersenne.catalans_mersenne_conjecture)
  rows := rows.push (← inspectTarget `Oppermann.oppermann_conjecture)
  rows := rows.push (← inspectTarget `InverseGalois.inverse_galois_problem)
  rows := rows.push (← inspectTarget `Hadamard.HadamardConjecture)
  rows := rows.push (← inspectTarget `Lemoine.lemoine_conjecture)
  rows := rows.push (← inspectTarget `Green72.green_72)
  rows := rows.push (← inspectTarget `Dickson.dickson_conjecture)
  rows := rows.push (← inspectTarget `Brocard.brocard_conjecture)
  rows := rows.push (← inspectTarget `CarmichaelTotient.charmichaelTotient)
  rows := rows.push (← inspectTarget `NormalNumber.pi_normal_base_ten)
  rows := rows.push (← inspectTarget `InscribedSquare.inscribed_square_problem)
  rows := rows.push (← inspectTarget `PrimesAndPerfectSquares.infinite_prime_sq_add_one)
  rows := rows.push (← inspectTarget `GaussCircleProblem.error_isBigO)
  rows := rows.push (← inspectTarget `Goormaghtigh.goormaghtigh_conjecture)
  rows := rows.push (← inspectTarget `HardyLittlewood.first_hardy_littlewood_conjecture)
  rows := rows.push (← inspectTarget `HardyLittlewood.second_hardy_littlewood_conjecture)
  rows := rows.push (← inspectTarget `ArtinPrimitiveRootsConjecture.artin_primitive_roots.parts.i)
  rows := rows.push (← inspectTarget `Koethe.KotheConjecture)
  rows := rows.push (← inspectTarget `LehmerTotient.lehmer_totient)
  rows := rows.push (← inspectTarget `LehmerMahlerMeasureProblem.lehmer_mahler_measure_problem)
  rows := rows.push (← inspectTarget `Borsuk.borsuk_conjecture.four)
  rows := rows.push (← inspectTarget `Erdos107.erdos_107)
  rows := rows.push (← inspectTarget `ScholzConjecture.scholz_conjecture)
  IO.FS.writeFile "/audit/evidence/elaborated-targets.json" (toJson rows).pretty
