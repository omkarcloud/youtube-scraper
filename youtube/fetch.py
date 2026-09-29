"""YouTube transport: plain curl_cffi with Chrome-impersonated TLS against
YouTube's own JSON API (InnerTube). No browser, no cookies, no login
(validated 2026-09-27 from direct Indian egress: ~0.3-1 s a call).

Upstream surfaces:

  * InnerTube  POST https://www.youtube.com/youtubei/v1/<endpoint>
               {"context": {"client": {...}}, ...}
      search                 search results (+ protobuf filter params)
      browse                 channels and their tabs, playlists, posts,
                             hashtags, shorts sound pages, Explore feeds
      next                   the watch page (owner, description, chapters,
                             heatmap, music), comments, replies, related
      player                 WEB: videoDetails + microformat (UNPLAYABLE
                             without a PoToken, so no streams);
                             ANDROID: direct stream URLs + captions +
                             storyboards + end screens
      navigation/resolve_url handle / custom URL / legacy user -> channel id
      live_chat/get_live_chat(_replay)
    The WEB client version + API key are read from the homepage every
    config.YOUTUBE_CLIENT_REFRESH seconds (fallback: the config values).

  * Charts     POST https://charts.youtube.com/youtubei/v1/browse
               client WEB_MUSIC_ANALYTICS, its own key (the old FEtrending
               feed answers 400 on every client since 2025)
  * Suggest    GET https://suggestqueries-clients6.youtube.com/complete/search (JSONP)
  * Timedtext  GET the signed caption track URLs from the ANDROID player
  * Pages      GET https://www.youtube.com/post/<id> (ytInitialData) — a
               post's browse params need its channel id, the page does not

Blocks: YouTube bot-checks the player from some cloud ranges ("Sign in to
confirm you're not a bot") and may 403/429. On either, the worker thread's
next session goes through a sticky residential exit
(config.youtube_fallback_proxy()) for config.YOUTUBE_BLOCK_COOLDOWN seconds.

Failure taxonomy (scraper_errors, mapped to HTTP by route_glue):
  YoutubeUpstreamError  transport failure / 5xx / unparseable body   — retryable
  YoutubeBlocked        403 / 429 / bot check / HTML instead of JSON — retryable on a new exit
  YoutubeBadRequest     InnerTube 400 (bad params / cursor)          — never retried
  YoutubeNotFound       InnerTube 404, or the entity is missing      — never retried
"""
import json
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from scraper_errors import BadRequest, Blocked, NotFound, UpstreamError

SITE = "https://www.youtube.com"
CHARTS_SITE = "https://charts.youtube.com"
SUGGEST_URL = "https://suggestqueries-clients6.youtube.com/complete/search"
IMPERSONATE = "chrome"
TIMEOUT = 30
FANOUT_WORKERS = 8

ANDROID_UA = "com.google.android.youtube/{v} (Linux; U; Android 11) gzip"
BASE_HEADERS = {
    "accept": "*/*",
    "accept-language": "en-US,en;q=0.9",
    "content-type": "application/json",
    "origin": SITE,
    "referer": SITE + "/",
}
# SOCS=CAI answers the EU consent interstitial for page fetches.
PAGE_COOKIES = {"SOCS": "CAI", "CONSENT": "YES+"}


class YoutubeUpstreamError(UpstreamError):
    """Transport failure, 5xx or an unparseable body — retryable."""


class YoutubeBlocked(YoutubeUpstreamError, Blocked):
    """403 / 429 / bot check — retryable on a fresh exit."""


class YoutubeBadRequest(BadRequest):
    """InnerTube rejected the params or the cursor. Never retried."""


class YoutubeNotFound(NotFound):
    """The entity does not exist. Never retried."""


class YoutubeBotCheck(YoutubeBlocked):
    """The player answered 'Sign in to confirm you're not a bot'."""


# ---- sessions --------------------------------------------------------------------
_local = threading.local()
_fallback_until = 0.0
_fallback_lock = threading.Lock()


def _proxy_for_new_session():
    forced = config.youtube_proxy()
    if forced:
        return forced
    if time.time() < _fallback_until:
        return config.youtube_fallback_proxy()
    return None


def _session():
    sess = getattr(_local, "session", None)
    if sess is None:
        from curl_cffi import requests as curl_requests
        sess = curl_requests.Session(impersonate=IMPERSONATE)
        proxy = _proxy_for_new_session()
        if proxy:
            sess.proxies = {"http": proxy, "https": proxy}
        _local.session = sess
    return sess


def _drop_session():
    sess = getattr(_local, "session", None)
    _local.session = None
    if sess is not None:
        try:
            sess.close()
        except Exception:
            pass


def _note_block():
    """Refused or bot-checked on this egress: send new sessions through a
    residential exit for a while (no-op when no fallback is configured)."""
    global _fallback_until
    if config.youtube_fallback_proxy() is not None:
        with _fallback_lock:
            _fallback_until = time.time() + config.YOUTUBE_BLOCK_COOLDOWN


def dump_debug(name, text):
    """Keep the last body of each failure kind when YOUTUBE_DEBUG_DIR is set."""
    dbg = os.environ.get("YOUTUBE_DEBUG_DIR", "")
    if dbg and text:
        try:
            os.makedirs(dbg, exist_ok=True)
            with open(os.path.join(dbg, name + ".txt"), "w") as f:
                f.write(text if isinstance(text, str) else json.dumps(text, ensure_ascii=False))
        except OSError:
            pass


def _retrying(fn):
    last = None
    for attempt in range(1, config.MAX_RETRIES + 1):
        try:
            return fn()
        except YoutubeUpstreamError as e:        # includes YoutubeBlocked
            last = e
            _drop_session()
        if attempt < config.MAX_RETRIES:
            time.sleep(config.RETRY_BACKOFF * attempt)
    raise last


# ---- client config (WEB version + key, refreshed from the homepage) -------------------
_client = {"key": config.YOUTUBE_API_KEY, "web_version": config.YOUTUBE_WEB_CLIENT_VERSION, "at": 0.0}
_client_lock = threading.Lock()
_KEY_RE = re.compile(r'"INNERTUBE_API_KEY":"([^"]+)"')
_VERSION_RE = re.compile(r'"INNERTUBE_CLIENT_VERSION":"([^"]+)"')


def client_config():
    """{"key", "web_version"} — the homepage's current values, refreshed at
    most every YOUTUBE_CLIENT_REFRESH seconds; the config fallback when the
    homepage cannot be read."""
    now = time.time()
    if now - _client["at"] < config.YOUTUBE_CLIENT_REFRESH:
        return _client
    with _client_lock:
        if now - _client["at"] < config.YOUTUBE_CLIENT_REFRESH:
            return _client
        _client["at"] = now      # one attempt per period even when it fails
        try:
            resp = _session().get(SITE + "/?hl=en&gl=US", headers={"accept-language": "en-US,en;q=0.9"},
                                  cookies=PAGE_COOKIES, timeout=TIMEOUT)
            if resp.status_code == 200:
                key = _KEY_RE.search(resp.text or "")
                version = _VERSION_RE.search(resp.text or "")
                if key:
                    _client["key"] = key.group(1)
                if version:
                    _client["web_version"] = version.group(1)
        except Exception:
            _drop_session()
    return _client


def _context(client, hl, gl):
    if client == "ANDROID":
        c = {"clientName": "ANDROID", "clientVersion": config.YOUTUBE_ANDROID_CLIENT_VERSION,
             "androidSdkVersion": 30, "osName": "Android", "osVersion": "11"}
    else:
        c = {"clientName": "WEB", "clientVersion": client_config()["web_version"]}
    c["hl"] = hl or "en"
    c["gl"] = gl or "US"
    return {"client": c}


def _headers(client):
    headers = dict(BASE_HEADERS)
    if client == "ANDROID":
        headers["user-agent"] = ANDROID_UA.format(v=config.YOUTUBE_ANDROID_CLIENT_VERSION)
        headers["x-youtube-client-name"] = "3"
        headers["x-youtube-client-version"] = config.YOUTUBE_ANDROID_CLIENT_VERSION
    else:
        headers["x-youtube-client-name"] = "1"
        headers["x-youtube-client-version"] = client_config()["web_version"]
    return headers


def _error_message(resp):
    try:
        body = resp.json()
    except Exception:
        return None
    error = body.get("error") if isinstance(body, dict) else None
    if isinstance(error, dict) and isinstance(error.get("message"), str):
        return error["message"]
    return None


def _post_json(url, payload, headers, label):
    def once():
        sess = _session()
        try:
            resp = sess.post(url, headers=headers, data=json.dumps(payload), timeout=TIMEOUT)
        except Exception as e:
            raise YoutubeUpstreamError(f"request failed: {type(e).__name__}: {e}")
        status = resp.status_code
        if status in (403, 429):
            dump_debug("blocked", resp.text)
            _note_block()
            raise YoutubeBlocked(f"HTTP {status} on {label}")
        if status == 404:
            raise YoutubeNotFound(_error_message(resp) or f"{label}: not found")
        if status == 400:
            message = _error_message(resp) or "invalid argument"
            raise YoutubeBadRequest(f"{label}: {message}")
        if status != 200:
            raise YoutubeUpstreamError(f"HTTP {status} on {label}")
        body = resp.text or ""
        if not body.lstrip().startswith("{"):
            dump_debug("nonjson", body)
            _note_block()
            raise YoutubeBlocked(f"{label} returned HTML, not JSON")
        try:
            return resp.json()
        except Exception:
            dump_debug("badjson", body)
            raise YoutubeUpstreamError(f"could not parse JSON from {label}")
    return _retrying(once)


def innertube(endpoint, body, *, client="WEB", hl="en", gl="US"):
    """POST one InnerTube call -> the parsed JSON dict."""
    payload = dict(body)
    payload["context"] = _context(client, hl, gl)
    url = f"{SITE}/youtubei/v1/{endpoint}?prettyPrint=false&key={client_config()['key']}"
    return _post_json(url, payload, _headers(client), endpoint)


_BOT_RE = re.compile(r"not a bot|confirm you.re not|unusual traffic", re.I)


def player(video_id, *, client="WEB", hl="en", gl="US"):
    """The player response. A bot check ('Sign in to confirm you're not a
    bot') is a block: the call is retried from a residential exit."""
    def once():
        data = innertube("player", {"videoId": video_id, "contentCheckOk": True, "racyCheckOk": True},
                         client=client, hl=hl, gl=gl)
        status = (data.get("playabilityStatus") or {}) if isinstance(data, dict) else {}
        reason = " ".join(str(status.get(k) or "") for k in ("reason", "status"))
        if _BOT_RE.search(reason):
            dump_debug("botcheck", data)
            _note_block()
            raise YoutubeBotCheck(f"player bot check for {video_id}")
        return data
    last = None
    for attempt in range(1, config.MAX_RETRIES + 1):
        try:
            return once()
        except YoutubeBotCheck as e:      # transport errors already retried inside innertube()
            last = e
            _drop_session()
        if attempt < config.MAX_RETRIES:
            time.sleep(config.RETRY_BACKOFF * attempt)
    raise last


def charts(query, *, hl="en", gl="US"):
    """One charts.youtube.com browse (query = the chart_params string)."""
    payload = {
        "context": {"client": {"clientName": "WEB_MUSIC_ANALYTICS", "clientVersion": "2.0",
                               "hl": hl or "en", "gl": gl or "US", "theme": "MUSIC"}},
        "browseId": "FEmusic_analytics_charts_home",
        "query": query,
    }
    headers = dict(BASE_HEADERS, origin=CHARTS_SITE, referer=CHARTS_SITE + "/")
    url = f"{CHARTS_SITE}/youtubei/v1/browse?alt=json&key={config.YOUTUBE_CHARTS_API_KEY}"
    return _post_json(url, payload, headers, "charts")


def _get_text(url, *, params=None, headers=None, label="page", cookies=None, retry=True):
    def once():
        sess = _session()
        try:
            resp = sess.get(url, params=params, headers=headers or {"accept-language": "en-US,en;q=0.9"},
                            cookies=cookies, timeout=TIMEOUT)
        except Exception as e:
            raise YoutubeUpstreamError(f"request failed: {type(e).__name__}: {e}")
        status = resp.status_code
        if status in (403, 429):
            dump_debug("blocked", resp.text)
            if retry:
                _note_block()
            raise YoutubeBlocked(f"HTTP {status} on {label}")
        if status == 404:
            raise YoutubeNotFound(f"{label}: not found")
        if status != 200:
            raise YoutubeUpstreamError(f"HTTP {status} on {label}")
        return resp.text or ""
    return _retrying(once) if retry else once()


def suggest(query, *, hl="en", gl="US"):
    """Search suggestions (the JSONP answer, unwrapped) -> list."""
    text = _get_text(SUGGEST_URL, params={"client": "youtube", "ds": "yt", "q": query,
                                          "hl": hl or "en", "gl": (gl or "US").lower()},
                     label="suggest")
    start, end = text.find("("), text.rfind(")")
    if start < 0 or end <= start:
        raise YoutubeUpstreamError("suggest returned an unexpected body")
    try:
        return json.loads(text[start + 1:end])
    except ValueError:
        raise YoutubeUpstreamError("could not parse the suggest answer")


def timedtext(url, *, retry=True):
    """A caption track body (json3 / vtt / ...). retry=False for token-gated
    (translated) tracks, where a 429 means 'token refused', not 'this exit
    is blocked'."""
    return _get_text(url, label="timedtext", retry=retry)


def page_initial_data(path, *, hl="en", gl="US"):
    """ytInitialData of a www.youtube.com page (post pages)."""
    sep = "&" if "?" in path else "?"
    text = _get_text(f"{SITE}{path}{sep}hl={hl or 'en'}&gl={gl or 'US'}", label=path, cookies=PAGE_COOKIES)
    data = extract_json_var(text, "ytInitialData")
    if data is None:
        dump_debug("page", text)
        raise YoutubeUpstreamError(f"{path}: no ytInitialData in the page")
    return data


def extract_json_var(text, name):
    """The JSON object assigned to `var <name> = {...};` in a page."""
    index = text.find(name + " = ")
    if index < 0:
        index = text.find(name + "=")
        if index < 0:
            return None
    start = text.find("{", index)
    if start < 0:
        return None
    depth, in_string, escaped = 0, False, False
    for pos in range(start, len(text)):
        ch = text[pos]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
        elif ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start:pos + 1])
                except ValueError:
                    return None
    return None


# ---- fan-out --------------------------------------------------------------------

def run_parallel(fns):
    """Run callables concurrently -> results in order (exceptions propagate)."""
    if not fns:
        return []
    if len(fns) == 1:
        return [fns[0]()]
    with ThreadPoolExecutor(max_workers=min(FANOUT_WORKERS, len(fns))) as ex:
        futures = [ex.submit(fn) for fn in fns]
        return [f.result() for f in futures]


def run_parallel_safe(fns):
    """Like run_parallel, but each slot is (result, None) or (None, exception)."""
    def wrap(fn):
        def inner():
            try:
                return fn(), None
            except Exception as e:      # the caller decides what an error means
                return None, e
        return inner
    return run_parallel([wrap(fn) for fn in fns])


if __name__ == "__main__":
    # Smoke test: python youtube/fetch.py [video_id]
    vid = sys.argv[1] if len(sys.argv) > 1 else "dQw4w9WgXcQ"
    data = player(vid)
    print(json.dumps(data.get("videoDetails", {}), ensure_ascii=False)[:500])
