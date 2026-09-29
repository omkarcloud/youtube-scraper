"""Offline tests: ref parsing, protobuf builders, value helpers and every
normalizer against raw InnerTube / charts responses captured 2026-09-27
(youtube/fixtures/*.json.gz, tracking noise stripped).

    python -m pytest youtube/test_parsers.py -q
"""
import base64
import gzip
import json
import os
from urllib.parse import unquote

import pytest

from youtube import items as I
from youtube import page_parsers as PP
from youtube import protos, refs, schemas
from youtube import social_parsers as S
from youtube import values as V
from youtube import video_parsers as P

FIX = os.path.join(os.path.dirname(__file__), "fixtures")


def fx(name):
    with gzip.open(os.path.join(FIX, name + ".json.gz"), "rt", encoding="utf-8") as f:
        return json.load(f)


def _clean_keys(obj, path="$"):
    """Every emitted key is snake_case, none is named *url*, no __typename."""
    if isinstance(obj, dict):
        for key, value in obj.items():
            assert key == key.lower() and " " not in key and "-" not in key, f"{path}.{key} not snake_case"
            assert "url" not in key, f"{path}.{key} should be a *link"
            assert not key.startswith("_"), f"{path}.{key} leaks an internal key"
            _clean_keys(value, f"{path}.{key}")
    elif isinstance(obj, list):
        for i, value in enumerate(obj):
            _clean_keys(value, f"{path}[{i}]")


def _no_blank_strings(obj, path="$"):
    if isinstance(obj, dict):
        for key, value in obj.items():
            _no_blank_strings(value, f"{path}.{key}")
    elif isinstance(obj, list):
        for i, value in enumerate(obj):
            _no_blank_strings(value, f"{path}[{i}]")
    elif isinstance(obj, str):
        assert obj.strip(), f"{path} is an empty string (should be null)"


# ---- refs --------------------------------------------------------------------------------

@pytest.mark.parametrize("value", [
    "dQw4w9WgXcQ", "https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=42s", "https://youtu.be/dQw4w9WgXcQ?si=x",
    "youtube.com/shorts/dQw4w9WgXcQ", "https://m.youtube.com/watch?v=dQw4w9WgXcQ", "https://www.youtube.com/live/dQw4w9WgXcQ",
    "https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ", "https://music.youtube.com/watch?v=dQw4w9WgXcQ&list=RDAMVM",
])
def test_resolve_video(value):
    assert refs.resolve_video(value) == "dQw4w9WgXcQ"


@pytest.mark.parametrize("value", ["", "abc", "https://vimeo.com/123", "https://www.youtube.com/@MrBeast", "dQw4w9WgXcQQ"])
def test_resolve_video_rejects(value):
    with pytest.raises(ValueError):
        refs.resolve_video(value)


@pytest.mark.parametrize("value, expected", [
    ("UCX6OQ3DkcsbYNE6H8uQQuVA", {"id": "UCX6OQ3DkcsbYNE6H8uQQuVA"}),
    ("@MrBeast", {"path": "/@MrBeast"}), ("MrBeast", {"path": "/@MrBeast"}),
    ("https://www.youtube.com/@MrBeast/videos", {"path": "/@MrBeast"}),
    ("https://www.youtube.com/channel/UCX6OQ3DkcsbYNE6H8uQQuVA/about", {"id": "UCX6OQ3DkcsbYNE6H8uQQuVA"}),
    ("https://www.youtube.com/c/MrBeast6000", {"path": "/c/MrBeast6000"}),
    ("https://www.youtube.com/user/PewDiePie", {"path": "/user/PewDiePie"}),
    ("https://www.youtube.com/mrbeast", {"path": "/mrbeast"}),
])
def test_resolve_channel(value, expected):
    assert refs.resolve_channel(value) == expected


@pytest.mark.parametrize("value", ["", "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "https://twitch.tv/x", "a b"])
def test_resolve_channel_rejects(value):
    with pytest.raises(ValueError):
        refs.resolve_channel(value)


def test_resolve_other_refs():
    assert refs.resolve_playlist("https://www.youtube.com/watch?v=x&list=PLFgquLnL59alCl_2TQvOiD5Vgm1hCaGSI") == \
        "PLFgquLnL59alCl_2TQvOiD5Vgm1hCaGSI"
    assert refs.resolve_playlist("VLPLFgquLnL59alCl_2TQvOiD5Vgm1hCaGSI") == "PLFgquLnL59alCl_2TQvOiD5Vgm1hCaGSI"
    assert refs.resolve_post("https://www.youtube.com/post/UgkxzJ6r7VLAK03TN5Gjq7YpjHmjtee_Rdn9") == \
        "UgkxzJ6r7VLAK03TN5Gjq7YpjHmjtee_Rdn9"
    assert refs.resolve_post("https://www.youtube.com/channel/UCX/community?lb=UgkxzJ6r7VLAK03TN5Gjq7YpjHmjtee_Rdn9") == \
        "UgkxzJ6r7VLAK03TN5Gjq7YpjHmjtee_Rdn9"
    assert refs.resolve_hashtag("#LoFi") == "lofi" == refs.resolve_hashtag("https://www.youtube.com/hashtag/lofi")
    assert refs.resolve_videos("dQw4w9WgXcQ, https://youtu.be/dQw4w9WgXcQ,kJQP7kiw5Fk") == ["dQw4w9WgXcQ", "kJQP7kiw5Fk"]
    with pytest.raises(ValueError):
        refs.resolve_videos(",".join(["dQw4w9WgXcQ"] * 1 + [f"{i:011d}" for i in range(21)]))


def test_resolve_link():
    assert refs.resolve_link("https://youtu.be/dQw4w9WgXcQ?t=1m30s") == {"kind": "video", "id": "dQw4w9WgXcQ", "start_seconds": 90}
    assert refs.resolve_link("https://www.youtube.com/shorts/T_SMf9j50uc")["kind"] == "short"
    assert refs.resolve_link("https://www.youtube.com/playlist?list=PLFgquLnL59alCl_2TQvOiD5Vgm1hCaGSI")["kind"] == "playlist"
    assert refs.resolve_link("https://www.youtube.com/@MrBeast") == {"kind": "channel", "path": "/@MrBeast"}
    assert refs.resolve_link("https://www.youtube.com/hashtag/lofi") == {"kind": "hashtag", "id": "lofi"}


def test_schemas_single_param_per_input():
    for schema in (schemas.VideoSchema, schemas.ChannelSchema, schemas.PlaylistSchema, schemas.PostSchema):
        names = set(schema().fields)
        assert not names & {"url", "id", "video_id", "channel_id", "link"}, schema
    assert schemas.SearchSchema().load({"query": "x", "type": "short", "features": "4K, HD"})["features"] == ["4k", "hd"]
    with pytest.raises(Exception):
        schemas.SearchSchema().load({"query": "x", "features": "8k"})
    with pytest.raises(Exception):
        schemas.ChartsSchema().load({"chart": "top_artists", "period": "daily"})
    with pytest.raises(Exception):
        schemas.VideoSchema().load({"video": "dQw4w9WgXcQ", "foo": "1"})


# ---- protobuf builders ---------------------------------------------------------------------

def _bytes(token):
    token = unquote(token)
    return base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))


def test_comments_token_matches_youtube():
    # the exact first-page token YouTube itself put on the watch page of kJQP7kiw5Fk
    assert _bytes(protos.comments_token("kJQP7kiw5Fk", "top")) == _bytes(
        "Eg0SC2tKUVA3a2l3NUZrGAYyJSIRIgtrSlFQN2tpdzVGazAAeAJCEGNvbW1lbnRzLXNlY3Rpb24%3D")
    assert protos.comments_token("kJQP7kiw5Fk", "newest") != protos.comments_token("kJQP7kiw5Fk", "top")


def test_search_and_hashtag_params():
    assert protos.search_params() is None
    assert unquote(protos.search_params(type="video")) == "EgIQAQ=="
    assert unquote(protos.search_params(type="shorts")) == "EgIQCQ=="
    assert unquote(protos.search_params(type="channel")) == "EgIQAg=="
    assert unquote(protos.search_params(sort="popularity", type="video")) == "CAMSAhAB"
    assert unquote(protos.hashtag_params("lofi")) == "6gUKCgRsb2ZpEAAYAQ=="


# ---- values -------------------------------------------------------------------------------

@pytest.mark.parametrize("value, expected", [
    ("1,820,537,172 views", 1820537172), ("1.8B views", 1800000000), ("986 million views", 986000000),
    ("86K", 86000), ("4.55M subscribers", 4550000), ("No views", 0), ("973 replies", 973), ("12K watching", 12000),
    (213, 213), ("", None), (None, None), ("Mix", None),
])
def test_count(value, expected):
    assert V.count(value) == expected


def test_durations_and_dates():
    assert V.duration_seconds("1:02:03") == 3723 and V.duration_seconds("3:18") == 198
    assert V.duration_seconds("3 minutes, 18 seconds") == 198 and V.duration_seconds("LIVE") is None
    assert V.iso_datetime("2009-10-24T23:57:33-07:00") == "2009-10-25T06:57:33Z"
    assert V.written_date("Joined Feb 19, 2012") == "2012-02-19"
    assert V.written_date("Last updated on Jul 17, 2026") == "2026-07-17"
    assert V.written_date("2 days ago") is None
    assert V.unwrap_redirect("https://www.youtube.com/redirect?event=x&q=https%3A%2F%2Fexample.com%2F") == "https://example.com/"


def test_money():
    assert S.money("$5.00") == {"amount": 5.0, "currency": "USD", "text": "$5.00"}
    assert S.money("CA$10.00")["currency"] == "CAD"
    assert S.money("1.000,00 €")["amount"] == 1000.0
    assert S.money("₹1,000.00") == {"amount": 1000.0, "currency": "INR", "text": "₹1,000.00"}


# ---- videos -------------------------------------------------------------------------------

def test_video_details():
    video = P.video_details(fx("player_web_aircAruvnKk"), fx("player_android_aircAruvnKk"), fx("next_aircAruvnKk"))
    _clean_keys(video)
    _no_blank_strings(video)
    assert video["id"] == "aircAruvnKk" and video["duration_seconds"] == 1120
    assert video["view_count"] > 20_000_000 and video["like_count"] > 500_000 and video["comment_count"] > 1000
    assert video["published_at"] == "2017-10-05T15:11:25Z" and video["category"] == "Education"
    assert video["channel"]["id"] == "UCYO_jab_esuFRV4b17AJtAw" and video["channel"]["handle"] == "@3blue1brown"
    assert video["channel"]["is_verified"] and video["channel"]["subscriber_count"] > 1_000_000
    assert len(video["chapters"]) == 12 and video["chapters"][0] == {
        "title": "Introduction example", "start_seconds": 0, "end_seconds": 67,
        "thumbnail": video["chapters"][0]["thumbnail"]}
    assert video["chapters"][-1]["end_seconds"] == 1120
    assert len(video["heatmap"]) == 100 and 0 <= video["heatmap"][0]["intensity"] <= 1
    assert video["series"]["id"] == "PLZHQObOWTQDNU6R1_67000Dx_ZCJB-3pi"
    assert any(link["type"] == "timestamp" for link in video["description_links"])
    assert video["subtitles"] and video["end_screen"] and video["playability"]["status"] == "OK"
    assert len(video["available_countries"]) > 200 and not video["is_live"]


def test_video_details_music_and_live():
    music = P.video_details({}, {}, fx("next_kJQP7kiw5Fk"))
    assert music["music"][0] == {"title": "Despacito", "artist": "Luis Fonsi", "album": "Despacito",
                                 "thumbnail": music["music"][0]["thumbnail"]}
    assert music["comment_count"] == 4_300_000 and music["hashtags"][0] == "Despacito"
    live = P.video_details(fx("player_web_live"), {}, fx("next_live"))
    assert live["is_live"] and live["watching_count"] > 0 and live["live_broadcast"]["started_at"]
    assert live["are_comments_enabled"] is False
    age = P.video_details(fx("player_web_age"), {}, {})
    assert age["is_age_restricted"] and age["title"]


def test_streams_and_storyboards():
    android = fx("player_android_aircAruvnKk")
    streams = P.streams(android)
    _clean_keys(streams)
    assert streams["muxed"] and streams["video_only"] and streams["audio_only"]
    top = streams["video_only"][0]
    assert top["height"] >= 1080 and top["link"].startswith("https://") and top["audio_channels"] is None
    audio = streams["audio_only"][0]
    assert audio["width"] is None and audio["audio_sample_rate"] and audio["container"] in ("webm", "mp4")
    boards = P.storyboards(android, 1120)
    assert boards and boards[-1]["sheets"][0].startswith("https://i.ytimg.com/sb/aircAruvnKk/")


def test_captions_and_transcript():
    android = fx("player_android_aircAruvnKk")
    tracks, _ = P.caption_tracks(android)
    assert any(t["language_code"] == "en" and not t["is_auto_generated"] for t in tracks)
    assert all(t["link"].endswith("&fmt=vtt") for t in tracks)
    chosen, _ = P.base_caption_url(android)
    assert chosen["languageCode"] == "en"
    assert P.base_caption_url(android, "xx")[0] is None
    segments = P.json3_segments({"events": [{"tStartMs": 1360, "dDurationMs": 1680, "segs": [{"utf8": "[♪♪♪]"}]},
                                            {"tStartMs": 3000, "dDurationMs": 1000, "segs": [{"utf8": "\n"}]}]})
    assert segments == [{"start_seconds": 1.36, "end_seconds": 3.04, "duration_seconds": 1.68, "text": "[♪♪♪]"}]
    assert P.to_srt(segments).startswith("1\n00:00:01,360 --> 00:00:03,040\n[♪♪♪]")
    assert P.to_vtt(segments).startswith("WEBVTT\n\n00:00:01.360 --> 00:00:03.040")


def test_comments():
    total, rows, cursor = S.comments_page(fx("comments_first_page"), link_base=refs.video_link("dQw4w9WgXcQ"))
    _clean_keys(rows)
    assert total > 2_000_000 and len(rows) == 20 and cursor
    pinned = rows[0]
    assert pinned["is_pinned"] and pinned["is_hearted"] and pinned["author"]["handle"] == "@YouTube"
    assert pinned["like_count"] > 1000 and pinned["reply_count"] > 100 and pinned["replies_cursor"]
    assert pinned["link"].endswith("&lc=" + pinned["id"])
    _, replies, reply_cursor = S.comments_page(fx("comment_replies"))
    assert replies and all(r["reply_level"] == 1 for r in replies) and reply_cursor


def test_live_chat():
    messages, cursor, wait = S.chat_page(fx("live_chat"))
    _clean_keys(messages)
    assert len(messages) > 20 and cursor and wait
    assert messages[0]["type"] == "text" and messages[0]["published_at"].endswith("Z")
    assert messages[0]["author"]["id"].startswith("UC")


# ---- lists ----------------------------------------------------------------------------------

def _types(rows):
    return {r["type"] for r in rows}


def test_item_lists():
    videos = I.collect(fx("channel_videos_tab")["contents"])
    assert len(videos) == 30 and _types(videos) == {"video"} and videos[0]["view_count"] and videos[0]["duration_seconds"]
    shorts = I.collect(fx("channel_shorts_tab")["contents"])
    assert _types(shorts) == {"short"} and shorts[0]["view_count"] and shorts[0]["title"]
    releases = I.collect(fx("channel_releases_tab")["contents"])
    assert releases[0]["kind"] == "album" and releases[0]["videos"] and releases[0]["release_date"]
    search = I.collect(fx("search_all")["contents"])
    assert {"video", "short", "channel"} <= _types(search)
    channels = I.collect(fx("search_channels")["contents"])
    assert channels[0]["handle"] == "@LofiGirl" and channels[0]["subscriber_count"] > 1_000_000
    movies = [r for r in I.collect(fx("search_movies")["contents"]) if r["type"] == "movie"]
    assert movies and movies[0]["duration_seconds"] and movies[0]["year"]
    playlist = I.collect(fx("playlist_browse")["contents"])
    assert len([r for r in playlist if r["type"] == "video"]) == 100
    related = I.collect(I.continuation_items(fx("related_continuation")))
    assert related and I.next_cursor(I.continuation_items(fx("related_continuation")))
    tagged = I.collect(fx("hashtag_browse")["contents"])
    assert tagged and _types(tagged) == {"video"}
    for rows in (videos, shorts, releases, search, channels, movies, related):
        _clean_keys(rows)
        _no_blank_strings(rows)


def test_sections():
    home = PP.selected_tab(fx("channel_home_tab"))[1]["content"]
    sections = I.sections(home)
    titles = [s["title"] for s in sections]
    assert titles[0] is None and "Shorts" in titles and "Posts" in titles and "Featured Channels" in titles
    movies = I.sections(fx("explore_movies")["contents"])
    assert movies and any(i["type"] == "movie" for s in movies for i in s["items"])


def test_cursor_skips_reply_and_sort_stubs():
    data = fx("comments_first_page")
    token = I.next_cursor(I.continuation_items(data))
    assert token and token == S.comments_page(data)[2]


# ---- pages ----------------------------------------------------------------------------------

def test_channel_details():
    channel = PP.channel_details(fx("channel_browse"), fx("channel_about"))
    _clean_keys(channel)
    _no_blank_strings(channel)
    assert channel["id"] == "UCX6OQ3DkcsbYNE6H8uQQuVA" and channel["handle"] == "@MrBeast"
    assert channel["joined_date"] == "2012-02-19" and channel["country"] == "United States"
    assert channel["view_count"] > 100_000_000_000 and channel["video_count"] > 900
    assert channel["is_verified"] and channel["has_business_email"]
    assert channel["links"][0]["link"].startswith("https://") and "redirect" not in channel["links"][0]["link"]
    assert {"key": "videos", "title": "Videos"} in channel["tabs"]
    assert PP.selected_tab(fx("channel_live_tab"))[0] == "live"
    assert PP.selected_tab(fx("channel_live_missing_tab"))[0] == "home"      # no Live tab -> YouTube falls back to Home


def test_playlist_hashtag_sound():
    playlist = PP.playlist_details(fx("playlist_browse"), "PLFgquLnL59alCl_2TQvOiD5Vgm1hCaGSI")
    assert playlist["video_count"] > 100 and playlist["view_count"] > 1_000_000_000
    assert playlist["updated_date"] and playlist["channel"]["id"] == "UC-9-kyTW8ZkZNDHQJ6FgpwQ"
    tag = PP.hashtag_header(fx("hashtag_browse"), "lofi")
    assert tag["video_count"] > 1_000_000 and tag["channel_count"] > 100_000
    page = fx("shorts_sound_page")
    sound = PP.sound_header(page, "T_SMf9j50uc")
    assert sound["title"] and sound["shorts_count"] and sound["creator"] == "@MrBeast"
    assert _types(I.collect(page["contents"])) == {"short"}


def test_posts():
    posts = S.posts_in(fx("channel_posts_tab"))
    assert len(posts) >= 10
    poll = next(p for p in posts if p["attachment"] and p["attachment"]["type"] == "poll")
    assert poll["attachment"]["poll"]["total_votes"] > 0 and len(poll["attachment"]["poll"]["choices"]) >= 2
    assert poll["like_count"] > 1000 and poll["comment_count"] > 100
    detail = S.posts_in(fx("post_browse"))[0]
    assert detail["id"] == "UgkxzJ6r7VLAK03TN5Gjq7YpjHmjtee_Rdn9" and detail["author"]["handle"] == "@MrBeast"
    total, rows, cursor = S.comments_page(fx("post_comments"), link_base=refs.post_link(detail["id"]))
    assert rows and cursor and rows[0]["link"].startswith("https://www.youtube.com/post/")


def test_charts():
    for name, kind in (("charts_top_videos", "video"), ("charts_top_songs", "song"),
                       ("charts_top_artists", "artist"), ("charts_top_podcasts", "podcast")):
        meta, rows = PP.chart_rows(fx(name), None)
        _clean_keys(rows)
        assert rows and rows[0]["type"] == kind and rows[0]["rank"] == 1, name
        assert meta["end_date"] and meta["period"] == "weekly"
    _, videos = PP.chart_rows(fx("charts_top_videos"), None)
    assert videos[0]["artists"] and videos[0]["view_count"] and videos[0]["release_date"]


def test_suggestions():
    rows = PP.suggestions(fx("suggest"))
    assert rows and all(isinstance(r, str) and r for r in rows)
