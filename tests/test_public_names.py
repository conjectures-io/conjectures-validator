"""Names other people read: what `public_names` lets through and what it refuses."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from conjectures_subnet import public_names
from conjectures_subnet.attribution import public_credit, public_credit_from_values

# The display name an account on dev and production actually carries.
XSS_PROBE = "<img src=x onerror=alert(document.domain)>"


@pytest.mark.parametrize(
    "name",
    [
        "Ada",
        "rob_dendrite",
        "JenW1N",
        "Thomas Helm",
        "Sofía Kovalevskaya",
        "Erdős Pál",
        "O'Brien",
        "O’Brien",
        "Jean-Pierre Serre",
        "J. R. Smith, Jr.",
        "Smith & Jones",
        "Noether Group (Göttingen)",
        "李小龙",
        "Παύλος",
        "Ибрагим",
        "مريم",
        "é́́",  # three combining marks on one base: still a name
        "2048",
    ],
)
def test_real_names_and_handles_are_accepted(name: str) -> None:
    assert public_names.name_problem(name) is None


@pytest.mark.parametrize(
    "name",
    [
        XSS_PROBE,
        "<script>alert(1)</script>",
        'Ada" onmouseover="x',
        "`Ada`",
        "{{7*7}}",
        "${7*7}",
        "[link](https://example.org)",
        "back\\slash",
        "@everyone",
        "ada@example.org",
        "javascript:alert(1)",
        "Ada‮ecilA",  # right-to-left override: renders as another name
        "Ada​Lovelace",  # zero-width space
        "Ada Lovelace",  # no-break space passes for a space but is not one
        "Ada\nLovelace",
        "Ada\tLovelace",
        "Ada  Lovelace",
        "...",
        "-",
        "é́́́",  # stacked marks drawing over the neighbours
    ],
)
def test_markup_invisible_and_stacked_characters_are_refused(name: str) -> None:
    assert public_names.name_problem(name) is not None


def test_a_display_name_is_trimmed_and_normalised_before_it_is_checked() -> None:
    assert public_names.display_name("  Ada  ") == "Ada"
    # Decomposed é becomes the composed one, so two spellings of a name are one name.
    assert public_names.display_name("Sofía") == "Sofía"
    with pytest.raises(ValueError, match="1-64 characters"):
        public_names.display_name("   ")
    with pytest.raises(ValueError, match="1-64 characters"):
        public_names.display_name("a" * 65)
    with pytest.raises(ValueError, match="U\\+003C"):
        public_names.display_name(XSS_PROBE)


def test_a_new_credit_name_is_held_to_the_same_rule_without_being_rewritten() -> None:
    with pytest.raises(ValueError, match="public credit name"):
        public_credit(XSS_PROBE)
    with pytest.raises(ValueError, match="public credit name"):
        public_credit("Ada  Lovelace")
    credit = public_credit("Emmy Noether")
    assert credit is not None and credit.name == "Emmy Noether"


def test_a_stored_credit_from_before_the_rule_still_reads() -> None:
    """Results re-read stored credit on every page. Refusing an old row there would be a 500."""
    stored = SimpleNamespace(
        public_credit_name="<b>Old</b>", public_credit_url=None, public_credit_orcid=None
    )
    credit = public_credit_from_values(stored)
    assert credit is not None and credit.name == "<b>Old</b>"
