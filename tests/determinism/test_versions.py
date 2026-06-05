"""determinism tests for `stable_hash` and producer-version composition.

proof target: stable hashing / producer-version helpers are deterministic.
"""

from __future__ import annotations

import pytest

from extractx.core import (
    algorithmic_producer_version,
    soft_producer_version,
    stable_hash,
)


class TestStableHash:
    def test_identical_inputs_identical_hashes(self) -> None:
        a = stable_hash({"a": 1, "b": [1, 2, 3]})
        b = stable_hash({"a": 1, "b": [1, 2, 3]})
        assert a == b

    def test_key_order_does_not_matter(self) -> None:
        a = stable_hash({"a": 1, "b": 2})
        b = stable_hash({"b": 2, "a": 1})
        assert a == b

    def test_different_values_different_hashes(self) -> None:
        a = stable_hash({"a": 1})
        b = stable_hash({"a": 2})
        assert a != b

    def test_tuples_and_lists_canonicalize_equal(self) -> None:
        # tuple and list are the same shape at the json layer.
        a = stable_hash((1, 2, 3))
        b = stable_hash([1, 2, 3])
        assert a == b

    def test_nested_structure_deterministic(self) -> None:
        value = {
            "fields": [
                {"field_id": "a", "depends_on": ["b"]},
                {"field_id": "b", "depends_on": []},
            ],
            "version": 1,
        }
        assert stable_hash(value) == stable_hash(value)


class TestProducerVersionHelpers:
    def test_algorithmic_shape(self) -> None:
        assert algorithmic_producer_version("abc123") == "code:abc123"

    def test_algorithmic_rejects_empty(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            algorithmic_producer_version("")

    def test_soft_shape(self) -> None:
        v = soft_producer_version(
            model_id="gpt-5",
            prompt_template_hash="sha256:deadbeef",
            code_hash="abc123",
        )
        assert v == "gpt-5|sha256:deadbeef|abc123"

    def test_soft_rejects_missing_model(self) -> None:
        with pytest.raises(ValueError, match="model_id"):
            soft_producer_version(
                model_id="",
                prompt_template_hash="x",
                code_hash="y",
            )

    def test_soft_rejects_missing_prompt_hash(self) -> None:
        with pytest.raises(ValueError, match="prompt_template_hash"):
            soft_producer_version(
                model_id="gpt-5",
                prompt_template_hash="",
                code_hash="y",
            )

    def test_soft_rejects_missing_code_hash(self) -> None:
        with pytest.raises(ValueError, match="code_hash"):
            soft_producer_version(
                model_id="gpt-5",
                prompt_template_hash="x",
                code_hash="",
            )
