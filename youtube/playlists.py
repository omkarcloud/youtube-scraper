"""/youtube/playlists/* — a playlist's details and its videos (100 a
page). Works for user playlists, albums (OLAK5uy_), uploads (UU) and
podcasts; mixes (RD) have no browse page and 404."""
from youtube import items as I
from youtube import page_parsers as PP
from youtube import values as V
from youtube.fetch import YoutubeBadRequest, YoutubeNotFound, innertube
from youtube.shared import locale, page_result


def _browse(playlist, hl, gl):
    try:
        data = innertube("browse", {"browseId": "VL" + playlist}, hl=hl, gl=gl)
    except (YoutubeNotFound, YoutubeBadRequest):
        raise YoutubeNotFound(f"playlist '{playlist}' not found")
    alerts = " ".join(V.text(a) or "" for a in V.walk(data.get("alerts") or [], "text"))
    if not data.get("contents") or ("does not exist" in alerts.lower() and not data.get("header")):
        raise YoutubeNotFound(f"playlist '{playlist}' not found" + (f": {alerts.strip()}" if alerts.strip() else ""))
    return data


def _video_list(data):
    """The playlist's own item section (not the 'more playlists' shelf after it)."""
    for section in V.walk(data.get("contents") or {}, "itemSectionRenderer"):
        if isinstance(section, dict) and section.get("contents"):
            return section
    return {}


def details(playlist, country="US", language="en"):
    """Title, description, owner, video count, total views, last-updated
    date, privacy and kind (playlist / album / uploads / mix)."""
    hl, gl = locale(country, language)
    data = _browse(playlist, hl, gl)
    return {"playlist": PP.playlist_details(data, playlist)}


def videos(playlist, cursor=None, country="US", language="en"):
    """The playlist's videos in order, 100 a page, with a running position."""
    hl, gl = locale(country, language)
    if cursor:
        offset, _, token = cursor.partition(".")
        start = int(offset) if offset.isdigit() and token else 0
        token = token or cursor
        try:
            data = innertube("browse", {"continuation": token}, hl=hl, gl=gl)
        except YoutubeBadRequest:
            raise YoutubeBadRequest("cursor is not valid for this list (use the next_cursor this endpoint returned)")
        body = I.continuation_items(data)
        header = None
    else:
        data = _browse(playlist, hl, gl)
        body = _video_list(data)
        start = 0
        header = PP.playlist_details(data, playlist)
    rows = [r for r in I.collect(body, dedupe=False) if r["type"] in ("video", "short")]
    for index, row in enumerate(rows, start + 1):
        row["position"] = index
    token = I.next_cursor(body)
    next_cursor = f"{start + len(rows)}.{token}" if token else None
    ref = {"id": playlist, "title": header["title"] if header else None,
           "link": header["link"] if header else f"https://www.youtube.com/playlist?list={playlist}",
           "video_count": header["video_count"] if header else None}
    return page_result("videos", rows, next_cursor, playlist=ref)
