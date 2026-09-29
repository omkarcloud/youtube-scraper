"""Page-level normalizers: the channel header + About panel + metadata, a
channel's tab list, playlist / hashtag / shorts-sound headers, YouTube
Charts rows and search suggestions.

Dropped as noise: app-index / deep links (android-app://, ios-app://,
vnd.youtube://), the pageview-conversion pixel URL, colour palettes,
subscribe / join / share button models, "…more" truncation strings,
image processors and avatar sizes, the About panel's section labels
("Description", "Links", "More info").
"""
import re
import shlex

from youtube import refs
from youtube import values as V
from youtube.items import handle_of


# ---- channel -----------------------------------------------------------------------------------

def tabs(browse):
    """[(key, title, is_selected, tab_renderer)] for a channel browse response."""
    out = []
    for tab in V.find_key(browse, "contents", "twoColumnBrowseResultsRenderer", "tabs") or []:
        renderer = (tab.get("tabRenderer") or tab.get("expandableTabRenderer")) if isinstance(tab, dict) else None
        if not isinstance(renderer, dict):
            continue
        title = V.clean(renderer.get("title"))
        if not title and "expandableTabRenderer" in tab:
            title = "Search"
        if title:
            out.append((title.lower().replace(" ", "_"), title, bool(renderer.get("selected")), renderer))
    return out


def selected_tab(browse):
    for key, title, selected, renderer in tabs(browse):
        if selected:
            return key, renderer
    return None, None


def _image_names(obj):
    """clientResource image names inside a text (verified / artist badges)."""
    for sources in V.walk(obj, "sources"):
        for source in sources if isinstance(sources, list) else []:
            name = V.find_key(source, "clientResource", "imageName")
            if isinstance(name, str):
                yield name


def _header_rows(header):
    rows = V.find_key(header, "metadata", "contentMetadataViewModel", "metadataRows") or []
    parts = []
    for row in rows:
        for part in (row.get("metadataParts") or []) if isinstance(row, dict) else []:
            label = V.text(part.get("text")) if isinstance(part, dict) else None
            if label:
                parts.append((label, V.clean(part.get("accessibilityLabel")), part))
    return parts


def channel_header(browse):
    """pageHeaderViewModel -> the header facts (counts are display values)."""
    header = V.find_key(browse, "header", "pageHeaderRenderer", "content", "pageHeaderViewModel") or {}
    title_runs = V.find_key(header, "title", "dynamicTextViewModel", "text") or {}
    icons = set(_image_names(title_runs))
    is_artist = any("AUDIO_BADGE" in name for name in icons)
    is_verified = bool(icons & {"CHECK_CIRCLE_FILLED", "CHECK_CIRCLE_THICK"})
    out = {"name": V.text(title_runs), "handle": None, "subscriber_count": None, "video_count": None,
           "is_verified": is_verified or is_artist, "is_artist": is_artist,
           "avatar": V.best_thumbnail(V.find_key(header, "image", "decoratedAvatarViewModel", "avatar", "avatarViewModel", "image")),
           "banner": V.best_thumbnail(V.find_key(header, "banner", "imageBannerViewModel", "image")),
           "tagline": V.text(V.find_key(header, "description", "descriptionPreviewViewModel", "description"))}
    for label, a11y, _ in _header_rows(header):
        if label.startswith("@") and not out["handle"]:
            out["handle"] = label
        elif re.search(r"subscriber", label, re.I):
            out["subscriber_count"] = V.count(a11y or label)
        elif re.search(r"\bvideos?\b", label, re.I):
            out["video_count"] = V.count(label)
    return out


def _about_link(model):
    title = V.text(model.get("title"))
    shown = V.text(model.get("link"))
    target = V.nav_link(model.get("link")) if isinstance(model.get("link"), dict) else None
    if not target and shown:
        target = shown if shown.startswith("http") else "https://" + shown
    return {"title": title, "link": target, "display_link": shown}


def about(about_data):
    """The About panel (a browse continuation) -> exact counts, dates, links."""
    model = V.first(about_data, "aboutChannelViewModel") or {}
    if not model:
        return {}
    joined = V.text(model.get("joinedDateText"))
    links = [_about_link(link) for link in V.walk(model.get("links"), "channelExternalLinkViewModel")
             if isinstance(link, dict)]
    return {
        "description": V.clean(model.get("description")),
        "country": V.clean(model.get("country")),
        "joined_date": V.written_date(joined),
        "view_count": V.count(model.get("viewCountText")),
        "video_count": V.count(model.get("videoCountText")),
        "subscriber_count": V.count(model.get("subscriberCountText")),
        "has_business_email": bool(model.get("signInForBusinessEmail") or model.get("businessEmailButton")),
        "links": links,
        "canonical_link": refs.absolute(model.get("canonicalChannelUrl")),
    }


def about_token(browse):
    """The header 'more' continuation that loads the About panel."""
    header = V.find_key(browse, "header", "pageHeaderRenderer") or {}
    for command in V.walk(header, "continuationCommand"):
        if isinstance(command, dict) and command.get("token"):
            return command["token"]
    return None


def _keywords(value):
    """'"lofi hip hop" beats study' -> ['lofi hip hop', 'beats', 'study']."""
    if not isinstance(value, str) or not value.strip():
        return []
    try:
        return [k for k in shlex.split(value) if k]
    except ValueError:
        return [k for k in value.split() if k]


def channel_details(browse, about_data=None):
    """Header + About + metadata -> one channel object."""
    meta = V.find_key(browse, "metadata", "channelMetadataRenderer") or {}
    header = channel_header(browse)
    info = about(about_data) if about_data else {}
    cid = meta.get("externalId") or (V.first(about_data, "channelId") if about_data else None)
    handle = header["handle"] or handle_of(re.sub(r"^https?://www\.youtube\.com", "", meta.get("vanityChannelUrl") or ""))
    tab_list = [{"key": key, "title": title} for key, title, _, _ in tabs(browse) if key != "search"]
    return {
        "id": cid,
        "name": V.clean(meta.get("title")) or header["name"],
        "handle": handle,
        "link": refs.channel_link(cid, handle),
        "description": info.get("description") or V.clean(meta.get("description")),
        "tagline": header["tagline"],
        "country": info.get("country"),
        "joined_date": info.get("joined_date"),
        "subscriber_count": info.get("subscriber_count") or header["subscriber_count"],
        "video_count": info.get("video_count") or header["video_count"],
        "view_count": info.get("view_count"),
        "is_verified": header["is_verified"],
        "is_artist": header["is_artist"],
        "is_family_safe": V.bool_or_none(meta.get("isFamilySafe")),
        "has_business_email": info.get("has_business_email"),
        "keywords": _keywords(meta.get("keywords")),
        "avatar": header["avatar"] or V.best_thumbnail(meta.get("avatar")),
        "banner": header["banner"],
        "links": info.get("links") or [],
        "tabs": tab_list,
        "rss_link": meta.get("rssUrl"),
        "available_countries": sorted(meta.get("availableCountryCodes") or []),
    }


# ---- playlists ----------------------------------------------------------------------------------

def playlist_details(browse, playlist_id):
    """The playlist page header / sidebar -> the playlist object."""
    header = V.find_key(browse, "header", "pageHeaderRenderer", "content", "pageHeaderViewModel") or {}
    sidebar = V.first(browse, "playlistSidebarPrimaryInfoRenderer") or {}
    owner_block = V.first(browse, "playlistSidebarSecondaryInfoRenderer") or {}
    micro = V.find_key(browse, "microformat", "microformatDataRenderer") or {}
    title = V.text(V.find_key(header, "title", "dynamicTextViewModel", "text")) or V.text(sidebar.get("title")) \
        or V.clean(micro.get("title"))
    video_count = view_count = updated = None
    for stat in sidebar.get("stats") or []:
        label = V.text(stat) or ""
        if re.search(r"\bvideos?\b|\bepisodes?\b", label, re.I):
            video_count = V.count(label)
        elif re.search(r"\bviews?\b", label, re.I):
            view_count = V.count(label)
        elif "updated" in label.lower():
            updated = V.written_date(label) or V.relative_date(label)
    channel = None
    owner = V.find_key(owner_block, "videoOwner", "videoOwnerRenderer")
    stack = V.first(header, "avatarStackViewModel") or {}
    if isinstance(owner, dict):
        cid = V.browse_id(owner.get("title") or owner.get("navigationEndpoint"))
        path = V.canonical_path(owner.get("title") or owner.get("navigationEndpoint"))
        channel = {"id": cid, "name": V.text(owner.get("title")), "handle": handle_of(path),
                   "link": refs.channel_link(cid, handle_of(path)), "thumbnail": V.best_thumbnail(owner.get("thumbnail"))}
    elif stack:
        name = V.text(stack.get("text")) or ""
        cid = V.browse_id(stack)
        path = V.canonical_path(stack)
        channel = {"id": cid, "name": name[3:] if name.lower().startswith("by ") else name, "handle": handle_of(path),
                   "link": refs.channel_link(cid, handle_of(path)),
                   "thumbnail": V.best_thumbnail(V.find_key(stack, "avatars", 0, "avatarViewModel", "image"))}
    privacy = None
    for label, _, _ in _header_rows(header):
        if label.lower() in ("public", "unlisted", "private"):
            privacy = label.lower()
        elif video_count is None and re.search(r"\bvideos?\b", label, re.I):
            video_count = V.count(label)
        elif view_count is None and re.search(r"\bviews?\b", label, re.I):
            view_count = V.count(label)
        elif updated is None and "updated" in label.lower():
            updated = V.written_date(label) or V.relative_date(label)
    description = V.text(V.find_key(header, "description", "descriptionPreviewViewModel", "description")) \
        or V.text(sidebar.get("description")) or V.clean(micro.get("description"))
    return {
        "id": playlist_id,
        "title": title,
        "link": refs.playlist_link(playlist_id),
        "description": description,
        "thumbnail": V.best_thumbnail(micro.get("thumbnail")) or V.best_thumbnail(
            V.find_key(header, "heroImage", "contentPreviewImageViewModel", "image")),
        "kind": "mix" if playlist_id.startswith("RD") else ("album" if playlist_id.startswith("OLAK5uy_") else
                                                           ("uploads" if playlist_id.startswith("UU") else "playlist")),
        "video_count": video_count,
        "view_count": view_count,
        "updated_date": updated,
        "privacy": privacy or "public",
        "channel": channel,
    }


# ---- hashtags / sounds ---------------------------------------------------------------------------

def hashtag_header(browse, tag):
    header = V.find_key(browse, "header", "pageHeaderRenderer", "content", "pageHeaderViewModel") or {}
    video_count = channel_count = None
    for label, _, _ in _header_rows(header):
        for piece in label.split("•"):
            if re.search(r"\bvideos?\b", piece, re.I):
                video_count = V.count(piece)
            elif re.search(r"\bchannels?\b", piece, re.I):
                channel_count = V.count(piece)
    return {"name": tag, "link": refs.hashtag_link(tag), "video_count": video_count, "channel_count": channel_count}


def sound_header(browse, video_id):
    header = V.find_key(browse, "header", "pageHeaderRenderer", "content", "pageHeaderViewModel") or {}
    stack = V.first(header, "avatarStackViewModel") or {}
    creator = V.text(stack.get("text"))
    shorts = None
    for label, _, _ in _header_rows(header):
        if re.search(r"\bshorts?\b", label, re.I):
            shorts = V.count(label)
    return {
        "title": V.text(V.find_key(header, "title", "dynamicTextViewModel", "text")),
        "creator": creator,
        "creator_link": refs.channel_link(None, creator) if creator and creator.startswith("@") else None,
        "thumbnail": V.best_thumbnail(V.find_key(header, "image", "contentPreviewImageViewModel", "image")),
        "shorts_count": shorts,
        "source_video_id": video_id,
    }


# ---- charts ---------------------------------------------------------------------------------------

def _entry_stats(entry):
    meta = entry.get("chartEntryMetadata") or {}
    change = V.to_float(meta.get("percentViewsChange"))
    return {
        "rank": V.to_int(meta.get("currentPosition")),
        "previous_rank": V.to_int(meta.get("previousPosition")),
        "periods_on_chart": V.to_int(meta.get("periodsOnChart")),
        "views_change_percent": round(change * 100, 2) if change is not None else None,
    }


def _release(entry):
    date = entry.get("releaseDate") or {}
    try:
        return f"{int(date['year']):04d}-{int(date.get('month') or 1):02d}-{int(date.get('day') or 1):02d}" if date.get("year") else None
    except (TypeError, ValueError):
        return None


def _artists(entry):
    return [V.clean(a.get("name")) for a in entry.get("artists") or [] if isinstance(a, dict) and V.clean(a.get("name"))]


def chart_rows(data, chart):
    """charts.youtube.com browse -> (meta, rows) for one chart type."""
    section = V.first(data, "musicAnalyticsSectionRenderer") or {}
    content = section.get("content") or {}
    meta = content.get("perspectiveMetadata") or {}
    rows, info = [], {}
    for block_key in ("videos", "trackTypes", "artists", "podcastShows"):
        for block in content.get(block_key) or []:
            if not isinstance(block, dict):
                continue
            info = {"end_date": block.get("endDate"),
                    "period": str(block.get("chartPeriodType") or "").replace("CHART_PERIOD_TYPE_", "").lower() or None}
            for entry in block.get("videoViews") or []:
                stats = _entry_stats(entry)
                rows.append({**stats,
                             "type": "video", "id": entry.get("id"), "title": V.clean(entry.get("title")),
                             "link": refs.video_link(entry.get("id")),
                             "thumbnail": V.best_thumbnail(entry.get("thumbnail")),
                             "artists": _artists(entry), "view_count": V.to_int(entry.get("viewCount")),
                             "duration_seconds": V.to_int(entry.get("videoDuration")),
                             "release_date": _release(entry),
                             "songwriters": [s for s in entry.get("songwriters") or [] if isinstance(s, str)],
                             "producers": [s for s in entry.get("songProducers") or [] if isinstance(s, str)],
                             "channel": {"id": entry.get("externalChannelId"), "name": V.clean(entry.get("channelName")),
                                         "link": refs.channel_link(entry.get("externalChannelId"))}
                             if entry.get("externalChannelId") else None})
            for entry in block.get("trackViews") or []:
                stats = _entry_stats(entry)
                vid = entry.get("encryptedVideoId") or entry.get("atvExternalVideoId")
                rows.append({**stats,
                             "type": "song", "id": vid, "title": V.clean(entry.get("name")),
                             "link": refs.video_link(vid), "thumbnail": V.best_thumbnail(entry.get("thumbnail")),
                             "artists": _artists(entry), "view_count": V.to_int(entry.get("viewCount")),
                             "label": V.clean(entry.get("sublabel")), "release_date": _release(entry),
                             "songwriters": [s for s in entry.get("songwriters") or [] if isinstance(s, str)],
                             "producers": [s for s in entry.get("songProducers") or [] if isinstance(s, str)],
                             "art_track_video_id": entry.get("atvExternalVideoId")})
            for entry in block.get("artistViews") or []:
                stats = _entry_stats(entry)
                cid = entry.get("externalChannelId")
                rows.append({**stats,
                             "type": "artist", "id": cid, "name": V.clean(entry.get("name")),
                             "link": refs.channel_link(cid), "thumbnail": V.best_thumbnail(entry.get("thumbnail")),
                             "view_count": V.to_int(entry.get("viewCount"))})
            for entry in block.get("podcastShowEntries") or []:
                stats = _entry_stats(entry)
                pid = entry.get("externalPlaylistId")
                # podcast entries carry no externalChannelId, only the channel page link
                channel_match = re.search(r"/channel/(UC[\w-]{22})",
                                          str(V.find_key(entry, "channelNavigationEndpoint", "urlEndpoint", "url") or ""))
                podcast_cid = entry.get("externalChannelId") or (channel_match.group(1) if channel_match else None)
                rows.append({**stats,
                             "type": "podcast", "id": pid, "title": V.clean(entry.get("name")),
                             "link": refs.playlist_link(pid), "thumbnail": V.best_thumbnail(entry.get("thumbnail")),
                             "channel": {"id": podcast_cid, "name": V.clean(entry.get("channelName")),
                                         "link": refs.channel_link(podcast_cid)}
                             if podcast_cid else None})
    available = []
    for item in meta.get("availableChartsInfo") or []:
        if isinstance(item, dict):
            available.append({"chart_type": str(item.get("chartType") or "").replace("CHART_TYPE_", "").lower() or None,
                              "period": str(item.get("chartPeriodType") or "").replace("CHART_PERIOD_TYPE_", "").lower() or None,
                              "latest_date": item.get("latestEndDate"), "earliest_date": item.get("earliestEndDate")})
    return {"end_date": info.get("end_date"), "period": info.get("period"), "available_charts": available}, rows


# ---- suggestions ------------------------------------------------------------------------------------

def suggestions(payload):
    """['q', [['text', 0, [..]], ...], {...}] -> ['text', ...]."""
    if not isinstance(payload, list) or len(payload) < 2 or not isinstance(payload[1], list):
        return []
    out = []
    for row in payload[1]:
        value = row[0] if isinstance(row, list) and row else row
        if isinstance(value, str) and value.strip() and value not in out:
            out.append(value.strip())
    return out
