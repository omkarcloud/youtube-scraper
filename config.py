"""Configuration for the YouTube Scraper. Everything can be set with an
environment variable; the defaults work out of the box.

    PORT           port the API listens on (default 8000)
    YOUTUBE_PROXY  proxy URL, e.g. http://user:pass@host:port (default: none —
                   direct). YouTube answers direct requests fine at normal
                   volume. When set, every request goes through it; set it if
                   you start seeing HTTP 429 or "Sign in to confirm you're not
                   a bot" at high volume.

Everything else below is a plain constant with a working default — edit it
here if you need to.
"""
import os

PORT = int(os.environ.get("PORT", "8000"))

# Retry policy for transport errors and blocks (every request).
MAX_RETRIES = 3
RETRY_BACKOFF = 2          # seconds, multiplied by the attempt number

YOUTUBE_PROXY = os.environ.get("YOUTUBE_PROXY") or None

# Public InnerTube values the youtube/ package reads. The web client version
# and API key are refreshed from youtube.com automatically every
# YOUTUBE_CLIENT_REFRESH seconds; these are only the fallback.
YOUTUBE_API_KEY = "AIzaSyAO_FJ2SlqU8Q4STEHLGCilw_Y9_11qcW8"
YOUTUBE_WEB_CLIENT_VERSION = "2.20260922.06.00"
YOUTUBE_ANDROID_CLIENT_VERSION = "20.10.38"
YOUTUBE_CHARTS_API_KEY = "AIzaSyCzEW7JUJdSql0-2V4tHUb6laYm4iAE_dM"
YOUTUBE_CLIENT_REFRESH = 6 * 3600
YOUTUBE_BLOCK_COOLDOWN = 900   # seconds new sessions use the fallback proxy after a block


def youtube_proxy():
    return os.environ.get("YOUTUBE_PROXY") or None


def youtube_fallback_proxy():
    return os.environ.get("YOUTUBE_PROXY") or None
