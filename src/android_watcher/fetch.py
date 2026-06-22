"""Async HTTP fetch layer for android-watcher.

Provides ``Fetcher``, a concurrency-limited async client that:
- Sets a descriptive User-Agent.
- Honors robots.txt per host (urllib.robotparser).
- Applies a per-host crawl delay.
- Retries with exponential backoff on 5xx / transport errors.
- Supports conditional GET via Store.http_cache_get/put.
"""

from __future__ import annotations

import asyncio
import logging
import urllib.robotparser
from urllib.parse import urlsplit

import httpx

from .models import Disallowed, FetchResult
from .store import Store

log = logging.getLogger("android_watcher.fetch")

USER_AGENT = "android-watcher/{version}"

MAX_RETRIES = 4
BACKOFF_BASE = 0.5
# Per-request httpx timeouts (connect/read/write/pool). read is the gap between
# bytes, not total, so a large steady download is fine; a stalled one trips it.
_TIMEOUT = httpx.Timeout(connect=10.0, read=30.0, write=30.0, pool=10.0)
# Hard ceiling on a single fetch including retries + backoff, so one bad URL can
# never hang a run indefinitely.
FETCH_DEADLINE = 120.0


class Fetcher:
	def __init__(
		self,
		store: Store,
		*,
		user_agent: str,
		concurrency: int = 4,
		crawl_delay: float = 0.5,
	):
		self._store = store
		self._user_agent = user_agent
		self._crawl_delay = crawl_delay
		self._sem = asyncio.Semaphore(concurrency)
		self._client = httpx.AsyncClient(
			headers={"User-Agent": user_agent},
			follow_redirects=True,
			timeout=_TIMEOUT,
		)
		self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}
		self._last_fetch: dict[str, float] = {}

	async def fetch(self, url: str, *, conditional: bool = False) -> FetchResult:
		if not await self._robots_ok(url):
			raise Disallowed(url)

		headers: dict[str, str] = {}
		if conditional:
			etag, last_modified = self._store.http_cache_get(url)
			if etag:
				headers["If-None-Match"] = etag
			if last_modified:
				headers["If-Modified-Since"] = last_modified

		async with self._sem:
			await self._respect_crawl_delay(url)
			log.info("downloading %s", url)
			resp = await asyncio.wait_for(
				self._get_with_backoff(url, headers), timeout=FETCH_DEADLINE
			)

		if resp.status_code == 304:
			return FetchResult(url=url, status=304, text="", not_modified=True)

		etag = resp.headers.get("ETag", "")
		last_modified = resp.headers.get("Last-Modified", "")
		# Only persist validators when at least one is non-empty; never clobber
		# an existing cache entry with ("","") on a validator-less 200.
		if conditional and (etag or last_modified):
			self._store.http_cache_put(url, etag, last_modified)

		return FetchResult(
			url=url,
			status=resp.status_code,
			text=resp.text,
			etag=etag,
			last_modified=last_modified,
		)

	async def close(self) -> None:
		await self._client.aclose()

	async def _get_with_backoff(self, url: str, headers: dict[str, str]) -> httpx.Response:
		# Merge User-Agent into per-request headers so it is sent even when
		# tests inject a bare _client that has no default headers set.
		request_headers = {"User-Agent": self._user_agent, **headers}
		last_exc: Exception | None = None
		for attempt in range(MAX_RETRIES):
			try:
				resp = await self._client.get(url, headers=request_headers)
			except (httpx.TransportError, httpx.TimeoutException) as exc:
				last_exc = exc
				resp = None
			if resp is not None and resp.status_code < 500:
				return resp
			if attempt == MAX_RETRIES - 1:
				if resp is not None:
					return resp
				raise last_exc  # type: ignore[misc]
			await asyncio.sleep(BACKOFF_BASE * (2**attempt))
		raise last_exc  # unreachable

	async def _respect_crawl_delay(self, url: str) -> None:
		host = _host_root(url)
		delay = self._crawl_delay_for(url)
		loop = asyncio.get_event_loop()
		now = loop.time()
		last = self._last_fetch.get(host)
		if last is not None:
			wait = delay - (now - last)
			if wait > 0:
				await asyncio.sleep(wait)
		self._last_fetch[host] = loop.time()

	def _crawl_delay_for(self, url: str) -> float:
		rp = self._robots.get(_host_root(url))
		if rp is not None:
			cd = rp.crawl_delay(self._user_agent)
			if cd is not None:
				return float(cd)
		return self._crawl_delay

	async def _robots_ok(self, url: str) -> bool:
		host = _host_root(url)
		if host not in self._robots:
			rp: urllib.robotparser.RobotFileParser | None = urllib.robotparser.RobotFileParser()
			try:
				# Fetch via the timed httpx client. urllib's RobotFileParser.read()
				# uses urlopen with NO timeout and can hang a run forever if a host
				# stalls; the shared client carries a 30s timeout instead.
				resp = await self._client.get(f"{host}/robots.txt")
				if resp.status_code >= 400:
					rp = None  # treat missing/forbidden robots as "allow"
				else:
					rp.parse(resp.text.splitlines())
			except (httpx.HTTPError, httpx.InvalidURL):
				rp = None  # robots unavailable => allow
			self._robots[host] = rp
		rp = self._robots[host]
		if rp is None:
			return True
		return rp.can_fetch(self._user_agent, url)


def _host_root(url: str) -> str:
	parts = urlsplit(url)
	return f"{parts.scheme}://{parts.netloc}"
