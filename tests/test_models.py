from verifier.models import CatalogDeclaration

from conftest import declaration


def test_catalog_declaration_round_trip():
    original = declaration()
    assert CatalogDeclaration.from_dict(original.to_dict()) == original
