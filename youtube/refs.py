"""One param per input (tripadvisor QueryOrIdField convention): every
resolver takes a bare id / handle OR any YouTube link and hands back what
the InnerTube calls need. Each raises ValueError with a user-facing
message; schemas.py wraps them as RefField subclasses.

    resolve_video     "dQw4w9WgXcQ" | https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=42
                      | https://youtu.be/dQw4w9WgXcQ | /shorts/<id> | /live/<id> | /embed/<id>
                      -> "dQw4w9WgXcQ"
    resolve_videos    comma list of the above (max 20)
    resolve_channel   "UCX6OQ3DkcsbYNE6H8uQQuVA" | "@MrBeast" | "MrBeast"
                      | https://www.youtube.com/@MrBeast/videos | /channel/UC... | /c/name | /user/name
                      -> {"id": "UC..."} | {"path": "/@MrBeast"} (resolved by shared.channel_id)
    resolve_playlist  "PLFgquLnL59alCl_2TQvOiD5Vgm1hCaGSI" | any link with ?list=
    resolve_post      "UgkxzJ6r7VLAK03TN5Gjq7YpjHmjtee_Rdn9" | https://www.youtube.com/post/<id>
                      | /channel/<id>/community?lb=<id>
    resolve_hashtag   "lofi" | "#lofi" | https://www.youtube.com/hashtag/lofi
    resolve_link      any YouTube link -> {"kind", "link", ...} for /youtube/resolve
"""
import re
from urllib.parse import parse_qs, quote, unquote, urlparse

SITE = "https://www.youtube.com"

_VIDEO_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
_CHANNEL_ID_RE = re.compile(r"^UC[A-Za-z0-9_-]{22}$")
_HANDLE_RE = re.compile(r"^[A-Za-z0-9._\-À-￿]{3,100}$")
_PLAYLIST_RE = re.compile(r"^(?:PL|UU|LL|FL|RD|OL|UL|EL|TL|SP|WL|LP|PU|OLAK5uy_)[A-Za-z0-9_-]{0,70}$")
_POST_RE = re.compile(r"^Ug[A-Za-z0-9_-]{20,60}$")
_HASHTAG_RE = re.compile(r"^[^\s#/?&]{1,100}$")

_HOSTS = ("youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be",
          "youtube-nocookie.com", "www.youtube-nocookie.com", "gaming.youtube.com")
# first path segments that are pages, never a legacy /<custom> channel name
_RESERVED = {"watch", "shorts", "live", "embed", "v", "e", "playlist", "post", "hashtag", "results",
             "feed", "channel", "c", "user", "source", "gaming", "premium", "music", "kids", "t",
             "about", "account", "signin", "logout", "redirect", "attribution_link", "oembed", "s",
             "clip", "tv", "howyoutubeworks", "creators", "ads", "yt", "select_site", "paid_memberships",
             "reporthistory", "upload", "trends", "podcasts", "storefront", "movies", "news"}


def _is_url(value):
    low = value.lower()
    return low.startswith(("http://", "https://", "//")) or any(
        low.startswith(host + "/") or low == host for host in _HOSTS)


def _parts(value):
    """A YouTube link -> (host, [path segments], query dict). ValueError otherwise."""
    raw = value if value.lower().startswith(("http://", "https://")) else \
        ("https:" + value if value.startswith("//") else "https://" + value)
    parsed = urlparse(raw)
    host = (parsed.hostname or "").lower()
    if host not in _HOSTS and not host.endswith(".youtube.com"):
        raise ValueError("must be a youtube.com / youtu.be link")
    segments = [unquote(s) for s in parsed.path.split("/") if s]
    return host, segments, parse_qs(parsed.query)


def _video_from_url(value):
    host, segments, query = _parts(value)
    if host == "youtu.be":
        return segments[0] if segments else None
    if query.get("v"):
        return query["v"][0]
    low = [s.lower() for s in segments]
    for index, segment in enumerate(low):
        if segment in ("shorts", "live", "embed", "v", "e", "watch") and index + 1 < len(segments):
            return segments[index + 1]
    if host.startswith("music.") and query.get("v"):
        return query["v"][0]
    return None


def resolve_video(value):
    value = str(value or "").strip()
    if not value:
        raise ValueError("video is required")
    if _is_url(value):
        try:
            candidate = _video_from_url(value)
        except ValueError:
            raise ValueError("video must be an 11-character YouTube video id or a youtube.com / youtu.be video link")
        if not candidate:
            raise ValueError("video link must look like https://www.youtube.com/watch?v=dQw4w9WgXcQ or https://youtu.be/dQw4w9WgXcQ")
        value = candidate
    value = value.split("?")[0].split("&")[0].split("#")[0].strip()
    if not _VIDEO_RE.match(value):
        raise ValueError("video must be an 11-character YouTube video id (dQw4w9WgXcQ) or a video link")
    return value


def resolve_videos(value, max_items=20):
    out = []
    for raw in str(value or "").split(","):
        raw = raw.strip()
        if raw:
            vid = resolve_video(raw)
            if vid not in out:
                out.append(vid)
    if not out:
        raise ValueError("videos is required")
    if len(out) > max_items:
        raise ValueError(f"at most {max_items} videos")
    return out


def _handle(value):
    value = value.strip().lstrip("@")
    if not _HANDLE_RE.match(value):
        raise ValueError("channel must be a channel id (UC...), a handle (@MrBeast) or a youtube.com channel link")
    return {"path": "/@" + value}


def resolve_channel(value):
    value = str(value or "").strip()
    if not value:
        raise ValueError("channel is required")
    if _is_url(value):
        try:
            host, segments, _ = _parts(value)
        except ValueError:
            raise ValueError("channel must be a channel id (UC...), a handle (@MrBeast) or a youtube.com channel link")
        if not segments or host == "youtu.be":
            raise ValueError("channel link must look like https://www.youtube.com/@MrBeast or https://www.youtube.com/channel/UC...")
        first, low = segments[0], segments[0].lower()
        if first.startswith("@"):
            return _handle(first)
        if low == "channel" and len(segments) >= 2 and _CHANNEL_ID_RE.match(segments[1]):
            return {"id": segments[1]}
        if low in ("c", "user") and len(segments) >= 2:
            return {"path": f"/{low}/{quote(segments[1])}"}
        if low not in _RESERVED and len(segments) <= 2:
            return {"path": "/" + quote(first)}
        raise ValueError("that link is not a channel page (expected https://www.youtube.com/@MrBeast or /channel/UC...)")
    if _CHANNEL_ID_RE.match(value):
        return {"id": value}
    return _handle(value)


def resolve_playlist(value):
    value = str(value or "").strip()
    if not value:
        raise ValueError("playlist is required")
    if _is_url(value):
        try:
            _, _, query = _parts(value)
        except ValueError:
            raise ValueError("playlist must be a playlist id (PL...) or a youtube.com link with ?list=")
        if not query.get("list"):
            raise ValueError("playlist link must carry ?list=<id>, e.g. https://www.youtube.com/playlist?list=PL...")
        value = query["list"][0]
    value = value.split("&")[0].split("#")[0]
    if value.startswith("VL") and _PLAYLIST_RE.match(value[2:]):
        value = value[2:]
    if not _PLAYLIST_RE.match(value) or len(value) < 12:
        raise ValueError("playlist must be a YouTube playlist id (PLFgquLnL59alCl_2TQvOiD5Vgm1hCaGSI) or a playlist link")
    return value


def resolve_post(value):
    value = str(value or "").strip()
    if not value:
        raise ValueError("post is required")
    if _is_url(value):
        try:
            _, segments, query = _parts(value)
        except ValueError:
            raise ValueError("post must be a community post id (Ugkx...) or a youtube.com/post link")
        candidate = None
        if query.get("lb"):
            candidate = query["lb"][0]
        else:
            for index, segment in enumerate(segments):
                if segment.lower() == "post" and index + 1 < len(segments):
                    candidate = segments[index + 1]
                    break
        if not candidate:
            raise ValueError("post link must look like https://www.youtube.com/post/Ugkx...")
        value = candidate
    value = value.split("?")[0].split("#")[0]
    if not _POST_RE.match(value):
        raise ValueError("post must be a YouTube community post id (UgkxzJ6r7VLAK03TN5Gjq7YpjHmjtee_Rdn9) or a post link")
    return value


def resolve_hashtag(value):
    value = str(value or "").strip()
    if not value:
        raise ValueError("hashtag is required")
    if _is_url(value):
        try:
            _, segments, _ = _parts(value)
        except ValueError:
            raise ValueError("hashtag must be a hashtag (lofi, #lofi) or a youtube.com/hashtag link")
        if len(segments) >= 2 and segments[0].lower() == "hashtag":
            value = segments[1]
        else:
            raise ValueError("hashtag link must look like https://www.youtube.com/hashtag/lofi")
    value = value.lstrip("#").strip()
    if not _HASHTAG_RE.match(value):
        raise ValueError("hashtag must be one word without spaces (lofi or #lofi)")
    return value.lower()


def resolve_link(value):
    """Any YouTube link -> what it points at, without a network call when
    the link says so itself: {"kind": video|short|channel|playlist|post|hashtag, ...}.
    Channel handles / custom URLs keep a `path` that shared.channel_id resolves."""
    value = str(value or "").strip()
    if not value:
        raise ValueError("link is required")
    if not _is_url(value):
        raise ValueError("link must be a youtube.com / youtu.be link")
    try:
        host, segments, query = _parts(value)
    except ValueError as e:
        raise ValueError(str(e))
    low = [s.lower() for s in segments]
    if query.get("lb") or (low and low[0] == "post"):
        return {"kind": "post", "id": resolve_post(value)}
    if low and low[0] == "hashtag":
        return {"kind": "hashtag", "id": resolve_hashtag(value)}
    video = None
    try:
        video = _video_from_url(value)
    except ValueError:
        video = None
    if video and _VIDEO_RE.match(video):
        out = {"kind": "short" if low and low[0] == "shorts" else "video", "id": video}
        if query.get("list"):
            try:
                out["playlist_id"] = resolve_playlist(query["list"][0])
            except ValueError:
                pass
        if query.get("t"):
            out["start_seconds"] = _timestamp(query["t"][0])
        return out
    if query.get("list"):
        return {"kind": "playlist", "id": resolve_playlist(value)}
    ref = resolve_channel(value)
    return {"kind": "channel", **ref}


def _timestamp(value):
    """'42' | '1m30s' | '1h2m3s' -> seconds (None when unreadable)."""
    value = str(value or "").strip().lower()
    if value.isdigit():
        return int(value)
    match = re.fullmatch(r"(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s?)?", value)
    if not match or not any(match.groups()):
        return None
    h, m, s = (int(g or 0) for g in match.groups())
    return h * 3600 + m * 60 + s


# ---- links ----------------------------------------------------------------------------

def video_link(video_id):
    return f"{SITE}/watch?v={video_id}" if video_id else None


def short_link(video_id):
    return f"{SITE}/shorts/{video_id}" if video_id else None


def channel_link(channel_id=None, handle=None):
    if handle:
        return f"{SITE}/{handle if handle.startswith('@') else '@' + handle}"
    return f"{SITE}/channel/{channel_id}" if channel_id else None


def playlist_link(playlist_id):
    return f"{SITE}/playlist?list={playlist_id}" if playlist_id else None


def post_link(post_id):
    return f"{SITE}/post/{post_id}" if post_id else None


def hashtag_link(tag):
    return f"{SITE}/hashtag/{quote(tag)}" if tag else None


def absolute(path):
    """'/@MrBeast' -> 'https://www.youtube.com/@MrBeast'; absolute links pass through."""
    if not path or not isinstance(path, str):
        return None
    if path.startswith("//"):
        return "https:" + path
    if path.startswith("/"):
        return SITE + path
    if path.startswith("http://"):
        return "https://" + path[len("http://"):]
    return path
