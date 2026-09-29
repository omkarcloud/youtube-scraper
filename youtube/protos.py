"""Tiny protobuf encoder for the InnerTube `params` / continuation blobs we
build ourselves instead of paying a round trip to read them off a page
(all layouts decoded from live tokens 2026-09-27):

    comments_token(video_id, sort)   first comments page of a video
                                     {2:{2:vid}, 3:6, 6:{4:{4:vid, 6:sort, 15:2}, 8:"comments-section"}}
                                     sort 0 = top, 1 = newest
    search_params(...)               the search filter blob
                                     {1:sort, 2:{1:upload_date, 2:type, 3:duration, <feature>:1 ...}}
    hashtag_params(tag, shorts)      FEhashtag   {93:{1:tag, 2:0, 3:1 | 3 (shorts)}}

Channel tab params are fixed strings (CHANNEL_TABS) — the same for every
channel.
"""
import base64
from urllib.parse import quote


def _varint(n):
    out = bytearray()
    while True:
        byte = n & 0x7F
        n >>= 7
        if n:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def encode(fields):
    """[(field_number, value), ...] -> bytes. int/bool = varint, str/bytes =
    length-delimited, list = nested message."""
    out = bytearray()
    for number, value in fields:
        if isinstance(value, bool):
            value = int(value)
        if isinstance(value, int):
            out += _varint(number << 3) + _varint(value)
            continue
        if isinstance(value, list):
            value = encode(value)
        elif isinstance(value, str):
            value = value.encode("utf-8")
        out += _varint(number << 3 | 2) + _varint(len(value)) + value
    return bytes(out)


def b64(fields):
    """Encoded message as the URL-safe base64, percent-escaped the way
    YouTube writes params (padding as %3D)."""
    return quote(base64.urlsafe_b64encode(encode(fields)).decode("ascii"), safe="")


# ---- comments ---------------------------------------------------------------------

COMMENT_SORTS = {"top": 0, "newest": 1}


def comments_token(video_id, sort="top"):
    return b64([
        (2, [(2, video_id)]),
        (3, 6),
        (6, [(4, [(4, video_id), (6, COMMENT_SORTS.get(sort, 0)), (15, 2)]), (8, "comments-section")]),
    ])


# ---- search -----------------------------------------------------------------------
# public value -> protobuf value
SEARCH_TYPES = {"video": 1, "channel": 2, "playlist": 3, "movie": 4, "shorts": 9}
SEARCH_UPLOAD_DATES = {"hour": 1, "today": 2, "week": 3, "month": 4, "year": 5}
SEARCH_DURATIONS = {"under_4_minutes": 1, "4_to_20_minutes": 3, "over_20_minutes": 2}
SEARCH_SORTS = {"relevance": 0, "popularity": 3}     # upload-date / rating sorts are ignored upstream now
SEARCH_FEATURES = {
    "hd": 4, "subtitles": 5, "creative_commons": 6, "3d": 7, "live": 8, "purchased": 9,
    "4k": 14, "360": 15, "location": 23, "hdr": 25, "vr180": 26,
}


def search_params(type=None, upload_date=None, duration=None, sort=None, features=None):
    """The search `params` blob, or None when no filter is set."""
    filters = []
    if upload_date:
        filters.append((1, SEARCH_UPLOAD_DATES[upload_date]))
    if type:
        filters.append((2, SEARCH_TYPES[type]))
    if duration:
        filters.append((3, SEARCH_DURATIONS[duration]))
    for feature in features or ():
        filters.append((SEARCH_FEATURES[feature], 1))
    message = []
    if sort and SEARCH_SORTS.get(sort):
        message.append((1, SEARCH_SORTS[sort]))
    if filters:
        message.append((2, sorted(filters)))
    return b64(message) if message else None


# ---- hashtags ---------------------------------------------------------

def hashtag_params(tag, shorts=False):
    return b64([(93, [(1, tag), (2, 0), (3, 3 if shorts else 1)])])



# ---- channel tabs -----------------------------------------------------------------------
# Identical for every channel. A channel without the tab answers with its
# Home tab, so callers check which tab came back selected.
CHANNEL_TABS = {
    "home": ("Home", "EghmZWF0dXJlZPIGBAoCMgA%3D"),
    "videos": ("Videos", "EgZ2aWRlb3PyBgQKAjoA"),
    "shorts": ("Shorts", "EgZzaG9ydHPyBgUKA5oBAA%3D%3D"),
    "live": ("Live", "EgdzdHJlYW1z8gYECgJ6AA%3D%3D"),
    "releases": ("Releases", "EghyZWxlYXNlc_IGBQoDsgEA"),
    "podcasts": ("Podcasts", "Eghwb2RjYXN0c_IGBQoDugEA"),
    "playlists": ("Playlists", "EglwbGF5bGlzdHPyBgoKCEIGCgIQaCIA"),
    "posts": ("Posts", "EgVwb3N0c_IGBAoCSgA%3D"),
    "courses": ("Courses", "Egdjb3Vyc2Vz8gYFCgPCAQA%3D"),
    "store": ("Store", "EgVzdG9yZfIGBAoCGgA%3D"),
    "search": ("Search", "EgZzZWFyY2jyBgQKAloA"),
}
