"""Synthetic recorded HTTP exchanges; sockets are forbidden by conftest."""

import hashlib
import io
import json
from datetime import UTC, datetime, timedelta
from http.client import HTTPResponse
from pathlib import Path
from urllib.error import HTTPError

import pytest

from skillware.ingest.fetch import USER_AGENT, Response, fetch_transcripts
from skillware.schema import StageOutcome

FIXTURES = Path(__file__).parent / "fixtures" / "http" / "transcripts.json"
URL = "https://www.ai.engineer/talks/fixture.md"
SECOND = "https://www.ai.engineer/talks/second.md"
ROBOTS = "https://www.ai.engineer/robots.txt"


@pytest.mark.parametrize("directive", ["Crawl-delay: 86400", "Request-rate: 1/86400"])
def test_review_followup_excessive_robots_delay_blocks_origin(tmp_path, directive):
    robots = Response(200, {}, f"User-agent: *\nAllow: /\n{directive}\n".encode())
    wire = Recorded([(ROBOTS, robots)])
    result = wire.fetch(tmp_path, [URL, SECOND])
    assert result.outcome == StageOutcome.BLOCKED
    assert result.counts["blocked"] == 2
    assert [url for url, _, _ in wire.calls] == [ROBOTS]
    assert wire.sleeps == []
    assert json.loads(result.receipt_path.read_text())["outcome"] == "BLOCKED"


@pytest.mark.parametrize("status", [429, 503])
@pytest.mark.parametrize(
    "retry", ["99999999", "Wed, 11 Nov 2026 01:00:00 GMT", "9" * 400]
)
def test_review_followup_excessive_retry_after_blocks_later_requests(
    tmp_path, recorded, status, retry
):
    wire = Recorded(
        [
            (ROBOTS, recorded["robots"]),
            (URL, Response(status, {"Retry-After": retry})),
        ]
    )
    result = wire.fetch(tmp_path, [URL, SECOND])
    assert result.outcome == StageOutcome.BLOCKED
    assert result.counts["blocked"] == 2
    assert [url for url, _, _ in wire.calls] == [ROBOTS, URL]
    assert max(wire.sleeps, default=0) <= 300
    assert all("required delay exceeds 300 seconds" in error for error in result.errors)


def test_review_followup_delay_at_limit_is_respected(tmp_path, recorded):
    robots = Response(200, {}, b"User-agent: *\nAllow: /\nCrawl-delay: 300\n")
    wire = Recorded([(ROBOTS, robots), (URL, recorded["first"])])
    assert wire.fetch(tmp_path, [URL]).outcome == StageOutcome.PASS
    assert wire.sleeps == [300]


def test_review_followup_excessive_configured_delay_blocks_before_transport(tmp_path):
    wire = Recorded([])
    result = fetch_transcripts(
        tmp_path / "cache",
        tmp_path,
        [URL],
        transport=wire.transport,
        clock=wire.clock,
        sleeper=wire.sleep,
        now=wire.now,
        min_interval=301,
    )
    assert result.outcome == StageOutcome.BLOCKED
    assert wire.calls == wire.sleeps == []


class Recorded:
    def __init__(self, entries):
        self.entries = list(entries)
        self.calls = []
        self.seconds = 0.0
        self.sleeps = []

    def transport(self, url, headers):
        self.calls.append((url, dict(headers), self.seconds))
        expected, response = self.entries.pop(0)
        assert url == expected
        if isinstance(response, BaseException):
            raise response
        return response

    def clock(self):
        return self.seconds

    def sleep(self, delay):
        self.sleeps.append(delay)
        self.seconds += delay

    def now(self):
        return datetime(2026, 11, 11, tzinfo=UTC) + timedelta(seconds=self.seconds)

    def fetch(self, tmp_path, urls=None):
        return fetch_transcripts(
            tmp_path / "cache",
            tmp_path,
            urls,
            transport=self.transport,
            clock=self.clock,
            sleeper=self.sleep,
            now=self.now,
        )


@pytest.fixture
def recorded():
    data = json.loads(FIXTURES.read_text())
    return {
        name: Response(item["status"], item["headers"], item["body"].encode())
        for name, item in data.items()
        if isinstance(item, dict)
    }


def metadata(tmp_path, url=URL):
    key = hashlib.sha256(url.encode()).hexdigest()
    return json.loads((tmp_path / "cache" / f"{key}.json").read_text())


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
def test_review_edge_redirect_excessive_delay_blocks_origin(tmp_path, recorded, status):
    wire = Recorded(
        [
            (ROBOTS, recorded["robots"]),
            (URL, Response(status, {"Location": SECOND, "Retry-After": "600"})),
        ]
    )
    result = wire.fetch(tmp_path, [URL, SECOND])
    assert result.outcome == StageOutcome.BLOCKED
    assert result.counts["blocked"] == 2
    assert [url for url, _, _ in wire.calls] == [ROBOTS, URL]
    assert all("required delay exceeds 300 seconds" in error for error in result.errors)
    assert max(wire.sleeps, default=0) <= 300


@pytest.mark.parametrize("retry", ["Wed, 11 Nov 2026 01:00:00 GMT", "9" * 400])
def test_review_edge_redirect_excessive_delay_stops_cross_origin(
    tmp_path, recorded, retry
):
    target = "https://ai.engineer/talks/target.md"
    wire = Recorded(
        [
            (ROBOTS, recorded["robots"]),
            (URL, Response(302, {"Location": target, "Retry-After": retry})),
        ]
    )
    result = wire.fetch(tmp_path, [URL, SECOND])
    assert result.outcome == StageOutcome.BLOCKED
    assert result.counts["blocked"] == 2
    assert [url for url, _, _ in wire.calls] == [ROBOTS, URL]
    assert all("required delay exceeds 300 seconds" in error for error in result.errors)


@pytest.mark.parametrize("cross_origin", [False, True])
@pytest.mark.parametrize("retry", ["60", "Wed, 11 Nov 2026 00:01:03 GMT", "300"])
def test_review_edge_redirect_waits_before_following(
    tmp_path, recorded, cross_origin, retry
):
    target = "https://ai.engineer/talks/target.md" if cross_origin else SECOND
    entries = [
        (ROBOTS, recorded["robots"]),
        (URL, Response(302, {"Location": target, "Retry-After": retry})),
    ]
    if cross_origin:
        entries.append(("https://ai.engineer/robots.txt", recorded["robots"]))
    entries.append((target, recorded["first"]))
    wire = Recorded(entries)
    result = wire.fetch(tmp_path, [URL])
    assert result.outcome == StageOutcome.PASS
    assert not wire.entries
    required = 300 if retry == "300" else 60
    # Even the destination's robots request must wait for the redirect cooldown.
    assert wire.calls[2][2] - wire.calls[1][2] >= required
    assert max(wire.sleeps) == required


def test_review_edge_bom_fetch_ingest_preserves_original_source(tmp_path, recorded):
    from skillware.ingest.pipeline import ingest_transcripts
    from skillware.schema import Segment, Talk
    from skillware.store import Store

    raw = b"\xef\xbb\xbf" + recorded["first"].body
    wire = Recorded(
        [
            (ROBOTS, recorded["robots"]),
            (URL, Response(200, {"Content-Type": "text/markdown"}, raw)),
        ]
    )
    assert wire.fetch(tmp_path, [URL]).outcome == StageOutcome.PASS
    assert next((tmp_path / "cache").glob("*.md")).read_bytes() == raw
    db = tmp_path / "private.sqlite3"
    assert (
        ingest_transcripts(tmp_path / "cache", db, tmp_path).outcome
        == StageOutcome.PASS
    )
    with Store(db) as store:
        (row,) = store.connection.execute("SELECT payload FROM talks").fetchall()
        talk = Talk.model_validate_json(row[0])
        segments = [
            Segment.model_validate_json(row[0])
            for row in store.connection.execute("SELECT payload FROM segments")
        ]
    assert talk.transcript_sha256 == hashlib.sha256(raw).hexdigest()
    assert talk.fetch_provenance is not None
    assert talk.fetch_provenance.content_sha256 == hashlib.sha256(raw).hexdigest()
    assert segments
    for segment in segments:
        assert (
            segment.text
            == raw.decode("utf-8")[segment.source_start : segment.source_end]
        )


def test_fetch_persists_url_utc_hash_and_private_raw_bytes(tmp_path, recorded):
    wire = Recorded([(ROBOTS, recorded["robots"]), (URL, recorded["first"])])
    result = wire.fetch(tmp_path, [URL])
    assert result.outcome == StageOutcome.PASS
    assert result.counts == {
        "requested": 1,
        "fetched": 1,
        "unchanged": 0,
        "blocked": 0,
        "requests": 2,
    }
    assert not wire.entries
    cached = metadata(tmp_path)
    assert cached["source_url"] == URL
    assert cached["fetched_at"] == "2026-11-11T00:00:03Z"
    assert (
        cached["content_sha256"] == hashlib.sha256(recorded["first"].body).hexdigest()
    )
    paths = list((tmp_path / "cache").glob("*.md"))
    assert [path.read_bytes() for path in paths] == [recorded["first"].body]
    assert paths[0].stat().st_mode & 0o777 == 0o600
    assert (tmp_path / "cache").stat().st_mode & 0o777 == 0o700
    receipt = json.loads(result.receipt_path.read_text())
    assert receipt["outcome"] == "PASS"
    assert receipt["config_version"] == "c03-fetch-v1"
    assert receipt["input_hashes"][URL] == cached["content_sha256"]
    assert set(result.output_refs) <= {str(p) for p in (tmp_path / "cache").rglob("*")}
    assert all(headers["User-Agent"] == USER_AGENT for _, headers, _ in wire.calls)
    assert wire.calls[1][2] - wire.calls[0][2] >= 3


def test_conditional_cache_resume_preserves_original_fetch_date(tmp_path, recorded):
    first = Recorded([(ROBOTS, recorded["robots"]), (URL, recorded["first"])])
    first.fetch(tmp_path, [URL])
    original = metadata(tmp_path)
    resume = Recorded([(ROBOTS, recorded["robots"]), (URL, recorded["unchanged"])])
    resume.seconds = 100
    result = resume.fetch(tmp_path, [URL])
    assert result.outcome == StageOutcome.PASS
    assert result.counts["fetched"] == 0
    assert result.counts["unchanged"] == 1
    assert metadata(tmp_path)["fetched_at"] == original["fetched_at"]
    assert metadata(tmp_path)["last_checked_at"] != original["last_checked_at"]
    headers = resume.calls[1][1]
    assert headers["If-None-Match"] == '"fixture-v1"'
    assert headers["If-Modified-Since"] == "Tue, 10 Nov 2026 00:00:00 GMT"


def test_same_body_200_is_unchanged_and_changed_body_retains_history(
    tmp_path, recorded
):
    Recorded([(ROBOTS, recorded["robots"]), (URL, recorded["first"])]).fetch(
        tmp_path, [URL]
    )
    original = metadata(tmp_path)
    repeat = Recorded([(ROBOTS, recorded["robots"]), (URL, recorded["first"])])
    repeat.seconds = 50
    assert repeat.fetch(tmp_path, [URL]).counts["unchanged"] == 1
    assert metadata(tmp_path)["fetched_at"] == original["fetched_at"]
    changed = Recorded([(ROBOTS, recorded["robots"]), (URL, recorded["changed"])])
    changed.seconds = 100
    result = changed.fetch(tmp_path, [URL])
    assert result.counts["fetched"] == 1
    assert metadata(tmp_path)["fetched_at"] != original["fetched_at"]
    objects = list((tmp_path / "cache" / ".objects").glob("*.txt"))
    assert {item.read_bytes() for item in objects} == {
        recorded["first"].body,
        recorded["changed"].body,
    }


def test_interrupt_keeps_completed_entries_then_resume(tmp_path, recorded):
    first = Recorded(
        [
            (ROBOTS, recorded["robots"]),
            (URL, recorded["first"]),
            (SECOND, KeyboardInterrupt()),
        ]
    )
    result = first.fetch(tmp_path, [URL, SECOND])
    assert result.outcome == StageOutcome.BLOCKED
    assert result.counts["fetched"] == 1
    assert "interrupted" in result.errors[0]
    resume = Recorded(
        [
            (ROBOTS, recorded["robots"]),
            (URL, recorded["unchanged"]),
            (SECOND, recorded["first"]),
        ]
    )
    result = resume.fetch(tmp_path, [URL, SECOND])
    assert result.outcome == StageOutcome.PASS
    assert result.counts["unchanged"] == result.counts["fetched"] == 1


def test_corrupt_or_partial_cache_is_refetched_without_conditionals(tmp_path, recorded):
    Recorded([(ROBOTS, recorded["robots"]), (URL, recorded["first"])]).fetch(
        tmp_path, [URL]
    )
    next((tmp_path / "cache").glob("*.md")).write_text("interrupted bytes")
    wire = Recorded([(ROBOTS, recorded["robots"]), (URL, recorded["first"])])
    assert wire.fetch(tmp_path, [URL]).outcome == StageOutcome.PASS
    assert "If-None-Match" not in wire.calls[1][1]


def test_robots_denial_and_denied_redirect_never_fetch_target(tmp_path, recorded):
    direct = Recorded([(ROBOTS, recorded["robots"])])
    result = direct.fetch(tmp_path, ["https://www.ai.engineer/private/blocked.md"])
    assert result.outcome == StageOutcome.BLOCKED
    assert "disallows" in result.errors[0]
    redirected = Recorded([(ROBOTS, recorded["robots"]), (URL, recorded["redirect"])])
    result = redirected.fetch(tmp_path, [URL])
    assert result.outcome == StageOutcome.BLOCKED
    assert len(redirected.calls) == 2
    assert not list((tmp_path / "cache").glob("*.md"))


def test_retry_after_delays_remaining_work_and_records_blockage(tmp_path, recorded):
    wire = Recorded(
        [
            (ROBOTS, recorded["robots"]),
            (URL, recorded["limited"]),
            (SECOND, recorded["first"]),
        ]
    )
    result = wire.fetch(tmp_path, [URL, SECOND])
    assert result.outcome == StageOutcome.BLOCKED
    assert result.counts["blocked"] == result.counts["fetched"] == 1
    assert wire.calls[-1][2] - wire.calls[-2][2] >= 12


def test_default_discovery_uses_recorded_index_and_sitemap(tmp_path, recorded):
    wire = Recorded(
        [
            (ROBOTS, recorded["robots"]),
            ("https://www.ai.engineer/", recorded["index"]),
            ("https://www.ai.engineer/sitemap.xml", recorded["sitemap"]),
            (URL, recorded["first"]),
        ]
    )
    result = wire.fetch(tmp_path)
    assert result.outcome == StageOutcome.PASS
    assert result.counts["fetched"] == 1
    assert not wire.entries


@pytest.mark.parametrize(
    "url",
    [
        "http://www.ai.engineer/talk.md",
        "https://evil.example/talk.md",
        "https://www.ai.engineer.evil.example/talk.md",
        "https://user@www.ai.engineer/talk.md",
        "https://www.ai.engineer:444/talk.md",
        "https://www.ai.engineer/a\nb.md",
    ],
)
def test_unsafe_urls_are_blocked_without_request(tmp_path, url):
    wire = Recorded([])
    result = wire.fetch(tmp_path, [url])
    assert result.outcome == StageOutcome.BLOCKED
    assert not wire.calls
    assert json.loads(result.receipt_path.read_text())["outcome"] == "BLOCKED"


@pytest.mark.parametrize(
    "response",
    [
        Response(503, {}),
        Response(302, {"Location": "https://evil.example/robots.txt"}),
        Response(200, {"Content-Type": "text/html"}, b"<html>error</html>"),
    ],
)
def test_unavailable_robots_blocks_fetch(tmp_path, response):
    wire = Recorded([(ROBOTS, response)])
    assert wire.fetch(tmp_path, [URL]).outcome == StageOutcome.BLOCKED
    assert len(wire.calls) == 1


@pytest.mark.parametrize(
    "response",
    [
        Response(404, {}),
        Response(200, {"Content-Type": "text/html"}, b"<html>error</html>"),
        Response(200, {}, b""),
        Response(304, {}),
        Response(302, {"Location": "https://evil.example/talk.md"}),
    ],
)
def test_bad_transcript_responses_block_with_receipt(tmp_path, recorded, response):
    wire = Recorded([(ROBOTS, recorded["robots"]), (URL, response)])
    result = wire.fetch(tmp_path, [URL])
    assert result.outcome == StageOutcome.BLOCKED
    assert result.counts["fetched"] == 0
    assert not list((tmp_path / "cache").glob("*.md"))
    assert json.loads(result.receipt_path.read_text())["errors"]


def test_cache_outside_root_and_symlink_escape_rejected(tmp_path):
    with pytest.raises(ValueError):
        fetch_transcripts(tmp_path / "outside", tmp_path / "declared", [URL])
    root = tmp_path / "declared"
    root.mkdir()
    escape = tmp_path / "outside"
    escape.mkdir()
    (root / "cache").symlink_to(escape, target_is_directory=True)
    with pytest.raises(ValueError):
        fetch_transcripts(root / "cache", root, [URL])
    assert not list(escape.iterdir())


def test_robots_404_allows_fetch_and_last_modified_only_cache(tmp_path, recorded):
    first = recorded["first"]
    response = Response(
        200,
        {
            "Content-Type": "text/markdown",
            "Last-Modified": "Tue, 10 Nov 2026 00:00:00 GMT",
        },
        first.body,
    )
    wire = Recorded([(ROBOTS, Response(404, {})), (URL, response)])
    assert wire.fetch(tmp_path, [URL]).outcome == StageOutcome.PASS
    resumed = Recorded([(ROBOTS, Response(404, {})), (URL, recorded["unchanged"])])
    assert resumed.fetch(tmp_path, [URL]).outcome == StageOutcome.PASS
    assert "If-Modified-Since" in resumed.calls[-1][1]
    assert "If-None-Match" not in resumed.calls[-1][1]


def test_specific_user_agent_robots_rule_is_obeyed(tmp_path, recorded):
    robots = Response(
        200,
        {},
        b"User-agent: SkillwareTranscriptFetcher\nDisallow: /talks/\n\nUser-agent: *\nAllow: /\n",
    )
    wire = Recorded([(ROBOTS, robots)])
    assert wire.fetch(tmp_path, [URL]).outcome == StageOutcome.BLOCKED
    assert len(wire.calls) == 1


def test_automatic_transport_redirect_is_rejected(tmp_path, recorded):
    response = Response(
        200, {}, recorded["first"].body, "https://www.ai.engineer/private/blocked.md"
    )
    wire = Recorded([(ROBOTS, recorded["robots"]), (URL, response)])
    result = wire.fetch(tmp_path, [URL])
    assert result.outcome == StageOutcome.BLOCKED
    assert "unchecked redirect" in result.errors[0]


def test_fetch_history_retains_original_provenance(tmp_path, recorded):
    first = Recorded([(ROBOTS, recorded["robots"]), (URL, recorded["first"])])
    first.fetch(tmp_path, [URL])
    old = metadata(tmp_path)
    second = Recorded([(ROBOTS, recorded["robots"]), (URL, recorded["changed"])])
    second.seconds = 100
    second.fetch(tmp_path, [URL])
    snapshots = [
        json.loads(path.read_text())
        for path in (tmp_path / "cache" / ".objects").glob("*.json")
    ]
    assert len(snapshots) == 2
    assert {
        "source_url": URL,
        "fetched_at": old["fetched_at"],
        "content_sha256": old["content_sha256"],
    } in snapshots


def test_cache_destination_symlink_is_rejected_without_touching_target(
    tmp_path, recorded
):
    cache = tmp_path / "cache"
    cache.mkdir()
    protected = tmp_path / "unrelated"
    protected.write_text("keep me")
    key = hashlib.sha256(URL.encode()).hexdigest()
    (cache / f"{key}.md").symlink_to(protected)
    wire = Recorded([])
    result = wire.fetch(tmp_path, [URL])
    assert result.outcome == StageOutcome.BLOCKED
    assert protected.read_text() == "keep me"
    assert not wire.calls


@pytest.mark.parametrize(
    "index_url,fixture",
    [
        ("https://www.ai.engineer/transcripts/", "index"),
        ("https://www.ai.engineer/llms.txt", "llms"),
        ("https://www.ai.engineer/catalog.xml", "sitemap"),
    ],
)
def test_explicit_index_discovers_markdown(tmp_path, recorded, index_url, fixture):
    wire = Recorded(
        [
            (ROBOTS, recorded["robots"]),
            (index_url, recorded[fixture]),
            (URL, recorded["first"]),
        ]
    )
    result = wire.fetch(tmp_path, [index_url])
    assert result.outcome == StageOutcome.PASS
    assert result.counts["fetched"] == 1
    assert (
        result.input_hashes[index_url]
        == hashlib.sha256(recorded[fixture].body).hexdigest()
    )
    assert not wire.entries


@pytest.mark.parametrize("cycle_length", [1, 2])
@pytest.mark.parametrize("unrelated_success", [False, True])
def test_discovery_cycles_without_transcripts_block(
    tmp_path, recorded, cycle_length, unrelated_success
):
    target = URL if cycle_length == 1 else SECOND
    index = Response(
        200, {"Content-Type": "text/html"}, f'<a href="{target}">Index</a>'.encode()
    )
    calls = [(ROBOTS, recorded["robots"]), (URL, index)]
    urls = [URL]
    if unrelated_success:
        unrelated = "https://www.ai.engineer/talks/unrelated.md"
        urls.append(unrelated)
        calls.append((unrelated, recorded["first"]))
    if cycle_length == 2:
        calls.append(
            (
                SECOND,
                Response(
                    200,
                    {"Content-Type": "text/html"},
                    f'<a href="{URL}">Index</a>'.encode(),
                ),
            )
        )
    wire = Recorded(calls)
    result = wire.fetch(tmp_path, urls)
    assert result.outcome == StageOutcome.BLOCKED
    assert result.counts["blocked"] == cycle_length
    assert result.counts["fetched"] == int(unrelated_success)
    assert result.counts["unchanged"] == 0
    assert all("did not resolve" in error for error in result.errors)
    assert not wire.entries
    receipt = json.loads(result.receipt_path.read_text())
    assert receipt["outcome"] == "BLOCKED"
    assert receipt["errors"] == result.errors
    if not unrelated_success:
        assert result.output_refs == []
        assert not list((tmp_path / "cache").glob("*.md"))


@pytest.mark.parametrize("resume", [False, True])
def test_discovery_cycle_reaching_a_cached_transcript_passes(
    tmp_path, recorded, resume
):
    transcript = "https://www.ai.engineer/talks/actual.md"
    if resume:
        first = Recorded(
            [(ROBOTS, recorded["robots"]), (transcript, recorded["first"])]
        )
        assert first.fetch(tmp_path, [transcript]).outcome == StageOutcome.PASS
    wire = Recorded(
        [
            (ROBOTS, recorded["robots"]),
            (
                URL,
                Response(
                    200,
                    {"Content-Type": "text/html"},
                    f'<a href="{SECOND}">Index</a>'.encode(),
                ),
            ),
            (
                SECOND,
                Response(
                    200,
                    {"Content-Type": "text/html"},
                    (
                        f'<a href="{URL}">Index</a>'
                        f'<a href="{transcript}">Transcript</a>'
                    ).encode(),
                ),
            ),
            (transcript, recorded["unchanged" if resume else "first"]),
        ]
    )
    result = wire.fetch(tmp_path, [URL])
    assert result.outcome == StageOutcome.PASS
    assert result.counts["fetched"] == int(not resume)
    assert result.counts["unchanged"] == int(resume)
    assert result.counts["blocked"] == 0
    assert result.errors == []
    assert result.output_refs
    assert not wire.entries


def test_default_discovery_falls_back_to_recorded_llms_index(tmp_path, recorded):
    wire = Recorded(
        [
            (ROBOTS, recorded["robots"]),
            ("https://www.ai.engineer/", recorded["empty_index"]),
            ("https://www.ai.engineer/sitemap.xml", recorded["empty_sitemap"]),
            ("https://www.ai.engineer/llms.txt", recorded["llms"]),
            (URL, recorded["first"]),
        ]
    )
    result = wire.fetch(tmp_path)
    assert result.outcome == StageOutcome.PASS
    assert result.counts["fetched"] == 1
    assert not wire.entries


def test_interruption_between_markdown_and_sidecar_recovers_without_conditional(
    tmp_path, recorded, monkeypatch
):
    from skillware.ingest import fetch

    atomic = fetch._atomic

    def interrupted(path, data, root):
        if (
            path.parent == tmp_path / "cache"
            and path.suffix == ".json"
            and len(path.stem) == 64
        ):
            raise KeyboardInterrupt
        atomic(path, data, root)

    monkeypatch.setattr(fetch, "_atomic", interrupted)
    first = Recorded([(ROBOTS, recorded["robots"]), (URL, recorded["first"])])
    assert first.fetch(tmp_path, [URL]).outcome == StageOutcome.BLOCKED
    assert len(list((tmp_path / "cache").glob("*.md"))) == 1
    monkeypatch.setattr(fetch, "_atomic", atomic)
    resumed = Recorded([(ROBOTS, recorded["robots"]), (URL, recorded["first"])])
    assert resumed.fetch(tmp_path, [URL]).outcome == StageOutcome.PASS
    assert "If-None-Match" not in resumed.calls[-1][1]
    assert metadata(tmp_path)["source_url"] == URL


@pytest.mark.parametrize(
    "case",
    json.loads((FIXTURES.parent / "robots_rules.json").read_text()),
    ids=lambda case: case["name"],
)
def test_recorded_robots_rules_enforce_specificity_wildcards_and_octets(
    tmp_path, recorded, case
):
    url = "https://www.ai.engineer" + case["path"]
    robots = Response(200, {"Content-Type": "text/plain"}, case["rules"].encode())
    calls = [(ROBOTS, robots)]
    if case["allowed"]:
        calls.append((url, recorded["first"]))
    wire = Recorded(calls)
    result = wire.fetch(tmp_path, [url])
    assert result.outcome == (
        StageOutcome.PASS if case["allowed"] else StageOutcome.BLOCKED
    )
    assert not wire.entries
    if not case["allowed"]:
        assert "disallows" in result.errors[0]
        assert len(wire.calls) == 1


@pytest.mark.parametrize(
    "path",
    [
        "/public/../private/talk.md",
        "/public/%2e%2e/private/talk.md",
        "/public/%2Fprivate/talk.md",
        "/public/%5cprivate/talk.md",
        "/public\\private/talk.md",
        "//private/talk.md",
    ],
)
def test_ambiguous_url_paths_are_rejected_before_robots(tmp_path, path):
    wire = Recorded([])
    assert (
        wire.fetch(tmp_path, ["https://www.ai.engineer" + path]).outcome
        == StageOutcome.BLOCKED
    )
    assert not wire.calls


def test_decimal_delay_and_merged_group_maximum_rate(tmp_path, recorded):
    robots = Response(
        200,
        {},
        b"User-agent: SkillwareTranscriptFetcher\nCrawl-delay: 2.5\nAllow: /\nUser-agent: SkillwareTranscriptFetcher\nRequest-rate: 1/4\nAllow: /\n",
    )
    wire = Recorded([(ROBOTS, robots), (URL, recorded["first"])])
    assert wire.fetch(tmp_path, [URL]).outcome == StageOutcome.PASS
    assert wire.calls[-1][2] - wire.calls[0][2] == 4
    decimal = Response(200, {}, b"User-agent: *\nCrawl-delay: 2.5\nAllow: /\n")
    wire = Recorded([(ROBOTS, decimal), (URL, recorded["first"])])
    assert wire.fetch(tmp_path, [URL]).outcome == StageOutcome.PASS
    assert wire.calls[-1][2] - wire.calls[0][2] == 2.5


def test_wildcard_fallback_retains_pacing(tmp_path, recorded):
    robots = Response(
        200,
        {},
        b"User-agent: *\nAllow: /\nCrawl-delay: 4\n"
        b"User-agent: SkillwareTranscriptFetcher\nCrawl-delay: 2\n",
    )
    wire = Recorded([(ROBOTS, robots), (URL, recorded["first"])])
    result = wire.fetch(tmp_path, [URL])
    assert result.outcome == StageOutcome.PASS
    assert wire.calls[-1][2] - wire.calls[0][2] == 4
    assert not wire.entries


@pytest.mark.parametrize(
    "directive",
    [
        "Crawl-delay: nan",
        "Crawl-delay: inf",
        "Request-rate: 0/1",
        "Request-rate: -1/-1",
        "Request-rate: nan/1",
    ],
)
def test_invalid_rate_policy_blocks_cleanly(tmp_path, directive):
    wire = Recorded(
        [(ROBOTS, Response(200, {}, f"User-agent: *\n{directive}\n".encode()))]
    )
    result = wire.fetch(tmp_path, [URL])
    assert result.outcome == StageOutcome.BLOCKED
    assert "invalid robots" in result.errors[0]


def test_304_refreshes_validators_without_changing_fetch_provenance(tmp_path, recorded):
    Recorded([(ROBOTS, recorded["robots"]), (URL, recorded["first"])]).fetch(
        tmp_path, [URL]
    )
    old = metadata(tmp_path)
    response = Response(
        304,
        {"ETag": '"fresh-validator"', "Last-Modified": "Wed, 11 Nov 2026 00:00:00 GMT"},
    )
    wire = Recorded([(ROBOTS, recorded["robots"]), (URL, response)])
    wire.seconds = 100
    assert wire.fetch(tmp_path, [URL]).outcome == StageOutcome.PASS
    changed = metadata(tmp_path)
    assert changed["etag"] == '"fresh-validator"'
    assert changed["last_modified"] == "Wed, 11 Nov 2026 00:00:00 GMT"
    assert changed["fetched_at"] == old["fetched_at"]
    assert changed["content_sha256"] == old["content_sha256"]
    next_run = Recorded([(ROBOTS, recorded["robots"]), (URL, Response(304, {}))])
    assert next_run.fetch(tmp_path, [URL]).outcome == StageOutcome.PASS
    assert next_run.calls[-1][1]["If-None-Match"] == '"fresh-validator"'


def _real_http_wire(monkeypatch, entries):
    """Exercise HTTP framing with in-memory sockets and bounded real reads."""
    from skillware.ingest import fetch

    pending = list(entries)
    reads = []

    class MemorySocket:
        def __init__(self, raw):
            self.raw = raw

        def makefile(self, mode):
            assert mode == "rb"
            return io.BytesIO(self.raw)

    class Opener:
        def open(self, request, timeout):
            expected_url, raw = pending.pop(0)
            assert request.full_url == expected_url
            assert timeout == 30
            response = HTTPResponse(MemorySocket(raw))
            response.begin()
            original_read = response.read

            def bounded_read(amount):
                reads.append(amount)
                assert amount == fetch.MAX_BYTES + 1
                return original_read(amount)

            response.read = bounded_read
            if response.status >= 300:
                raise HTTPError(
                    expected_url, response.status, "fixture", response.headers, response
                )
            return response

    monkeypatch.setattr(fetch, "build_opener", lambda *_: Opener())
    return pending, reads


def _raw_response(status, headers, body):
    return f"HTTP/1.1 {status} Fixture\r\n{headers}\r\n\r\n".encode() + body


@pytest.mark.parametrize("status", [200, 503])
def test_review_transport_rejects_truncated_content_length(monkeypatch, status):
    from skillware.ingest.fetch import FetchBlocked, http_get

    _real_http_wire(
        monkeypatch, [(URL, _raw_response(status, "Content-Length: 1000", b"partial"))]
    )
    with pytest.raises(FetchBlocked, match="incomplete HTTP response body"):
        http_get(URL, {})


@pytest.mark.parametrize("body", [b"10\r\nshort", b"3\r\nabc\r\n", b"bogus\r\n"])
def test_review_transport_chunked_eof_is_typed_block(monkeypatch, body):
    from skillware.ingest.fetch import FetchBlocked, http_get

    _real_http_wire(
        monkeypatch, [(URL, _raw_response(200, "Transfer-Encoding: chunked", body))]
    )
    with pytest.raises(FetchBlocked, match="incomplete or invalid HTTP response"):
        http_get(URL, {})


@pytest.mark.parametrize(
    "headers,wire_body,expected",
    [
        ("Content-Length: 3", b"abc", b"abc"),
        ("Connection: close", b"abc", b"abc"),
        ("Transfer-Encoding: chunked", b"3\r\nabc\r\n0\r\n\r\n", b"abc"),
        (
            "Transfer-Encoding: chunked\r\nContent-Length: 999",
            b"3\r\nabc\r\n0\r\n\r\n",
            b"abc",
        ),
        ("Content-Length: 0", b"", b""),
    ],
)
def test_review_transport_valid_framing(monkeypatch, headers, wire_body, expected):
    from skillware.ingest.fetch import http_get

    pending, reads = _real_http_wire(
        monkeypatch, [(URL, _raw_response(200, headers, wire_body))]
    )
    assert http_get(URL, {}).body == expected
    assert not pending
    assert len(reads) == 1


@pytest.mark.parametrize("status", [204, 304])
def test_review_transport_bodyless_status_ignores_representation_length(
    monkeypatch, status
):
    from skillware.ingest.fetch import http_get

    _real_http_wire(
        monkeypatch, [(URL, _raw_response(status, "Content-Length: 1000", b""))]
    )
    response = http_get(URL, {})
    assert response.status == status
    assert response.body == b""


@pytest.mark.parametrize("framing", ["length", "close", "chunked"])
@pytest.mark.parametrize("size", [32, 33])
def test_review_transport_size_cap_stays_bounded(monkeypatch, framing, size):
    from skillware.ingest import fetch

    monkeypatch.setattr(fetch, "MAX_BYTES", 32)
    body = b"x" * size
    headers = {
        "length": f"Content-Length: {size}",
        "close": "Connection: close",
        "chunked": "Transfer-Encoding: chunked",
    }[framing]
    wire_body = (
        f"{size:x}\r\n".encode() + body + b"\r\n0\r\n\r\n"
        if framing == "chunked"
        else body
    )
    _, reads = _real_http_wire(
        monkeypatch, [(URL, _raw_response(200, headers, wire_body))]
    )
    if size > 32:
        with pytest.raises(fetch.FetchBlocked, match="exceeds"):
            fetch.http_get(URL, {})
    else:
        assert fetch.http_get(URL, {}).body == body
    assert reads == [33]


@pytest.mark.parametrize("resume", [False, True])
def test_review_transport_truncated_fetch_never_publishes_partial_cache(
    tmp_path, recorded, monkeypatch, resume
):
    from skillware.ingest import fetch

    if resume:
        Recorded([(ROBOTS, recorded["robots"]), (URL, recorded["first"])]).fetch(
            tmp_path, [URL]
        )
    before = {
        str(p.relative_to(tmp_path / "cache")): p.read_bytes()
        for p in (tmp_path / "cache").rglob("*")
        if p.is_file() and p.name != "fetch-receipt.json"
    }
    body = recorded["changed"].body
    robots = b"User-agent: *\nAllow: /\n"
    pending, _ = _real_http_wire(
        monkeypatch,
        [
            (ROBOTS, _raw_response(200, f"Content-Length: {len(robots)}", robots)),
            (
                URL,
                _raw_response(
                    200,
                    f"Content-Type: text/markdown\r\nContent-Length: {len(body) + 100}",
                    body,
                ),
            ),
        ],
    )
    clock = Recorded([])
    result = fetch.fetch_transcripts(
        tmp_path / "cache",
        tmp_path,
        [URL],
        transport=fetch.http_get,
        clock=clock.clock,
        sleeper=clock.sleep,
        now=clock.now,
    )
    assert result.outcome == StageOutcome.BLOCKED
    assert not pending
    assert any("incomplete HTTP response body" in error for error in result.errors)
    assert result.counts["fetched"] == 0
    receipt = json.loads(result.receipt_path.read_text())
    assert receipt["outcome"] == "BLOCKED"
    assert receipt["output_refs"] == []
    after = {
        str(p.relative_to(tmp_path / "cache")): p.read_bytes()
        for p in (tmp_path / "cache").rglob("*")
        if p.is_file() and p.name != "fetch-receipt.json"
    }
    assert after == before
