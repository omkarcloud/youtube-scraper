"""The 28 YouTube endpoints. Every path is served with and without the
`/youtube` prefix, so code generated against the hosted API on RapidAPI
(paths like /videos/details) runs unchanged against this server.

Params are validated by the marshmallow schemas in youtube/schemas.py:
unknown or invalid params answer 400 with the reason. Lists page with the
`next_cursor` each response carries (pass it back as `cursor`)."""
import json

from bottle import request, response, route

from schema_fields import load_query
from scraper_errors import BadRequest, NotFound
from youtube import channels, discover, playlists, posts, search, videos
from youtube import schemas as S


def json_response(data, status=200):
    response.status = status
    response.content_type = "application/json"
    return json.dumps(data, ensure_ascii=False)


def query_dict():
    """The query as unicode strings (bottle 0.12's .get() hands back latin-1
    decoded bytes, so a UTF-8 "Beyoncé" would arrive as "BeyoncÃ©")."""
    return {key: request.query.getunicode(key) for key in request.query.keys()}


def call(label, schema, fn):
    """Validate, run, map errors: bad params -> 400, missing entity -> 404,
    retries exhausted / blocked -> 500."""
    data, error = load_query(schema, query_dict())
    if error:
        return json_response(error, 400)
    try:
        result = fn(**data)
    except ValueError as e:
        return json_response({"error": str(e)}, 400)
    except BadRequest as e:
        return json_response({"error": f"youtube rejected the request: {e}"}, 400)
    except NotFound as e:
        return json_response({"error": str(e) or "not found"}, 404)
    except Exception as e:
        return json_response({"error": f"youtube {label} failed: {e}"}, 500)
    return json_response(result)


ENDPOINTS = [
    # (path, schema, function)
    ("/videos/details", S.VideoSchema, videos.details),
    ("/search/suggestions", S.SuggestionsSchema, search.suggestions),
    ("/search", S.SearchSchema, search.search),
    ("/videos/details/batch", S.VideosSchema, videos.batch),
    ("/videos/comments", S.CommentsSchema, videos.comments),
    ("/comments/replies", S.RepliesSchema, videos.replies),
    ("/videos/transcript", S.TranscriptSchema, videos.transcript),
    ("/videos/subtitles", S.SubtitlesSchema, videos.subtitles),
    ("/videos/related", S.RelatedSchema, videos.related),
    ("/videos/streams", S.StreamsSchema, videos.streams),
    ("/videos/live-chat", S.LiveChatSchema, videos.live_chat),
    ("/channels/details", S.ChannelSchema, channels.details),
    ("/channels/videos", S.ChannelVideosSchema, channels.videos),
    ("/channels/shorts", S.ChannelVideosSchema, channels.shorts),
    ("/channels/live", S.ChannelVideosSchema, channels.live),
    ("/channels/playlists", S.ChannelPlaylistsSchema, channels.playlists),
    ("/channels/posts", S.ChannelPagedSchema, channels.posts),
    ("/channels/home", S.ChannelSchema, channels.home),
    ("/channels/search", S.ChannelSearchSchema, channels.search),
    ("/playlists/details", S.PlaylistSchema, playlists.details),
    ("/playlists/videos", S.PlaylistVideosSchema, playlists.videos),
    ("/posts/details", S.PostSchema, posts.details),
    ("/posts/comments", S.PostCommentsSchema, posts.comments),
    ("/hashtags/videos", S.HashtagSchema, search.hashtag),
    ("/shorts/sound", S.ShortsSoundSchema, discover.shorts_sound),
    ("/charts", S.ChartsSchema, discover.charts),
    ("/explore", S.ExploreSchema, discover.explore),
    ("/resolve", S.ResolveSchema, discover.resolve),
]


def mount(path, schema, fn):
    """Serve a handler at /path and /youtube/path."""
    def handler():
        return call(path.strip("/"), schema, fn)
    handler.__name__ = "youtube_" + path.strip("/").replace("/", "_").replace("-", "_")
    route(path, method="GET")(handler)
    route("/youtube" + path, method="GET")(handler)


for _path, _schema, _fn in ENDPOINTS:
    mount(_path, _schema, _fn)


@route("/", method="GET")
@route("/health", method="GET")
def health():
    return json_response({"status": "ok", "endpoints": [p for p, _, _ in ENDPOINTS]})
