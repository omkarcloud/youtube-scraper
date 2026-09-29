"""/youtube/charts, /youtube/explore, /youtube/shorts/sound and
/youtube/resolve — YouTube Charts (the replacement for the retired
Trending feed), the Explore destination feeds, the Shorts that use one
sound, and "what is this link" resolution."""
from youtube import items as I
from youtube import page_parsers as PP
from youtube import refs
from youtube.fetch import YoutubeBadRequest, YoutubeNotFound, charts as charts_call, innertube, page_initial_data
from youtube.schemas import CHARTS, FEEDS
from youtube.shared import channel_id, locale


def charts(chart="top_videos", country="US", period=None):
    """One YouTube Charts list (100 rows; trending 30, shorts songs 50):
    rank, previous rank, periods on chart, view change, views, artists,
    songwriters / producers, release date."""
    locale(country if len(country or "") == 2 else None)
    chart_type, periods = CHARTS[chart]
    period = period or periods[0]
    code = (country or "US").lower()
    code = "global" if code in ("global", "zz") else code
    query = (f"perspective=CHART_DETAILS&chart_params_country_code={code}"
             f"&chart_params_chart_type={chart_type}&chart_params_period_type={period.upper()}")
    try:
        data = charts_call(query)
    except YoutubeBadRequest:
        raise YoutubeNotFound(f"YouTube Charts has no {chart} chart for '{country}'")
    meta, rows = PP.chart_rows(data, chart)
    return {"chart": chart, "country": code.upper() if code != "global" else "global",
            "period": meta["period"] or period, "end_date": meta["end_date"],
            "count": len(rows), "entries": rows}


def explore(feed, country="US", language="en"):
    """One Explore destination (news, live, sports, learning, courses,
    gaming, movies, shopping) as titled sections of videos / playlists."""
    hl, gl = locale(country, language)
    try:
        data = innertube("browse", {"browseId": FEEDS[feed]}, hl=hl, gl=gl)
    except YoutubeBadRequest:
        raise YoutubeNotFound(f"the {feed} feed is not available in '{country}'")
    shelves = I.sections(data.get("contents") or {})
    total = sum(len(s["items"]) for s in shelves)
    return {"feed": feed, "count": total, "sections": shelves}


def shorts_sound(video, country="US", language="en"):
    """The sound used by a Short (title, creator, how many Shorts use it)
    and the Shorts made with it. Logged-out sessions only get the first
    batch (~24): the sound page's own continuation answers empty without a
    login (verified 2026-09-27), so there is no cursor."""
    hl, gl = locale(country, language)
    try:
        data = page_initial_data(f"/source/{video}/shorts", hl=hl, gl=gl)
    except YoutubeNotFound:
        raise YoutubeNotFound(f"no sound page for Short '{video}'")
    header = PP.sound_header(data, video)
    rows = [r for r in I.collect(data.get("contents") or {}) if r["type"] == "short"]
    if not rows and not header["title"]:
        raise YoutubeNotFound(f"no sound page for Short '{video}' (is it a Short?)")
    return {"sound": header, "count": len(rows), "shorts": rows}


def resolve(link):
    """Any YouTube link -> what it points to, with canonical ids and links;
    channel handles / custom URLs / legacy /user names become UC ids."""
    locale()
    kind = link["kind"]
    out = {"type": kind}
    if kind in ("video", "short"):
        out.update(id=link["id"], link=refs.short_link(link["id"]) if kind == "short" else refs.video_link(link["id"]))
        if link.get("playlist_id"):
            out["playlist_id"] = link["playlist_id"]
        if link.get("start_seconds") is not None:
            out["start_seconds"] = link["start_seconds"]
    elif kind == "playlist":
        out.update(id=link["id"], link=refs.playlist_link(link["id"]))
    elif kind == "post":
        out.update(id=link["id"], link=refs.post_link(link["id"]))
    elif kind == "hashtag":
        out.update(id=link["id"], link=refs.hashtag_link(link["id"]))
    else:
        ref = {k: v for k, v in link.items() if k in ("id", "path")}
        cid = channel_id(ref)
        path = ref.get("path") or ""
        handle = path[1:] if path.startswith("/@") else None
        out.update(id=cid, handle=handle, link=refs.channel_link(cid, handle))
    return out
