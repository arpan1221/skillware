import pytest
from pydantic import BaseModel, ValidationError

from skillware.extract.adapters import FixtureAdapter, FixtureUnavailableError


class ExampleResponse(BaseModel):
    statement: str


def test_fixture_response_is_typed_with_reproducible_provenance():
    adapter = FixtureAdapter({"example": '{"statement":"Keep source evidence."}'})
    first, receipt = adapter.generate_json(
        "example", {"text": "Keep source evidence."}, ExampleResponse
    )
    second, repeated_receipt = adapter.generate_json(
        "example", {"text": "Keep source evidence."}, ExampleResponse
    )
    assert first == second == ExampleResponse(statement="Keep source evidence.")
    assert receipt == repeated_receipt
    assert (receipt.adapter_id, receipt.provider_id, receipt.model_id) == (
        "fixture",
        "fixture",
        "fixture",
    )
    _, changed_receipt = adapter.generate_json(
        "example", {"text": "Different input."}, ExampleResponse
    )
    assert receipt.input_sha256 != changed_receipt.input_sha256
    assert len(receipt.schema_sha256) == len(receipt.response_sha256) == 64


@pytest.mark.parametrize("response", ["not json", "{}", '{"statement":42}'])
def test_invalid_fixture_response_fails_validation(response):
    adapter = FixtureAdapter({"example": response})
    with pytest.raises(ValidationError):
        adapter.generate_json("example", {}, ExampleResponse)


def test_missing_fixture_has_no_fallback():
    adapter = FixtureAdapter({})
    with pytest.raises(FixtureUnavailableError, match="No fixture for task"):
        adapter.generate_json("missing", {}, ExampleResponse)
