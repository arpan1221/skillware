from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from skillware.schema import FetchProvenance, Talk


def test_fetch_provenance_normalizes_explicit_timezone_and_preserves_hash():
    provenance = FetchProvenance(
        source_url="https://www.ai.engineer/synthetic.md",
        fetched_at="2026-11-10T18:30:00-05:00",
        content_sha256="a" * 64,
    )
    assert provenance.fetched_at == datetime(2026, 11, 10, 23, 30, tzinfo=UTC)
    assert (
        FetchProvenance.model_validate_json(provenance.model_dump_json()) == provenance
    )
    with pytest.raises(ValidationError, match="UTC offset"):
        FetchProvenance(
            source_url=provenance.source_url,
            fetched_at="2026-11-10T18:30:00",
            content_sha256="a" * 64,
        )


def test_talk_rejects_contradictory_fetch_hash():
    with pytest.raises(ValidationError, match="must match transcript_sha256"):
        Talk(
            id="synthetic",
            title="Synthetic",
            speaker="Fixture speaker",
            source_url="https://www.ai.engineer/synthetic.md",
            transcript_path="synthetic.md",
            transcript_sha256="a" * 64,
            fetch_provenance={
                "source_url": "https://www.ai.engineer/synthetic.md",
                "fetched_at": "2026-11-10T18:30:00Z",
                "content_sha256": "b" * 64,
            },
        )
