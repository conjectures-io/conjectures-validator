import json

from verifier.catalog import find_declaration, load_catalog

from conftest import catalog, declaration


def test_load_and_find_catalog(tmp_path):
    item = declaration()
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(catalog(item).to_dict()), encoding="utf-8")
    assert find_declaration(load_catalog(path), item.theorem) == item
