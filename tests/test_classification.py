from verifier.classification import catalog_statistics
from verifier.models import Classification

from conftest import declaration


def test_statistics_have_all_exact_buckets():
    stats = catalog_statistics(
        (
            declaration(),
            declaration(theorem="Fixture.propAnswer", classification=Classification.PROP_ANSWER_WRAPPER),
            declaration(theorem="Fixture.general", classification=Classification.GENERAL_VALUE_ANSWER),
            declaration(theorem="Fixture.unsupported", classification=Classification.UNSUPPORTED, category="test"),
        )
    )
    assert stats["total_declarations"] == 4
    assert stats["by_category"]["research open"] == 3
    assert stats["by_classification"]["DIRECT_PROP"] == 1
    assert stats["adapter_required"] == 2
    assert stats["unsupported"] == 1
