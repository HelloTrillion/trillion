import pytest

from trillion.spine.hashing import args_hash, canonical_json


def test_canonical_json_is_order_independent():
    assert canonical_json({"b": 1, "a": [1, 2]}) == canonical_json({"a": [1, 2], "b": 1})
    assert canonical_json({"a": 1}) == '{"a":1}'


def test_args_hash_is_sha256_hex():
    h = args_hash({"x": "y"})
    assert len(h) == 64
    assert h == args_hash({"x": "y"})
    assert h != args_hash({"x": "z"})


def test_nan_is_refused():
    with pytest.raises(ValueError):
        canonical_json({"x": float("nan")})
