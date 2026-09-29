"""Live endpoint smoke tests: one call per /youtube/* route against a running
service, with example values proven to return data (2026-09-27), plus
every cursor and the main 400 / 404 paths. The listing tooling reads each
route's FIRST call from this file's AST as its working example, so the
values stay literals.

Skipped unless YOUTUBE_BASE points at a running service:

    ONLY_SCRAPER=youtube SCRAPERS_PORT=6071 python run.py
    YOUTUBE_BASE=http://127.0.0.1:6071 python -m pytest youtube/test_endpoints.py -q
"""
import os

import pytest

BASE = os.environ.get("YOUTUBE_BASE", "").rstrip("/")

pytestmark = pytest.mark.skipif(not BASE, reason="set YOUTUBE_BASE to run live endpoint tests")


def call(path, status=200, **params):
    from curl_cffi import requests
    resp = requests.get(BASE + path, params=params, timeout=180)
    assert resp.status_code == status, f"{path} {params} -> {resp.status_code} {resp.text[:300]}"
    body = resp.json()
    assert body, f"{path} returned an empty body"
    return body


def test_videos():
    video = call("/youtube/videos/details", video="dQw4w9WgXcQ")["video"]
    assert video["view_count"] > 1_000_000_000 and video["like_count"] > 1_000_000
    assert video["channel"]["id"] == "UCuAXFkgsw1L7xaCfnd5JJOw" and video["published_at"].startswith("2009-10-25")
    assert video["subtitles"] and video["heatmap"] and video["end_screen"]
    chaptered = call("/youtube/videos/details", video="https://www.youtube.com/watch?v=aircAruvnKk&t=67s")["video"]
    assert len(chaptered["chapters"]) >= 10 and chaptered["series"]["id"]
    short = call("/youtube/videos/details", video="https://www.youtube.com/shorts/T_SMf9j50uc")["video"]
    assert short["is_short"] and short["link"].endswith("/shorts/T_SMf9j50uc")
    call("/youtube/videos/details", status=404, video="aaaaaaaaaaa")
    call("/youtube/videos/details", status=400, video="https://vimeo.com/123")
    batch = call("/youtube/videos/details/batch", videos="dQw4w9WgXcQ,kJQP7kiw5Fk,aaaaaaaaaaa")
    assert batch["count"] == 3 and batch["found_count"] == 2 and batch["videos"][2]["error"] == "unavailable"


def test_streams_and_subtitles():
    streams = call("/youtube/videos/streams", video="dQw4w9WgXcQ")
    assert streams["muxed"] and streams["video_only"] and streams["audio_only"] and streams["storyboards"]
    assert streams["audio_only"][0]["link"].startswith("https://")
    subtitles = call("/youtube/videos/subtitles", video="aircAruvnKk")
    assert subtitles["count"] > 5 and subtitles["subtitles"][0]["link"]
    transcript = call("/youtube/videos/transcript", video="aircAruvnKk")
    assert transcript["language_code"] == "en" and transcript["segments"] and transcript["text"]
    srt = call("/youtube/videos/transcript", video="dQw4w9WgXcQ", language="en", format="srt")
    assert srt["content"].startswith("1\n") and "segments" not in srt
    call("/youtube/videos/transcript", status=404, video="dQw4w9WgXcQ", language="xx")


def test_comments():
    page = call("/youtube/videos/comments", video="dQw4w9WgXcQ", sort="top")
    assert page["total_count"] > 1_000_000 and page["comments"] and page["next_cursor"]
    assert call("/youtube/videos/comments", video="dQw4w9WgXcQ", cursor=page["next_cursor"])["comments"]
    newest = call("/youtube/videos/comments", video="aircAruvnKk", sort="newest")
    assert newest["comments"] and newest["sort"] == "newest"
    parent = next(c for c in page["comments"] if c["replies_cursor"])
    replies = call("/youtube/comments/replies", cursor=parent["replies_cursor"])
    assert replies["replies"] and replies["replies"][0]["reply_level"] == 1
    if replies["next_cursor"]:
        assert call("/youtube/comments/replies", cursor=replies["next_cursor"])["replies"]
    call("/youtube/comments/replies", status=400)


def test_related_and_live_chat():
    related = call("/youtube/videos/related", video="dQw4w9WgXcQ")
    assert related["items"] and related["next_cursor"]
    assert call("/youtube/videos/related", video="dQw4w9WgXcQ", cursor=related["next_cursor"])["items"]
    live = call("/youtube/search", query="news live", type="video", features="live")["results"]
    live_id = next(r["id"] for r in live if r.get("is_live"))
    chat = call("/youtube/videos/live-chat", video=live_id)
    assert "messages" in chat and chat["is_replay"] is False
    call("/youtube/videos/live-chat", status=404, video="dQw4w9WgXcQ")


def test_channels():
    channel = call("/youtube/channels/details", channel="@MrBeast")["channel"]
    assert channel["id"] == "UCX6OQ3DkcsbYNE6H8uQQuVA" and channel["view_count"] > 10_000_000_000
    assert channel["joined_date"] == "2012-02-19" and channel["links"]
    assert call("/youtube/channels/details", channel="https://www.youtube.com/channel/UCuAXFkgsw1L7xaCfnd5JJOw")["channel"]["is_artist"]
    call("/youtube/channels/details", status=404, channel="@doesnotexist_zz_9999")
    videos = call("/youtube/channels/videos", channel="@MrBeast", sort="popular")
    assert len(videos["videos"]) >= 20 and videos["next_cursor"]
    assert call("/youtube/channels/videos", channel="@MrBeast", cursor=videos["next_cursor"])["videos"]
    shorts = call("/youtube/channels/shorts", channel="@MrBeast")
    assert shorts["shorts"] and shorts["next_cursor"]
    assert call("/youtube/channels/live", channel="UCuAXFkgsw1L7xaCfnd5JJOw")["has_tab"]
    assert call("/youtube/channels/live", channel="@MrBeast")["has_tab"] is False
    assert call("/youtube/channels/playlists", channel="UCuAXFkgsw1L7xaCfnd5JJOw")["playlists"]
    releases = call("/youtube/channels/playlists", channel="UCuAXFkgsw1L7xaCfnd5JJOw", type="releases")
    assert releases["playlists"][0]["kind"] == "album"
    posts = call("/youtube/channels/posts", channel="@MrBeast")
    assert posts["posts"] and posts["next_cursor"]
    assert call("/youtube/channels/posts", channel="@MrBeast", cursor=posts["next_cursor"])["posts"]
    home = call("/youtube/channels/home", channel="UCsooa4yRKGN_zEE8iknghZA")
    assert len(home["sections"]) > 5
    found = call("/youtube/channels/search", channel="@MrBeast", query="island")
    assert found["items"]


def test_playlists_and_posts():
    playlist = call("/youtube/playlists/details", playlist="PLFgquLnL59alCl_2TQvOiD5Vgm1hCaGSI")["playlist"]
    assert playlist["video_count"] > 50 and playlist["view_count"] > 1_000_000
    page = call("/youtube/playlists/videos", playlist="https://www.youtube.com/playlist?list=PLFgquLnL59alCl_2TQvOiD5Vgm1hCaGSI")
    assert len(page["videos"]) == 100 and page["next_cursor"]
    second = call("/youtube/playlists/videos", playlist="PLFgquLnL59alCl_2TQvOiD5Vgm1hCaGSI", cursor=page["next_cursor"])
    assert second["videos"] and second["videos"][0]["position"] == 101
    call("/youtube/playlists/details", status=404, playlist="PLxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx")
    post = call("/youtube/posts/details", post="UgkxzJ6r7VLAK03TN5Gjq7YpjHmjtee_Rdn9")["post"]
    assert post["author"]["id"] == "UCX6OQ3DkcsbYNE6H8uQQuVA" and post["like_count"] > 0
    comments = call("/youtube/posts/comments", post="https://www.youtube.com/post/UgkxzJ6r7VLAK03TN5Gjq7YpjHmjtee_Rdn9")
    assert comments["comments"] and comments["next_cursor"]
    parent = next((c for c in comments["comments"] if c["replies_cursor"]), None)
    if parent:
        assert call("/youtube/comments/replies", cursor=parent["replies_cursor"])["replies"]


def test_search():
    page = call("/youtube/search", query="lofi")
    assert page["results"] and page["next_cursor"] and page["estimated_total_count"] > 0
    assert call("/youtube/search", query="lofi", cursor=page["next_cursor"])["results"]
    shorts = call("/youtube/search", query="lofi", type="short")
    assert shorts["results"] and all(r["type"] == "short" for r in shorts["results"])
    channels = call("/youtube/search", query="lofi", type="channel")
    assert all(r["type"] == "channel" for r in channels["results"])
    filtered = call("/youtube/search", query="lofi", type="video", upload_date="week", duration="over_20_minutes",
                    sort="popularity", features="4k,hd")
    assert filtered["results"]
    call("/youtube/search", status=400, query="lofi", features="8k")
    call("/youtube/search", status=400, query="lofi", cursor="not-a-real-cursor")
    assert call("/youtube/search/suggestions", query="lofi")["suggestions"]
    tag = call("/youtube/hashtags/videos", hashtag="lofi")
    assert tag["hashtag"]["video_count"] > 1000 and tag["videos"] and tag["next_cursor"]
    assert call("/youtube/hashtags/videos", hashtag="#lofi", cursor=tag["next_cursor"])["videos"]
    assert all(v["type"] == "short" for v in call("/youtube/hashtags/videos", hashtag="lofi", type="shorts")["videos"])


def test_discovery():
    chart = call("/youtube/charts", chart="top_videos", country="US")
    assert chart["count"] == 100 and chart["entries"][0]["rank"] == 1
    assert call("/youtube/charts", chart="top_songs", country="global")["entries"]
    assert call("/youtube/charts", chart="trending_videos", country="IN")["entries"]
    call("/youtube/charts", status=400, chart="top_artists", period="daily")
    news = call("/youtube/explore", feed="news")
    assert news["sections"] and news["count"] > 20
    sound = call("/youtube/shorts/sound", video="T_SMf9j50uc")
    # shorts_count is absent in one of the page variants YouTube A/B-serves (15 or 24 Shorts)
    assert len(sound["shorts"]) > 10 and sound["sound"]["title"] and sound["sound"]["creator"] == "@MrBeast"
    resolved = call("/youtube/resolve", link="https://www.youtube.com/@MrBeast")
    assert resolved["type"] == "channel" and resolved["id"] == "UCX6OQ3DkcsbYNE6H8uQQuVA"
    assert call("/youtube/resolve", link="https://youtu.be/dQw4w9WgXcQ?t=42")["start_seconds"] == 42
