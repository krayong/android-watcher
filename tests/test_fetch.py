"""Tests for fetch.py — Fetcher: basic GET, conditional GET, robots, backoff."""

from __future__ import annotations

import httpx
import pytest

from android_watcher import __version__
from android_watcher.fetch import BACKOFF_BASE, USER_AGENT, Fetcher
from android_watcher.models import FetchResult


class FakeStore:
	def __init__(self):
		self.cache = {}

	def http_cache_get(self, url):
		return self.cache.get(url, ("", ""))

	def http_cache_put(self, url, etag, last_modified):
		self.cache[url] = (etag, last_modified)


def make_fetcher(handler, store=None, **kw):
	transport = httpx.MockTransport(handler)
	f = Fetcher(store or FakeStore(), user_agent=USER_AGENT.format(version=__version__), **kw)
	f._client = httpx.AsyncClient(transport=transport)  # inject test transport

	async def _allow(url):  # bypass robots in this test
		return True

	f._robots_ok = _allow
	return f


# ---------------------------------------------------------------------------
# Task 1: basic GET, USER_AGENT constant, close()
# ---------------------------------------------------------------------------


def test_user_agent_constant_has_placeholder():
	assert "{version}" in USER_AGENT


async def test_basic_get_returns_text_and_status():
	def handler(request):
		assert request.headers["user-agent"] == f"android-watcher/{__version__}"
		return httpx.Response(200, text="hello body")

	f = make_fetcher(handler)
	res = await f.fetch("https://example.com/x")
	assert isinstance(res, FetchResult)
	assert res.status == 200
	assert res.text == "hello body"
	assert res.not_modified is False
	await f.close()


# ---------------------------------------------------------------------------
# Task 2: conditional GET (304), robots disallow, backoff
# ---------------------------------------------------------------------------


async def test_conditional_get_sends_validators_and_caches():
	store = FakeStore()
	store.cache["https://example.com/s"] = ('"etag-1"', "Wed, 21 Oct 2025 07:28:00 GMT")
	seen = {}

	def handler(request):
		seen["inm"] = request.headers.get("If-None-Match")
		seen["ims"] = request.headers.get("If-Modified-Since")
		return httpx.Response(
			200,
			text="fresh",
			headers={
				"ETag": '"etag-2"',
				"Last-Modified": "Thu, 22 Oct 2025 07:28:00 GMT",
			},
		)

	f = make_fetcher(handler, store=store)
	res = await f.fetch("https://example.com/s", conditional=True)
	assert seen["inm"] == '"etag-1"'
	assert seen["ims"] == "Wed, 21 Oct 2025 07:28:00 GMT"
	assert res.status == 200 and res.text == "fresh"
	assert store.cache["https://example.com/s"] == (
		'"etag-2"',
		"Thu, 22 Oct 2025 07:28:00 GMT",
	)
	await f.close()


async def test_validator_less_200_does_not_clobber_cached_validators():
	# A 200 with no ETag/Last-Modified must NOT overwrite previously-stored
	# validators with ("","") — otherwise the next run can't send conditional
	# headers and re-fetches everything.
	store = FakeStore()
	store.cache["https://example.com/n"] = ('"keep-etag"', "Wed, 21 Oct 2025 07:28:00 GMT")

	def handler(request):
		return httpx.Response(200, text="body, but server sent no validators")

	f = make_fetcher(handler, store=store)
	res = await f.fetch("https://example.com/n", conditional=True)
	assert res.status == 200
	assert store.cache["https://example.com/n"] == (
		'"keep-etag"',
		"Wed, 21 Oct 2025 07:28:00 GMT",
	)
	await f.close()


async def test_304_returns_not_modified_blank_text():
	store = FakeStore()
	store.cache["https://example.com/s"] = ('"etag-1"', "")

	def handler(request):
		return httpx.Response(304)

	f = make_fetcher(handler, store=store)
	res = await f.fetch("https://example.com/s", conditional=True)
	assert res.not_modified is True
	assert res.status == 304  # real HTTP status per CONTRACTS, not 0
	assert res.text == ""
	await f.close()


async def test_robots_disallow_raises():
	from android_watcher.models import Disallowed  # defined in models.py

	f = make_fetcher(lambda r: httpx.Response(200, text="x"))

	async def _deny(url):
		return False

	f._robots_ok = _deny
	with pytest.raises(Disallowed):
		await f.fetch("https://example.com/blocked")
	await f.close()


async def test_backoff_retries_on_5xx_then_succeeds(monkeypatch):
	calls = {"n": 0}
	slept: list[float] = []

	async def fake_sleep(*a):
		slept.append(a[0] if a else None)

	def handler(request):
		calls["n"] += 1
		if calls["n"] < 3:
			return httpx.Response(503)
		return httpx.Response(200, text="ok")

	f = make_fetcher(handler)
	monkeypatch.setattr("android_watcher.fetch.asyncio.sleep", fake_sleep)
	res = await f.fetch("https://example.com/flaky")
	assert calls["n"] == 3
	assert res.status == 200
	# two 503s -> two backoff sleeps with exponential growth: 0.5, 1.0
	assert slept == [BACKOFF_BASE * (2**0), BACKOFF_BASE * (2**1)]
	await f.close()
