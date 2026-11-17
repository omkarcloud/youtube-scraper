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
confirm you're not a bot") and may 403/429. Since 2026-11-11 there are two
lanes, each moving on its own refusals for config.YOUTUBE_BLOCK_COOLDOWN
seconds (see "sessions" below):
  * everything but the player: own IP -> the shared gateway
    (config.YOUTUBE_FALLBACK_EXIT = "gateway", exit_gateway.py) -> a sticky
    residential exit (config.YOUTUBE_FALLBACK_PROXY_COUNTRY) when the
    gateway is refused too, or for the LAST retry of a call it keeps failing;
  * the player (and the caption tracks it signs): own IP -> residential.
    gateway addresses are bot-checked on the player, WEB and ANDROID alike
    (2026-11-11 through the gateway: 12 of 18 player calls, every video
    but one), so it has no gateway step.
The numbers are in config.py's YouTube block.

Failure taxonomy (scraper_errors, mapped to HTTP by route_glue):
  YoutubeUpstreamError  transport failure / 5xx / unparseable body   — retryable
  YoutubeBlocked        403 / 429 / bot check / HTML instead of JSON — retryable on a new exit
  YoutubeBadRequest     InnerTube 400 (bad params / cursor)          — never retried
  YoutubeNotFound       InnerTube 404, or the entity is missing      — never retried
"""
import json
import fastjson
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
import egress
from scraper_errors import BadRequest, Blocked, NotFound, UpstreamError
from scraper_utils import debug, fanout, sessions as sessions_utils

SITE = "https://www.youtube.com"
CHARTS_SITE = "https://charts.youtube.com"
SUGGEST_URL = "https://suggestqueries-clients6.youtube.com/complete/search"
IMPERSONATE = "chrome"
TIMEOUT = 30
FANOUT_WORKERS = 8

ANDROID_UA = "com.google.android.youtube/{v} (Linux; U; Android 11) gzip"
BASE_HEADERS = {
    "accept": "*/*",
    "accept-language": egress.site_accept_language("youtube"),
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
# One session per worker thread and lane (egress.new_session: curl_cffi, or
# the gateway exit's look-alike). Two lanes since 2026-11-11, each with its
# own block state, because the gateway exit carries everything BUT the player:
#   API     search / browse / next / resolve_url / live_chat, charts, suggest
#           and the pages: own IP -> the gateway exit -> residential
#   PLAYER  the player call and the caption tracks it signs: own IP ->
#           residential (gateway addresses get the bot check on the player,
#           WEB and ANDROID; the numbers are in config.py)
# A lane moves one step down for config.YOUTUBE_BLOCK_COOLDOWN seconds when
# ITS exit is refused, then goes back to direct. Before, one bot-checked
# player call sent every surface through the residential exit.
API, PLAYER = "api", "player"
_local = threading.local()
_fallback = {API: {"until": 0.0, "step": 1}, PLAYER: {"until": 0.0, "step": 1}}
_fallback_lock = threading.Lock()


def _exits(lane):
    return config.youtube_fallback_exits(player=lane == PLAYER)


def _gateway_step(lane, step):
    """True when the lane's fallback step `step` is the gateway exit with
    another exit configured behind it."""
    exits = _exits(lane)
    return 0 < step < len(exits) and bool(config.gateway_exit(exits[step - 1]))


def _step(lane=API):
    """The fallback step a session of `lane` built NOW rides: 0 = the
    configured exit (the server's own IP), 1, 2 = the lane's fallback exits
    while its cooldown runs; one step past the gateway for the last retry of
    a call (_retrying)."""
    state = _fallback[lane]
    step = state["step"] if time.time() < state["until"] else 0
    if getattr(_local, "past_gateway", None) == lane and _gateway_step(lane, step):
        step += 1
    return step


def _proxy_for_new_session(lane=API, step=None):
    forced = config.youtube_proxy()
    if forced:
        return forced
    step = _step(lane) if step is None else step
    return config.youtube_fallback_proxy(step, player=lane == PLAYER) if step else None


def _sessions():
    sessions = getattr(_local, "sessions", None)
    if sessions is None:
        sessions = _local.sessions = {}
    return sessions


def _session(lane=API):
    sess = _sessions().get(lane)
    step = _step(lane)
    if sess is not None and getattr(sess, "_yt_step", 0) != step:
        _drop_session(lane)          # a session follows its lane's step
        sess = None
    if sess is None:
        # None (direct), a proxy URL, or the gateway exit ("gateway")
        sess = egress.new_session(_proxy_for_new_session(lane, step), impersonate=IMPERSONATE)
        sess._yt_step = step
        _sessions()[lane] = sess
    return sess


def _drop_session(lane=API):
    sess = _sessions().pop(lane, None)
    sessions_utils.close_quietly(sess)


def _with_cookies(sess, headers, cookies):
    """(headers, cookies) for one request on `sess`. A curl session takes
    `cookies=` as it is. The gateway exit's session keeps cookies per target
    host and sends them in a Cookie header of its own; a `cookies=` there is
    set for the gateway's host and leaves as a second Cookie header, so on
    it they go into the explicit header (which the session merges)."""
    if cookies and getattr(sess, "via_exit_gateway", False):
        return dict(headers or {}, cookie="; ".join(f"{k}={v}" for k, v in cookies.items())), None
    return headers, cookies


def _note_block(lane=API):
    """The exit this thread's `lane` session rides was refused or
    bot-checked: the lane's new sessions move one fallback step down for a
    cooldown (on the last step they stay there, each on a fresh exit).
    No-op when no fallback is configured, and for the exit past the gateway
    that only a call's last retry borrowed (_retrying): the gateway step was
    not the one refused, so the lane stays on it."""
    exits = _exits(lane)
    if not exits:
        return
    sess_step = getattr(_sessions().get(lane), "_yt_step", _step(lane))
    state = _fallback[lane]
    own = state["step"] if time.time() < state["until"] else 0
    if getattr(_local, "past_gateway", None) == lane and _gateway_step(lane, own) and sess_step == own + 1:
        return
    step = min(sess_step + 1, len(exits))
    with _fallback_lock:
        if time.time() >= state["until"] or step > state["step"]:
            state["step"] = step
        state["until"] = time.time() + config.YOUTUBE_BLOCK_COOLDOWN
    if config.gateway_exit(exits[0]):     # how often each step is refused is the number to watch
        print(f"youtube: exit refused, {lane} sessions on fallback step {state['step']} "
              f"({exits[state['step'] - 1]}) for {config.YOUTUBE_BLOCK_COOLDOWN} s")


def dump_debug(name, text):
    """Keep the last body of each failure kind when YOUTUBE_DEBUG_DIR is set."""
    debug.dump("YOUTUBE_DEBUG_DIR", name, text, render=lambda body: json.dumps(body, ensure_ascii=False))


def _retrying(fn, lane=API):
    """Run fn() under the shared retry policy, each retry on a fresh session
    / exit. While the lane is on the gateway exit step the LAST retry of a
    call rides the next step (the residential exit), for that call only: a
    gateway failure then costs latency, not the call."""
    last = None
    _local.past_gateway = None
    try:
        for attempt in range(1, config.MAX_RETRIES + 1):
            try:
                return fn()
            except YoutubeUpstreamError as e:        # includes YoutubeBlocked
                last = e
                _drop_session(lane)
            if attempt < config.MAX_RETRIES:
                _local.past_gateway = lane if attempt + 1 == config.MAX_RETRIES else None    # read by _step() for the session built next
                time.sleep(config.RETRY_BACKOFF * attempt)
        raise last
    finally:
        _local.past_gateway = None      # _session() rebuilds on the lane's own step for the next call


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
            sess = _session()
            headers, cookies = _with_cookies(sess, {"accept-language": egress.site_accept_language("youtube")}, PAGE_COOKIES)
            resp = sess.get(SITE + "/?hl=en&gl=US", headers=headers, cookies=cookies, timeout=TIMEOUT)
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


def _post_json(url, payload, headers, label, lane=API):
    def once():
        sess = _session(lane)
        try:
            resp = sess.post(url, headers=headers, data=json.dumps(payload), timeout=TIMEOUT)
        except Exception as e:
            raise YoutubeUpstreamError(f"request failed: {type(e).__name__}: {e}")
        status = resp.status_code
        if status in (403, 429):
            dump_debug("blocked", resp.text)
            _note_block(lane)
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
            _note_block(lane)
            raise YoutubeBlocked(f"{label} returned HTML, not JSON")
        try:
            return resp.json()
        except Exception:
            dump_debug("badjson", body)
            raise YoutubeUpstreamError(f"could not parse JSON from {label}")
    return _retrying(once, lane)


def innertube(endpoint, body, *, client="WEB", hl="en", gl="US"):
    """POST one InnerTube call -> the parsed JSON dict. The player rides
    its own lane (PLAYER: no gateway exit step), everything else API."""
    payload = dict(body)
    payload["context"] = _context(client, hl, gl)
    url = f"{SITE}/youtubei/v1/{endpoint}?prettyPrint=false&key={client_config()['key']}"
    return _post_json(url, payload, _headers(client), endpoint, lane=PLAYER if endpoint == "player" else API)


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
            _note_block(PLAYER)
            raise YoutubeBotCheck(f"player bot check for {video_id}")
        return data
    last = None
    for attempt in range(1, config.MAX_RETRIES + 1):
        try:
            return once()
        except YoutubeBotCheck as e:      # transport errors already retried inside innertube()
            last = e
            _drop_session(PLAYER)
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


def _get_text(url, *, params=None, headers=None, label="page", cookies=None, retry=True, lane=API):
    def once():
        sess = _session(lane)
        head, jar = _with_cookies(sess, headers or {"accept-language": egress.site_accept_language("youtube")}, cookies)
        try:
            resp = sess.get(url, params=params, headers=head, cookies=jar, timeout=TIMEOUT)
        except Exception as e:
            raise YoutubeUpstreamError(f"request failed: {type(e).__name__}: {e}")
        status = resp.status_code
        if status in (403, 429):
            dump_debug("blocked", resp.text)
            if retry:
                _note_block(lane)
            raise YoutubeBlocked(f"HTTP {status} on {label}")
        if status == 404:
            raise YoutubeNotFound(f"{label}: not found")
        if status != 200:
            raise YoutubeUpstreamError(f"HTTP {status} on {label}")
        return resp.text or ""
    return _retrying(once, lane) if retry else once()


def suggest(query, *, hl="en", gl="US"):
    """Search suggestions (the JSONP answer, unwrapped) -> list."""
    text = _get_text(SUGGEST_URL, params={"client": "youtube", "ds": "yt", "q": query,
                                          "hl": hl or "en", "gl": (gl or "US").lower()},
                     label="suggest")
    start, end = text.find("("), text.rfind(")")
    if start < 0 or end <= start:
        raise YoutubeUpstreamError("suggest returned an unexpected body")
    try:
        return fastjson.loads(text[start + 1:end])
    except ValueError:
        raise YoutubeUpstreamError("could not parse the suggest answer")


def timedtext(url, *, retry=True):
    """A caption track body (json3 / vtt / ...). retry=False for token-gated
    (translated) tracks, where a 429 means 'token refused', not 'this exit
    is blocked'. The track link is signed by the player answer, so it
    rides the player's lane (the exit that answered the player call)."""
    return _get_text(url, label="timedtext", retry=retry, lane=PLAYER)


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
                    return fastjson.loads(text[start:pos + 1])
                except ValueError:
                    return None
    return None


# ---- fan-out --------------------------------------------------------------------

def run_parallel(fns):
    """Run callables concurrently -> results in order (exceptions propagate)."""
    return fanout.per_call(fns, FANOUT_WORKERS)


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
