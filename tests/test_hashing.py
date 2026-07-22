from verifier.hashing import canonical_json_bytes, sha256_bytes


def test_canonical_json_and_hash_are_stable():
    assert canonical_json_bytes({"b": 2, "a": 1}) == b'{"a":1,"b":2}\n'
    assert sha256_bytes(b"abc") == "sha256:ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
