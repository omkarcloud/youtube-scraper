"""/youtube/channels/* — a channel's profile (header + About panel) and
every content tab: videos / shorts / live (latest, popular, oldest),
playlists / releases / podcasts / courses, community posts, the Home
shelves and in-channel search. Every function takes the resolved
`channel` ref ({"id"} or {"path"}), turned into a UC id first."""
from youtube import items as I
from youtube import page_parsers as PP
from youtube import social_parsers as S
from youtube import values as V
from youtube.fetch import YoutubeBadRequest, YoutubeNotFound, innertube
from youtube.shared import channel_id, channel_label, channel_ref_of, channel_tab, continuation, locale, page_result

_SORT_LABELS = {"latest": ("latest", "newest", "recently uploaded"), "popular": ("popular", "most popular"),
                "oldest": ("oldest",)}


def _browse_channel(cid, hl, gl, ref):
    try:
        data = innertube("browse", {"browseId": cid}, hl=hl, gl=gl)
    except (YoutubeNotFound, YoutubeBadRequest):
        raise YoutubeNotFound(f"channel '{channel_label(ref)}' not found")
    if not (data.get("metadata") or {}).get("channelMetadataRenderer"):
        raise YoutubeNotFound(f"channel '{channel_label(ref)}' not found")
    return data


def details(channel, country="US", language="en"):
    """Name, handle, description, exact subscriber / video / view counts,
    joined date, country, links, business-email flag, keywords, avatar,
    banner, available tabs, RSS feed and availability."""
    hl, gl = locale(country, language)
    cid = channel_id(channel)
    data = _browse_channel(cid, hl, gl, channel)
    about_data = None
    token = PP.about_token(data)
    if token:
        try:
            about_data = innertube("browse", {"continuation": token}, hl=hl, gl=gl)
        except (YoutubeNotFound, YoutubeBadRequest):
            about_data = None
    return {"channel": PP.channel_details(data, about_data)}


def _chip_token(content, sort):
    wanted = _SORT_LABELS.get(sort, ())
    chips = [chip for chip in V.walk(content, "chipViewModel") if isinstance(chip, dict)]
    for chip in chips:
        label = (V.clean(chip.get("text")) or "").lower()
        if label in wanted:
            command = V.first(chip, "continuationCommand")
            if isinstance(command, dict) and command.get("token"):
                return command["token"]
    # labels follow `language` (fr: "Les plus récentes" / "Populaires" / ...):
    # fall back to the chips' fixed Latest / Popular / Oldest order
    if len(chips) == len(_SORT_LABELS) and sort in _SORT_LABELS:
        command = V.first(chips[list(_SORT_LABELS).index(sort)], "continuationCommand")
        if isinstance(command, dict) and command.get("token"):
            return command["token"]
    return None


def _drop_own_channel(rows, cid):
    for row in rows:                  # every row belongs to this channel: its ref is the header's
        if "channel" in row and (row["channel"] is None or row["channel"].get("id") in (None, cid)):
            row.pop("channel")
    return rows


def _tab_list(channel, tab, key, cursor, country, language, sort=None, query=None, parse=None):
    hl, gl = locale(country, language)
    cid = channel_id(channel)
    collect = parse or I.collect
    if cursor:
        rows, next_cursor = continuation("browse", cursor, hl, gl, parse=collect)
        return page_result(key, _drop_own_channel(rows, cid), next_cursor, channel={"id": cid})
    try:
        data, content = channel_tab(cid, tab, hl, gl, query=query)
    except (YoutubeNotFound, YoutubeBadRequest):
        raise YoutubeNotFound(f"channel '{channel_label(channel)}' not found")
    ref = channel_ref_of(data, cid)
    if content is None:
        return page_result(key, [], None, channel=ref, has_tab=False)
    if sort and sort != "latest":
        token = _chip_token(content, sort)
        if token:
            data = innertube("browse", {"continuation": token}, hl=hl, gl=gl)
            content = I.continuation_items(data) or data
    rows = _drop_own_channel(collect(content), cid)
    out = page_result(key, rows, I.next_cursor(content), channel=ref, has_tab=True)
    if sort:
        out["sort"] = sort
    return out


def videos(channel, sort="latest", cursor=None, country="US", language="en"):
    """Long-form uploads, 30 a page, latest / popular / oldest first."""
    return _tab_list(channel, "videos", "videos", cursor, country, language, sort=sort)


def shorts(channel, sort="latest", cursor=None, country="US", language="en"):
    """Shorts, ~48 a page, latest / popular / oldest first."""
    return _tab_list(channel, "shorts", "shorts", cursor, country, language, sort=sort)


def live(channel, sort="latest", cursor=None, country="US", language="en"):
    """Live streams (live now, upcoming and past broadcasts)."""
    return _tab_list(channel, "live", "videos", cursor, country, language, sort=sort)


def playlists(channel, type="playlists", cursor=None, country="US", language="en"):
    """Playlists, or the Releases (albums / singles), Podcasts or Courses tab."""
    out = _tab_list(channel, type, "playlists", cursor, country, language)
    out["type"] = type
    return out


def posts(channel, cursor=None, country="US", language="en"):
    """Community posts: text, images, polls, quizzes, linked videos, with
    like and comment counts."""
    def parse(node):
        rows = S.posts_in(node)
        for row in rows:
            row.pop("detail_params", None)
        return rows
    return _tab_list(channel, "posts", "posts", cursor, country, language, parse=parse)


def home(channel, country="US", language="en"):
    """The Home tab as titled shelves (featured video, For you, Popular,
    playlists, featured channels ...)."""
    hl, gl = locale(country, language)
    cid = channel_id(channel)
    try:
        data, content = channel_tab(cid, "home", hl, gl)
    except (YoutubeNotFound, YoutubeBadRequest):
        raise YoutubeNotFound(f"channel '{channel_label(channel)}' not found")
    shelves = I.sections(content or {})
    if shelves and shelves[0]["title"] is None:
        shelves[0]["title"] = "Featured video"          # the trailer / featured video above the shelves
    return {"channel": channel_ref_of(data, cid), "count": len(shelves), "sections": shelves}


def search(channel, query, cursor=None, country="US", language="en"):
    """Search inside one channel's uploads and playlists."""
    return _tab_list(channel, "search", "items", cursor, country, language, query=query)
