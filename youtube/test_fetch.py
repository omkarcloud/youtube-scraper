"""YouTube transport egress (2026-11-11): two lanes, each with its own block
state. API (search / browse / next / charts / suggest / pages): own IP ->
the gateway exit (config.YOUTUBE_FALLBACK_EXIT) -> residential. PLAYER (the
player call and its caption tracks): own IP -> residential, never the
gateway (gateway addresses are bot-checked on it). Offline, no network.

    python -m pytest youtube/test_fetch.py -q
"""
import json
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
from youtube import fetch as F  # noqa: E402
from scraper_utils.testing import hosted_only

RESI = "http://resi-us:1"
BOT = {"playabilityStatus": {"status": "LOGIN_REQUIRED", "reason": "Sign in to confirm you’re not a bot"}}
PLAYABLE = {"playabilityStatus": {"status": "OK"}, "videoDetails": {"videoId": "abc"}}


class Resp:
    def __init__(self, status, text):
        self.status_code, self.text = status, text

    def json(self):
        return json.loads(self.text)


class Session:
    """What egress.new_session() hands out, answering per exit from `plan`
    (exit -> a status, "bot" for a bot-checked player answer, a list of
    them, or an exception to raise)."""

    def __init__(self, exit, plan, seen):
        self.exit, self.plan, self.seen = exit, plan, seen

    def _answer(self, url, ok):
        self.seen.append((self.exit, "player" if "/v1/player" in url else "timedtext" if "timedtext" in url else "api"))
        answer = self.plan[self.exit]
        answer = answer.pop(0) if isinstance(answer, list) else answer
        if isinstance(answer, Exception):
            raise answer
        if answer == "bot":
            return Resp(200, json.dumps(BOT) if "/v1/player" in url else ok)
        return Resp(answer, ok if answer == 200 else "<html>refused</html>")

    def post(self, url, headers=None, data=None, **kwargs):
        return self._answer(url, json.dumps(PLAYABLE if "/v1/player" in url else {"contents": []}))

    def get(self, url, **kwargs):
        return self._answer(url, "WEBVTT")

    def close(self):
        pass


@pytest.fixture
def ladder(monkeypatch):
    """(plan, seen): the exits' answers and the (exit, surface) of every request sent."""
    plan, seen = {}, []
    monkeypatch.setattr(F, "_fallback", {F.API: {"until": 0.0, "step": 1}, F.PLAYER: {"until": 0.0, "step": 1}})
    monkeypatch.setattr(F._local, "sessions", {}, raising=False)
    monkeypatch.setattr(F._local, "past_gateway", None, raising=False)
    monkeypatch.setattr(F, "_client", {"key": "k", "web_version": "2.0", "at": time.time()})    # no homepage read
    monkeypatch.setattr(F.egress, "new_session", lambda exit=None, impersonate=None: Session(exit, plan, seen))
    monkeypatch.setattr(F.time, "sleep", lambda s: None)
    monkeypatch.setattr(config, "residential_proxy", lambda c, states=None: f"http://resi-{c}:1")
    monkeypatch.setattr(config, "MAX_RETRIES", 3)
    monkeypatch.setattr(config, "YOUTUBE_PROXY_COUNTRY", None)
    monkeypatch.setattr(config, "YOUTUBE_FALLBACK_EXIT", "gateway")
    monkeypatch.setattr(config, "YOUTUBE_FALLBACK_PROXY_COUNTRY", "us")
    return plan, seen


def search():
    return F.innertube("search", {"query": "cats"})


def exits(seen):
    return [exit for exit, _ in seen]


@hosted_only
def test_default_is_own_ip_then_the_gateway_then_residential():
    assert config.YOUTUBE_PROXY_COUNTRY is None
    assert config.YOUTUBE_FALLBACK_EXIT == "gateway"
    assert config.YOUTUBE_FALLBACK_PROXY_COUNTRY == "us"


@hosted_only
def test_fallback_steps_per_lane(ladder, monkeypatch):
    assert config.youtube_proxy() is None
    assert [config.youtube_fallback_proxy(step) for step in (1, 2, 3)] == ["gateway", RESI, None]
    assert [config.youtube_fallback_proxy(step, player=True) for step in (1, 2)] == [RESI, None]    # no gateway step
    monkeypatch.setattr(config, "YOUTUBE_FALLBACK_EXIT", None)         # the ladder before the gateway
    assert [config.youtube_fallback_proxy(step) for step in (1, 2)] == [RESI, None]
    monkeypatch.setattr(config, "YOUTUBE_FALLBACK_PROXY_COUNTRY", None)
    assert config.youtube_fallback_exits() == [] and config.youtube_fallback_exits(player=True) == []
    monkeypatch.setattr(config, "YOUTUBE_PROXY_COUNTRY", "gateway")         # the main knob takes the gateway too
    assert config.youtube_proxy() == "gateway"


@hosted_only
def test_a_refused_own_ip_costs_gateway_requests_not_residential(ladder):
    plan, seen = ladder
    plan.update({None: 403, "gateway": 200})
    assert search() == {"contents": []}
    assert exits(seen) == [None, "gateway"]
    search()                                                # new API sessions stay on the gateway for the cooldown
    assert F._get_text("https://www.youtube.com/post/x") == "WEBVTT"     # pages ride the API lane too
    assert exits(seen) == [None, "gateway", "gateway", "gateway"] and F._step(F.API) == 1
    assert F._step(F.PLAYER) == 0                           # the player lane was not refused: still the own IP
    F._fallback[F.API]["until"] = time.time() - 1           # cooldown over: the own IP again
    plan[None] = 200
    search()
    assert seen[-1] == (None, "api")


@hosted_only
def test_a_refused_gateway_steps_on_to_residential_and_stays(ladder):
    plan, seen = ladder
    plan.update({None: 429, "gateway": 403, RESI: 200})
    search()
    assert exits(seen) == [None, "gateway", RESI]
    search()
    assert exits(seen) == [None, "gateway", RESI, RESI] and F._step(F.API) == 2


@hosted_only
def test_last_retry_of_a_call_the_gateway_keeps_failing_rides_residential(ladder):
    plan, seen = ladder
    plan.update({None: 403, "gateway": 200, RESI: 200})
    search()                                                # the API lane is on the gateway step now
    del seen[:]
    plan["gateway"] = [RuntimeError("GatewayError: gateway 504"), 502, 200]
    search()
    assert exits(seen) == ["gateway", "gateway", RESI]
    search()                                                # for that call only: no cooldown on the paid exit
    assert exits(seen) == ["gateway", "gateway", RESI, "gateway"] and F._step(F.API) == 1


@hosted_only
def test_a_bot_checked_player_goes_straight_to_residential_and_moves_nothing_else(ladder):
    plan, seen = ladder
    plan.update({None: "bot", "gateway": "bot", RESI: 200})
    assert F.player("abc", client="ANDROID") == PLAYABLE
    assert seen == [(None, "player"), (RESI, "player")]     # never the gateway: it is bot-checked there
    assert F.timedtext("https://www.youtube.com/api/timedtext?v=abc") == "WEBVTT"
    assert seen[-1] == (RESI, "timedtext")                  # the caption track rides the exit that answered the player
    search()
    assert seen[-1] == (None, "api")                        # the other surfaces stay on the own IP
    assert F._step(F.PLAYER) == 1 and F._step(F.API) == 0
    F.player("abc")
    assert seen[-1] == (RESI, "player")                     # the player lane holds its cooldown


@hosted_only
def test_an_api_lane_on_the_gateway_leaves_the_player_on_the_own_ip(ladder):
    plan, seen = ladder
    plan.update({None: [403, 200], "gateway": 200})
    search()                                                # API lane -> the gateway
    assert F.player("abc") == PLAYABLE
    assert seen == [(None, "api"), ("gateway", "api"), (None, "player")]


@hosted_only
def test_a_pinned_exit_still_carries_every_call(ladder, monkeypatch):
    plan, seen = ladder
    monkeypatch.setattr(config, "YOUTUBE_PROXY_COUNTRY", "gb")
    plan.update({"http://resi-gb:1": [403, 200, 200]})
    search()
    F.player("abc")
    assert exits(seen) == ["http://resi-gb:1"] * 3


@hosted_only
def test_a_refused_last_retry_exit_leaves_the_lane_on_the_gateway(ladder):
    """The residential exit a call's last retry borrows is refused: that
    says nothing about the gateway step, so the lane does not move onto
    the paid exit for a cooldown."""
    plan, seen = ladder
    plan.update({None: 403, "gateway": 200, RESI: 429})
    search()                                                # the API lane is on the gateway step now
    del seen[:]
    plan["gateway"] = [RuntimeError("GatewayError: gateway 504"), 502, 200]
    with pytest.raises(F.YoutubeBlocked):
        search()
    assert exits(seen) == ["gateway", "gateway", RESI]
    search()
    assert exits(seen)[-1] == "gateway" and F._step(F.API) == 1


@hosted_only
def test_page_cookies_go_into_the_cookie_header_on_a_gateway_session(ladder, monkeypatch):
    """curl takes `cookies=`; the gateway session sends its own Cookie
    header per target host, so there the page cookies ride that header."""
    sent = []

    class Recorder:
        def __init__(self, exit):
            self.via_exit_gateway = exit == "gateway"

        def get(self, url, **kwargs):
            sent.append(kwargs)
            return Resp(200, "page")

        def close(self):
            pass

    monkeypatch.setattr(F.egress, "new_session", lambda exit=None, impersonate=None: Recorder(exit))
    F._get_text("https://www.youtube.com/post/x", cookies=F.PAGE_COOKIES)                 # the own IP
    assert sent[-1]["cookies"] == F.PAGE_COOKIES and "cookie" not in sent[-1]["headers"]
    F._fallback[F.API].update(until=time.time() + 60, step=1)                            # the lane on the gateway
    F._get_text("https://www.youtube.com/post/x", cookies=F.PAGE_COOKIES)
    assert sent[-1]["cookies"] is None and sent[-1]["headers"]["cookie"] == "SOCS=CAI; CONSENT=YES+"
    assert sent[-1]["headers"]["accept-language"]
