"""A provider-neutral contract and offline fixture implementation.

No production transport is implemented in C02. Fixture success establishes only
response structure, never extraction quality or publication eligibility.
"""

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from pydantic import BaseModel


@dataclass(frozen=True)
class AdapterReceipt:
    adapter_id: str
    provider_id: str
    model_id: str
    input_sha256: str
    schema_sha256: str
    response_sha256: str


class JsonAdapter(Protocol):
    def generate_json[Response: BaseModel](
        self, task: str, payload: Mapping[str, object], response_schema: type[Response]
    ) -> tuple[Response, AdapterReceipt]: ...


class FixtureUnavailableError(LookupError):
    """The fixture adapter has no response for the requested task."""


def _digest(value: object) -> str:
    serialized = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


class FixtureAdapter:
    """Return explicit JSON fixtures without launching a provider or transport."""

    def __init__(self, responses: Mapping[str, str]):
        self._responses = dict(responses)

    def generate_json[Response: BaseModel](
        self, task: str, payload: Mapping[str, object], response_schema: type[Response]
    ) -> tuple[Response, AdapterReceipt]:
        try:
            raw_response = self._responses[task]
        except KeyError as exc:
            raise FixtureUnavailableError(f"No fixture for task: {task}") from exc
        result = response_schema.model_validate_json(raw_response)
        receipt = AdapterReceipt(
            adapter_id="fixture",
            provider_id="fixture",
            model_id="fixture",
            input_sha256=_digest({"task": task, "payload": dict(payload)}),
            schema_sha256=_digest(response_schema.model_json_schema()),
            response_sha256=hashlib.sha256(raw_response.encode("utf-8")).hexdigest(),
        )
        return result, receipt
