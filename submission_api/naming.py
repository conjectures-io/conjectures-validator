"""A human-facing name for a conjecture, derived from the identifiers upstream actually publishes.

Upstream has no human titles. `conjectures.title` is the fully-qualified source theorem —
`Erdos1.erdos_1.variants.lb` — which is the right thing to *cite* and the wrong thing to put in a
heading. Every client that renders the catalog therefore ends up parsing that identifier, and a
client-side parser gets the common shapes right and leaves raw Lean in the UI for the rest.

So the parse lives here instead, once, against the whole pinned catalog rather than against the
shapes one reader happened to notice.

**Derived, never invented.** Everything published here is a re-spacing of tokens that are already
in the audited module path and theorem name: `«17»` under `HilbertProblems` becomes "Hilbert's 17th
problem", and `CollatzConjecture` becomes "Collatz Conjecture". No name is supplied from outside
the catalog and no mathematical claim is attached to one — that is the line this module does not
cross, for the same reason `title` is not prose. `COLLECTION_PHRASES` names each collection and
says how its numbering reads; it adds no fact about any individual problem.

**Not an identity.** A display title names the *problem*, so the several root theorems upstream
files in one module share one. `slug` remains the unique key, and it is what a client should sort,
group and link on. Two rows reading "Erdős problem 1" are two formalisations of one problem, which
is the honest rendering of what the pool holds.

The three published parts — `collection`, `reference`, `qualifier` — are the pieces the phrase is
built from, so a client that wants a different wording can compose its own without going back to
parsing Lean. `reference` generalises the `erdos_problem_number` the problem index already
publishes: every collection has an identifier, and only one of them is an Erdős number.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Lean qualifies names with dots but also permits `«...»` around a segment that is not a legal
# identifier — which is how upstream files a numbered problem, and how an arXiv id keeps its own
# dot. So splitting a module or theorem on "." is wrong: `«2303.01089»` is one segment. Every
# reader here goes through `segments`.
SEGMENT = re.compile(r"«[^»]*»|[^.]+")
GUILLEMETS = re.compile(r"^«(.*)»$")
# Where a word boundary falls inside a Lean identifier. Deliberately conservative: it inserts
# spaces and changes no characters, so the result is always the upstream tokens re-spaced.
WORD_BOUNDARY = re.compile(
    r"(?<=[a-z0-9])(?=[A-Z])"  # collatzConjecture -> collatz|Conjecture
    r"|(?<=[A-Z])(?=[A-Z][a-z])"  # ABCConjecture    -> ABC|Conjecture
    r"|(?<=[A-Za-z])(?=[0-9])"  # GraphConjecture1 -> …Conjecture|1
    r"|(?<=[0-9])(?=[A-Za-z])"  # 1a               -> 1|a
)
NON_ALNUM = re.compile(r"[^a-z0-9]+")

# The two qualifier markers upstream uses. `variants` is a sibling formalisation of the same
# problem — a weaker form, a one-sided bound, a named special case. `parts` is one numbered part of
# a problem stated in several, so it reads as "part iii" rather than as a variant's name.
VARIANTS = "variants"
PARTS = "parts"

# Words that say only "this is the problem" and so carry no information once the problem is already
# named. A residual made of nothing but these is not a qualifier: `erdos_330_statement` is Erdős
# problem 330, not a variant of it. A residual that also carries a number is kept — `conjecture_1_3`
# and `conjecture_1_4` are two different conjectures in one paper, and dropping "conjecture 1 3"
# would give both the same title.
FILLER = frozenset(("problem", "conjecture", "theorem", "statement", "question", "prob"))

ORDINAL_SUFFIXES = frozenset(("st", "nd", "rd", "th"))

# Identifiers whose word boundaries cannot be found mechanically, mapped to the same characters with
# the spaces put in. Re-spacing only: nothing may be added, removed or respelled here, which is what
# keeps this from becoming a table of invented names. `PvsNP` is the case that needs it — every rule
# that splits `vs` out of it also splits `Proof` into "Pro of".
RESPACED = {
    "PvsNP": "P vs NP",
}


@dataclass(frozen=True)
class Collection:
    """One upstream collection, and how a reference into it reads.

    `phrase` is the sentence fragment naming a problem. `{reference}` is the identifier within the
    collection and `{name}` the module's own name for it; a collection with no `phrase` is named by
    its module tail alone, because its modules are already names — `CollatzConjecture`,
    `RiemannHypothesis` — rather than numbers.
    """

    key: str
    label: str
    phrase: str | None = None
    # How the guillemet-quoted module segment is rendered as a reference.
    reference: str = "{raw}"


# Keyed by the namespace directly under `FormalConjectures`. A namespace absent from here is not an
# error: it gets its key and label from its own name and is named by its module tail, which is what
# every unnumbered collection already does. So a collection added upstream renders as readable words
# on the first pin that carries it, rather than falling back to a Lean identifier.
COLLECTION_PHRASES = {
    "ErdosProblems": Collection("erdos_problems", "Erdős problems", "Erdős problem {reference}"),
    "GreensOpenProblems": Collection(
        "greens_open_problems", "Green's open problems", "Green's open problem {reference}"
    ),
    "OEIS": Collection("oeis", "OEIS", "OEIS sequence {reference}", "A{raw}"),
    "OpenQuantumProblems": Collection(
        "open_quantum_problems", "Open quantum problems", "Open quantum problem {reference}"
    ),
    "HilbertProblems": Collection(
        "hilbert_problems", "Hilbert's problems", "Hilbert's {reference} problem", "{ordinal}"
    ),
    "Mathoverflow": Collection(
        "mathoverflow", "MathOverflow", "MathOverflow question {reference}"
    ),
    # The paper's own module name leads, because "Curling Number Conjecture" is what a reader is
    # looking for and the identifier is what they cite. Both are in the phrase.
    "Arxiv": Collection("arxiv", "arXiv", "{name} (arXiv:{reference})"),
    # Upstream writes the notebook's `19.25` as `«19_25»`, a dot being illegal in a module name.
    "Kourovka": Collection(
        "kourovka", "Kourovka notebook", "Kourovka notebook problem {reference}", "{dotted}"
    ),
    "LittProblems": Collection("litt_problems", "Litt's problems", "Litt problem {reference}"),
    "OptimizationConstants": Collection(
        "optimization_constants", "Optimization constants", "Optimization constant {reference}"
    ),
    # Numbered, but in the module name rather than in guillemets: `GraphConjecture100`. The
    # collection is named in the phrase because "Graph Conjecture 100" alone does not say whose.
    "WrittenOnTheWallII": Collection(
        "written_on_the_wall_ii", "Written on the Wall II", "Written on the Wall II, {name}"
    ),
    "Wikipedia": Collection("wikipedia", "Wikipedia"),
    "Paper": Collection("paper", "Papers"),
    "Books": Collection("books", "Books"),
    # Upstream spells the directory "Millenium". The label is spelled correctly because it is prose
    # shown to a reader; the key follows the label, not the typo, so a client is not made to carry
    # it either. The lookup above is what has to match upstream, and it does.
    "Millenium": Collection("millennium", "Millennium Prize problems"),
    "Other": Collection("other", "Other"),
}


@dataclass(frozen=True)
class ProblemName:
    """A conjecture's name for a reader, and the parts it was built from.

    `display_title` is composed from the other three. It is published as well as them because the
    composition is a wording decision — where the em dash goes, whether the collection is named —
    and having every client re-make it is how two surfaces of one API come to disagree.
    """

    display_title: str
    collection: str
    collection_label: str
    # The identifier within the collection: "1", "A228828", "2303.01089", "17th", "Collatz
    # Conjecture". Never null — a collection that does not number its problems names them.
    reference: str
    # Which formalisation of the problem this is, in words, or None when it is the problem itself.
    qualifier: str | None


def problem_name(*, module: str, theorem: str) -> ProblemName:
    """The display name for the conjecture declared as `theorem` in `module`.

    Takes the two strings rather than a `Conjecture` so it serves a retired target too: a
    `RetiredConjecture` carries the same `CatalogDeclaration` and no manifest, and naming a
    conjecture must not depend on whether it can still be submitted against.
    """
    parts = segments(module)
    namespace = parts[1] if len(parts) > 1 else (parts[0] if parts else "")
    tail = parts[2:]
    collection = COLLECTION_PHRASES.get(namespace)

    numbered = bare(tail[0]) if tail and GUILLEMETS.match(tail[0]) else None
    named = ", ".join(words_of(segment) for segment in (tail[1:] if numbered else tail))
    if not named:
        named = words_of(namespace)

    reference = _reference(collection, numbered=numbered, named=named)
    phrase = collection.phrase if collection is not None else None
    base = (
        reference
        if phrase is None
        else phrase.format(reference=reference, name=named)
    )

    qualifier = _qualifier(
        theorem,
        # Every way this module names itself, so a declaration that merely repeats one of them is
        # recognised as the problem's own statement rather than published as a variant of itself.
        identities=frozenset(slugify(segment) for segment in parts),
        numbered=numbered,
    )
    return ProblemName(
        display_title=base if qualifier is None else f"{base} - {qualifier}",
        collection=collection.key if collection is not None else slugify(namespace) or "unknown",
        collection_label=collection.label if collection is not None else words_of(namespace),
        reference=reference,
        qualifier=qualifier,
    )


def segments(dotted: str) -> list[str]:
    """A dotted Lean name as its segments, keeping a `«...»` segment whole."""
    return SEGMENT.findall(dotted)


def bare(segment: str) -> str:
    return GUILLEMETS.sub(r"\1", segment)


def words_of(identifier: str) -> str:
    """A Lean identifier as spaced words, with every character it started with.

    Capitalisation is left alone. Lower-casing would read better for `CollatzConjecture` and would
    wreck `CasasAlvero`, `Kourovka` and the several hundred other surnames in the pool — and there
    is no way to tell a proper noun from a common one here.
    """
    plain = bare(identifier)
    plain = RESPACED.get(plain, plain)
    return " ".join(WORD_BOUNDARY.sub(" ", plain.replace("_", " ").replace(".", " ")).split())


def slugify(text: str) -> str:
    return NON_ALNUM.sub("", text.lower())


def ordinal(number: str) -> str:
    value = int(number)
    if 10 <= value % 100 <= 20:
        return f"{value}th"
    return f"{value}{ {1: 'st', 2: 'nd', 3: 'rd'}.get(value % 10, 'th') }"


def _reference(
    collection: Collection | None, *, numbered: str | None, named: str
) -> str:
    """The identifier within the collection, rendered for a reader."""
    if collection is None or numbered is None:
        return named
    return collection.reference.format(
        raw=numbered,
        dotted=numbered.replace("_", "."),
        ordinal=ordinal(numbered) if numbered.isdigit() else numbered,
    )


def _qualifier(theorem: str, *, identities: frozenset[str], numbered: str | None) -> str | None:
    """Which formalisation of the problem this theorem is, or None when it is the problem itself.

    Two shapes. An explicit `.variants.` or `.parts.` marker is authoritative and what follows it is
    the answer. Otherwise the declaration's own name is taken and stripped of the problem's
    identifier: what survives in `erdos_100_piepmeyer` is "piepmeyer", and nothing survives in
    `erdos_1`.

    The declaration's own name is the *last* segment, and nothing before it is read. Lean puts the
    namespace first and the declaration last, so the leading segments are always a restatement of
    the module — `Erdos1.` before `erdos_1`, `Arxiv.id2303_01089.` before `conjecture_1_3` — and
    taking the last one needs no list of prefixes to strip. It also absorbs the shapes no such list
    would have anticipated: Lean mangles a private declaration into
    `_private.FormalConjectures.LittProblems.«1».0.LamLitt.den_coprime_of_mem_adjoinInvNat`, and the
    last segment is still exactly the name.
    """
    local = segments(theorem)
    for index, segment in enumerate(local):
        if segment in (VARIANTS, PARTS):
            return _marked(local[index:]) or None

    return _residual(local[-1] if local else "", identities=identities, numbered=numbered)


def _marked(tail: list[str]) -> str:
    """Render the segments from the first qualifier marker on.

    The two markers nest: `erdos_357.variants.monotone.parts.i` is part i of the monotone variant,
    and upstream writes that as one dotted name. So `parts` becomes the word "part" wherever it
    falls rather than only at the front, and `variants` contributes nothing but the fact that what
    follows is a variant's name.
    """
    rendered: list[str] = []
    for segment in tail:
        if segment == VARIANTS:
            continue
        if segment == PARTS:
            rendered.append("part" if not rendered else ", part")
            continue
        rendered.append(words_of(segment))
    return " ".join(rendered).replace(" ,", ",").strip()


def _residual(name: str, *, identities: frozenset[str], numbered: str | None) -> str | None:
    """What a declaration's own name adds beyond naming its problem, in words, or None."""
    tokens = words_of(name).split()
    tokens = _without_identity(tokens, identities=identities, numbered=numbered)
    if not tokens or all(token.lower() in FILLER for token in tokens):
        return None
    if _restates(tokens, identities):
        return None
    return " ".join(tokens)


def _restates(tokens: list[str], identities: frozenset[str]) -> bool:
    """Whether the whole residual is the module's own name, said shorter or without its prefix.

    `CollatzConjecture.collatz` drops the last word and `GraphConjecture100.conjecture100` drops the
    first, so neither is caught by stripping a leading run — and both would otherwise publish
    "Collatz Conjecture — collatz". Bounded to three characters so a two-letter lemma name like `lb`
    cannot be swallowed by happening to sit inside some longer identifier.
    """
    slug = slugify("".join(tokens))
    if len(slug) < 3:
        return False
    return any(
        identity.startswith(slug) or identity.endswith(slug) for identity in identities
    )


def _without_identity(
    tokens: list[str], *, identities: frozenset[str], numbered: str | None
) -> list[str]:
    """Drop a leading run of tokens that spells the problem's own identifier.

    Matched on the concatenated slug rather than token by token, because the module writes the
    identifier as one camel-cased word (`CurlingNumberConjecture`) and the theorem writes it as
    several snake-cased ones (`curling_number_conjecture`). Longest run first, so
    `zariski_cancellation_problem` loses both words of `ZariskiCancellation` rather than neither.
    """
    for length in range(len(tokens), 0, -1):
        if slugify("".join(tokens[:length])) in identities:
            return tokens[length:]
    if numbered is None or numbered not in tokens:
        return tokens

    # The declaration spells out the problem's number: `erdos_100_piepmeyer`, `large_green_4`. Which
    # side of the number the informative word falls on is not fixed — "piepmeyer" trails it and
    # "large" leads it — so this drops the number and the words that only restate the collection,
    # and keeps whatever is left wherever it was.
    at = tokens.index(numbered)
    rest = tokens[:at] + tokens[at + 1 :]
    # `hilbert_17th_problem` splits as "hilbert | 17 | th | problem", because a digit always ends a
    # word. The ordinal suffix has to go with the number, or the title reads "…17th problem — th".
    if len(rest) > at and rest[at].lower() in ORDINAL_SUFFIXES:
        rest = rest[:at] + rest[at + 1 :]
    return [token for token in rest if not _names_the_collection(token, identities)]


def _names_the_collection(token: str, identities: frozenset[str]) -> bool:
    """Whether one token only repeats the collection's own name, or is a filler word.

    `erdos` in `erdos_100_piepmeyer` and `green` in `large_green_4` say nothing the phrase has not
    already said. Matched against the module's identifiers so it needs no list of collection words.
    """
    if token.lower() in FILLER:
        return True
    slug = slugify(token)
    if len(slug) < 3:
        return False
    return any(identity.startswith(slug) or identity.endswith(slug) for identity in identities)


__all__ = [
    "COLLECTION_PHRASES",
    "FILLER",
    "RESPACED",
    "Collection",
    "ProblemName",
    "ordinal",
    "problem_name",
    "segments",
    "slugify",
    "words_of",
]
