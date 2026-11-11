"""Polite, resumable acquisition of AI Engineer Markdown into private storage.

The HTTP boundary is injectable so tests never contact the live site. No model,
provider credential, video downloader, or arbitrary-host scraper is involved.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote, unquote, urljoin, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from xml.etree import ElementTree

from skillware.ingest.paths import contained_path, private_cache
from skillware.schema import StageOutcome

CONFIG_VERSION = "c03-fetch-v1"
USER_AGENT = (
    "SkillwareTranscriptFetcher/0.1 (private research; robots-aware Markdown retrieval)"
)
DEFAULT_INDEX = "https://www.ai.engineer/"
MAX_BYTES = 10 * 1024 * 1024
ALLOWED_HOSTS = frozenset({"ai.engineer", "www.ai.engineer"})


@dataclass(frozen=True)
class Response:
    status: int
    headers: Mapping[str, str]
    body: bytes = b""
    url: str | None = None


@dataclass
class FetchResult:
    outcome: StageOutcome
    counts: dict[str, int]
    errors: list[str]
    input_hashes: dict[str, str]
    receipt_path: Path
    output_refs: list[str] = field(default_factory=list)


class FetchBlocked(ValueError):
    """A prerequisite, HTTP result, or robots decision prevents acquisition."""


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def http_get(url: str, headers: Mapping[str, str]) -> Response:
    """Perform one bounded request; redirect targets require separate checks."""
    opener = build_opener(_NoRedirect())
    request = Request(url, headers=dict(headers), method="GET")
    try:
        response = opener.open(request, timeout=30)
    except HTTPError as error:
        response = error
    with response:
        body = response.read(MAX_BYTES + 1)
        if len(body) > MAX_BYTES:
            raise FetchBlocked("HTTP response exceeds the 10 MiB limit")
        return Response(response.status, dict(response.headers), body, url)


def _url(value: str) -> str:
    if any(character.isspace() or ord(character) < 32 for character in value):
        raise FetchBlocked("URL contains whitespace or control characters")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as error:
        raise FetchBlocked("invalid source URL") from error
    if (
        parsed.scheme != "https"
        or parsed.hostname not in ALLOWED_HOSTS
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
    ):
        raise FetchBlocked("fetch URLs must use HTTPS on ai.engineer")
    if (
        re.search(r"%(?![0-9a-fA-F]{2})", parsed.path + parsed.query)
        or re.search(r"%(?:2f|5c)", parsed.path, re.IGNORECASE)
        or "\\" in parsed.path
        or "//" in parsed.path
        or any(part in {".", ".."} for part in unquote(parsed.path).split("/"))
    ):
        raise FetchBlocked("URL contains ambiguous path components or encoding")
    path = quote(parsed.path or "/", safe="/:@-._~!$&'()*+,;=%")
    query = quote(parsed.query, safe="/?:@-._~!$&'()*+,;=%[]")
    return urlunsplit(("https", parsed.hostname, path, query, ""))


def _headers(response: Response) -> dict[str, str]:
    return {key.lower(): value for key, value in response.headers.items()}


def _utc(now: Callable[[], datetime]) -> str:
    value = now()
    if value.tzinfo is None:
        raise ValueError("fetch clock must return a timezone-aware datetime")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _atomic(path: Path, data: bytes, root: Path) -> None:
    contained_path(path, root)
    temporary = contained_path(path.with_suffix(path.suffix + ".tmp"), root)
    # O_NOFOLLOW also rejects a pre-existing temporary-file symlink.
    descriptor = os.open(
        temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600
    )
    os.fchmod(descriptor, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    path.chmod(0o600)


class _Links(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in {"a", "link"}:
            values = dict(attrs)
            if values.get("href"):
                self.links.append(values["href"])


def _robots_path(value: str) -> str:
    """Compare UTF-8 octets, decoding only RFC 3986 unreserved escapes."""
    unreserved = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~"
    result = []
    position = 0
    while position < len(value):
        character = value[position]
        if character == "%" and re.fullmatch(
            r"[0-9A-Fa-f]{2}", value[position + 1 : position + 3]
        ):
            octet = int(value[position + 1 : position + 3], 16)
            result.append(chr(octet) if chr(octet) in unreserved else f"%{octet:02X}")
            position += 3
        else:
            result.append(character if ord(character) < 128 else quote(character))
            position += 1
    return "".join(result)


@dataclass
class _RobotsGroup:
    agents: list[str] = field(default_factory=list)
    directives: list[tuple[str, str]] = field(default_factory=list)


class _RobotsPolicy:
    """Robots rule selection with specificity, wildcards, and Allow ties.

    urllib.robotparser uses first matching prefix rules, so it is unsuitable for
    the common Allow:/ plus Disallow:/private/ and wildcard policies used here.
    """

    def __init__(self, lines: list[str]):
        groups: list[_RobotsGroup] = []
        current = _RobotsGroup()
        self.sitemaps: list[str] = []
        for raw in lines:
            line = raw.split("#", 1)[0]
            key, separator, value = line.partition(":")
            if not separator:
                continue
            key, value = key.strip().lower(), value.strip()
            if key == "sitemap":
                if value:
                    self.sitemaps.append(value)
            elif key == "user-agent":
                if current.directives:
                    groups.append(current)
                    current = _RobotsGroup()
                if value:
                    current.agents.append(value.lower())
            elif current.agents and key in {
                "allow",
                "disallow",
                "crawl-delay",
                "request-rate",
            }:
                current.directives.append((key, value))
        groups.append(current)
        product = USER_AGENT.split("/", 1)[0].lower()
        specificities = [
            max(
                (
                    len(agent)
                    for agent in group.agents
                    if agent != "*" and product.startswith(agent)
                ),
                default=0,
            )
            for group in groups
        ]
        longest = max(specificities, default=0)
        selected = [
            group
            for group, length in zip(groups, specificities, strict=True)
            if (length == longest if longest else "*" in group.agents)
        ]
        self.rules: list[tuple[int, bool, re.Pattern[str]]] = []
        self.fallback_rules: list[tuple[int, bool, re.Pattern[str]]] = []
        fallback = [group for group in groups if "*" in group.agents] if longest else []
        self.interval = 0.0
        for group, rules in [
            *((group, self.rules) for group in selected),
            *((group, self.fallback_rules) for group in fallback),
        ]:
            for key, value in group.directives:
                if key in {"allow", "disallow"}:
                    if not value:
                        continue
                    normalized = _robots_path(value)
                    anchored = normalized.endswith("$")
                    pattern = normalized[:-1] if anchored else normalized
                    literal = pattern.replace("*", "")
                    specificity = len(re.findall(r"%[0-9A-F]{2}|.", literal))
                    expression = "^" + re.escape(pattern).replace(r"\*", ".*")
                    if anchored:
                        expression += "$"
                    rules.append((specificity, key == "allow", re.compile(expression)))
                else:
                    try:
                        if key == "crawl-delay":
                            interval = float(value)
                        else:
                            requests, seconds = value.split("/", 1)
                            requests, seconds = float(requests), float(seconds)
                            if (
                                not math.isfinite(requests)
                                or requests <= 0
                                or seconds < 0
                            ):
                                raise ValueError
                            interval = seconds / requests
                        if not math.isfinite(interval) or interval < 0:
                            raise ValueError
                    except (ValueError, ZeroDivisionError) as error:
                        raise FetchBlocked(f"invalid robots {key}") from error
                    self.interval = max(self.interval, interval)

    def can_fetch(self, url: str) -> bool:
        parsed = urlsplit(url)
        target = _robots_path(
            (parsed.path or "/") + ("?" + parsed.query if parsed.query else "")
        )
        # A specific agent group can contain only pacing directives, empty rules,
        # or rules for other paths. Keep the wildcard policy available when no
        # specific rule applies; an applicable specific rule still takes priority.
        for rules in (self.rules, self.fallback_rules):
            matches = [
                (length, allow)
                for length, allow, pattern in rules
                if pattern.match(target)
            ]
            if matches:
                return max(matches)[1]
        return True


class _Client:
    def __init__(self, transport, clock, sleeper, now, min_interval):
        if not math.isfinite(min_interval) or min_interval < 1.0:
            raise ValueError("minimum request interval must be at least one second")
        self.transport = transport
        self.clock = clock
        self.sleeper = sleeper
        self.now = now
        self.min_interval = min_interval
        self.next_request = 0.0
        self.robots: dict[str, _RobotsPolicy] = {}
        self.sitemaps: list[str] = []
        self.requests = 0
        self.input_hashes: dict[str, str] = {}

    def _request(self, url: str, headers: dict[str, str], interval: float) -> Response:
        delay = self.next_request - self.clock()
        if delay > 0:
            self.sleeper(delay)
        self.requests += 1
        self.next_request = self.clock() + interval
        response = self.transport(url, {"User-Agent": USER_AGENT, **headers})
        if response.url is not None and _url(response.url) != url:
            raise FetchBlocked("transport followed an unchecked redirect")
        if len(response.body) > MAX_BYTES:
            raise FetchBlocked("HTTP response exceeds the 10 MiB limit")
        retry_after = _headers(response).get("retry-after")
        if response.status in {429, 503} and retry_after:
            try:
                wait = float(retry_after)
            except ValueError:
                try:
                    wait = (
                        parsedate_to_datetime(retry_after) - self.now()
                    ).total_seconds()
                except (ValueError, TypeError, OverflowError):
                    wait = interval
            if not math.isfinite(wait):
                wait = interval
            self.next_request = max(self.next_request, self.clock() + max(0, wait))
        return response

    def policy(self, url: str) -> tuple[_RobotsPolicy, float]:
        parsed = urlsplit(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        if origin not in self.robots:
            robots_url = origin + "/robots.txt"
            response = self._request(robots_url, {}, self.min_interval)
            headers = _headers(response)
            if response.status == 404:
                lines = []
            elif response.status in {401, 403}:
                lines = ["User-agent: *", "Disallow: /"]
            elif response.status != 200:
                raise FetchBlocked(f"robots.txt unavailable: HTTP {response.status}")
            elif "html" in headers.get("content-type", "").lower():
                raise FetchBlocked("robots.txt returned HTML")
            else:
                try:
                    lines = response.body.decode("utf-8-sig").splitlines()
                except UnicodeDecodeError as error:
                    raise FetchBlocked("robots.txt is not UTF-8") from error
            robot = _RobotsPolicy(lines)
            self.input_hashes[robots_url] = hashlib.sha256(response.body).hexdigest()
            self.robots[origin] = robot
            self.sitemaps.extend(robot.sitemaps)
        robot = self.robots[origin]
        interval = max(self.min_interval, robot.interval)
        # The first request after reading robots must observe its declared delay.
        self.next_request = max(self.next_request, self.clock() + interval)
        return robot, interval

    def get(
        self, url: str, headers: dict[str, str] | None = None
    ) -> tuple[str, Response]:
        url = _url(url)
        for _ in range(6):
            robot, interval = self.policy(url)
            if not robot.can_fetch(url):
                raise FetchBlocked(f"robots.txt disallows {url}")
            response = self._request(url, headers or {}, interval)
            if response.status not in {301, 302, 303, 307, 308}:
                return url, response
            target = _headers(response).get("location")
            if not target:
                raise FetchBlocked("redirect is missing Location")
            url = _url(urljoin(url, target))
        raise FetchBlocked("too many redirects")

    def discover(
        self, index_url: str = DEFAULT_INDEX, initial: Response | None = None
    ) -> list[str]:
        """Read HTML, sitemap, or llms.txt indexes for linked Markdown.

        Discovery follows only explicit .md links and index links, never invents
        transcript endpoints from talk titles or downloads arbitrary pages.
        """
        result: set[str] = set()
        pending: list[tuple[str, Response | None]] = [(index_url, initial)]
        seen: set[str] = set()
        tried_llms = False
        while pending:
            current, response = pending.pop(0)
            current = _url(current)
            if current in seen:
                continue
            if len(seen) >= 100:
                raise FetchBlocked(
                    "index discovery limit reached; supply transcript --url"
                )
            seen.add(current)
            if response is None:
                current, response = self.get(current)
            if response.status == 404 and current != index_url:
                if (
                    not pending
                    and not result
                    and not tried_llms
                    and index_url == DEFAULT_INDEX
                ):
                    tried_llms = True
                    pending.append((urljoin(DEFAULT_INDEX, "llms.txt"), None))
                continue
            if response.status != 200:
                raise FetchBlocked(f"source index unavailable: HTTP {response.status}")
            self.input_hashes[current] = hashlib.sha256(response.body).hexdigest()
            mime = _headers(response).get("content-type", "").lower()
            is_sitemap = "xml" in mime or urlsplit(current).path.endswith(".xml")
            is_index = False
            if is_sitemap:
                try:
                    document = ElementTree.fromstring(response.body)
                except ElementTree.ParseError as error:
                    raise FetchBlocked("source sitemap is not valid XML") from error
                is_index = document.tag.rsplit("}", 1)[-1] == "sitemapindex"
                hrefs = [
                    element.text.strip()
                    for element in document.iter()
                    if element.tag.rsplit("}", 1)[-1] == "loc" and element.text
                ]
            else:
                text = response.body.decode("utf-8-sig")
                links = _Links()
                links.feed(text)
                hrefs = links.links + re.findall(r"\]\(<?([^\s)>]+)>?\)", text)
                hrefs += re.findall(
                    r"https://[^\s<>\[\]()]+\.md(?:\?[^\s<>\[\]()]*)?", text
                )
            for href in hrefs:
                joined = urljoin(current, href)
                if urlsplit(joined).hostname not in ALLOWED_HOSTS:
                    continue
                candidate = _url(joined)
                path = urlsplit(candidate).path.lower()
                if path.endswith(".md"):
                    result.add(candidate)
                elif is_index or path.endswith((".xml", "/llms.txt", "/llms-full.txt")):
                    pending.append((candidate, None))
            if current == DEFAULT_INDEX and index_url == DEFAULT_INDEX:
                pending.extend(
                    (url, None)
                    for url in (
                        self.sitemaps or [urljoin(DEFAULT_INDEX, "sitemap.xml")]
                    )
                )
            if (
                not pending
                and not result
                and not tried_llms
                and index_url == DEFAULT_INDEX
            ):
                tried_llms = True
                pending.append((urljoin(DEFAULT_INDEX, "llms.txt"), None))
        if not result:
            raise FetchBlocked("no Markdown links found; supply a transcript --url")
        return sorted(result)


def _cached(markdown: Path, metadata_path: Path, root: Path) -> dict | None:
    contained_path(markdown, root)
    contained_path(metadata_path, root)
    if not markdown.exists() or not metadata_path.exists():
        return None
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        expected = hashlib.sha256(markdown.read_bytes()).hexdigest()
        fetched_at = datetime.fromisoformat(
            metadata["fetched_at"].replace("Z", "+00:00")
        )
        if (
            metadata["content_sha256"] != expected
            or fetched_at.tzinfo is None
            or _url(metadata["source_url"]) != metadata["source_url"]
            or any(
                value is not None and not isinstance(value, str)
                for value in (metadata.get("etag"), metadata.get("last_modified"))
            )
        ):
            return None
        return metadata
    except (ValueError, KeyError, TypeError, AttributeError):
        return None


def fetch_transcripts(
    out: Path,
    root: Path,
    urls: list[str] | None = None,
    *,
    transport: Callable[[str, Mapping[str, str]], Response] | None = None,
    clock: Callable[[], float] | None = None,
    sleeper: Callable[[float], None] | None = None,
    now: Callable[[], datetime] | None = None,
    min_interval: float = 1.0,
) -> FetchResult:
    """Fetch with per-file durability, conditional reuse, and a stage receipt.

    Interruption preserves completed entries and records BLOCKED. A later run
    validates cached bytes before sending conditional headers. No raw transcript
    or cache is permitted within a repository or outside the declared root.
    """
    out = private_cache(Path(out), Path(root))
    root = Path(root).resolve()
    receipt_path = contained_path(out / "fetch-receipt.json", root)
    result = FetchResult(
        StageOutcome.BLOCKED,
        {"requested": 0, "fetched": 0, "unchanged": 0, "blocked": 0, "requests": 0},
        [],
        {},
        receipt_path,
    )
    now = now or (lambda: datetime.now(UTC))
    client = _Client(
        transport or http_get,
        clock or time.monotonic,
        sleeper or time.sleep,
        now,
        min_interval,
    )
    started_at = _utc(now)
    try:
        source_urls = (
            list(dict.fromkeys(_url(url) for url in urls))
            if urls
            else client.discover()
        )
        completed_urls: set[str] = set()
        discovery_links: dict[str, set[str]] = {}
        for requested_url in source_urls:
            result.counts["requested"] += 1
            try:
                key = hashlib.sha256(requested_url.encode()).hexdigest()
                markdown = contained_path(out / f"{key}.md", root)
                metadata_path = contained_path(out / f"{key}.json", root)
                old = _cached(markdown, metadata_path, root)
                if old and old.get("requested_url", old["source_url"]) != requested_url:
                    raise FetchBlocked("cache source URL does not match its key")
                headers = {"Accept": "text/markdown, text/plain;q=0.9"}
                if old:
                    if old.get("etag"):
                        headers["If-None-Match"] = old["etag"]
                    if old.get("last_modified"):
                        headers["If-Modified-Since"] = old["last_modified"]
                source_url, response = client.get(requested_url, headers)
                response_headers = _headers(response)
                checked_at = _utc(now)
                if response.status == 304:
                    if old is None:
                        raise FetchBlocked("304 without a verified cached transcript")
                    if source_url != old["source_url"]:
                        raise FetchBlocked(
                            "304 redirected to a different cached source"
                        )
                    metadata = {**old, "last_checked_at": checked_at}
                    for header, field in (
                        ("etag", "etag"),
                        ("last-modified", "last_modified"),
                    ):
                        if header in response_headers:
                            metadata[field] = response_headers[header]
                    result.counts["unchanged"] += 1
                elif response.status == 200:
                    mime = (
                        response_headers.get("content-type", "")
                        .split(";", 1)[0]
                        .lower()
                    )
                    if mime in {
                        "text/html",
                        "application/xhtml+xml",
                        "application/xml",
                        "text/xml",
                    } or urlsplit(source_url).path.endswith((".txt", ".xml")):
                        discovered_urls = client.discover(source_url, response)
                        discovery_links[requested_url] = set(discovered_urls)
                        for discovered in discovered_urls:
                            if discovered not in source_urls:
                                source_urls.append(discovered)
                        continue
                    if mime and mime not in {
                        "text/markdown",
                        "text/plain",
                        "text/x-markdown",
                    }:
                        raise FetchBlocked(f"transcript is not Markdown: {mime}")
                    text = response.body.decode("utf-8-sig")
                    if not text.strip() or text.lstrip().lower().startswith(
                        ("<!doctype html", "<html")
                    ):
                        raise FetchBlocked("transcript is empty or HTML")
                    digest = hashlib.sha256(response.body).hexdigest()
                    unchanged = (
                        old
                        and old["content_sha256"] == digest
                        and old["source_url"] == source_url
                    )
                    metadata = {
                        "source_url": source_url,
                        "requested_url": requested_url,
                        "fetched_at": old["fetched_at"] if unchanged else checked_at,
                        "content_sha256": digest,
                        "etag": response_headers.get("etag"),
                        "last_modified": response_headers.get("last-modified"),
                        "last_checked_at": checked_at,
                    }
                    objects = contained_path(out / ".objects", root)
                    objects.mkdir(mode=0o700, exist_ok=True)
                    objects.chmod(0o700)
                    blob = contained_path(objects / f"{digest}.txt", root)
                    if not blob.exists():
                        _atomic(blob, response.body, root)
                    elif hashlib.sha256(blob.read_bytes()).hexdigest() != digest:
                        raise FetchBlocked("immutable cached source history is corrupt")
                    provenance = {
                        field: metadata[field]
                        for field in ("source_url", "fetched_at", "content_sha256")
                    }
                    provenance_bytes = (
                        json.dumps(provenance, sort_keys=True, indent=2) + "\n"
                    ).encode()
                    provenance_key = hashlib.sha256(provenance_bytes).hexdigest()
                    history = contained_path(objects / f"{provenance_key}.json", root)
                    if not history.exists():
                        _atomic(history, provenance_bytes, root)
                    elif history.read_bytes() != provenance_bytes:
                        raise FetchBlocked(
                            "immutable cached provenance history is corrupt"
                        )
                    result.output_refs.extend([str(blob), str(history)])
                    if not unchanged:
                        _atomic(markdown, response.body, root)
                    result.counts["unchanged" if unchanged else "fetched"] += 1
                else:
                    raise FetchBlocked(f"HTTP {response.status} for {requested_url}")
                _atomic(
                    metadata_path,
                    (json.dumps(metadata, sort_keys=True, indent=2) + "\n").encode(),
                    root,
                )
                result.input_hashes[source_url] = metadata["content_sha256"]
                result.output_refs.extend([str(markdown), str(metadata_path)])
                completed_urls.add(requested_url)
            except (OSError, ValueError) as error:
                result.errors.append(f"{requested_url}: {error}")
                result.counts["blocked"] += 1
        # A link alone is not a retrieved transcript: HTML served at .md URLs
        # may point back to itself or form a cycle of discovery pages. Resolve
        # indexes only when their link graph reaches a durable cache entry.
        resolved = set(completed_urls)
        while True:
            newly_resolved = {
                url
                for url, links in discovery_links.items()
                if url not in resolved and links & resolved
            }
            if not newly_resolved:
                break
            resolved.update(newly_resolved)
        for url in sorted(discovery_links.keys() - resolved):
            result.errors.append(
                f"{url}: discovery did not resolve to a cached Markdown transcript"
            )
            result.counts["blocked"] += 1
        result.outcome = StageOutcome.BLOCKED if result.errors else StageOutcome.PASS
    except (OSError, ValueError) as error:
        result.errors.append(str(error))
        result.counts["blocked"] += 1
    except KeyboardInterrupt:
        result.errors.append("fetch interrupted; completed cache entries are resumable")
        result.counts["blocked"] += 1
    finally:
        result.counts["requests"] = client.requests
        result.input_hashes.update(client.input_hashes)
        receipt = {
            "stage": "ingest.fetch",
            "config_version": CONFIG_VERSION,
            "input_hashes": result.input_hashes,
            "counts": result.counts,
            "outcome": result.outcome.value,
            "errors": result.errors,
            "output_refs": result.output_refs,
            "started_at": started_at,
            "finished_at": _utc(now),
        }
        _atomic(
            receipt_path,
            (json.dumps(receipt, indent=2, sort_keys=True) + "\n").encode(),
            root,
        )
    return result
