"""SQLite persistence layer.

Single synchronous connection per Store instance. All datetimes are stored as
ISO-8601 strings with UTC offset; the Store coerces naive datetimes to UTC at
the boundary so callers never need to worry about timezone hygiene.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime

from .models import Change, SignalType, Verdict


@dataclass
class Snapshot:
	source_id: str
	url: str
	signal_type: SignalType
	content_hash: str
	lastmod: str
	excerpt: str
	fetched_at: datetime
	# Full normalized page text (not just the 500-char excerpt), kept so the next
	# run can diff old vs new and send only the changed regions to triage.
	content_text: str = ""


def _now_iso() -> str:
	return datetime.now(UTC).isoformat()


def _to_utc(value: datetime) -> datetime:
	"""Coerce any datetime to UTC-aware at the Store boundary.

	A naive value is assumed to be UTC; an aware value is converted to UTC.
	"""
	if value.tzinfo is None:
		return value.replace(tzinfo=UTC)
	return value.astimezone(UTC)


def _parse_iso(value: str) -> datetime:
	dt = datetime.fromisoformat(value)
	return _to_utc(dt)


# C0 control chars to strip from seed literals, keeping tab/newline/return.
# executescript() runs SQL as a C string, so an embedded NUL (or other control
# byte that can slip in from page text) truncates or rejects the whole script.
_CTRL_STRIP = {c: None for c in range(0x20) if c not in (0x09, 0x0A, 0x0D)}


def _sql_str(value: object) -> str:
	"""Render a seed column value as a SQL string literal.

	Every seed-table column is declared TEXT NOT NULL, so values are strings;
	single quotes are doubled per SQL escaping, and control characters (notably
	NUL) are stripped so executescript() can run the dump.
	"""
	return "'" + str(value).translate(_CTRL_STRIP).replace("'", "''") + "'"


class Store:
	"""Synchronous SQLite wrapper. Datetimes stored ISO-8601 UTC."""

	def __init__(self, path: str) -> None:
		self.path = path
		self._conn = sqlite3.connect(path)
		self._conn.row_factory = sqlite3.Row
		self._conn.execute("PRAGMA foreign_keys = ON")

	def close(self) -> None:
		"""Close the underlying connection. Idempotent."""
		self._conn.close()

	def __enter__(self) -> Store:
		return self

	def __exit__(self, *exc: object) -> None:
		self.close()

	def migrate(self) -> None:
		self._conn.executescript(
			"""
            -- NOTE: spec section 6 keys snapshots by source_id only; we add a
            -- `url` column (PK (source_id, url)) because get_snapshot needs
            -- per-URL keying. Intentional deviation, documented in Interfaces.
            CREATE TABLE IF NOT EXISTS snapshots (
                source_id    TEXT NOT NULL,
                url          TEXT NOT NULL,
                signal_type  TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                lastmod      TEXT NOT NULL DEFAULT '',
                excerpt      TEXT NOT NULL DEFAULT '',
                fetched_at   TEXT NOT NULL,
                PRIMARY KEY (source_id, url)
            );

            CREATE TABLE IF NOT EXISTS changes (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id    TEXT NOT NULL,
                detected_at  TEXT NOT NULL,
                url          TEXT NOT NULL,
                change_kind  TEXT NOT NULL,
                title        TEXT NOT NULL DEFAULT '',
                raw_diff     TEXT NOT NULL DEFAULT '',
                description  TEXT,
                verdict      TEXT,
                fetched_hash TEXT NOT NULL DEFAULT '',
                -- supersede_older sets this to 1 so older undelivered rows for a
                -- (source_id, url) neither deliver nor recount in the digest.
                superseded   INTEGER NOT NULL DEFAULT 0,
                group_key      TEXT,
                group_summary  TEXT,
                group_title    TEXT
            );

            -- Idempotency key for record_change: re-detecting the same content
            -- hash for a url must not duplicate rows or reset its verdict.
            CREATE UNIQUE INDEX IF NOT EXISTS ux_changes_identity
                ON changes (source_id, url, fetched_hash);

            CREATE TABLE IF NOT EXISTS deliveries (
                change_id INTEGER NOT NULL,
                channel   TEXT NOT NULL,
                sent_at   TEXT NOT NULL,
                PRIMARY KEY (change_id, channel)
            );

            CREATE TABLE IF NOT EXISTS digests (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at   TEXT NOT NULL,
                committed_at TEXT
            );

            CREATE TABLE IF NOT EXISTS seen_feed_items (
                source_id    TEXT NOT NULL,
                item_id      TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                PRIMARY KEY (source_id, item_id)
            );

            CREATE TABLE IF NOT EXISTS http_cache (
                url           TEXT PRIMARY KEY,
                etag          TEXT NOT NULL DEFAULT '',
                last_modified TEXT NOT NULL DEFAULT '',
                fetched_at    TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS run_state (
                key      TEXT PRIMARY KEY,
                value    TEXT NOT NULL
            );
            """
		)
		self._conn.commit()
		self._add_column_if_missing("changes", "group_key", "TEXT")
		self._add_column_if_missing("changes", "group_summary", "TEXT")
		self._add_column_if_missing("changes", "group_title", "TEXT")
		self._add_column_if_missing("snapshots", "content_text", "TEXT NOT NULL DEFAULT ''")

	def _add_column_if_missing(self, table: str, column: str, decl: str) -> None:
		cols = {r["name"] for r in self._conn.execute(f"PRAGMA table_info({table})").fetchall()}
		if column not in cols:
			self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
			self._conn.commit()

	# snapshots ----------------------------------------------------------

	def snapshot_count(self) -> int:
		"""How many baseline snapshots exist (0 => a fresh, unseeded DB)."""
		row = self._conn.execute("SELECT COUNT(*) AS n FROM snapshots").fetchone()
		return int(row["n"])

	def source_has_snapshots(self, source_id: str) -> bool:
		"""Whether this source already has a baseline. A never-seen URL counts as
		genuinely 'new' only once the source has been baselined (so the first run /
		seed import does not flood every URL as new)."""
		row = self._conn.execute(
			"SELECT 1 FROM snapshots WHERE source_id = ? LIMIT 1", (source_id,)
		).fetchone()
		return row is not None

	def get_snapshot(self, source_id: str, url: str) -> Snapshot | None:
		row = self._conn.execute(
			"SELECT * FROM snapshots WHERE source_id = ? AND url = ?",
			(source_id, url),
		).fetchone()
		if row is None:
			return None
		return Snapshot(
			source_id=row["source_id"],
			url=row["url"],
			signal_type=row["signal_type"],
			content_hash=row["content_hash"],
			lastmod=row["lastmod"],
			excerpt=row["excerpt"],
			fetched_at=_parse_iso(row["fetched_at"]),
			content_text=row["content_text"],
		)

	def upsert_snapshot(
		self,
		source_id: str,
		url: str,
		*,
		signal_type: SignalType,
		content_hash: str,
		lastmod: str,
		excerpt: str,
		content_text: str = "",
	) -> None:
		self._conn.execute(
			"""
            INSERT INTO snapshots
                (source_id, url, signal_type, content_hash, lastmod, excerpt,
                 content_text, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_id, url) DO UPDATE SET
                signal_type = excluded.signal_type,
                content_hash = excluded.content_hash,
                lastmod = excluded.lastmod,
                excerpt = excluded.excerpt,
                content_text = excluded.content_text,
                fetched_at = excluded.fetched_at
            """,
			(source_id, url, signal_type, content_hash, lastmod, excerpt, content_text, _now_iso()),
		)
		self._conn.commit()

	# changes ------------------------------------------------------------

	def record_change(self, change: Change) -> int:
		"""Insert a change, IDEMPOTENT on (source_id, url, fetched_hash).

		If a row with that identity already exists, return its id without
		inserting or touching its verdict. Otherwise insert a fresh row with
		verdict = NULL.

		The insert uses ON CONFLICT DO NOTHING so the unique index
		ux_changes_identity enforces idempotency atomically, eliminating the
		SELECT-then-INSERT race window.
		"""
		self._conn.execute(
			"""
            INSERT INTO changes
                (source_id, detected_at, url, change_kind, title, raw_diff,
                 description, verdict, fetched_hash)
            VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?)
            ON CONFLICT(source_id, url, fetched_hash) DO NOTHING
            """,
			(
				change.source_id,
				_to_utc(change.detected_at).isoformat(),
				change.url,
				change.change_kind,
				change.title,
				change.raw_diff,
				change.description,
				change.fetched_hash,
			),
		)
		self._conn.commit()
		row = self._conn.execute(
			"SELECT id FROM changes WHERE source_id = ? AND url = ? AND fetched_hash = ?",
			(change.source_id, change.url, change.fetched_hash),
		).fetchone()
		change.id = int(row["id"])
		return change.id

	def changes_needing_triage(self) -> list[Change]:
		"""Every ledger row still awaiting a verdict (verdict IS NULL, not superseded).

		The triage worklist is sourced from the ledger, not just this run's fresh
		detections, so a change recorded during a run that could not triage (the
		triager returned unavailable, leaving the verdict NULL) is picked up and
		triaged on a later run. Without this it would never be re-detected — its
		content hash / feed seen-set already matches — and so would strand forever.
		"""
		rows = self._conn.execute(
			"""
            SELECT * FROM changes
            WHERE verdict IS NULL AND superseded = 0
            ORDER BY detected_at DESC, id DESC
            """
		).fetchall()
		return [self._row_to_change(r) for r in rows]

	def changes_for_digest(self, channels: set[str]) -> list[Change]:
		"""Substantive changes not yet delivered to EVERY channel in `channels`.

		CONTRACTS edge rules:
		  - If `channels` is empty, return [] (run_once also short-circuits
		    before opening a digest when no channel is enabled).
		  - At most ONE row per (source_id, url): the latest by detected_at
		    (then id, as a stable tiebreak). Older undelivered substantive rows
		    for the same (source_id, url) are not emitted, so a page that
		    changes twice before delivery yields one digest line about current
		    content, not one current + one stale.
		  - `superseded` rows are excluded entirely.
		A change is returned when it still misses at least one requested
		channel, so a prior run's undelivered backlog is retried.
		"""
		if not channels:
			return []
		rows = self._conn.execute(
			"""
            SELECT c.* FROM changes c
            WHERE c.verdict = 'substantive' AND c.superseded = 0
              AND c.id = (
                  SELECT c2.id FROM changes c2
                  WHERE c2.source_id = c.source_id AND c2.url = c.url
                    AND c2.verdict = 'substantive' AND c2.superseded = 0
                  ORDER BY c2.detected_at DESC, c2.id DESC
                  LIMIT 1
              )
            ORDER BY c.detected_at DESC, c.id DESC
            """
		).fetchall()
		result: list[Change] = []
		for r in rows:
			delivered = self.delivered_channels(int(r["id"]))
			if not channels <= delivered:
				result.append(self._row_to_change(r))
		return result

	def supersede_older(self, source_id: str, url: str, keep_id: int) -> None:
		"""Mark undelivered substantive rows for (source_id, url) other than
		keep_id as superseded, so they neither deliver nor recount."""
		self._conn.execute(
			"""
            UPDATE changes SET superseded = 1
            WHERE source_id = ? AND url = ? AND id != ?
              AND verdict = 'substantive' AND superseded = 0
              AND id NOT IN (SELECT change_id FROM deliveries)
            """,
			(source_id, url, keep_id),
		)
		self._conn.commit()

	def set_verdict(
		self,
		change_id: int,
		verdict: Verdict,
		description: str | None,
		group_key: str | None = None,
		group_summary: str | None = None,
		group_title: str | None = None,
	) -> None:
		"""WRITE-ONCE: only sets verdict/group fields on a row whose verdict IS NULL."""
		self._conn.execute(
			"UPDATE changes SET verdict = ?, description = ?, group_key = ?, group_summary = ?, "
			"group_title = ? WHERE id = ? AND verdict IS NULL",
			(verdict, description, group_key, group_summary, group_title, change_id),
		)
		self._conn.commit()

	@staticmethod
	def _row_to_change(row: sqlite3.Row) -> Change:
		return Change(
			source_id=row["source_id"],
			url=row["url"],
			change_kind=row["change_kind"],
			title=row["title"],
			raw_diff=row["raw_diff"],
			fetched_hash=row["fetched_hash"],
			detected_at=_parse_iso(row["detected_at"]),
			id=row["id"],
			verdict=row["verdict"],
			description=row["description"],
			group_key=row["group_key"],
			group_summary=row["group_summary"],
			group_title=row["group_title"],
		)

	# per-channel delivery -----------------------------------------------

	def delivered_channels(self, change_id: int) -> set[str]:
		rows = self._conn.execute(
			"SELECT channel FROM deliveries WHERE change_id = ?", (change_id,)
		).fetchall()
		return {r["channel"] for r in rows}

	def record_delivery(self, change_id: int, channel: str) -> None:
		self._conn.execute(
			"""
            INSERT INTO deliveries (change_id, channel, sent_at)
            VALUES (?, ?, ?)
            ON CONFLICT(change_id, channel) DO NOTHING
            """,
			(change_id, channel, _now_iso()),
		)
		self._conn.commit()

	# in-flight digest ---------------------------------------------------

	def open_digest(self) -> int:
		cur = self._conn.execute("INSERT INTO digests (created_at) VALUES (?)", (_now_iso(),))
		self._conn.commit()
		return int(cur.lastrowid)

	def commit_digest(self, digest_id: int) -> None:
		self._conn.execute(
			"UPDATE digests SET committed_at = ? WHERE id = ?",
			(_now_iso(), digest_id),
		)
		self._conn.commit()

	def inflight_digest(self) -> int | None:
		row = self._conn.execute(
			"SELECT id FROM digests WHERE committed_at IS NULL ORDER BY id DESC LIMIT 1"
		).fetchone()
		return int(row["id"]) if row else None

	# feed seen-set ------------------------------------------------------

	def seen_feed_item(self, source_id: str, item_id: str) -> str | None:
		row = self._conn.execute(
			"SELECT content_hash FROM seen_feed_items WHERE source_id = ? AND item_id = ?",
			(source_id, item_id),
		).fetchone()
		return row["content_hash"] if row else None

	def upsert_seen_feed_item(self, source_id: str, item_id: str, content_hash: str) -> None:
		self._conn.execute(
			"""
            INSERT INTO seen_feed_items (source_id, item_id, content_hash)
            VALUES (?, ?, ?)
            ON CONFLICT(source_id, item_id) DO UPDATE SET
                content_hash = excluded.content_hash
            """,
			(source_id, item_id, content_hash),
		)
		self._conn.commit()

	# http conditional-GET cache -----------------------------------------

	def http_cache_get(self, url: str) -> tuple[str, str]:
		row = self._conn.execute(
			"SELECT etag, last_modified FROM http_cache WHERE url = ?", (url,)
		).fetchone()
		if row is None:
			return ("", "")
		return (row["etag"], row["last_modified"])

	def http_cache_put(self, url: str, etag: str, last_modified: str) -> None:
		self._conn.execute(
			"""
            INSERT INTO http_cache (url, etag, last_modified, fetched_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(url) DO UPDATE SET
                etag = excluded.etag,
                last_modified = excluded.last_modified,
                fetched_at = excluded.fetched_at
            """,
			(url, etag, last_modified, _now_iso()),
		)
		self._conn.commit()

	# run bookkeeping ----------------------------------------------------

	def last_successful_run(self) -> datetime | None:
		row = self._conn.execute(
			"SELECT value FROM run_state WHERE key = 'last_successful_run'"
		).fetchone()
		return _parse_iso(row["value"]) if row else None

	def mark_successful_run(self, when: datetime) -> None:
		self._conn.execute(
			"""
            INSERT INTO run_state (key, value) VALUES ('last_successful_run', ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
			(_to_utc(when).isoformat(),),
		)
		self._conn.commit()

	# seed import / export -----------------------------------------------
	#
	# A shipped seed is a pre-built baseline (snapshots + feed seen-set + HTTP
	# validators) tagged with the date it was generated. Importing it on a fresh
	# DB gives users a starting point so the first scheduled run diffs against it
	# instead of crawling every page to establish a baseline.

	# Tables carried in a seed; run_state's seed_date marker is appended separately.
	_SEED_TABLES = ("snapshots", "seen_feed_items", "http_cache")

	# Columns held back from the seed. content_text is the full page body, a local
	# runtime cache used only to diff a page against its prior version; it is not
	# needed to detect changes (content_hash does that) and shipping it would bloat
	# the seed past GitHub's 100MB file limit and the wheel. Seeded rows import it
	# empty and self-heal on the first change per page.
	_SEED_EXCLUDED_COLS = frozenset({"content_text"})

	def seed_date(self) -> str | None:
		"""The date the imported baseline was generated, or None if unseeded."""
		row = self._conn.execute("SELECT value FROM run_state WHERE key = 'seed_date'").fetchone()
		return row["value"] if row else None

	def export_seed_sql(self, seed_date: str) -> str:
		"""Serialize the baseline tables to portable `INSERT OR IGNORE` SQL.

		Emits no schema (the importing DB already migrated), so it layers onto an
		existing schema without clashing. The maintainer seed-builder gzips this.
		"""
		lines = [f"-- android-watcher seed; generated {seed_date}"]
		for table in self._SEED_TABLES:
			rows = self._conn.execute(f"SELECT * FROM {table}").fetchall()
			for row in rows:
				cols = [c for c in row.keys() if c not in self._SEED_EXCLUDED_COLS]
				vals = ", ".join(_sql_str(row[c]) for c in cols)
				lines.append(f"INSERT OR IGNORE INTO {table} ({', '.join(cols)}) VALUES ({vals});")
		lines.append(
			"INSERT OR IGNORE INTO run_state (key, value) VALUES "
			f"('seed_date', {_sql_str(seed_date)});"
		)
		return "\n".join(lines) + "\n"

	def import_seed_sql(self, sql: str) -> None:
		"""Apply seed `INSERT OR IGNORE` statements; existing rows are preserved."""
		self._conn.executescript(sql)
		self._conn.commit()
