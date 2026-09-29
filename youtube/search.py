"""/youtube/search* and /youtube/hashtags — keyword search with the site's
filters (type, upload date, duration, features, sort), search
suggestions and a hashtag's videos or Shorts."""
from youtube import items as I
from youtube import page_parsers as PP
from youtube import protos
from youtube import values as V
from youtube.fetch import YoutubeBadRequest, YoutubeNotFound, innertube, suggest
from youtube.shared import continuation, locale, page_result

_TYPES = {"all": None, "video": "video", "short": "shorts", "channel": "channel", "playlist": "playlist",
          "movie": "movie"}


def _corrections(data):
    """'Showing results for' / 'Did you mean' blocks -> {corrected_query, original_query}."""
    for key in ("showingResultsForRenderer", "didYouMeanRenderer"):
        block = V.first(data, key)
        if isinstance(block, dict):
            corrected = V.text(block.get("correctedQuery")) or V.text(block.get("didYouMean"))
            original = V.text(block.get("originalQuery"))
            return {"corrected_query": corrected, "original_query": original,
                    "is_auto_corrected": key == "showingResultsForRenderer"}
    return None


def _only_type(rows, type):
    if type == "short":
        return [r for r in rows if r["type"] == "short"]
    if type in ("video", "channel", "playlist"):
        return [r for r in rows if r["type"] == type]
    if type == "movie":
        return [r for r in rows if r["type"] in ("movie", "video")]
    return rows


def search(query, type="all", upload_date=None, duration=None, sort="relevance", features=None, cursor=None,
           country="US", language="en"):
    """Search results: videos, Shorts, channels, playlists, movies (mixed
    for type=all), ~20 a page, with the estimated total and any spelling
    correction YouTube applied."""
    hl, gl = locale(country, language)
    if cursor:
        rows, next_cursor = continuation("search", cursor, hl, gl)
        return page_result("results", _only_type(rows, type), next_cursor, query=query)
    body = {"query": query}
    params = protos.search_params(type=_TYPES.get(type), upload_date=upload_date, duration=duration,
                                  sort=sort, features=features)
    if params:
        body["params"] = params
    data = innertube("search", body, hl=hl, gl=gl)
    contents = V.find_key(data, "contents", "twoColumnSearchResultsRenderer", "primaryContents") or data.get("contents") or {}
    rows = _only_type(I.collect(contents), type)
    out = page_result("results", rows, I.next_cursor(contents), query=query,
                      estimated_total_count=V.to_int(data.get("estimatedResults")),
                      correction=_corrections(contents))
    return out


def suggestions(query, country="US", language="en"):
    """Search-box autocomplete for a partial query."""
    hl, gl = locale(country, language)
    rows = PP.suggestions(suggest(query, hl=hl, gl=gl))
    return {"query": query, "count": len(rows), "suggestions": rows}


def hashtag(hashtag, type="all", cursor=None, country="US", language="en"):
    """Videos (or only Shorts) tagged with a hashtag, with the hashtag's
    video / channel counts."""
    hl, gl = locale(country, language)
    if cursor:
        rows, next_cursor = continuation("browse", cursor, hl, gl)
        return page_result("videos", rows, next_cursor, hashtag={"name": hashtag})
    try:
        data = innertube("browse", {"browseId": "FEhashtag", "params": protos.hashtag_params(hashtag, type == "shorts")},
                         hl=hl, gl=gl)
    except (YoutubeNotFound, YoutubeBadRequest):
        raise YoutubeNotFound(f"hashtag '#{hashtag}' not found")
    header = PP.hashtag_header(data, hashtag)
    contents = data.get("contents") or {}
    rows = I.collect(contents)
    if not rows and header["video_count"] is None:
        raise YoutubeNotFound(f"hashtag '#{hashtag}' has no videos")
    return page_result("videos", rows, I.next_cursor(contents), hashtag=header, type=type)
