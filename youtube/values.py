"""Value helpers for the YouTube normalizers: InnerTube text shapes, display
counts ("1.8B views", "973 replies", "986 million views"), durations
("1:02:03", "3 minutes, 18 seconds"), dates, thumbnails and the walkers
every parser shares. The package imports nothing from other sites'
packages, so it can be lifted out on its own.

Counts are parsed from English display strings. Requests default to
hl=en; with another `language` (set per request through set_language()),
count() turns strict: only unambiguous full numbers ("9.131.554.093
Aufrufe") are kept, abbreviated localized forms ("518 Mio.", "4,3 Mio.")
come back null rather than wrong.
"""
import re
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone

from youtube import refs

__all__ = [
    "to_int", "to_float", "bool_or_none", "text", "clean", "count", "duration_seconds",
    "duration_text", "iso_date", "iso_datetime", "iso_from_usec", "written_date",
    "thumbnails", "best_thumbnail", "walk", "first", "find_key", "browse_id", "canonical_path",
    "nav_link", "is_relative_time",
]

_WORD_MULT = {"thousand": 1e3, "million": 1e6, "billion": 1e9, "trillion": 1e12,
              "k": 1e3, "m": 1e6, "b": 1e9, "t": 1e12, "lakh": 1e5, "crore": 1e7}
_COUNT_RE = re.compile(r"(\d[\d,.   ]*)\s*(thousand|million|billion|trillion|lakh|crore|[KkMmBbTt])?(?![A-Za-z])")
_NO_COUNT_RE = re.compile(r"^\s*(no|zero)\b", re.I)


# ---- scalars --------------------------------------------------------------------------------
# Kept local (not imported from another site's package) so the youtube
# package stays self-contained.

def to_int(value):
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return None


def to_float(value):
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def bool_or_none(value):
    return value if isinstance(value, bool) else None


# ---- text ---------------------------------------------------------------------------------

def clean(value):
    if not isinstance(value, str):
        return None
    value = value.replace(" ", " ").replace("​", "").strip()
    return value or None


def text(value):
    """Any InnerTube text shape -> plain string (None when empty):
    'x' | {"simpleText"} | {"runs": [{"text"}]} | {"content"} (view models)."""
    if value is None:
        return None
    if isinstance(value, str):
        return clean(value)
    if isinstance(value, dict):
        if "simpleText" in value:
            return clean(value.get("simpleText"))
        if isinstance(value.get("runs"), list):
            return clean("".join(str(r.get("text") or "") for r in value["runs"] if isinstance(r, dict)))
        if "content" in value:
            return clean(value.get("content"))
        if "text" in value:
            return text(value.get("text"))
    return None


# ---- numbers ------------------------------------------------------------------------------

_LANGUAGE = ContextVar("youtube_response_language", default="en")
_FULL_NUMBER_RE = re.compile(r"^\d{1,3}(?:[.,\u00a0\u202f ]\d{3})+$")


def set_language(language):
    """The interface language of the responses being parsed (hl)."""
    _LANGUAGE.set((language or "en").lower())


def _strict_count(s):
    """Non-English display strings: a number only when it cannot be an
    abbreviation — digits alone, or 4+ digits in groups of three."""
    match = re.search(r"\d[\d.,\u00a0\u202f ]*\d|\d", s)
    if not match:
        return 0 if _NO_COUNT_RE.match(s) else None
    number = match.group(0).strip()
    rest = (s[:match.start()] + s[match.end():]).strip()
    if number.isdigit() and not rest:
        return int(number)
    if _FULL_NUMBER_RE.match(number):
        return int(re.sub(r"\D", "", number))
    return None


def count(value):
    """'1,820,537,172 views' -> 1820537172; '1.8B views' -> 1800000000;
    '986 million views' -> 986000000; '86K' -> 86000; 'No views' -> 0;
    ints pass through; junk -> None."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    s = text(value) if not isinstance(value, str) else clean(value)
    if not s:
        return None
    if not _LANGUAGE.get().startswith("en"):
        return _strict_count(s)
    if _NO_COUNT_RE.match(s):
        return 0
    match = _COUNT_RE.search(s)
    if not match:
        return None
    number, suffix = match.group(1).strip(), (match.group(2) or "").lower()
    number = number.replace(" ", "").replace(" ", "").replace(" ", "")
    if suffix:
        # abbreviated: '1.8', '4.55' (dot decimal) — a comma here is a decimal comma only in other locales
        try:
            amount = float(number.replace(",", ""))
        except ValueError:
            return None
        return int(round(amount * _WORD_MULT[suffix]))
    digits = number.replace(",", "").replace(".", "")
    return int(digits) if digits.isdigit() else None


def duration_seconds(value):
    """'1:02:03' -> 3723, '3:18' -> 198, '45' -> 45, 213 -> 213;
    '3 minutes, 18 seconds' -> 198; 'LIVE' / junk -> None."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    s = text(value) if not isinstance(value, str) else clean(value)
    if not s:
        return None
    if re.fullmatch(r"\d+(?::\d{1,2}){0,2}", s):
        total = 0
        for part in s.split(":"):
            total = total * 60 + int(part)
        return total
    if s.isdigit():
        return int(s)
    units = {"hour": 3600, "minute": 60, "second": 1}
    found = re.findall(r"(\d+)\s*(hour|minute|second)s?", s, re.I)
    if found:
        return sum(int(n) * units[u.lower()] for n, u in found)
    return None


def duration_text(seconds):
    """3723 -> '1:02:03', 198 -> '3:18' (YouTube's own display form)."""
    if seconds is None:
        return None
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


# ---- dates ------------------------------------------------------------------------------------

_MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def iso_datetime(value):
    """'2009-10-24T23:57:33-07:00' -> '2009-10-25T06:57:33Z' (UTC)."""
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def iso_date(value):
    """'2009-10-24T23:57:33-07:00' -> '2009-10-24' (the date as YouTube shows it, uploader-local)."""
    if not isinstance(value, str):
        return None
    match = re.match(r"(\d{4}-\d{2}-\d{2})", value.strip())
    return match.group(1) if match else None


def iso_from_usec(value):
    """Microseconds since the epoch (live chat timestamps) -> ISO UTC."""
    usec = to_int(value)
    if usec is None:
        return None
    try:
        return datetime.fromtimestamp(usec / 1e6, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (OverflowError, OSError, ValueError):
        return None


def written_date(value):
    """'Jan 12, 2017' | 'Joined Feb 19, 2012' | 'Last updated on Jul 17, 2026'
    | 'Premiered Sep 1, 2026' | '12 Jan 2017' -> 'YYYY-MM-DD'. Relative
    forms ('2 days ago', 'Updated today') -> None (see is_relative_time)."""
    s = text(value) if not isinstance(value, str) else clean(value)
    if not s:
        return None
    match = re.search(r"([A-Za-z]{3,9})\.?\s+(\d{1,2}),?\s+(\d{4})", s)
    if match:
        month = _MONTHS.get(match.group(1)[:3].lower())
        day, year = int(match.group(2)), int(match.group(3))
    else:
        match = re.search(r"(\d{1,2})\s+([A-Za-z]{3,9})\.?,?\s+(\d{4})", s)
        if not match:
            match = re.search(r"(\d{4})-(\d{2})-(\d{2})", s)
            if match:
                return match.group(0)
            return None
        day, month, year = int(match.group(1)), _MONTHS.get(match.group(2)[:3].lower()), int(match.group(3))
    if not month:
        return None
    try:
        return datetime(year, month, day).strftime("%Y-%m-%d")
    except ValueError:
        return None


def relative_date(value, now=None):
    """'Updated today' / 'Updated yesterday' / 'Updated 3 days ago' -> YYYY-MM-DD
    (day-level only; used for playlist 'last updated')."""
    s = (clean(value) or "").lower()
    now = now or datetime.now(timezone.utc)
    if "today" in s:
        return now.strftime("%Y-%m-%d")
    if "yesterday" in s:
        return (now - timedelta(days=1)).strftime("%Y-%m-%d")
    match = re.search(r"(\d+)\s+day", s)
    if match:
        return (now - timedelta(days=int(match.group(1)))).strftime("%Y-%m-%d")
    return None


_RELATIVE_RE = re.compile(r"\b(ago|streamed|premiered|premieres|scheduled|live|today|yesterday|"
                          r"\d+\s*(?:y|mo|w|d|h|min|m|s)\b)", re.I)


def is_relative_time(value):
    s = clean(value) if isinstance(value, str) else text(value)
    return bool(s and _RELATIVE_RE.search(s))


# ---- thumbnails ---------------------------------------------------------------------------------

def _thumb_list(value):
    if isinstance(value, dict):
        if isinstance(value.get("thumbnails"), list):
            return value["thumbnails"]
        if isinstance(value.get("sources"), list):
            return value["sources"]
        if isinstance(value.get("image"), dict):
            return _thumb_list(value["image"])
        if isinstance(value.get("thumbnail"), dict):
            return _thumb_list(value["thumbnail"])
    if isinstance(value, list):
        return value
    return []


def thumbnails(value):
    """Every size, smallest first: [{link, width, height}] (protocol-relative
    links made absolute; icon-only 'clientResource' entries dropped)."""
    out = []
    for item in _thumb_list(value):
        if not isinstance(item, dict) or not isinstance(item.get("url"), str):
            continue
        out.append({"link": refs.absolute(item["url"]), "width": to_int(item.get("width")),
                    "height": to_int(item.get("height"))})
    out.sort(key=lambda t: (t["width"] or 0) * (t["height"] or 0))
    return out


def best_thumbnail(value):
    """The largest thumbnail's link (None when there is none)."""
    items = thumbnails(value)
    return items[-1]["link"] if items else None


# ---- walkers --------------------------------------------------------------------------------------

def walk(obj, key):
    """Every value stored under `key`, depth-first, anywhere in obj."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == key:
                yield v
            if isinstance(v, (dict, list)):
                yield from walk(v, key)
    elif isinstance(obj, list):
        for v in obj:
            if isinstance(v, (dict, list)):
                yield from walk(v, key)


def first(obj, key, default=None):
    for value in walk(obj, key):
        return value
    return default


def find_key(obj, *path, default=None):
    """Nested dict/list access: find_key(d, "a", 0, "b") (None-safe)."""
    node = obj
    for step in path:
        if isinstance(step, int):
            if not isinstance(node, list) or not -len(node) <= step < len(node):
                return default
            node = node[step]
        else:
            if not isinstance(node, dict) or step not in node:
                return default
            node = node[step]
    return node


def browse_id(obj):
    """First browseEndpoint.browseId in obj (channel ids behind a byline / avatar)."""
    for endpoint in walk(obj, "browseEndpoint"):
        if isinstance(endpoint, dict) and endpoint.get("browseId"):
            return endpoint["browseId"]
    return None


def canonical_path(obj):
    for endpoint in walk(obj, "browseEndpoint"):
        if isinstance(endpoint, dict) and isinstance(endpoint.get("canonicalBaseUrl"), str):
            return endpoint["canonicalBaseUrl"]
    return None


def nav_link(obj):
    """The target of a navigation/command endpoint: urlEndpoint (redirects
    unwrapped to the real destination) or the webCommandMetadata url."""
    for endpoint in walk(obj, "urlEndpoint"):
        if isinstance(endpoint, dict) and isinstance(endpoint.get("url"), str):
            return unwrap_redirect(endpoint["url"])
    for meta in walk(obj, "webCommandMetadata"):
        if isinstance(meta, dict) and isinstance(meta.get("url"), str):
            return refs.absolute(meta["url"])
    return None


def unwrap_redirect(link):
    """youtube.com/redirect?...&q=<target> -> <target>."""
    from urllib.parse import parse_qs, urlparse
    if not isinstance(link, str):
        return None
    absolute = refs.absolute(link)
    parsed = urlparse(absolute)
    if parsed.path == "/redirect":
        target = parse_qs(parsed.query).get("q")
        if target:
            return target[0]
    return absolute
