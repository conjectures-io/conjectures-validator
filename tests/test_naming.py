"""The display name derived from a conjecture's module path and theorem name.

Pure functions over two strings, so no database and no HTTP — the endpoints that publish this are
covered in test_api_catalog.py and test_api_results.py.

Two kinds of test here, and the second is the one that matters. The parametrised cases pin the
shapes a reader will actually see, one per upstream collection. `test_no_title_in_the_pinned_catalog
_reads_as_a_lean_identifier` then sweeps all 3267 declarations in `data/catalog.json`, because this
is a parser over data nobody in this repository wrote: the next pin rotation can introduce a module
shape no hand-written case anticipated, and the failure mode is silent — a heading with
`maximalLength_ge_of_isSquare` in it renders perfectly well.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

import pytest

from submission_api.naming import ordinal, problem_name, segments, words_of

# One per collection in the pinned catalog, plus the shapes that broke a client-side parser.
# (module, theorem, display_title, collection, reference, qualifier)
NAMES = (
    # --- numbered collections: the identifier lives in guillemets, because a bare digit is not a
    # legal Lean module name.
    (
        "FormalConjectures.ErdosProblems.«1»",
        "Erdos1.erdos_1",
        "Erdős problem 1",
        "erdos_problems",
        "1",
        None,
    ),
    (
        # The commonest qualified shape in the pool.
        "FormalConjectures.ErdosProblems.«1»",
        "Erdos1.erdos_1.variants.lb",
        "Erdős problem 1 — lb",
        "erdos_problems",
        "1",
        "lb",
    ),
    (
        # Both markers at once: part i of the monotone variant. Upstream nests the qualifier rather
        # than the variant, so this is one dotted name and not two.
        "FormalConjectures.ErdosProblems.«357»",
        "Erdos357.erdos_357.variants.monotone.parts.i",
        "Erdős problem 357 — monotone, part i",
        "erdos_problems",
        "357",
        "monotone, part i",
    ),
    (
        # No marker, and the declaration is not the canonical `erdos_100`. What distinguishes it is
        # everything after the problem's own number.
        "FormalConjectures.ErdosProblems.«100»",
        "Erdos100.erdos_100_piepmeyer",
        "Erdős problem 100 — piepmeyer",
        "erdos_problems",
        "100",
        "piepmeyer",
    ),
    (
        # `erdos_330_statement` is problem 330, not a variant of it: a residual made of nothing but
        # filler words is not a qualifier.
        "FormalConjectures.ErdosProblems.«330»",
        "Erdos330.erdos_330_statement",
        "Erdős problem 330",
        "erdos_problems",
        "330",
        None,
    ),
    (
        # Which side of the number the informative word falls on is not fixed: "piepmeyer" above
        # trails it, "large" here leads it. Slicing at the number would silently publish this as
        # plain "Green's open problem 4", colliding with `green_4` itself.
        "FormalConjectures.GreensOpenProblems.«4»",
        "Green4.large_green_4",
        "Green's open problem 4 — large",
        "greens_open_problems",
        "4",
        "large",
    ),
    (
        # An ordinal, and the trap in it: a digit always ends a word, so `hilbert_17th_problem`
        # splits as "hilbert | 17 | th | problem" and dropping the number has to drop the "th".
        "FormalConjectures.HilbertProblems.«17»",
        "Hilbert17.hilbert_17th_problem",
        "Hilbert's 17th problem",
        "hilbert_problems",
        "17th",
        None,
    ),
    (
        "FormalConjectures.OEIS.«228828»",
        "OeisA228828.a_0",
        "OEIS sequence A228828 — a 0",
        "oeis",
        "A228828",
        "a 0",
    ),
    (
        "FormalConjectures.GreensOpenProblems.«14»",
        "Green14.W_3_10",
        "Green's open problem 14 — W 3 10",
        "greens_open_problems",
        "14",
        "W 3 10",
    ),
    (
        "FormalConjectures.Mathoverflow.«17560»",
        "Mathoverflow17560.mathoverflow_17560.variants.all_nats",
        "MathOverflow question 17560 — all nats",
        "mathoverflow",
        "17560",
        "all nats",
    ),
    (
        # Upstream writes the notebook's `19.25` as `«19_25»`, a dot being illegal in a module name.
        # The theorem then repeats the whole identity twice and adds nothing.
        "FormalConjectures.Kourovka.«19_25»",
        "Kourovka.«19.25».kourovka.«19.25»",
        "Kourovka notebook problem 19.25",
        "kourovka",
        "19.25",
        None,
    ),
    (
        "FormalConjectures.OpenQuantumProblems.«13»",
        "OpenQuantumProblem13.Qubit.bloch",
        "Open quantum problem 13 — bloch",
        "open_quantum_problems",
        "13",
        "bloch",
    ),
    # --- an arXiv id keeps its own dot inside the guillemets, and the paper's module name is the
    # part a reader is looking for. Both are in the phrase.
    (
        "FormalConjectures.Arxiv.«0912.2382».CurlingNumberConjecture",
        "Arxiv.«0912.2382».curling_number_conjecture",
        "Curling Number Conjecture (arXiv:0912.2382)",
        "arxiv",
        "0912.2382",
        None,
    ),
    (
        # Two conjectures from one paper, distinguished by nothing but their numbering. Swallowing
        # "conjecture 1 3" as filler would give both the same title.
        "FormalConjectures.Arxiv.«2303.01089».FurstenbergTimesPTimesQ",
        "Arxiv.id2303_01089.conjecture_1_3",
        "Furstenberg Times P Times Q (arXiv:2303.01089) — conjecture 1 3",
        "arxiv",
        "2303.01089",
        "conjecture 1 3",
    ),
    # --- collections whose modules are names rather than numbers.
    (
        "FormalConjectures.Wikipedia.CollatzConjecture",
        "CollatzConjecture.collatz",
        "Collatz Conjecture",
        "wikipedia",
        "Collatz Conjecture",
        None,
    ),
    (
        # A bare acronym: `ABC` is the whole name upstream gives it, and re-spacing it is a no-op.
        "FormalConjectures.Wikipedia.ABC",
        "ABC.abc.variants.quality",
        "ABC — quality",
        "wikipedia",
        "ABC",
        "quality",
    ),
    (
        # The one identifier whose word boundaries cannot be found mechanically. Re-spaced from a
        # table, which adds no characters: every rule that splits `vs` out of `PvsNP` also turns
        # `Proof` into "Pro of".
        "FormalConjectures.Millenium.PvsNP",
        "ComplexityTheory.P_ne_NP",
        "P vs NP — P ne NP",
        "millennium",
        "P vs NP",
        "P ne NP",
    ),
    (
        "FormalConjectures.Paper.CasasAlvero",
        "CasasAlvero.casas_alvero.prime_power",
        "Casas Alvero — prime power",
        "paper",
        "Casas Alvero",
        "prime power",
    ),
    (
        # Numbered, but in the module name rather than in guillemets. The collection is named in the
        # phrase because "Graph Conjecture 100" alone does not say whose.
        "FormalConjectures.WrittenOnTheWallII.GraphConjecture100",
        "WrittenOnTheWallII.GraphConjecture100.conjecture100",
        "Written on the Wall II, Graph Conjecture 100",
        "written_on_the_wall_ii",
        "Graph Conjecture 100",
        None,
    ),
    (
        # Lean mangles a private declaration into a `_private.…0.` prefix. Reading the last segment
        # rather than stripping a list of known prefixes is what makes this an ordinary case.
        "FormalConjectures.LittProblems.«1»",
        "_private.FormalConjectures.LittProblems.«1».0.LamLitt.den_coprime_of_mem_adjoinInvNat",
        "Litt problem 1 — den coprime of mem adjoin Inv Nat",
        "litt_problems",
        "1",
        "den coprime of mem adjoin Inv Nat",
    ),
)


@pytest.mark.parametrize("module,theorem,display_title,collection,reference,qualifier", NAMES)
def test_a_conjecture_is_named_from_its_module_and_theorem(
    module, theorem, display_title, collection, reference, qualifier
):
    name = problem_name(module=module, theorem=theorem)
    assert name.display_title == display_title
    assert name.collection == collection
    assert name.reference == reference
    assert name.qualifier == qualifier


def test_the_display_title_is_the_parts_joined():
    """`display_title` is published as well as its parts, so it must agree with them."""
    name = problem_name(
        module="FormalConjectures.ErdosProblems.«1»", theorem="Erdos1.erdos_1.variants.lb"
    )
    assert name.display_title.startswith("Erdős problem 1")
    assert name.display_title.endswith(name.qualifier)
    assert name.reference in name.display_title


def test_a_collection_nobody_taught_this_about_still_reads_as_words():
    """Upstream adds collections. An unrecognised one must degrade to prose, not to Lean.

    This is the whole reason the namespace lookup has no `else: raise`. A pin that introduced a new
    directory would otherwise take out the catalog endpoints at startup, or publish an identifier.
    """
    name = problem_name(
        module="FormalConjectures.ImaginaryNewCollection.SomeNamedProblem",
        theorem="SomeNamedProblem.some_named_problem.variants.weak",
    )
    assert name.display_title == "Some Named Problem — weak"
    assert name.collection == "imaginarynewcollection"
    assert name.collection_label == "Imaginary New Collection"
    assert name.qualifier == "weak"


def test_a_guillemet_segment_is_one_segment():
    """An arXiv id carries a dot, so splitting a module on "." would shred it."""
    assert segments("FormalConjectures.Arxiv.«2303.01089».Paper") == [
        "FormalConjectures",
        "Arxiv",
        "«2303.01089»",
        "Paper",
    ]


def test_re_spacing_only_ever_inserts_spaces():
    """The line this module does not cross: no character is added, dropped or respelled.

    Guillemets, underscores and dots are separators rather than characters, so they go; everything
    else survives, capitalisation included. Lower-casing would read better for `CollatzConjecture`
    and would wreck the several hundred surnames in the pool.
    """
    for identifier in ("CollatzConjecture", "casas_alvero", "«19.25»", "isBigO", "GraphConjecture1"):
        stripped = re.sub(r"[^0-9A-Za-zÀ-ῼ]", "", identifier)
        assert re.sub(r"[^0-9A-Za-zÀ-ῼ]", "", words_of(identifier)) == stripped, identifier


@pytest.mark.parametrize(
    "number,expected",
    (("1", "1st"), ("2", "2nd"), ("3", "3rd"), ("4", "4th"), ("11", "11th"), ("17", "17th"),
     ("21", "21st"), ("13", "13th")),
)
def test_an_ordinal_reads_as_one(number, expected):
    assert ordinal(number) == expected


# --- the sweep -----------------------------------------------------------------------------------

# What it means for a title to have failed: snake_case, a guillemet, or a camel hump inside one
# token. `arXiv:` and `MathOverflow` are how their own sources spell themselves.
BRANDING = re.compile(r"^\(?(?:arXiv:\S+|MathOverflow)\)?[,.]?$")
CAMEL_HUMP = re.compile(r"[a-z][A-Z]")
RAW_LEAN = re.compile(r"[_«»]")


def _reads_as_lean(title: str) -> bool:
    if RAW_LEAN.search(title):
        return True
    return any(
        CAMEL_HUMP.search(token) and not BRANDING.match(token) for token in title.split()
    )


def test_no_title_in_the_pinned_catalog_reads_as_a_lean_identifier():
    """The guarantee the FE is being given, checked against every declaration rather than examples.

    A client that renders `display_title` should never have to fall back to parsing, so a single
    surviving `maximalLength_ge_of_isSquare` is a bug — and one that renders without complaint.
    """
    declarations = _declarations()
    for entry in declarations:
        name = problem_name(module=entry["module"], theorem=entry["theorem"])
        assert not _reads_as_lean(name.display_title), (name.display_title, entry["theorem"])
        if name.qualifier is not None:
            assert not _reads_as_lean(name.qualifier), name.qualifier


def test_every_declaration_in_the_pinned_catalog_gets_a_name():
    """No blanks and no unknown collections, which are the two ways this fails quietly.

    An empty `display_title` renders as an empty heading; `unknown` means a namespace the lookup
    does not carry, which is survivable but is a signal that the pin moved and the table did not.
    """
    declarations = _declarations()
    named = [problem_name(module=x["module"], theorem=x["theorem"]) for x in declarations]

    assert all(name.display_title.strip() for name in named)
    assert all(name.reference.strip() for name in named)
    assert all(name.collection_label.strip() for name in named)

    collections = Counter(name.collection for name in named)
    assert collections["unknown"] == 0, collections
    # Guard the guard: a sweep that resolved one collection would pass everything above.
    assert len(collections) >= 15, collections


def test_a_display_title_is_not_an_identity_and_the_catalog_proves_it():
    """Pinned deliberately, because a client that keyed on `display_title` would look fine at first.

    Upstream files several root theorems in one module and distinguishes some of them only by a
    middle namespace segment — `Erdos633.IsCuttable.ne_zero` against
    `Erdos633.IsSimiliCuttable.ne_zero` — which a title built from the problem and the declaration
    name does not carry. It is a handful out of thousands, and the alternative is putting Lean back
    in the heading. `slug` is the key.
    """
    declarations = _declarations()
    titles = Counter(
        problem_name(module=x["module"], theorem=x["theorem"]).display_title
        for x in declarations
    )
    shared = {title: count for title, count in titles.items() if count > 1}
    assert shared, "no collisions at all would mean this caveat is obsolete — drop it"
    # Small enough that it is a caveat rather than a defect. If a pin rotation pushes it up, the
    # rule for building a qualifier has probably stopped fitting the data.
    assert len(shared) < len(declarations) // 100, len(shared)


def _declarations() -> list[dict]:
    catalog = json.loads(
        (Path(__file__).resolve().parent.parent / "data" / "catalog.json").read_text(
            encoding="utf-8"
        )
    )
    declarations = catalog["declarations"]
    # Guard the guard: an empty sweep would make both tests above vacuous.
    assert len(declarations) > 3000, len(declarations)
    return declarations
