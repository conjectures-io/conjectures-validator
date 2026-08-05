from __future__ import annotations

import re
from pathlib import Path


REFERENCE_HEADING = re.compile(
    r"^\s*(?:\*{1,2}References?(?::\*{1,2}|\*{1,2}:?)|References?:)\s*(.*?)\s*$"
)
REFERENCE_ITEM = re.compile(r"^\s*[-*]\s+(.+?)\s*$")
REFERENCE_KEY = re.compile(r"^\[([^\]]+)\](\s+.+)$")
MARKDOWN_LINK = re.compile(r"\[[^\]]+\]\(https?://[^)\s]+\)")

# The pinned Formal Conjectures docstrings include several older bibliography
# entries as citation keys without URLs. Keep their wording intact while
# pairing each key with a stable publication, preprint, or archive record.
REFERENCE_URLS_BY_KEY = {
    "AbHa70": "https://doi.org/10.1112/blms/2.3.324",
    "BM22": "https://arxiv.org/abs/2211.07689",
    "CFS14": "https://arxiv.org/abs/1310.0632",
    "EGP66": "https://doi.org/10.4153/CJM-1966-014-3",
    "Er46": "https://doi.org/10.2307/2305092",
    "Er64": "https://users.renyi.hu/~p_erdos/1964-10.pdf",
    "Er71": "https://users.renyi.hu/~p_erdos/1971-25.pdf",
    "Er73": "https://users.renyi.hu/~p_erdos/1973-21.pdf",
    "Er82c": "https://users.renyi.hu/~p_erdos/1982-08.pdf",
    "ErGy99": "https://doi.org/10.1016/S0012-365X(98)00323-9",
    "GuKa15": "https://doi.org/10.4007/annals.2015.181.1.2",
    "He88": "https://oeis.org/A187054",
    "Mo52": "https://doi.org/10.2307/2307105",
    "Si56": "https://zbmath.org/?q=an%3A0075.03302",
}


def _with_reference_link(reference: str) -> str:
    if MARKDOWN_LINK.search(reference):
        return reference
    keyed = REFERENCE_KEY.fullmatch(reference)
    if keyed is None:
        return reference
    key, citation = keyed.groups()
    url = REFERENCE_URLS_BY_KEY.get(key)
    return f"[{key}]({url}){citation}" if url else reference


def extract_module_references(source: Path) -> tuple[str, ...]:
    """Extract Markdown reference entries from a Lean module docstring."""
    text = source.read_text(encoding="utf-8")
    start = text.find("/-!")
    if start < 0:
        return ()
    end = text.find("-/", start + 3)
    if end < 0:
        return ()

    lines = text[start + 3 : end].splitlines()
    for index, line in enumerate(lines):
        heading = REFERENCE_HEADING.fullmatch(line)
        if heading is None:
            continue
        inline = heading.group(1)
        if inline:
            return (_with_reference_link(inline),)

        candidates = lines[index + 1 :]
        first = next(
            (offset for offset, candidate in enumerate(candidates) if candidate.strip()),
            None,
        )
        if first is None:
            return ()
        if REFERENCE_ITEM.fullmatch(candidates[first]) is None:
            return (_with_reference_link(candidates[first].strip()),)

        references: list[str] = []
        current: list[str] = []
        for candidate in candidates[first:]:
            item = REFERENCE_ITEM.fullmatch(candidate)
            if item is not None:
                if current:
                    references.append(" ".join(current))
                current = [item.group(1)]
                continue
            if current and candidate[:1].isspace() and candidate.strip():
                current.append(candidate.strip())
                continue
            if current:
                references.append(" ".join(current))
            break
        else:
            if current:
                references.append(" ".join(current))
        return tuple(_with_reference_link(reference) for reference in references)
    return ()
