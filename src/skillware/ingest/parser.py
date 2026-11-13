"""Parse declared Markdown without rewriting the evidence used by offsets."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from decimal import Decimal
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

from skillware.schema import StageOutcome

METADATA_KEYS = {"title", "speaker", "company", "source_url", "youtube_video_id"}
ALIASES = {
    "source": "source_url",
    "url": "source_url",
    "video_id": "youtube_video_id",
    "author": "speaker",
}
_TIME = r"(?:\d+:)?\d{1,2}:\d{2}(?:\.\d+)?"
_STAMP = re.compile(
    rf"^\s*(?:\[|\()?({_TIME})(?:\s*(?:-->|–|-)\s*({_TIME}))?"
    r"(?:\]|\))?(?:\s+|$)"
)
_META = re.compile(r"^\s*(?:\*\*)?([A-Za-z_ ]+)(?:\*\*)?:\s*(?:\*\*)?(.*?)\s*$")
_NAME_WORD = r"[^\W\d_](?:[^\W\d_]|[.'’\-\u0300-\u036f])*"
_NAME_PARTICLES = {
    "al",
    "ap",
    "ben",
    "bin",
    "da",
    "dal",
    "de",
    "del",
    "della",
    "den",
    "der",
    "di",
    "dos",
    "du",
    "el",
    "ibn",
    "la",
    "le",
    "los",
    "van",
    "von",
    "ten",
    "ter",
    "zu",
    "zum",
    "zur",
}
_SPEAKER = re.compile(
    r"^(?:\*\*(?P<bold>[^*:\n]+)(?::\*\*|\*\*:)|"
    r"\[Speaker:\s*(?P<bracket>[^\]\n]+)\]|"
    rf"(?P<plain>{_NAME_WORD}(?: {_NAME_WORD})*):)\s+"
)


@dataclass
class Issue:
    outcome: StageOutcome
    message: str


@dataclass
class ParsedSegment:
    start: int
    end: int
    start_seconds: Decimal | None = None
    end_seconds: Decimal | None = None
    speaker: str | None = None
    evidence: str | None = None


@dataclass
class ParsedTranscript:
    text: str
    sha256: str
    metadata: dict[str, str | None] = field(default_factory=dict)
    segments: list[ParsedSegment] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)


def _query_pairs(query: str) -> list[tuple[str, str]]:
    if re.search(r"%(?![0-9a-fA-F]{2})", query):
        raise ValueError("source_url contains invalid query encoding")
    try:
        return parse_qsl(query, keep_blank_values=True, errors="strict")
    except UnicodeDecodeError as exc:
        raise ValueError("source_url contains invalid query encoding") from exc


def canonical_url(value: str) -> str:
    """Normalize transport/tracking variants, retaining identity-bearing queries."""
    value = value.strip()
    if any(character.isspace() or ord(character) < 32 for character in value):
        raise ValueError("source_url contains whitespace or control characters")
    parts = urlsplit(value.strip())
    if (
        parts.scheme.lower() not in {"http", "https"}
        or not parts.hostname
        or parts.username
        or parts.password
    ):
        raise ValueError(
            "source_url must be an absolute HTTP(S) URL without credentials"
        )
    hostname = parts.hostname.lower().removeprefix("www.")
    if ":" in hostname:
        hostname = f"[{hostname}]"
    # Only a port that is default for the *supplied* scheme is redundant.
    # Keep the scheme for non-default ports so normalization stays idempotent:
    # http://host:443 is not the same endpoint as https://host:443.
    nondefault_port = parts.port not in {
        None,
        80 if parts.scheme.lower() == "http" else 443,
    }
    scheme = parts.scheme.lower() if nondefault_port else "https"
    if nondefault_port:
        hostname += f":{parts.port}"
    path = parts.path
    if re.search(r"%(?![0-9a-fA-F]{2})", path) or "\\" in path:
        raise ValueError("source_url contains invalid path encoding")
    unreserved = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~"

    def normalize_escape(match: re.Match[str]) -> str:
        character = chr(int(match[0][1:], 16))
        return character if character in unreserved else match[0].upper()

    # Reserved escapes such as %2F are data, not path delimiters. Decoding them
    # would silently merge distinct sources before duplicate checks can run.
    path = re.sub(r"%[0-9a-fA-F]{2}", normalize_escape, path)
    path = quote(path, safe="/:@-._~!$&'()*+,;=%").rstrip("/") or "/"
    if any(part in {".", ".."} for part in path.split("/")):
        raise ValueError("source_url must not contain dot path components")
    pairs = [
        (key, val)
        for key, val in _query_pairs(parts.query)
        if not key.lower().startswith("utm_") and key.lower() != "fbclid"
    ]
    video = youtube_id(value)
    if video:
        return f"https://youtube.com/watch?v={video}"
    # Distinct keys can be sorted; repeated-key values retain their source order
    # because an endpoint can select the first or last supplied value.
    return urlunsplit(
        (scheme, hostname, path, urlencode(sorted(pairs, key=lambda pair: pair[0])), "")
    )


def youtube_id(value: str) -> str | None:
    parts = urlsplit(value)
    if parts.port not in {None, 80 if parts.scheme.lower() == "http" else 443}:
        return None
    host = (parts.hostname or "").lower().removeprefix("www.")
    result = None
    if host == "youtu.be":
        result = parts.path.strip("/").split("/")[0]
    elif host in {"youtube.com", "m.youtube.com", "youtube-nocookie.com"}:
        if parts.path.rstrip("/") == "/watch":
            values = [v for k, v in _query_pairs(parts.query) if k == "v"]
            if len(set(values)) > 1:
                raise ValueError("contradictory video IDs in source URL")
            result = values[0] if values else None
        elif re.match(r"^/(?:embed|shorts|live)/", parts.path):
            result = parts.path.split("/")[2]
    if result is not None and not re.fullmatch(r"[A-Za-z0-9_-]{11}", result):
        raise ValueError("invalid YouTube video ID in source URL")
    return result


def _scalar(raw: str) -> object:
    raw = raw.strip()
    if raw.lower() in {"null", "none", "~", ""}:
        return None
    if raw[:1] in {'"', "[", "{"}:
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return []  # Unsupported syntax must not become invented metadata.
    if raw.startswith("'"):
        if re.fullmatch(r"'(?:[^']|'')*'", raw):
            return raw[1:-1].replace("''", "'")
        return []
    if raw[0] in "&*!|>@`%]}" or re.match(r"[-?:](?:\s|$)", raw):
        # Anchors, aliases, tags, block scalars, and other YAML constructs are
        # outside the supported scalar mapping. Quoted literals remain valid.
        return []
    return raw


def merge_metadata(
    parsed: ParsedTranscript, metadata: dict[str, object], *, origin: str
) -> None:
    for supplied_key, value in metadata.items():
        normalized_key = supplied_key.lower().strip().replace(" ", "_")
        key = ALIASES.get(normalized_key, normalized_key)
        if key not in METADATA_KEYS:
            continue
        if value is not None and (not isinstance(value, str) or not value.strip()):
            parsed.issues.append(
                Issue(StageOutcome.BLOCKED, f"{origin}: ambiguous {key}")
            )
            continue
        normalized = value.strip() if isinstance(value, str) else None
        if key == "source_url" and normalized:
            try:
                canonical_url(normalized)
            except ValueError as exc:
                parsed.issues.append(Issue(StageOutcome.FAIL, f"{origin}: {exc}"))
                continue
        existing = parsed.metadata.get(key)
        if existing and normalized:
            agrees = (
                canonical_url(existing) == canonical_url(normalized)
                if key == "source_url"
                else existing == normalized
            )
            if not agrees:
                parsed.issues.append(
                    Issue(StageOutcome.FAIL, f"{origin}: contradictory {key}")
                )
                continue
        if normalized is not None or key not in parsed.metadata:
            parsed.metadata[key] = normalized


def _seconds(value: str) -> Decimal:
    pieces = value.split(":")
    seconds = Decimal(pieces[-1])
    minutes = int(pieces[-2])
    if seconds >= 60 or (len(pieces) == 3 and minutes >= 60):
        raise ValueError(f"invalid transcript timestamp {value}")
    return seconds + minutes * 60 + (int(pieces[0]) * 3600 if len(pieces) == 3 else 0)


def parse_markdown(raw: bytes) -> ParsedTranscript:
    """Keep segment text as exact slices of the original UTF-8 source."""
    text = raw.decode("utf-8")
    parsed = ParsedTranscript(text=text, sha256=hashlib.sha256(raw).hexdigest())
    offset = 0
    lines = text.splitlines(keepends=True)
    if lines and lines[0].startswith("\ufeff"):
        # Ignore only the leading BOM for recognition, retaining raw text/hash
        # and its character in every subsequent source offset.
        lines[0] = lines[0][1:]
        offset = 1
    index = 0
    if lines and lines[0].strip() == "---":
        index = 1
        offset += len(lines[0])
        while index < len(lines) and lines[index].strip() != "---":
            line = lines[index]
            match = _META.match(line)
            if match:
                merge_metadata(
                    parsed, {match[1]: _scalar(match[2])}, origin="frontmatter"
                )
            elif line.strip() and not line.lstrip().startswith("#"):
                parsed.issues.append(
                    Issue(StageOutcome.BLOCKED, "unsupported frontmatter")
                )
            offset += len(line)
            index += 1
        if index == len(lines):
            parsed.issues.append(Issue(StageOutcome.FAIL, "unterminated frontmatter"))
            return parsed
        offset += len(lines[index])
        index += 1
    # A conventional Markdown preamble can provide metadata without frontmatter.
    while index < len(lines):
        line = lines[index]
        match = _META.match(line)
        key = match[1].lower().strip().replace(" ", "_") if match else None
        if re.fullmatch(r"#{1,6}\s+Transcript\s*", line.strip(), re.I):
            pass
        elif line.startswith("# "):
            merge_metadata(parsed, {"title": line[2:].strip()}, origin="heading")
        elif match and ALIASES.get(key, key) in METADATA_KEYS:
            merge_metadata(
                parsed, {match[1].lower(): _scalar(match[2])}, origin="preamble"
            )
        elif line.strip() and not re.fullmatch(
            r"#{1,6}\s+Transcript\s*", line.strip(), re.I
        ):
            break
        offset += len(line)
        index += 1

    current: ParsedSegment | None = None
    for line in lines[index:]:
        end = offset + len(line)
        stamp = _STAMP.match(line)
        content = line[stamp.end() :] if stamp else line.lstrip()
        label = _SPEAKER.match(content)
        if label is None:
            plain_prefix = re.match(r"^([^:\n]+):\s+", content)
            if plain_prefix and _ambiguous_plain_label(plain_prefix[1]):
                parsed.issues.append(
                    Issue(
                        StageOutcome.BLOCKED,
                        f"ambiguous segment speaker attribution at offset {offset}",
                    )
                )
        if label:
            name = next(v for v in label.groupdict().values() if v is not None).strip()
            if label["plain"]:
                if _ambiguous_speaker(name):
                    if not _ambiguous_plain_label(name):
                        label = None
                else:
                    kind = _plain_name_kind(name)
                    if kind == "uncertain":
                        parsed.issues.append(
                            Issue(
                                StageOutcome.BLOCKED,
                                f"unresolved plain speaker attribution at offset {offset}",
                            )
                        )
                    elif kind == "prose":
                        label = None
            if name in {"Note", "Example", "Warning", "Tip", "Source", "Title"}:
                label = None
        if not line.strip():
            current = None
        elif stamp or label or current is None:
            start_time = end_time = None
            if stamp:
                try:
                    start_time = _seconds(stamp[1])
                    end_time = _seconds(stamp[2]) if stamp[2] else None
                    if end_time is not None and end_time < start_time:
                        raise ValueError("transcript timestamp ends before it starts")
                except ValueError as exc:
                    parsed.issues.append(Issue(StageOutcome.FAIL, str(exc)))
            current = ParsedSegment(
                start=offset,
                end=end,
                start_seconds=start_time,
                end_seconds=end_time,
                speaker=name if label else None,
                evidence=label[0].strip() if label else None,
            )
            parsed.segments.append(current)
        else:
            current.end = end
        offset = end
    if not parsed.segments:
        parsed.issues.append(Issue(StageOutcome.BLOCKED, "transcript has no body"))
    return parsed


_UNKNOWN_SPEAKERS = {"unknown", "anonymous", "tbd", "n/a", "?", "panel", "multiple"}
_NUMBERED_PARTICIPANT = re.compile(
    r"(?:speaker|participant|panel(?:l)?ist)[\s_-]*(?:#\s*)?\d+", re.I
)


def _unidentified_speaker(label: str) -> bool:
    """Generic numbered participants do not establish a person's identity."""
    label = label.strip()
    return label.casefold() in _UNKNOWN_SPEAKERS or bool(
        _NUMBERED_PARTICIPANT.fullmatch(label)
    )


def _plain_name_kind(name: str) -> str:
    """Reuse the plain-label grammar without interpreting every colon as a name."""
    if name in {"Note", "Example", "Warning", "Tip", "Source", "Title"}:
        return "prose"
    if not re.fullmatch(rf"{_NAME_WORD}(?: {_NAME_WORD})*", name):
        return "prose"
    parts = name.split()
    lower = [part for part in parts if part[0].islower()]
    unknown = [
        part
        for part in lower
        if part not in _NAME_PARTICLES
        and not (
            part[:2] in {"d'", "d’", "l'", "l’"}
            and len(part) > 2
            and part[2].isalpha()
            and not part[2].islower()
        )
    ]
    if lower and (unknown or all(part in _NAME_PARTICLES for part in parts)):
        if unknown and (
            not parts[-1][0].islower()
            or any(character.isupper() for part in unknown for character in part)
            or (
                any(part in _NAME_PARTICLES for part in lower)
                and not parts[0][0].islower()
            )
        ):
            return "uncertain"
        return "prose"
    return "name"


def _ambiguous_plain_label(prefix: str) -> bool:
    """Plain separators need name evidence; explicit speaker syntax does not."""
    prefix = prefix.strip()
    if not _ambiguous_speaker(prefix):
        return False
    if _unidentified_speaker(prefix):
        return True
    parts = re.split(
        r"\s+(?:and|or)(?:\s*/\s*(?:and|or))?\s+|[;&]|(?<!\bn)/|/(?!a\b)",
        prefix,
        flags=re.I,
    )
    # A plausible/uncertain name on either side remains unresolved attribution.
    # Only prefixes whose components are all clear prose fall back to body text.
    return any(
        _unidentified_speaker(part) or _plain_name_kind(part.strip()) != "prose"
        for part in parts
        if part.strip()
    )


def _ambiguous_speaker(speaker: str) -> bool:
    return (
        not speaker.strip()
        or _unidentified_speaker(speaker)
        or bool(re.search(r"\s(?:and|or)\s|[/;]|\S\s*&\s*\S", speaker, re.I))
    )


def validate_metadata(parsed: ParsedTranscript) -> None:
    for field_name in ("title", "speaker", "source_url"):
        if not parsed.metadata.get(field_name):
            parsed.issues.append(Issue(StageOutcome.BLOCKED, f"missing {field_name}"))
    speaker = parsed.metadata.get("speaker")
    if speaker and _ambiguous_speaker(speaker):
        parsed.issues.append(
            Issue(StageOutcome.BLOCKED, "ambiguous speaker attribution")
        )
    for segment in parsed.segments:
        if segment.speaker is not None and _ambiguous_speaker(segment.speaker):
            parsed.issues.append(
                Issue(
                    StageOutcome.BLOCKED,
                    f"ambiguous segment speaker attribution at offset {segment.start}",
                )
            )
    source = parsed.metadata.get("source_url")
    explicit_video = parsed.metadata.get("youtube_video_id")
    if explicit_video and not re.fullmatch(r"[A-Za-z0-9_-]{11}", explicit_video):
        parsed.issues.append(Issue(StageOutcome.FAIL, "invalid youtube_video_id"))
    if source:
        try:
            source_video = youtube_id(source)
            if explicit_video and source_video and explicit_video != source_video:
                parsed.issues.append(
                    Issue(StageOutcome.FAIL, "contradictory video identity")
                )
            elif source_video:
                parsed.metadata["youtube_video_id"] = source_video
        except ValueError as exc:
            parsed.issues.append(Issue(StageOutcome.FAIL, str(exc)))
