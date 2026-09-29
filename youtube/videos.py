"""/youtube/videos/* — one video's full details (and a batch form), its
stream formats + storyboards, comments and replies, related videos,
subtitle tracks, transcript and live chat. Every function takes the
resolved 11-character `video` id."""
import json

from youtube import items as I
from youtube import protos
from youtube import refs
from youtube import social_parsers as S
from youtube import values as V
from youtube import video_parsers as P
from youtube.fetch import (YoutubeBadRequest, YoutubeNotFound, YoutubeUpstreamError, innertube, player,
                           run_parallel_safe, timedtext)
from youtube.shared import BROWSE_CURSOR_PREFIX, continuation, locale, page_result


def _not_found(video, reason=None):
    return YoutubeNotFound(f"video '{video}' is unavailable" + (f": {reason}" if reason else ""))


def _video_ref(video, title=None):
    return {"id": video, "title": title, "link": refs.video_link(video)}


def details(video, country="US", language="en"):
    """Everything the watch page knows: metadata, exact views / likes,
    comment count, channel, chapters, most-replayed heatmap, music credits,
    description links, info cards, end screen, subtitle tracks, concerts,
    live broadcast state and availability — 3 calls in parallel."""
    hl, gl = locale(country, language)
    (web, web_err), (android, _), (nxt, next_err) = run_parallel_safe([
        lambda: player(video, client="WEB", hl=hl, gl=gl),
        lambda: player(video, client="ANDROID", hl=hl, gl=gl),
        lambda: innertube("next", {"videoId": video}, hl=hl, gl=gl),
    ])
    if web_err and next_err:
        raise web_err
    if P.is_unavailable(web, nxt):
        _, reason = P.playability(web)
        raise _not_found(video, reason)
    return {"video": P.video_details(web, android, nxt)}


def batch(videos, country="US", language="en"):
    """Up to 20 videos in one call (views, likes, duration, publish time,
    category, keywords, channel). A missing video yields {id, error}."""
    hl, gl = locale(country, language)
    results = run_parallel_safe([lambda v=v: player(v, client="WEB", hl=hl, gl=gl) for v in videos])
    rows = []
    for vid, (data, error) in zip(videos, results):
        summary = P.video_summary(data) if data else None
        if summary is None:
            if error is not None and not isinstance(error, (YoutubeNotFound, YoutubeBadRequest)):
                raise error
            rows.append({"id": vid, "link": refs.video_link(vid), "error": "unavailable"})
        else:
            rows.append(summary)
    return {"count": len(rows), "found_count": sum(1 for r in rows if "error" not in r), "videos": rows}


def streams(video, country="US"):
    """Direct stream links (muxed, video-only, audio-only), HLS / DASH
    manifests for live, storyboard sprite sheets. Links expire (see
    expires_at) and are not tied to the caller's IP."""
    _, gl = locale(country)
    data = player(video, client="ANDROID", gl=gl)
    status, reason = P.playability(data)
    details = data.get("videoDetails") or {}
    if status in ("ERROR",) or not details.get("videoId"):
        raise _not_found(video, reason)
    if status == "LOGIN_REQUIRED":
        raise YoutubeBadRequest(f"video '{video}' needs a signed-in (age-verified) account: {reason}")
    out = {"video": {"id": video, "title": V.clean(details.get("title")),
                     "link": refs.video_link(video),
                     "duration_seconds": V.to_int(details.get("lengthSeconds")) or None,
                     "is_live": bool(details.get("isLive")),
                     "playability": {"status": status, "reason": reason}}}
    out.update(P.streams(data))
    out["storyboards"] = P.storyboards(data, V.to_int(details.get("lengthSeconds")))
    return out


def comments(video, sort="top", cursor=None, country="US", language="en"):
    """Top or newest comments, 20 a page. The first page also carries the
    exact total. Each comment's replies_cursor feeds /comments/replies."""
    hl, gl = locale(country, language)
    token = cursor or protos.comments_token(video, sort)
    try:
        data = innertube("next", {"continuation": token}, hl=hl, gl=gl)
    except YoutubeBadRequest:
        if cursor:
            raise YoutubeBadRequest("cursor is not valid for this list (use the next_cursor this endpoint returned)")
        raise
    total, rows, next_cursor = S.comments_page(data, link_base=refs.video_link(video))
    if cursor is None and not rows and total is None:
        # no comment section at all: disabled, a live stream, or no such video
        probe = innertube("next", {"videoId": video}, hl=hl, gl=gl)
        if not P.watch_renderer(probe, "videoPrimaryInfoRenderer"):
            raise _not_found(video)
        return page_result("comments", [], None, video=_video_ref(video), are_comments_enabled=P.comments_enabled(probe),
                           total_count=None, sort=sort)
    return page_result("comments", rows, next_cursor, video=_video_ref(video), are_comments_enabled=True,
                       total_count=total, sort=sort)


def replies(cursor, country="US", language="en"):
    """The replies under one comment (video or post), ~10 then ~50 a page."""
    hl, gl = locale(country, language)
    endpoint, token = ("browse", cursor[len(BROWSE_CURSOR_PREFIX):]) if cursor.startswith(BROWSE_CURSOR_PREFIX) else ("next", cursor)
    try:
        data = innertube(endpoint, {"continuation": token}, hl=hl, gl=gl)
    except YoutubeBadRequest:
        raise YoutubeBadRequest("cursor is not a valid replies_cursor (take it from a comment in /comments)")
    _, rows, next_cursor = S.comments_page(data)
    if next_cursor and endpoint == "browse":
        next_cursor = BROWSE_CURSOR_PREFIX + next_cursor
    return page_result("replies", rows, next_cursor)


def related(video, cursor=None, country="US", language="en"):
    """The watch-next column: related videos, mixes and playlists."""
    hl, gl = locale(country, language)
    if cursor:
        rows, next_cursor = continuation("next", cursor, hl, gl)
    else:
        data = innertube("next", {"videoId": video}, hl=hl, gl=gl)
        column = V.find_key(data, "contents", "twoColumnWatchNextResults", "secondaryResults", "secondaryResults")
        if column is None:
            if not P.watch_renderer(data, "videoPrimaryInfoRenderer"):
                raise _not_found(video)
            column = {}
        rows, next_cursor = I.collect(column), I.next_cursor(column)
    rows = [r for r in rows if r.get("id") != video]
    return page_result("items", rows, next_cursor, video=_video_ref(video))


def _android_player(video):
    locale()
    data = player(video, client="ANDROID")
    status, reason = P.playability(data)
    if status == "ERROR" or not (data.get("videoDetails") or {}).get("videoId"):
        raise _not_found(video, reason)
    return data


def subtitles(video):
    """Every subtitle track (manual and auto-generated) with a ready-made
    WebVTT link (signed for any IP, valid ~6 h)."""
    data = _android_player(video)
    tracks, _ = P.caption_tracks(data)
    details = data.get("videoDetails") or {}
    return {"video": _video_ref(video, V.clean(details.get("title"))), "count": len(tracks), "subtitles": tracks}


def _timedtext_json(link):
    body = timedtext(link)
    try:
        return json.loads(body) if body and body.strip() else {}
    except ValueError:
        raise YoutubeUpstreamError("timedtext returned an unparseable body")


def transcript(video, language=None, format="json"):
    """The transcript as timed segments (+ plain text, SRT or WebVTT) from
    the chosen track (default: the video's own language). Machine
    translation (timedtext `tlang`) is not offered: YouTube answers it with
    429 unless a browser-minted PoToken rides along, and even then rate-
    limits the egress within a few requests (tested 2026-09-27)."""
    data = _android_player(video)
    details = data.get("videoDetails") or {}
    track, any_track = P.base_caption_url(data, language)
    if any_track is None:
        raise YoutubeNotFound(f"video '{video}' has no subtitles")
    if track is None:
        available = sorted({t.get("languageCode") for t in
                            (V.find_key(data, "captions", "playerCaptionsTracklistRenderer", "captionTracks") or [])
                            if isinstance(t, dict)})
        raise YoutubeNotFound(f"video '{video}' has no '{language}' subtitles (available: {', '.join(available)})")
    segments = P.json3_segments(_timedtext_json(P.caption_link(track["baseUrl"], "json3")))
    out = {
        "video": _video_ref(video, V.clean(details.get("title"))),
        "language_code": track.get("languageCode"),
        "language": V.text(track.get("name")),
        "is_auto_generated": track.get("kind") == "asr",
        "count": len(segments),
        "text": P.to_text(segments),
        "segments": segments,
    }
    if format == "srt":
        out["content"] = P.to_srt(segments)
    elif format == "vtt":
        out["content"] = P.to_vtt(segments)
    if format in ("text", "srt", "vtt"):
        out.pop("segments")
        if format != "text":
            out.pop("text")
    return out


_REPLAY_PREFIX = "r."


def _chat_tokens(next_data):
    """(renderer, {'top': token, 'all': token}) from the watch page."""
    renderer = V.first(V.find_key(next_data, "contents", "twoColumnWatchNextResults", "conversationBar") or {},
                       "liveChatRenderer") or {}
    tokens = {}
    menu = V.first(renderer.get("header") or {}, "subMenuItems") or []
    for index, item in enumerate(menu):
        token = V.first(item, "continuation")
        if isinstance(token, str):
            tokens["top" if index == 0 else "all"] = token
    if "top" not in tokens:
        for entry in renderer.get("continuations") or []:
            token = V.first(entry, "continuation")
            if isinstance(token, str):
                tokens["top"] = token
                break
    return renderer, tokens


def live_chat(video, type="top", cursor=None):
    """Live chat (a live stream) or chat replay (a finished stream):
    messages, super chats / stickers, memberships and gifts, with author
    roles. Poll again with next_cursor after poll_after_seconds."""
    locale()
    if cursor:
        replay = cursor.startswith(_REPLAY_PREFIX)
        token = cursor[len(_REPLAY_PREFIX):] if replay else cursor
    else:
        nxt = innertube("next", {"videoId": video})
        renderer, tokens = _chat_tokens(nxt)
        if not renderer:
            if not P.watch_renderer(nxt, "videoPrimaryInfoRenderer"):
                raise _not_found(video)
            raise YoutubeNotFound(f"video '{video}' has no live chat (not a live stream, or chat is off)")
        replay = bool(renderer.get("isReplay"))
        token = tokens.get(type) or tokens.get("top")
        if not token:
            raise YoutubeNotFound(f"video '{video}' has no live chat")
    endpoint = "live_chat/get_live_chat_replay" if replay else "live_chat/get_live_chat"
    body = {"continuation": token}
    if replay:
        body["currentPlayerState"] = {"playerOffsetMs": "0"}
    try:
        data = innertube(endpoint, body)
    except YoutubeBadRequest:
        raise YoutubeBadRequest("cursor is not a valid live chat cursor")
    messages, next_token, wait_ms = S.chat_page(data)
    next_cursor = (_REPLAY_PREFIX + next_token if replay else next_token) if next_token else None
    return page_result("messages", messages, next_cursor, video=_video_ref(video), is_replay=replay,
                       poll_after_seconds=round(wait_ms / 1000, 1) if wait_ms and not replay else None)
