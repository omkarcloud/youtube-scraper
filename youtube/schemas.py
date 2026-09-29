"""Marshmallow request schemas for every /youtube/* route. Generic fields
come from the shared schema_fields.py; this module adds the YouTube
resolvers. Every schema's load() output is the kwargs dict its endpoint
function takes.

ONE param per input (tripadvisor QueryOrIdField convention, never a
sibling `url` / `id` pair):

    video      an 11-character id (dQw4w9WgXcQ) OR any watch / youtu.be /
               shorts / live / embed link
    videos     comma-separated ids or links (max 20)
    channel    a channel id (UC...), a handle (@MrBeast or MrBeast) OR any
               channel link (/@handle, /channel/UC..., /c/name, /user/name)
    playlist   a playlist id (PL..., OLAK5uy_..., RD..., UU...) OR any link with ?list=
    post       a community post id (Ugkx...) OR a /post link
    hashtag    lofi / #lofi OR a /hashtag link
    link       any YouTube link (for /youtube/resolve)

Locale on every data route: `country` (ISO 3166 alpha-2, default US) and
`language` (ISO 639-1, default en). Paged routes take the opaque `cursor`
returned as next_cursor by the previous page.
"""
from marshmallow import ValidationError, missing, validates_schema

from schema_fields import (BaseSchema, ChoiceField, CommaListField, CountryCodeField, LanguageCodeField,
                           QueryField, RefField, StrippedString)
from youtube import protos, refs


# ---- id-or-link fields ------------------------------------------------------------------

class VideoField(RefField):
    resolver = staticmethod(refs.resolve_video)


class VideoListField(RefField):
    resolver = staticmethod(refs.resolve_videos)


class ChannelField(RefField):
    resolver = staticmethod(refs.resolve_channel)


class PlaylistField(RefField):
    resolver = staticmethod(refs.resolve_playlist)


class PostField(RefField):
    resolver = staticmethod(refs.resolve_post)


class HashtagField(RefField):
    resolver = staticmethod(refs.resolve_hashtag)


class LinkField(RefField):
    resolver = staticmethod(refs.resolve_link)


class CursorField(StrippedString):
    """Opaque next_cursor from the previous page (absent = first page)."""

    def __init__(self, **kwargs):
        kwargs.setdefault("required", False)
        kwargs.setdefault("load_default", None)
        super().__init__(**kwargs)


def _country():
    return CountryCodeField(load_default="US", metadata={"description": "ISO country code (results region), default US"})


def _language():
    return LanguageCodeField(allow_locale=False, load_default="en",
                             metadata={"description": "ISO 639-1 language of the interface text, default en"})


class LocaleSchema(BaseSchema):
    country = _country()
    language = _language()


# ---- videos ------------------------------------------------------------------------------

class VideoSchema(LocaleSchema):
    video = VideoField()


class VideosSchema(LocaleSchema):
    videos = VideoListField()


class StreamsSchema(BaseSchema):
    video = VideoField()
    country = _country()


class CommentsSchema(LocaleSchema):
    video = VideoField()
    sort = ChoiceField(["top", "newest"], load_default="top")
    cursor = CursorField()


class RepliesSchema(LocaleSchema):
    cursor = CursorField(required=True, load_default=missing,
                         metadata={"description": "replies_cursor of a comment from /comments"})


class RelatedSchema(LocaleSchema):
    video = VideoField()
    cursor = CursorField()


class SubtitlesSchema(BaseSchema):
    video = VideoField()


class TranscriptSchema(BaseSchema):
    video = VideoField()
    language = StrippedString(load_default=None, metadata={
        "description": "subtitle track language code (en, es, pt-BR, ...); default: the video's main track"})
    format = ChoiceField(["json", "text", "srt", "vtt"], load_default="json")

    @validates_schema
    def _codes(self, data, **kwargs):
        for key in ("language",):
            value = data.get(key)
            if value and not (2 <= len(value) <= 10 and value.replace("-", "").isalnum()):
                raise ValidationError("Must be a language code such as en, es or pt-BR.", key)


class LiveChatSchema(BaseSchema):
    video = VideoField()
    type = ChoiceField(["top", "all"], load_default="top")
    cursor = CursorField()


# ---- shorts ------------------------------------------------------------------------------

class ShortsSoundSchema(LocaleSchema):
    video = VideoField(metadata={"description": "a Short (id or link) that uses the sound"})


# ---- channels ------------------------------------------------------------------------------

class ChannelSchema(LocaleSchema):
    channel = ChannelField()


class ChannelPagedSchema(ChannelSchema):
    cursor = CursorField()


class ChannelVideosSchema(ChannelPagedSchema):
    sort = ChoiceField(["latest", "popular", "oldest"], load_default="latest")


class ChannelPlaylistsSchema(ChannelPagedSchema):
    type = ChoiceField(["playlists", "releases", "podcasts", "courses"], load_default="playlists")


class ChannelSearchSchema(ChannelPagedSchema):
    query = QueryField(max_length=200)


# ---- playlists / posts ------------------------------------------------------------------------

class PlaylistSchema(LocaleSchema):
    playlist = PlaylistField()


class PlaylistVideosSchema(PlaylistSchema):
    cursor = CursorField()


class PostSchema(LocaleSchema):
    post = PostField()


class PostCommentsSchema(PostSchema):
    sort = ChoiceField(["top", "newest"], load_default="top")
    cursor = CursorField()


# ---- search / discovery ------------------------------------------------------------------------

class SearchSchema(LocaleSchema):
    query = QueryField(max_length=300)
    type = ChoiceField(["all", "video", "short", "channel", "playlist", "movie"], load_default="all")
    upload_date = ChoiceField(list(protos.SEARCH_UPLOAD_DATES))
    duration = ChoiceField(list(protos.SEARCH_DURATIONS))
    sort = ChoiceField(list(protos.SEARCH_SORTS), load_default="relevance")
    features = CommaListField(allowed=list(protos.SEARCH_FEATURES), upper=False, max_items=len(protos.SEARCH_FEATURES))
    cursor = CursorField()


class SuggestionsSchema(LocaleSchema):
    query = QueryField(max_length=200)


class HashtagSchema(LocaleSchema):
    hashtag = HashtagField()
    type = ChoiceField(["all", "shorts"], load_default="all")
    cursor = CursorField()


CHARTS = {
    "top_videos": ("VIDEOS", ("weekly", "daily")),
    "trending_videos": ("TRENDING_VIDEOS", ("daily",)),
    "top_songs": ("TRACKS", ("weekly",)),
    "top_artists": ("ARTISTS", ("weekly",)),
    "top_shorts_songs": ("SHORTS_TRACKS_BY_USAGE", ("weekly", "daily")),
    "top_podcasts": ("PODCAST_SHOWS_BY_WATCH_TIME", ("weekly",)),
}


class ChartsSchema(BaseSchema):
    chart = ChoiceField(list(CHARTS), load_default="top_videos")
    country = StrippedString(load_default="US", metadata={"description": "ISO country code or 'global'"})
    period = ChoiceField(["weekly", "daily"], load_default=None)

    @validates_schema
    def _chart_period(self, data, **kwargs):
        from schema_fields import COUNTRY_CODES
        country = (data.get("country") or "US").upper()
        if country not in COUNTRY_CODES and country not in ("GLOBAL", "ZZ"):
            raise ValidationError("Must be a 2-letter ISO country code (US, IN, GB, ...) or 'global'.", "country")
        periods = CHARTS[data.get("chart") or "top_videos"][1]
        if data.get("period") and data["period"] not in periods:
            raise ValidationError(f"{data['chart']} is only published {' / '.join(periods)}.", "period")


FEEDS = {
    "news": "FEnews_destination",
    "live": "FElive_destination",
    "sports": "FEsports_destination",
    "learning": "FElearning_destination",
    "courses": "FEcourses_destination",
    "gaming": "FEgaming_destination",
    "movies": "FEstorefront",
    "shopping": "FEshopping_destination",
}


class ExploreSchema(LocaleSchema):
    feed = ChoiceField(list(FEEDS), required=True, load_default=missing)


class ResolveSchema(BaseSchema):
    link = LinkField()
