"""Cache TTL per /youtube/* endpoint (cache.py, keyed on the validated params).

Tiers follow how fast each surface moves: live chat and stream links are
never cached (a poll must see new messages; stream links expire and are
signed), live stats move by the minute, a video's metadata by the hour,
a channel's About panel and a finished playlist barely at all. Charts
refresh once a day / week upstream.
"""
from datetime import timedelta

# --- videos ------------------------------------------------------------------
VIDEO_CACHE = timedelta(minutes=10)       # details (views / likes / live state move)
BATCH_CACHE = timedelta(minutes=10)
COMMENTS_CACHE = timedelta(minutes=10)
REPLIES_CACHE = timedelta(minutes=10)
RELATED_CACHE = timedelta(minutes=30)
SUBTITLES_CACHE = timedelta(hours=1)      # track links expire after ~6 h
TRANSCRIPT_CACHE = timedelta(hours=12)
# --- channels ----------------------------------------------------------------
CHANNEL_CACHE = timedelta(minutes=30)
CHANNEL_LIST_CACHE = timedelta(minutes=10)
# --- playlists / posts -------------------------------------------------------
PLAYLIST_CACHE = timedelta(minutes=30)
POST_CACHE = timedelta(minutes=10)
# --- search / discovery ------------------------------------------------------
SEARCH_CACHE = timedelta(minutes=10)
SUGGEST_CACHE = timedelta(hours=1)
HASHTAG_CACHE = timedelta(minutes=10)
SOUND_CACHE = timedelta(minutes=30)
CHARTS_CACHE = timedelta(hours=3)
EXPLORE_CACHE = timedelta(minutes=10)
RESOLVE_CACHE = timedelta(hours=24)
