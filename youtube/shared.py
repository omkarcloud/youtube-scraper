"""Helpers shared by the /youtube/* endpoint modules: channel-id
resolution (handles, custom URLs and legacy /user names -> UC ids,
remembered for a few hours), browse helpers for tabbed pages and the
continuation plumbing every paged list uses."""
import threading
import time

from youtube import items as I
from youtube import page_parsers as PP
from youtube import refs
from youtube import values as V
from youtube.fetch import YoutubeBadRequest, YoutubeNotFound, innertube

_CHANNEL_TTL = 6 * 3600
# replies of POST comments page through `browse` (video comment replies use
# `next`); the reply cursor we hand out carries this prefix to say which.
BROWSE_CURSOR_PREFIX = "b."
_channel_ids = {}
_lock = threading.Lock()


def locale(country=None, language=None):
    """(hl, gl) for InnerTube; English / US unless asked otherwise. Also
    tells the parsers which language the display counts will be in —
    every endpoint calls this first."""
    hl, gl = (language or "en"), (country or "US")
    V.set_language(hl)
    return hl, gl


def channel_label(ref):
    return ref.get("id") or (ref.get("path") or "").lstrip("/") or "?"


def channel_id(ref):
    """A resolved channel ref -> the UC channel id (one resolve_url call for
    handles / custom names, cached)."""
    if ref.get("id"):
        return ref["id"]
    path = ref.get("path")
    with _lock:
        hit = _channel_ids.get(path.lower())
        if hit and hit[1] > time.time():
            return hit[0]
    try:
        data = innertube("navigation/resolve_url", {"url": refs.SITE + path})
    except YoutubeNotFound:
        raise YoutubeNotFound(f"channel '{channel_label(ref)}' not found")
    browse = (data.get("endpoint") or {}).get("browseEndpoint") or {}
    cid = browse.get("browseId")
    if not cid or not str(cid).startswith("UC"):
        raise YoutubeNotFound(f"channel '{channel_label(ref)}' not found")
    with _lock:
        _channel_ids[path.lower()] = (cid, time.time() + _CHANNEL_TTL)
    return cid


def page_result(key, rows, cursor, **extra):
    """{**extra, "count", <key>: rows, "next_cursor"} — the list shape every endpoint returns."""
    out = dict(extra)
    out["count"] = len(rows)
    out[key] = rows
    out["next_cursor"] = cursor
    return out


def continuation(endpoint, cursor, hl, gl, parse=None):
    """One continuation call -> (items, next_cursor). A cursor YouTube
    rejects is the caller's mistake (400), not an upstream failure."""
    try:
        data = innertube(endpoint, {"continuation": cursor}, hl=hl, gl=gl)
    except YoutubeBadRequest:
        raise YoutubeBadRequest("cursor is not valid for this list (use the next_cursor this endpoint returned)")
    body = I.continuation_items(data) or data
    return (parse or I.collect)(body), I.next_cursor(body)


def channel_tab(cid, tab, hl, gl, query=None):
    """Browse one channel tab -> (browse data, tab content or None when the
    channel has no such tab — YouTube then silently answers with Home)."""
    from youtube.protos import CHANNEL_TABS
    _, params = CHANNEL_TABS[tab]
    body = {"browseId": cid, "params": params}
    if query:
        body["query"] = query
    data = innertube("browse", body, hl=hl, gl=gl)
    if not data.get("contents") and not data.get("header"):
        raise YoutubeNotFound(f"channel '{cid}' not found")
    key, renderer = PP.selected_tab(data)
    if renderer is None:
        return data, None
    # tab titles (-> key) are localized with hl ("Vidéos"); the tab's params are not
    selected_params = ((renderer.get("endpoint") or {}).get("browseEndpoint") or {}).get("params")
    if key != tab and selected_params != params:
        return data, None
    return data, renderer.get("content")


def channel_ref_of(data, cid):
    """A compact channel ref from a channel browse response (for list headers)."""
    meta = (data.get("metadata") or {}).get("channelMetadataRenderer") or {}
    header = PP.channel_header(data)
    handle = header.get("handle")
    return {"id": meta.get("externalId") or cid, "name": meta.get("title") or header.get("name"),
            "handle": handle, "link": refs.channel_link(meta.get("externalId") or cid, handle)}
