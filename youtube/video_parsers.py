"""Video normalizers: the WEB player (videoDetails + microformat), the
ANDROID player (captions, storyboards, end screen, streams) and the watch
page `next` response (owner, description with links, chapters, heatmap,
music credits, info cards, comment count, ticket shelf) -> one details
object, plus streams / subtitles / storyboards for the media endpoints.

Deliberately dropped as noise: tracking params, the like/dislike/share
button models (only their counts are kept), `isOwnerViewing` /
`isUnpluggedCorpus` / `isCrawlable` (viewer-session or index flags), the
embed iframe size, ad break params, `serverAbrStreamingUrl` and the
per-format `xtags` / `approxDurationMs` duplicates (the duration is on the
video), attestation / heartbeat / playback tracking blocks, the
description's style/attachment runs (icons), and the QUIZ / ANIMATION
marker lists (UI hints without content).
"""
import re
from urllib.parse import parse_qs, urlparse

from youtube import refs
from youtube import values as V
from youtube.items import handle_of, owner_flags


# ---- small pieces -----------------------------------------------------------------------------

def _watch_results(next_data):
    return V.find_key(next_data, "contents", "twoColumnWatchNextResults", "results", "results", "contents") or []


def watch_renderer(next_data, name):
    for block in _watch_results(next_data):
        if isinstance(block, dict) and isinstance(block.get(name), dict):
            return block[name]
    return {}


def _panel(next_data, identifier):
    for panel in next_data.get("engagementPanels") or []:
        renderer = panel.get("engagementPanelSectionListRenderer") if isinstance(panel, dict) else None
        if not isinstance(renderer, dict):
            continue
        if renderer.get("panelIdentifier") == identifier or V.find_key(renderer, "identifier", "tag") == identifier:
            return renderer
    return {}


def _mutations(data):
    return V.find_key(data, "frameworkUpdates", "entityBatchUpdate", "mutations") or []


def description_links(attributed):
    """attributedDescription.commandRuns -> [{text, type, link, start_seconds?}]:
    urls (redirects unwrapped), timestamps, hashtags, channels, videos."""
    if not isinstance(attributed, dict):
        return []
    content = attributed.get("content") or ""
    out = []
    for run in attributed.get("commandRuns") or []:
        if not isinstance(run, dict):
            continue
        start, length = V.to_int(run.get("startIndex")), V.to_int(run.get("length"))
        label = V.clean(content[start:start + length]) if start is not None and length else None
        command = V.find_key(run, "onTap", "innertubeCommand") or {}
        watch = command.get("watchEndpoint") if isinstance(command, dict) else None
        browse = command.get("browseEndpoint") if isinstance(command, dict) else None
        entry = {"text": label, "type": "url", "link": None}
        if isinstance(watch, dict) and watch.get("videoId"):
            seconds = V.to_int(watch.get("startTimeSeconds"))
            if seconds is None and label and re.fullmatch(r"0+(?::00){1,2}", label):
                seconds = 0                     # the "0:00" timestamp carries no startTimeSeconds
            entry["link"] = refs.video_link(watch["videoId"]) + (f"&t={seconds}s" if seconds else "")
            if seconds is not None and label and re.fullmatch(r"\d+(?::\d{2}){1,2}", label):
                entry["type"] = "timestamp"
                entry["start_seconds"] = seconds
            else:
                entry["type"] = "video"
        elif isinstance(browse, dict) and browse.get("browseId"):
            bid = browse["browseId"]
            if bid == "FEhashtag":
                entry["type"] = "hashtag"
                entry["link"] = refs.hashtag_link((label or "").lstrip("#").lower()) if label else None
            elif bid.startswith("UC"):
                entry["type"] = "channel"
                entry["link"] = refs.channel_link(bid, handle_of(browse.get("canonicalBaseUrl")))
            elif bid.startswith("VL"):
                entry["type"] = "playlist"
                entry["link"] = refs.playlist_link(bid[2:])
            else:
                entry["link"] = V.nav_link(command)
        else:
            entry["link"] = V.nav_link(command)
        if entry["link"] or entry["text"]:
            out.append(entry)
    return out


def chapters(next_data, duration=None):
    """DESCRIPTION_CHAPTERS (or AUTO_CHAPTERS) markers -> [{title, start_seconds,
    end_seconds, thumbnail}] with each chapter ending where the next begins."""
    markers = V.first(next_data, "markersMap") or []
    chosen = None
    for key in ("DESCRIPTION_CHAPTERS", "AUTO_CHAPTERS"):
        for entry in markers:
            if isinstance(entry, dict) and entry.get("key") == key:
                chosen = V.find_key(entry, "value", "chapters") or []
                break
        if chosen:
            break
    out = []
    for item in chosen or []:
        renderer = item.get("chapterRenderer") if isinstance(item, dict) else None
        if not isinstance(renderer, dict):
            continue
        start = V.to_int(renderer.get("timeRangeStartMillis"))
        out.append({"title": V.text(renderer.get("title")),
                    "start_seconds": start // 1000 if start is not None else None,
                    "end_seconds": None,
                    "thumbnail": V.best_thumbnail(renderer.get("thumbnail"))})
    for index, chapter in enumerate(out):
        nxt = out[index + 1]["start_seconds"] if index + 1 < len(out) else duration
        chapter["end_seconds"] = nxt
    return out


def heatmap(next_data):
    """'Most replayed' graph: [{start_seconds, duration_seconds, intensity}]
    (intensity 0-1, normalised by YouTube; ~100 points)."""
    for mutation in _mutations(next_data):
        entity = V.find_key(mutation, "payload", "macroMarkersListEntity")
        if not isinstance(entity, dict):
            continue
        markers = entity.get("markersList") or {}
        if markers.get("markerType") != "MARKER_TYPE_HEATMAP":
            continue
        out = []
        for marker in markers.get("markers") or []:
            start, length = V.to_int(marker.get("startMillis")), V.to_int(marker.get("durationMillis"))
            out.append({
                "start_seconds": round(start / 1000, 1) if start is not None else None,
                "duration_seconds": round(length / 1000, 1) if length is not None else None,
                "intensity": round(V.to_float(marker.get("intensityScoreNormalized")) or 0, 4),
            })
        return out
    return []


def music(next_data):
    """The description's 'Music' section -> [{title, artist, album, thumbnail}]."""
    panel = _panel(next_data, "engagement-panel-structured-description")
    out = []
    for card in V.walk(panel, "videoAttributeViewModel"):
        if not isinstance(card, dict):
            continue
        out.append({
            "title": V.clean(card.get("title")),
            "artist": V.clean(card.get("subtitle")),
            "album": V.text(card.get("secondarySubtitle")),
            "thumbnail": V.best_thumbnail(card.get("image")),
        })
    return out


def info_cards(next_data):
    """Linked videos / playlists / links from the description's info-card section."""
    panel = _panel(next_data, "engagement-panel-structured-description")
    out = []
    for card in V.walk(panel, "compactInfocardRenderer"):
        content = card.get("content") if isinstance(card, dict) else None
        if not isinstance(content, dict):
            continue
        for kind, renderer in content.items():
            if not isinstance(renderer, dict):
                continue
            endpoint = renderer.get("navigationEndpoint") or renderer.get("onTap") or renderer
            watch = V.first(endpoint, "watchEndpoint")
            entry = {
                "title": V.text(renderer.get("title")),
                "subtitle": V.text(renderer.get("subtitle")) or V.text(renderer.get("shortBylineText")),
                "link": refs.video_link(watch["videoId"]) if isinstance(watch, dict) and watch.get("videoId")
                else V.nav_link(endpoint),
                "thumbnail": V.best_thumbnail(renderer.get("thumbnail") or renderer.get("thumbnailDetails")),
                "duration_seconds": V.duration_seconds(V.text(renderer.get("lengthText"))),
            }
            details = V.text(renderer.get("metadataDetails"))
            if details:
                entry["details"] = details
            out.append(entry)
    return out


def events(next_data):
    """The ticket shelf (upcoming concerts near the requester's IP) above
    the description -> {title, events[{title, date_text, time_text, weekday,
    venue, has_multiple_offers, seller, link}]}."""
    shelf = watch_renderer(next_data, "ticketShelfRenderer")
    out = []
    for event in shelf.get("events") or []:
        renderer = event.get("ticketEventRenderer") if isinstance(event, dict) else None
        if not isinstance(renderer, dict):
            continue
        date_text = V.clean(renderer.get("timeDate"))
        venue = None
        for key in ("subtitle2", "subtitle1"):
            line = V.clean(renderer.get(key))
            if line and "·" in line and not (date_text and line.startswith(date_text)):
                venue = line
                break
        if venue is None:
            line = V.clean(renderer.get("subtitle1")) or ""
            venue = line.split("·", 1)[1].strip() if "·" in line else None
        multiple = bool(renderer.get("hasMultipleOffers"))
        out.append({
            "title": V.clean(renderer.get("title")),
            "date_text": date_text,
            "time_text": V.clean(renderer.get("timeTime")),
            "weekday": V.clean(renderer.get("timeWeekday")),
            "venue": venue,
            "has_multiple_offers": multiple,
            "seller": None if multiple else V.clean(renderer.get("linkText")),
            "link": V.nav_link(renderer.get("buttonCommand")),
        })
    return {"title": V.clean(shelf.get("title")), "events": out} if out else None


def comment_count(next_data):
    """The comments panel header count ('4.3M' -> 4300000; null when comments are off)."""
    panel = _panel(next_data, "engagement-panel-comments-section")
    label = V.text(V.find_key(panel, "header", "engagementPanelTitleHeaderRenderer", "contextualInfo"))
    return V.count(label)


def comments_enabled(next_data):
    return bool(_panel(next_data, "engagement-panel-comments-section")) or any(
        isinstance(b, dict) and V.find_key(b, "itemSectionRenderer", "sectionIdentifier") == "comment-item-section"
        for b in _watch_results(next_data))


def like_count(next_data):
    """Exact likes from the like button's a11y text ('along with 19,423,853 other people')."""
    for label in V.walk(next_data, "accessibilityText"):
        if isinstance(label, str) and "like this video along with" in label:
            return V.count(label.split("along with", 1)[1])
    return None


def owner(next_data):
    secondary = watch_renderer(next_data, "videoSecondaryInfoRenderer")
    renderer = V.find_key(secondary, "owner", "videoOwnerRenderer") or {}
    if not renderer:
        return None
    is_verified, is_artist = owner_flags(renderer.get("badges"))
    path = V.canonical_path(renderer.get("navigationEndpoint") or renderer.get("title"))
    cid = V.browse_id(renderer.get("navigationEndpoint") or renderer.get("title"))
    handle = handle_of(path)
    return {
        "id": cid,
        "name": V.text(renderer.get("title")),
        "handle": handle,
        "link": refs.channel_link(cid, handle),
        "thumbnail": V.best_thumbnail(renderer.get("thumbnail")),
        "subscriber_count": V.count(V.text(renderer.get("subscriberCountText"))),
        "is_verified": is_verified,
        "is_artist": is_artist,
    }


def _super_title(next_data):
    """The link row above the title: hashtags, or a series / playlist link."""
    primary = watch_renderer(next_data, "videoPrimaryInfoRenderer")
    runs = V.find_key(primary, "superTitleLink", "runs") or []
    hashtags, series = [], None
    for run in runs:
        label = V.clean(run.get("text")) if isinstance(run, dict) else None
        if not label:
            continue
        bid = V.browse_id(run)
        if label.startswith("#"):
            hashtags.append(label.lstrip("#"))
        elif bid and bid.startswith("VL"):
            series = {"id": bid[2:], "title": label, "link": refs.playlist_link(bid[2:])}
    return hashtags, series


def _country_list(codes):
    return sorted(c for c in codes if isinstance(c, str)) if isinstance(codes, list) else []


def caption_tracks(android_player):
    renderer = V.find_key(android_player, "captions", "playerCaptionsTracklistRenderer") or {}
    tracks = []
    for track in renderer.get("captionTracks") or []:
        if not isinstance(track, dict) or not track.get("baseUrl"):
            continue
        tracks.append({
            "language_code": V.clean(track.get("languageCode")),
            "language": V.text(track.get("name")),
            "name": V.clean(track.get("trackName")) or None,
            "is_auto_generated": track.get("kind") == "asr",
            "is_translatable": bool(track.get("isTranslatable")),
            "link": caption_link(track["baseUrl"], "vtt"),
        })
    translations = []
    for lang in renderer.get("translationLanguages") or []:
        if isinstance(lang, dict) and lang.get("languageCode"):
            translations.append({"language_code": lang["languageCode"], "language": V.text(lang.get("languageName"))})
    return tracks, translations


def caption_link(base_url, fmt):
    """The signed timedtext URL (signed with ip=0.0.0.0, so it works from
    any IP until it expires) with the output format swapped."""
    link = re.sub(r"([?&])fmt=[^&]*", r"\1", base_url).rstrip("&")
    return f"{link}&fmt={fmt}"


def base_caption_url(android_player, language_code=None):
    """(chosen track, any track) for a transcript. With `language_code`: that
    language, manual before auto-generated (chosen is None when the video has
    no such track). Without: the video's own language — the manual track in
    the auto-generated track's language, else that auto-generated track,
    else the first manual track."""
    renderer = V.find_key(android_player, "captions", "playerCaptionsTracklistRenderer") or {}
    tracks = [t for t in renderer.get("captionTracks") or [] if isinstance(t, dict) and t.get("baseUrl")]
    if not tracks:
        return None, None

    def lang(track):
        return str(track.get("languageCode") or "").lower()

    def same(a, b):
        return a == b or a.split("-")[0] == b.split("-")[0]

    manual = [t for t in tracks if t.get("kind") != "asr"]
    auto = [t for t in tracks if t.get("kind") == "asr"]
    if language_code:
        wanted = language_code.lower()
        for group in (manual, auto):
            exact = [t for t in group if lang(t) == wanted]
            loose = [t for t in group if same(lang(t), wanted)]
            if exact or loose:
                return (exact or loose)[0], tracks[0]
        return None, tracks[0]
    default_index = V.find_key(renderer, "audioTracks", 0, "defaultCaptionTrackIndex")
    if isinstance(default_index, int) and 0 <= default_index < len(renderer.get("captionTracks") or []):
        track = renderer["captionTracks"][default_index]
        if isinstance(track, dict) and track.get("baseUrl"):
            return track, tracks[0]
    if auto:
        spoken = lang(auto[0])
        match = [t for t in manual if same(lang(t), spoken)]
        return (match[0] if match else auto[0]), tracks[0]
    return manual[0], tracks[0]


def storyboards(android_player, duration=None):
    """The storyboard spec -> one entry per level: sprite sheets of frame
    thumbnails ({width, height, frame_count, columns, rows, interval_seconds, sheets[]})."""
    spec = V.find_key(android_player, "storyboards", "playerStoryboardSpecRenderer", "spec")
    if not isinstance(spec, str) or "|" not in spec:
        return []
    base, *levels = spec.split("|")
    out = []
    for index, level in enumerate(levels):
        parts = level.split("#")
        if len(parts) < 8:
            continue
        width, height, count, columns, rows, interval = (V.to_int(p) for p in parts[:6])
        name, sigh = parts[6], parts[7]
        if not all((width, height, count, columns, rows)):
            continue
        per_sheet = columns * rows
        sheets = []
        for sheet in range((count + per_sheet - 1) // per_sheet):
            link = base.replace("$L", str(index)).replace("$N", name.replace("$M", str(sheet)))
            sheets.append(link + ("&" if "?" in link else "?") + "sigh=" + sigh)
        out.append({
            "width": width, "height": height, "frame_count": count, "columns": columns, "rows": rows,
            "interval_seconds": round(interval / 1000, 3) if interval else
            (round(duration / count, 3) if duration and count else None),
            "sheets": sheets,
        })
    return out


def end_screen(android_player):
    """End-screen elements: videos, playlists, channels, links with the time window."""
    out = []
    elements = V.find_key(android_player, "endscreen", "endscreenRenderer", "elements") or []
    for element in elements:
        renderer = element.get("endscreenElementRenderer") if isinstance(element, dict) else None
        if not isinstance(renderer, dict):
            continue
        style = str(renderer.get("style") or "").lower()
        endpoint = renderer.get("endpoint") or {}
        watch = endpoint.get("watchEndpoint") if isinstance(endpoint, dict) else None
        browse = endpoint.get("browseEndpoint") if isinstance(endpoint, dict) else None
        entry = {"type": style or None, "id": None, "title": V.text(renderer.get("title")), "link": None,
                 "thumbnail": V.best_thumbnail(renderer.get("image")),
                 "details": V.text(renderer.get("metadata")),
                 "start_seconds": round(V.to_int(renderer.get("startMs")) / 1000, 1) if V.to_int(renderer.get("startMs")) is not None else None,
                 "end_seconds": round(V.to_int(renderer.get("endMs")) / 1000, 1) if V.to_int(renderer.get("endMs")) is not None else None}
        if style == "playlist" and isinstance(watch, dict):
            entry["id"] = watch.get("playlistId")
            entry["link"] = refs.playlist_link(watch.get("playlistId"))
            entry["video_count"] = V.count(V.text(renderer.get("playlistLength")))
        elif isinstance(watch, dict) and watch.get("videoId"):
            entry["id"] = watch["videoId"]
            entry["link"] = refs.video_link(watch["videoId"])
        elif isinstance(browse, dict) and browse.get("browseId"):
            entry["id"] = browse["browseId"]
            entry["link"] = refs.channel_link(browse["browseId"])
        else:
            entry["link"] = V.nav_link(endpoint)
        out.append(entry)
    return out


# ---- details ---------------------------------------------------------------------------------------

def playability(player):
    status = (player or {}).get("playabilityStatus") or {}
    reason = V.clean(status.get("reason")) or V.text(V.find_key(status, "errorScreen", "playerErrorMessageRenderer", "reason"))
    return str(status.get("status") or "") or None, reason


def is_unavailable(web_player, next_data):
    """True when the video does not exist / was removed / is private."""
    details = (web_player or {}).get("videoDetails")
    status, _ = playability(web_player)
    if isinstance(details, dict) and details.get("videoId"):
        return False
    return status in ("ERROR", "LOGIN_REQUIRED", "UNPLAYABLE", None) and not watch_renderer(next_data or {}, "videoPrimaryInfoRenderer")


def video_details(web_player, android_player, next_data):
    """WEB player + ANDROID player + watch `next` -> the full video object."""
    web_player, android_player, next_data = web_player or {}, android_player or {}, next_data or {}
    details = web_player.get("videoDetails") or android_player.get("videoDetails") or {}
    micro = V.find_key(web_player, "microformat", "playerMicroformatRenderer") or {}
    primary = watch_renderer(next_data, "videoPrimaryInfoRenderer")
    secondary = watch_renderer(next_data, "videoSecondaryInfoRenderer")
    vid = details.get("videoId") or micro.get("externalVideoId") or V.find_key(next_data, "currentVideoEndpoint", "watchEndpoint", "videoId")
    duration = V.to_int(details.get("lengthSeconds")) or V.to_int(micro.get("lengthSeconds"))
    live = micro.get("liveBroadcastDetails") or {}
    view_renderer = V.find_key(primary, "viewCount", "videoViewCountRenderer") or {}
    is_live_now = bool(live.get("isLiveNow")) or bool(view_renderer.get("isLive")) or bool(details.get("isLive"))
    android_status_raw = ((android_player.get("playabilityStatus") or {}).get("status"))
    is_upcoming = bool(details.get("isUpcoming")) or android_status_raw == "LIVE_STREAM_OFFLINE"
    status, reason = playability(web_player)
    android_status, android_reason = playability(android_player)
    is_age_restricted = any("confirm your age" in (r or "").lower() or "inappropriate" in (r or "").lower()
                            for r in (reason, android_reason))
    hashtags, series = _super_title(next_data)
    attributed = secondary.get("attributedDescription") or V.find_key(
        _panel(next_data, "engagement-panel-structured-description"), "content", "structuredDescriptionContentRenderer",
        "items", 1, "expandableVideoDescriptionBodyRenderer", "attributedDescriptionBodyText")
    description = V.text(attributed) or V.clean(details.get("shortDescription")) or V.text(micro.get("description"))
    tracks, translations = caption_tracks(android_player)
    channel = owner(next_data) or {
        "id": details.get("channelId") or micro.get("externalChannelId"),
        "name": V.clean(details.get("author")) or V.clean(micro.get("ownerChannelName")),
        "handle": handle_of(urlparse(micro.get("ownerProfileUrl") or "").path or None),
        "link": None, "thumbnail": None, "subscriber_count": None, "is_verified": None, "is_artist": None,
    }
    if channel.get("link") is None:
        channel["link"] = refs.channel_link(channel.get("id"), channel.get("handle"))
    views = V.to_int(details.get("viewCount")) or V.to_int(micro.get("viewCount"))
    watching = V.to_int(view_renderer.get("originalViewCount")) if is_live_now else None
    is_short = bool(micro.get("isShortsEligible"))
    thumbs = V.thumbnails(details.get("thumbnail")) or V.thumbnails(micro.get("thumbnail"))
    maxres = V.best_thumbnail(micro.get("thumbnail"))
    if maxres and all(t["link"] != maxres for t in thumbs):
        thumbs.append({"link": maxres, "width": 1280, "height": 720})
    return {
        "id": vid,
        "title": V.clean(details.get("title")) or V.text(micro.get("title")) or V.text(primary.get("title")),
        "link": refs.short_link(vid) if is_short else refs.video_link(vid),
        "thumbnail": thumbs[-1]["link"] if thumbs else None,
        "description": description,
        "duration_seconds": duration if duration else None,
        "category": V.clean(micro.get("category")),
        "keywords": [k for k in details.get("keywords") or [] if isinstance(k, str)],
        "hashtags": hashtags,
        "published_at": V.iso_datetime(micro.get("publishDate")),
        "uploaded_at": V.iso_datetime(micro.get("uploadDate")),
        "published_date": V.iso_date(micro.get("publishDate")) or V.written_date(V.text(primary.get("dateText"))),
        "view_count": views if not is_live_now else (views or None),
        "like_count": V.to_int(micro.get("likeCount")) if micro.get("likeCount") is not None else like_count(next_data),
        "comment_count": comment_count(next_data),
        "watching_count": watching,
        "is_short": is_short,
        "is_live": is_live_now,
        "is_live_content": bool(details.get("isLiveContent")),
        "is_upcoming": is_upcoming,
        "is_family_safe": V.bool_or_none(micro.get("isFamilySafe")),
        "is_unlisted": V.bool_or_none(micro.get("isUnlisted")),
        "is_private": V.bool_or_none(details.get("isPrivate")),
        "is_paid": V.bool_or_none(micro.get("hasYpcMetadata")),
        "is_age_restricted": is_age_restricted,
        "is_embeddable": V.bool_or_none(((android_player.get("playabilityStatus") or {}).get("playableInEmbed"))),
        "are_ratings_allowed": V.bool_or_none(details.get("allowRatings")),
        "are_comments_enabled": comments_enabled(next_data),
        "has_captions": bool(tracks),
        "live_broadcast": {
            "is_live_now": bool(live.get("isLiveNow")),
            "started_at": V.iso_datetime(live.get("startTimestamp")),
            "ended_at": V.iso_datetime(live.get("endTimestamp")),
        } if live else None,
        # the WEB player is always UNPLAYABLE without a PoToken; the ANDROID answer is the real one
        "playability": {"status": android_status or status,
                        "reason": android_reason if android_status else (reason if status != "UNPLAYABLE" else None)},
        "thumbnails": thumbs,
        "channel": channel,
        "series": series,
        "chapters": chapters(next_data, duration),
        "heatmap": heatmap(next_data),
        "music": music(next_data),
        "description_links": description_links(attributed),
        "info_cards": info_cards(next_data),
        "end_screen": end_screen(android_player),
        "subtitles": [{k: t[k] for k in ("language_code", "language", "name", "is_auto_generated")} for t in tracks],
        "concerts": events(next_data),
        "available_countries": _country_list(micro.get("availableCountries")),
    }


def video_summary(web_player):
    """A compact video object from the WEB player alone (batch lookups)."""
    details = (web_player or {}).get("videoDetails") or {}
    micro = V.find_key(web_player, "microformat", "playerMicroformatRenderer") or {}
    vid = details.get("videoId") or micro.get("externalVideoId")
    if not vid:
        return None
    live = micro.get("liveBroadcastDetails") or {}
    is_short = bool(micro.get("isShortsEligible"))
    handle = handle_of(urlparse(micro.get("ownerProfileUrl") or "").path or None)
    cid = details.get("channelId") or micro.get("externalChannelId")
    return {
        "id": vid,
        "title": V.clean(details.get("title")) or V.text(micro.get("title")),
        "link": refs.short_link(vid) if is_short else refs.video_link(vid),
        "thumbnail": V.best_thumbnail(micro.get("thumbnail")) or V.best_thumbnail(details.get("thumbnail")),
        "description": V.clean(details.get("shortDescription")) or V.text(micro.get("description")),
        "duration_seconds": V.to_int(details.get("lengthSeconds")) or None,
        "category": V.clean(micro.get("category")),
        "keywords": [k for k in details.get("keywords") or [] if isinstance(k, str)],
        "published_at": V.iso_datetime(micro.get("publishDate")),
        "view_count": V.to_int(details.get("viewCount")),
        "like_count": V.to_int(micro.get("likeCount")),
        "is_short": is_short,
        "is_live": bool(live.get("isLiveNow")),
        "is_live_content": bool(details.get("isLiveContent")),
        "is_family_safe": V.bool_or_none(micro.get("isFamilySafe")),
        "is_unlisted": V.bool_or_none(micro.get("isUnlisted")),
        "channel": {"id": cid, "name": V.clean(details.get("author")) or V.clean(micro.get("ownerChannelName")),
                    "handle": handle, "link": refs.channel_link(cid, handle)},
    }


# ---- streams --------------------------------------------------------------------------------------

_CODECS_RE = re.compile(r'codecs="([^"]+)"')


def _format(fmt, kind):
    mime = str(fmt.get("mimeType") or "")
    container = mime.split(";")[0].split("/")[-1] if "/" in mime else None
    codecs = _CODECS_RE.search(mime)
    audio_track = fmt.get("audioTrack") or {}
    out = {
        "itag": V.to_int(fmt.get("itag")),
        "type": kind,
        "link": fmt.get("url"),
        "mime_type": mime.split(";")[0] or None,
        "container": container,
        "codecs": [c.strip() for c in codecs.group(1).split(",")] if codecs else [],
        "quality": V.clean(fmt.get("qualityLabel")) or V.clean(fmt.get("quality")),
        "width": V.to_int(fmt.get("width")),
        "height": V.to_int(fmt.get("height")),
        "fps": V.to_int(fmt.get("fps")),
        "bitrate": V.to_int(fmt.get("bitrate")),
        "average_bitrate": V.to_int(fmt.get("averageBitrate")),
        "size_bytes": V.to_int(fmt.get("contentLength")),
        "audio_quality": str(fmt.get("audioQuality") or "").replace("AUDIO_QUALITY_", "").lower() or None,
        "audio_sample_rate": V.to_int(fmt.get("audioSampleRate")),
        "audio_channels": V.to_int(fmt.get("audioChannels")),
        "audio_language": V.clean(audio_track.get("id", "").split(".")[0]) if audio_track else None,
        "is_default_audio": V.bool_or_none(audio_track.get("audioIsDefault")) if audio_track else None,
        "is_hdr": "hdr" in str(fmt.get("qualityLabel") or "").lower()
        or (fmt.get("colorInfo") or {}).get("transferCharacteristics") == "COLOR_TRANSFER_CHARACTERISTICS_SMPTEST2084",
        "is_drc": bool(fmt.get("isDrc")) or None,
    }
    if kind == "audio":
        out.update(width=None, height=None, fps=None, is_hdr=None)
    elif kind == "video":
        out.update(audio_quality=None, audio_sample_rate=None, audio_channels=None,
                   audio_language=None, is_default_audio=None)
    return out


def streams(android_player):
    """ANDROID player streamingData -> muxed (video+audio), video-only and
    audio-only formats with direct links, plus HLS / DASH manifests."""
    data = (android_player or {}).get("streamingData") or {}
    muxed = [_format(f, "muxed") for f in data.get("formats") or [] if isinstance(f, dict) and f.get("url")]
    video_only, audio_only = [], []
    for fmt in data.get("adaptiveFormats") or []:
        if not isinstance(fmt, dict) or not fmt.get("url"):
            continue
        if str(fmt.get("mimeType") or "").startswith("audio/"):
            audio_only.append(_format(fmt, "audio"))
        else:
            video_only.append(_format(fmt, "video"))
    video_only.sort(key=lambda f: ((f["height"] or 0), (f["bitrate"] or 0)), reverse=True)
    audio_only.sort(key=lambda f: (f["bitrate"] or 0), reverse=True)
    expires = V.to_int(data.get("expiresInSeconds"))
    link = (muxed or video_only or audio_only or [{}])[0].get("link")
    expire_at = None
    if link:
        epoch = V.to_int((parse_qs(urlparse(link).query).get("expire") or [None])[0])
        expire_at = V.iso_from_usec(epoch * 1_000_000) if epoch else None
    return {
        "expires_in_seconds": expires,
        "expires_at": expire_at,
        "hls_manifest_link": data.get("hlsManifestUrl"),
        "dash_manifest_link": data.get("dashManifestUrl"),
        "muxed": muxed,
        "video_only": video_only,
        "audio_only": audio_only,
    }


# ---- transcripts ----------------------------------------------------------------------------------

def json3_segments(payload):
    """A timedtext json3 body -> [{start_seconds, end_seconds, duration_seconds, text}]."""
    out = []
    for event in (payload or {}).get("events") or []:
        if not isinstance(event, dict) or "segs" not in event:
            continue
        line = "".join(str(seg.get("utf8") or "") for seg in event.get("segs") or [] if isinstance(seg, dict))
        line = re.sub(r"\s+", " ", line).strip()
        if not line:
            continue
        start = V.to_int(event.get("tStartMs")) or 0
        length = V.to_int(event.get("dDurationMs")) or 0
        out.append({"start_seconds": round(start / 1000, 3), "end_seconds": round((start + length) / 1000, 3),
                    "duration_seconds": round(length / 1000, 3), "text": line})
    return out


def _stamp(seconds, sep):
    ms = int(round(seconds * 1000))
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def to_srt(segments):
    return "\n".join(f"{i}\n{_stamp(s['start_seconds'], ',')} --> {_stamp(s['end_seconds'], ',')}\n{s['text']}\n"
                     for i, s in enumerate(segments, 1))


def to_vtt(segments):
    body = "\n".join(f"{_stamp(s['start_seconds'], '.')} --> {_stamp(s['end_seconds'], '.')}\n{s['text']}\n" for s in segments)
    return "WEBVTT\n\n" + body


def to_text(segments):
    return " ".join(s["text"] for s in segments)
