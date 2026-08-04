from __future__ import annotations

from collections import Counter
from typing import Iterable

from verifier.models import (
    ADAPTER_REQUIRED_CLASSIFICATIONS,
    AUTOMATIC_CLASSIFICATIONS,
    CATEGORY_ORDER,
    CLASSIFICATION_ORDER,
    CatalogDeclaration,
    Classification,
)


def supported_modes(classification: Classification) -> tuple[str, ...]:
    if classification == Classification.DIRECT_PROP:
        return ("formalized", "counterexample")
    if classification == Classification.BOOL_ANSWER:
        return ("answer",)
    if classification in {
        Classification.NAT_ANSWER,
        Classification.INT_ANSWER,
        Classification.FINITE_ANSWER,
    }:
        return ("answer",)
    if classification == Classification.POINTER_DECLARATION:
        return ()
    return ()


def catalog_statistics(declarations: Iterable[CatalogDeclaration]) -> dict[str, object]:
    items = tuple(declarations)
    categories = Counter(item.category for item in items)
    classes = Counter(item.classification.value for item in items)
    return {
        "total_declarations": len(items),
        "by_category": {name: categories[name] for name in CATEGORY_ORDER},
        "by_classification": {name: classes[name] for name in CLASSIFICATION_ORDER},
        "automatically_taskable": sum(item.classification in AUTOMATIC_CLASSIFICATIONS for item in items),
        "adapter_required": sum(item.classification in ADAPTER_REQUIRED_CLASSIFICATIONS for item in items),
        "unsupported": classes[Classification.UNSUPPORTED.value],
    }


def is_adapter_required(classification: Classification) -> bool:
    return classification in ADAPTER_REQUIRED_CLASSIFICATIONS


def is_automatic(classification: Classification) -> bool:
    return classification in AUTOMATIC_CLASSIFICATIONS
