"""List-item normalizers: every InnerTube renderer / view model that stands
for ONE video, short, channel, playlist, movie or post -> one clean shape,
plus the walkers that pull items, shelves and the next cursor out of any
response (search, channel tabs, playlists, hashtags, feeds, related).

Item shapes (all carry `type`):

  video     {type, id, title, link, thumbnail, description, duration_seconds,
             view_count, watching_count, published_time_text, is_live,
             is_upcoming, scheduled_start_at, badges[], channel{...}}
  short     {type, id, title, link, thumbnail, view_count}
  channel   {type, id, name, handle, link, thumbnail, description,
             subscriber_count, video_count, is_verified, is_artist}
  playlist  {type, id, title, link, thumbnail, video_count, kind, first_video_id,
             updated_time_text, channel{...}, videos[] (preview, releases only)}
  movie     {type, id, title, link, thumbnail, duration_seconds, description,
             genre, year, credits[], badges[], channel{...}}
  game      {type, id, title, link, thumbnail, watching_count}   (gaming feed)
  post      see posts.post()

A channel ref inside an item is {id, name, handle, link, thumbnail,
is_verified, is_artist} (keys null when the renderer does not say).

Numbers in lists are YouTube's display counts ("73M views" -> 73000000):
exact where YouTube prints them exactly (search results), rounded where it
abbreviates (grids). Relative times stay text (`published_time_text`,
"7 days ago"); exact dates are on the details endpoints.

Dropped as noise: tracking/logging params, menus, hover overlays, animated
(webp) previews, "Now playing" / watch-later / add-to-queue affordances,
themed palettes, accessibility duplicates of the same text, ad slots.
"""
import re

from youtube import refs
from youtube import values as V

_VIEWS_RE = re.compile(r"\bviews?\b", re.I)
_WATCHING_RE = re.compile(r"\bwatching\b|\bwaiting\b", re.I)
_SUBS_RE = re.compile(r"\bsubscribers?\b", re.I)
_VIDEOS_RE = re.compile(r"\b(videos?|episodes?|lessons?|tracks?|songs?)\b", re.I)
_DURATION_RE = re.compile(r"^\d+(?::\d{1,2}){1,2}$")
_KIND_WORDS = {"playlist": "playlist", "podcast": "podcast", "course": "course", "mix": "mix",
               "album": "album", "single": "single", "ep": "ep", "show": "show"}


# ---- shared bits ---------------------------------------------------------------------------

def _badge_labels(badges):
    out = []
    for badge in badges or []:
        renderer = badge.get("metadataBadgeRenderer") if isinstance(badge, dict) else None
        if not isinstance(renderer, dict):
            continue
        label = V.clean(renderer.get("label")) or V.clean(renderer.get("tooltip"))
        if label and label not in out:
            out.append(label)
    return out


def owner_flags(badges):
    """ownerBadges -> (is_verified, is_artist)."""
    styles = set()
    for badge in badges or []:
        renderer = badge.get("metadataBadgeRenderer") if isinstance(badge, dict) else None
        if isinstance(renderer, dict):
            styles.add(str(renderer.get("style") or ""))
    is_artist = "BADGE_STYLE_TYPE_VERIFIED_ARTIST" in styles
    is_verified = is_artist or "BADGE_STYLE_TYPE_VERIFIED" in styles
    return is_verified, is_artist


def handle_of(path):
    if isinstance(path, str) and path.startswith("/@"):
        return path[1:]
    return None


def channel_ref(name=None, channel_id=None, path=None, thumbnail=None, is_verified=None, is_artist=None):
    handle = handle_of(path)
    if not any((name, channel_id, handle)):
        return None
    link = refs.channel_link(channel_id, handle) if (channel_id or handle) else refs.absolute(path)
    return {
        "id": channel_id,
        "name": name,
        "handle": handle,
        "link": link,
        "thumbnail": thumbnail,
        "is_verified": is_verified,
        "is_artist": is_artist,
    }


def byline_channel(renderer, *keys):
    """A channel ref from a byline text (ownerText / shortBylineText / ...)."""
    for key in keys or ("ownerText", "longBylineText", "shortBylineText"):
        byline = renderer.get(key)
        name = V.text(byline)
        if name:
            is_verified, is_artist = owner_flags(renderer.get("ownerBadges"))
            avatar = V.find_key(renderer, "channelThumbnailSupportedRenderers",
                                "channelThumbnailWithLinkRenderer", "thumbnail")
            return channel_ref(name, V.browse_id(byline), V.canonical_path(byline),
                               V.best_thumbnail(avatar) if avatar else None,
                               is_verified if renderer.get("ownerBadges") is not None else None,
                               is_artist if renderer.get("ownerBadges") is not None else None)
    return None


def _overlay_status(renderer):
    """thumbnailOverlayTimeStatusRenderer -> (duration_text, style)."""
    for overlay in renderer.get("thumbnailOverlays") or []:
        status = overlay.get("thumbnailOverlayTimeStatusRenderer") if isinstance(overlay, dict) else None
        if isinstance(status, dict):
            return V.text(status.get("text")), str(status.get("style") or "")
    return None, ""


def _upcoming_at(renderer):
    start = V.find_key(renderer, "upcomingEventData", "startTime")
    return V.iso_from_usec(int(start) * 1_000_000) if V.to_int(start) else None


def _video_count(value):
    s = V.text(value)
    return V.count(s) if s else None


# ---- videos ------------------------------------------------------------------------------------

def video_renderer(r):
    """videoRenderer / compactVideoRenderer / gridVideoRenderer / playlistVideoRenderer."""
    vid = r.get("videoId")
    if not vid:
        return None
    duration, style = _overlay_status(r)
    length = V.text(r.get("lengthText")) or duration
    views_text = V.text(r.get("viewCountText")) or V.text(r.get("shortViewCountText"))
    badges = _badge_labels(r.get("badges"))
    is_upcoming = style == "UPCOMING" or bool(r.get("upcomingEventData"))
    # an upcoming premiere / stream reads "123 waiting" — that is not a live video
    is_live = not is_upcoming and (style == "LIVE" or "LIVE" in badges
                                   or bool(views_text and _WATCHING_RE.search(views_text)))
    is_short = style == "SHORTS" or bool(V.find_key(r, "navigationEndpoint", "reelWatchEndpoint"))
    published = V.text(r.get("publishedTimeText"))
    info = r.get("videoInfo")                       # playlistVideoRenderer: "762M views • 2 years ago"
    if info and not views_text:
        parts = [V.text(run) for run in (info.get("runs") or []) if V.text(run) and V.text(run) != "•"]
        for part in parts:
            if _VIEWS_RE.search(part):
                views_text = part
            elif V.is_relative_time(part):
                published = part
    snippet = V.find_key(r, "detailedMetadataSnippets", 0, "snippetText") or r.get("descriptionSnippet")
    item = {
        "type": "short" if is_short else "video",
        "id": vid,
        "title": V.text(r.get("title")) or V.text(r.get("headline")),
        "link": refs.short_link(vid) if is_short else refs.video_link(vid),
        "thumbnail": V.best_thumbnail(r.get("thumbnail")),
        "description": V.text(snippet),
        "duration_seconds": V.to_int(r.get("lengthSeconds")) or V.duration_seconds(length),
        "view_count": None if is_live or is_upcoming else V.count(views_text),
        "watching_count": V.count(views_text) if is_live or (is_upcoming and views_text and _WATCHING_RE.search(views_text))
        else None,
        "published_time_text": published,
        "is_live": is_live,
        "is_upcoming": is_upcoming,
        "scheduled_start_at": _upcoming_at(r),
        "badges": [b for b in badges if b not in ("LIVE",)],
        "channel": byline_channel(r),
    }
    moments = key_moments(r.get("expandableMetadata"))
    if moments:
        item["chapters"] = moments
    index = V.to_int(V.text(r.get("index")))
    if index is not None:
        item["position"] = index
    return item


def key_moments(expandable):
    """Search results' expandable 'key moments' -> [{title, start_seconds, thumbnail}]."""
    out = []
    for marker in V.walk(expandable or {}, "macroMarkersListItemRenderer"):
        if not isinstance(marker, dict):
            continue
        start = V.find_key(marker, "onTap", "watchEndpoint", "startTimeSeconds")
        out.append({"title": V.text(marker.get("title")),
                    "start_seconds": V.to_int(start) if start is not None else V.duration_seconds(V.text(marker.get("timeDescription"))),
                    "thumbnail": V.best_thumbnail(marker.get("thumbnail"))})
    return out


def child_video(r):
    """childVideoRenderer (the track list inside a release card)."""
    vid = r.get("videoId")
    if not vid:
        return None
    return {"id": vid, "title": V.text(r.get("title")), "link": refs.video_link(vid),
            "duration_seconds": V.duration_seconds(V.text(r.get("lengthText")))}


def _lockup_parts(meta):
    """contentMetadataViewModel rows -> [(text, a11y, part)] flattened."""
    out = []
    rows = V.find_key(meta, "metadata", "contentMetadataViewModel", "metadataRows") or []
    for row in rows:
        for part in (row.get("metadataParts") or []) if isinstance(row, dict) else []:
            if not isinstance(part, dict):
                continue
            label = V.text(part.get("text"))
            if not label and isinstance(part.get("avatarStack"), dict):
                label = V.text(V.find_key(part, "avatarStack", "avatarStackViewModel", "text"))
            if label:
                out.append((label, V.clean(part.get("accessibilityLabel")), part))
    return out


def _lockup_badges(content_image):
    texts = []
    for badge in V.walk(content_image, "thumbnailBadgeViewModel"):
        if isinstance(badge, dict):
            label = V.clean(badge.get("text"))
            style = str(badge.get("badgeStyle") or "")
            if label or "LIVE" in style:
                texts.append((label or "", style))
    return texts


def lockup(r):
    """lockupViewModel — the 2025+ card for videos, playlists, mixes,
    podcasts and courses (channel tabs, related, playlists, search)."""
    content_id = r.get("contentId")
    kind = str(r.get("contentType") or "").replace("LOCKUP_CONTENT_TYPE_", "").lower()
    if not content_id:
        return None
    meta = V.find_key(r, "metadata", "lockupMetadataViewModel") or {}
    title = V.text(meta.get("title"))
    image = r.get("contentImage") or {}
    thumb = V.best_thumbnail(V.find_key(image, "thumbnailViewModel", "image")) or V.best_thumbnail(
        V.find_key(image, "collectionThumbnailViewModel", "primaryThumbnail", "thumbnailViewModel", "image"))
    avatar = V.find_key(meta, "image", "decoratedAvatarViewModel")
    channel_id = V.browse_id(avatar) if avatar else None
    channel_thumb = V.best_thumbnail(V.find_key(avatar, "avatar", "avatarViewModel", "image")) if avatar else None
    fields = {"view_count": None, "watching_count": None, "published_time_text": None, "video_count": None,
              "channel_name": None, "channel_path": None, "kind": None, "updated": None}
    for label, a11y, part in _lockup_parts(meta):
        probe = a11y or label
        low = label.lower()
        if _WATCHING_RE.search(probe):
            fields["watching_count"] = V.count(probe)
        elif _VIEWS_RE.search(probe):
            fields["view_count"] = V.count(probe)
        elif _VIDEOS_RE.search(probe) and V.count(probe) is not None:
            fields["video_count"] = V.count(probe)
        elif low.startswith("updated") or low.startswith("last updated"):
            fields["updated"] = label
        elif V.is_relative_time(probe) and re.search(r"\d|today|yesterday", probe, re.I):
            fields["published_time_text"] = a11y or label
        elif low in _KIND_WORDS:
            fields["kind"] = _KIND_WORDS[low]
        elif low in ("view full playlist", "view full course", "view full podcast", "course", "mix"):
            continue
        elif fields["channel_name"] is None:
            name = label[3:] if low.startswith("by ") else label
            fields["channel_name"] = name
            fields["channel_path"] = V.canonical_path(part)
            channel_id = channel_id or V.browse_id(part)
    badges = _lockup_badges(image)
    duration = next((V.duration_seconds(t) for t, _ in badges if _DURATION_RE.match(t)), None)
    is_live = any("LIVE" in style or t.upper() == "LIVE" for t, style in badges)
    is_upcoming = any(t.upper() in ("UPCOMING", "PREMIERE") for t, _ in badges)
    if fields["video_count"] is None:
        fields["video_count"] = next((V.count(t) for t, _ in badges if _VIDEOS_RE.search(t)), None)
    channel = channel_ref(fields["channel_name"], channel_id, fields["channel_path"], channel_thumb) \
        if (fields["channel_name"] or channel_id) else None
    if kind in ("video", "short"):
        return {
            "type": "video",
            "id": content_id,
            "title": title,
            "link": refs.video_link(content_id),
            "thumbnail": thumb,
            "description": None,
            "duration_seconds": duration,
            "view_count": None if is_live else fields["view_count"],
            "watching_count": fields["watching_count"],
            "published_time_text": fields["published_time_text"],
            "is_live": is_live,
            "is_upcoming": is_upcoming,
            "scheduled_start_at": None,
            "badges": [t for t, s in badges if t and not _DURATION_RE.match(t) and t.upper() not in ("LIVE",)],
            "channel": channel,
        }
    # playlists, mixes, podcasts, albums, courses
    first_video = V.find_key(r, "rendererContext", "commandContext", "onTap", "innertubeCommand",
                             "watchEndpoint", "videoId")
    # mixes (RD...) also print a "Playlist" part, so the id prefix wins
    playlist_kind = "mix" if content_id.startswith("RD") else (fields["kind"] or kind or "playlist")
    return {
        "type": "playlist",
        "id": content_id,
        "title": title,
        "link": refs.playlist_link(content_id),
        "thumbnail": thumb,
        "kind": playlist_kind,
        "video_count": fields["video_count"],
        "first_video_id": first_video,
        "updated_time_text": fields["updated"] or fields["published_time_text"],
        "channel": channel,
    }


def shorts_lockup(r):
    """shortsLockupViewModel (channel Shorts tab, search / hashtag grids)."""
    vid = V.find_key(r, "onTap", "innertubeCommand", "reelWatchEndpoint", "videoId")
    if not vid:
        entity = str(r.get("entityId") or "")
        vid = entity.rsplit("-item-", 1)[-1] if "-item-" in entity else None
    if not vid:
        return None
    overlay = r.get("overlayMetadata") or {}
    views_text = V.text(overlay.get("secondaryText"))
    a11y = V.clean(r.get("accessibilityText")) or ""
    a11y_views = re.search(r",\s*([\d.,]+\s*(?:thousand|million|billion)?\s*views?|no views)\s*-\s*play Short", a11y, re.I)
    title = V.text(overlay.get("primaryText"))
    if not title and a11y_views:          # slim grid cards carry the title only in the a11y text
        title = V.clean(a11y[:a11y_views.start()])
    return {
        "type": "short",
        "id": vid,
        "title": title,
        "link": refs.short_link(vid),
        "thumbnail": V.best_thumbnail(V.find_key(r, "thumbnailViewModel", "thumbnailViewModel", "image")
                                      or V.find_key(r, "thumbnail")),
        "view_count": V.count(a11y_views.group(1)) if a11y_views else V.count(views_text),
    }


def reel_item(r):
    """reelItemRenderer (older shorts shelves)."""
    vid = r.get("videoId")
    if not vid:
        return None
    return {"type": "short", "id": vid, "title": V.text(r.get("headline")), "link": refs.short_link(vid),
            "thumbnail": V.best_thumbnail(r.get("thumbnail")), "view_count": V.count(V.text(r.get("viewCountText")))}


# ---- channels ------------------------------------------------------------------------------------

def channel_renderer(r):
    """channelRenderer (search) / gridChannelRenderer (featured channels).
    Search swaps the fields: `subscriberCountText` carries the @handle and
    `videoCountText` the subscriber count — both orders are accepted."""
    cid = r.get("channelId")
    if not cid:
        return None
    path = V.canonical_path(r.get("navigationEndpoint"))
    handle = handle_of(path)
    subscribers = videos = None
    for key in ("subscriberCountText", "videoCountText"):
        label = V.text(r.get(key))
        if not label:
            continue
        if label.startswith("@"):
            handle = handle or label
        elif _SUBS_RE.search(label):
            subscribers = V.count(label)
        elif _VIDEOS_RE.search(label):
            videos = V.count(label)
    is_verified, is_artist = owner_flags(r.get("ownerBadges"))
    return {
        "type": "channel",
        "id": cid,
        "name": V.text(r.get("title")),
        "handle": handle,
        "link": refs.channel_link(cid, handle),
        "thumbnail": V.best_thumbnail(r.get("thumbnail")),
        "description": V.text(r.get("descriptionSnippet")),
        "subscriber_count": subscribers,
        "video_count": videos,
        "is_verified": is_verified,
        "is_artist": is_artist,
    }


# ---- playlists ------------------------------------------------------------------------------------

def playlist_renderer(r):
    """playlistRenderer (Releases tab, search) / gridPlaylistRenderer / radioRenderer."""
    pid = r.get("playlistId")
    if not pid:
        return None
    thumbs = r.get("thumbnails")
    thumb = V.best_thumbnail(thumbs[0]) if isinstance(thumbs, list) and thumbs else V.best_thumbnail(r.get("thumbnail"))
    byline = r.get("shortBylineText") or r.get("longBylineText")
    byline_text = V.text(byline) or ""
    channel = None
    if byline:
        runs = byline.get("runs") or []
        name = V.text(runs[0]) if runs else byline_text
        channel = channel_ref(name, V.browse_id(byline), V.canonical_path(byline))
    date_part = byline_text.split("·")[-1].strip() if "·" in byline_text else None
    videos = [child_video(v.get("childVideoRenderer")) for v in (r.get("videos") or [])
              if isinstance(v, dict) and isinstance(v.get("childVideoRenderer"), dict)]
    item = {
        "type": "playlist",
        "id": pid,
        "title": V.text(r.get("title")),
        "link": refs.playlist_link(pid),
        "thumbnail": thumb,
        "kind": "mix" if pid.startswith("RD") else ("album" if pid.startswith("OLAK5uy_") else "playlist"),
        "video_count": V.to_int(r.get("videoCount")) if r.get("videoCount") is not None
        else _video_count(r.get("videoCountText") or r.get("videoCountShortText")),
        "first_video_id": V.find_key(r, "navigationEndpoint", "watchEndpoint", "videoId"),
        "updated_time_text": V.text(r.get("publishedTimeText")),
        "channel": channel,
    }
    release = V.written_date(date_part) if date_part else None
    if release:
        item["release_date"] = release
    if videos:
        item["videos"] = [v for v in videos if v]
    return item


# ---- movies ------------------------------------------------------------------------------------

def movie_renderer(r):
    """movieRenderer (search) / gridMovieRenderer (Movies storefront)."""
    vid = r.get("videoId")
    if not vid:
        return None
    top = V.text((r.get("topMetadataItems") or [None])[0]) or V.text(r.get("metadata"))
    genre = year = None
    if top:
        parts = [p.strip() for p in top.split("•")]
        for part in parts:
            if re.fullmatch(r"\d{4}", part):
                year = int(part)
            elif genre is None:
                genre = part
    credits = []
    for item in r.get("bottomMetadataItems") or []:
        line = V.text(item)
        if line and ":" in line:
            role, names = line.split(":", 1)
            credits.append({"role": role.strip(), "names": [n.strip() for n in names.split(",") if n.strip()]})
    return {
        "type": "movie",
        "id": vid,
        "title": V.text(r.get("title")),
        "link": refs.video_link(vid),
        "thumbnail": V.best_thumbnail(r.get("thumbnail")),
        "duration_seconds": V.duration_seconds(V.text(r.get("lengthText"))),
        "description": V.text(r.get("descriptionSnippet")),
        "genre": genre,
        "year": year,
        "credits": credits,
        "badges": _badge_labels(r.get("badges")),
        "channel": byline_channel(r, "longBylineText", "shortBylineText"),
    }


def featured_video(r):
    """channelVideoPlayerRenderer — the channel trailer / featured video on Home."""
    vid = r.get("videoId")
    if not vid:
        return None
    return {
        "type": "video",
        "id": vid,
        "title": V.text(r.get("title")),
        "link": refs.video_link(vid),
        "thumbnail": f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg",
        "description": V.text(r.get("description")),
        "duration_seconds": None,
        "view_count": V.count(V.text(r.get("viewCountText"))),
        "watching_count": None,
        "published_time_text": V.text(r.get("publishedTimeText")),
        "is_live": False,
        "is_upcoming": False,
        "scheduled_start_at": None,
        "badges": [],
        "channel": None,
    }


def game_card(r):
    """gameCardRenderer (gaming Explore feed) -> {type: game, id, title, link, thumbnail, watching_count}."""
    game = V.find_key(r, "game", "gameDetailsRenderer") or {}
    endpoint = game.get("endpoint") or {}
    gid = V.browse_id(endpoint)
    if not gid:
        return None
    return {"type": "game", "id": gid, "title": V.text(game.get("title")),
            "link": refs.channel_link(gid), "thumbnail": V.best_thumbnail(game.get("boxArt")),
            "watching_count": V.count(V.text(game.get("liveViewersText")))}


def video_card(r):
    """videoCardRenderer (gaming feed): metadataText '3.6M views · Streamed 18 hours ago'."""
    vid = r.get("videoId")
    if not vid:
        return None
    views = published = None
    for part in (V.text(r.get("metadataText")) or "").split("·"):
        part = part.strip()
        if _VIEWS_RE.search(part):
            views = V.count(part)
        elif V.is_relative_time(part):
            published = part
    return {"type": "video", "id": vid, "title": V.text(r.get("title")), "link": refs.video_link(vid),
            "thumbnail": V.best_thumbnail(r.get("thumbnail")), "description": None,
            "duration_seconds": V.duration_seconds(V.text(r.get("lengthText"))), "view_count": views,
            "watching_count": None, "published_time_text": published, "is_live": False, "is_upcoming": False,
            "scheduled_start_at": None, "badges": _badge_labels(r.get("badges")),
            "channel": byline_channel(r, "bylineText")}


def community_post(r):
    """postRenderer / backstagePostRenderer inside shelves (channel Home)."""
    from youtube.social_parsers import post      # social_parsers imports this module
    row = post(r)
    if row:
        row.pop("detail_params", None)
    return row


# ---- dispatch ------------------------------------------------------------------------------------

ITEM_PARSERS = {
    "videoRenderer": video_renderer,
    "compactVideoRenderer": video_renderer,
    "gridVideoRenderer": video_renderer,
    "playlistVideoRenderer": video_renderer,
    "lockupViewModel": lockup,
    "shortsLockupViewModel": shorts_lockup,
    "reelItemRenderer": reel_item,
    "channelRenderer": channel_renderer,
    "gridChannelRenderer": channel_renderer,
    "playlistRenderer": playlist_renderer,
    "gridPlaylistRenderer": playlist_renderer,
    "compactPlaylistRenderer": playlist_renderer,
    "radioRenderer": playlist_renderer,
    "compactRadioRenderer": playlist_renderer,
    "movieRenderer": movie_renderer,
    "gridMovieRenderer": movie_renderer,
    "channelVideoPlayerRenderer": featured_video,
    "gameCardRenderer": game_card,
    "videoCardRenderer": video_card,
    "postRenderer": community_post,
    "backstagePostRenderer": community_post,
}

# never descended into (ads, chrome, pickers, the next-page stub)
SKIP_KEYS = {
    "adSlotRenderer", "promotedSparklesWebRenderer", "searchPyvRenderer", "promotedVideoRenderer",
    "continuationItemRenderer", "continuationItemViewModel", "menu", "menuRenderer", "topbar",
    "header", "subMenu", "chipCloudRenderer", "feedFilterChipBarRenderer", "sortFilterSubMenuRenderer",
    "frameworkUpdates", "responseContext", "microformat", "metadata", "sidebar", "thumbnailOverlays",
    "endScreen", "playerOverlays", "brandVideoShelfRenderer", "statementBannerRenderer",
    "backgroundPromoRenderer", "messageRenderer", "clarificationRenderer", "emergencyOneboxRenderer",
}


def parse_item(key, node):
    parser = ITEM_PARSERS.get(key)
    if parser is None or not isinstance(node, dict):
        return None
    try:
        return parser(node)
    except Exception:          # one odd card must never fail the whole page
        return None


def collect(node, *, dedupe=True):
    """Every item in `node` (depth-first, document order)."""
    out, seen = [], set()

    def rec(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if key in ITEM_PARSERS:
                    item = parse_item(key, child)
                    if item:
                        marker = (item["type"], item["id"])
                        if not dedupe or marker not in seen:
                            seen.add(marker)
                            out.append(item)
                    continue
                if key in SKIP_KEYS:
                    continue
                if isinstance(child, (dict, list)):
                    rec(child)
        elif isinstance(value, list):
            for child in value:
                rec(child)
    rec(node)
    return out


def _shelf_title(shelf):
    title = V.text(shelf.get("title")) or V.text(V.find_key(shelf, "header", "richListHeaderRenderer", "title")) \
        or V.text(V.find_key(shelf, "header", "sectionHeaderViewModel", "headline")) \
        or V.text(V.find_key(shelf, "header", "gridShelfHeaderViewModel", "title"))
    return title


def sections(node):
    """Titled shelves -> [{title, items}] (Explore feeds, channel Home,
    search shelves). Items outside any titled shelf land in a leading
    {title: null} section."""
    out = []
    loose = []

    def rec(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if key in ("shelfRenderer", "richShelfRenderer", "reelShelfRenderer", "gridShelfViewModel",
                           "horizontalCardListRenderer") \
                        and isinstance(child, dict):
                    items = collect(child)
                    if items:
                        out.append({"title": _shelf_title(child), "items": items})
                    continue
                if key in ITEM_PARSERS:
                    item = parse_item(key, child)
                    if item:
                        loose.append(item)
                    continue
                if key in SKIP_KEYS:
                    continue
                if isinstance(child, (dict, list)):
                    rec(child)
        elif isinstance(value, list):
            for child in value:
                rec(child)
    rec(node)
    if loose:
        out.insert(0, {"title": None, "items": loose})
    return out


def next_cursor(node, exclude=("commentRepliesRenderer", "sortFilterSubMenuRenderer", "subMenuItems",
                               "engagementPanels", "header")):
    """The token of the LAST next-page stub (continuationItemRenderer /
    continuationItemViewModel) in document order, skipping stubs that
    belong to reply threads, sort menus and side panels."""
    found = []

    def rec(value, blocked):
        if isinstance(value, dict):
            for key, child in value.items():
                if key in exclude:
                    continue
                if key in ("continuationItemRenderer", "continuationItemViewModel") and isinstance(child, dict):
                    for command in V.walk(child, "continuationCommand"):
                        if isinstance(command, dict) and command.get("token"):
                            found.append(command["token"])
                            break
                    continue
                if isinstance(child, (dict, list)):
                    rec(child, blocked)
        elif isinstance(value, list):
            for child in value:
                rec(child, blocked)
    rec(node, False)
    return found[-1] if found else None


def continuation_items(data):
    """The appended/reloaded items of a continuation response."""
    out = []
    for key in ("onResponseReceivedActions", "onResponseReceivedEndpoints", "onResponseReceivedCommands"):
        for action in data.get(key) or []:
            if not isinstance(action, dict):
                continue
            for inner in action.values():
                if isinstance(inner, dict) and isinstance(inner.get("continuationItems"), list):
                    out.extend(inner["continuationItems"])
    return out
