# CLAUDE.md

Implementation guide for `android-watcher`. The rules here are the durable source
of truth. Build to these, not to memory.

## What this is

A self-hosted Python CLI that watches official Google Android sites, detects
real (not cosmetic) changes on a schedule, uses Claude to triage and describe
them, ranks the result, and delivers a digest to email or Slack. Single user per
install. Configured through a Textual TUI that writes a TOML file and installs a
native scheduled job.

## Commands

```bash
uv sync                 # install deps from the lockfile
uv run pytest           # run tests (no live network; recorded fixtures only)
uv run pytest path::test -v   # one test
uv run ruff check .     # lint
uv run ruff format .    # format
uv tool install .       # install the CLI locally
```

## Package layout

```
src/android_watcher/
  models.py          # dataclasses, exceptions, Check, SignalType, INTERVAL_DELTA
  config.py          # Config + load_config (env interpolation, validation)
  catalog/           # catalog.toml (shipped data) + load_catalog
  store.py           # SQLite: snapshots, changes, deliveries, digests, seen_feed_items, http_cache, run_state; seed import/export
  seed/              # bundled baseline seed (seed.sql.gz) + apply_seed_if_empty
  lock.py            # single-instance run lock
  fetch.py           # async Fetcher: conditional GET, robots, backoff, sitemap cache
  registry.py        # generic name->class registry
  detect/            # base + feed, android_sitemap, sitemap, content
  rank.py            # scoring, per-source caps, overflow
  triage/            # base + claude_cli, noop
  notify/            # base + render, email, slack
  schedule.py        # launchd / systemd / crontab install/remove/status
  doctor.py          # health checks
  run.py             # run_once pipeline
  cli.py             # `android-watcher` entrypoint
  tui/               # Textual app + pure config<->TOML module
tests/               # mirrors src; tests/fixtures/ for recorded data
```

## Durable invariants

These are the rules that make the tool correct. They were hard-won; do not relax
them without understanding why they exist.

### Detection: candidate then confirm

- A feed item, a sitemap `lastmod` bump, or a content-hash change is a *candidate*, not a change. Confirm it against the
  actual fetched page content before recording a change. `lastmod` alone never counts: Google bumps it on bulk
  regenerations, template edits, and translation passes.
- A detector never emits a `Change` for a health problem (an empty/JS-only render, a path prefix that matches zero
  sitemap URLs). It logs a warning and returns nothing; `doctor` surfaces the condition.
- The content detector refuses to baseline a page whose extracted text is below a small threshold (a client-rendered
  shell), so it never silently hashes nothing forever.
- Feed dedupe keys on a stable identity: the Atom `<id>` verbatim, else a normalized link URL. Persist the full
  seen-set. An existing item counts as changed only when its title+summary hash moves.
- The `android_sitemap` detector is host-agnostic: it parses a host's sitemap (a `<sitemapindex>` of shards, or a single
  `<urlset>`) once per run, cached on the `Fetcher` keyed by the sitemap-index URL derived from each source's host (
  `<scheme>://<host>/sitemap.xml`). Sources on the same host share one download (guarded by an `asyncio.Lock`,
  conditional GET per shard); different hosts each get their own. It serves developer.android.com, source.android.com,
  developers.google.com, kotlinlang.org, etc. — never download a host's shards per source.
- English only: locale-prefixed URLs (`/fr/...`, `/pt-br/...`) and `?hl=<non-en>` query variants are dropped at parse
  time, so only canonical English pages are watched. Match the leading path segment against an explicit locale
  allowlist, never a generic two-letter regex. Real sections like `/tv`, `/xr`, `/ai` start with two letters and must
  not be mistaken for locales.
- Per-source filters (applied against the shared cached entry list): `path_prefix` (include; `""` = whole host),
  `exclude_prefixes` (drop subtrees), `require_segment` (keep only URLs with a matching path segment, e.g. `android`),
  and `reference_mode` (`keep` | `drop` | `index_only`). `index_only` keeps only reference index/summary pages — leaf in
  `{package-summary, packages, classes, composables, modifiers}`, Kotlin-preferred (a Java reference page is dropped
  when its `/reference/kotlin/...` twin exists), so the huge per-symbol class/function reference is excluded.
  Most-specific-prefix-wins (scoped per host) routes a URL to its nested source; a `""` catch-all coexists with curated
  sources for ranking weights.
- Version-dedup: URLs differing only by a dotted version segment (`9.4`) or a `?version=`/`?api=` query collapse to the
  latest. Bare-integer paths (`/about/versions/14` vs `/15`) are untouched: those are distinct releases, not versions of
  one page.
- Fetch-free first sight, then new-page detection: when a source has no baseline yet (first run / seed import), a
  brand-new URL is baselined from its `lastmod` alone (empty `content_hash`, no fetch), with no "everything is new"
  flood. Once a baseline exists, a never-seen URL is content-confirmed and reported as `Change(change_kind="new")`. An
  already-baselined URL whose `lastmod` moves is content-confirmed; the first real fetch of a fetch-free baseline is
  itself a silent capture (`confirm_candidate` treats an empty prior `content_hash` as first sight). `lastmod` alone
  never emits a `Change`.
- All XML parsing uses `defusedxml`. Honor `robots.txt`. Send a descriptive User-Agent and a crawl delay.

### Pipeline: the delivery ledger

- `run_once` holds a single-instance lock. Overlapping runs exit immediately.
- The authoritative digest source is the ledger, never this-run detections. Rank `changes_for_digest(enabled_channels)`:
  substantive changes not yet delivered to every enabled channel, at most one row per `(source_id, url)` (latest by
  `detected_at`).
- `record_change` is idempotent on `(source_id, url, fetched_hash)`: it returns the existing row id and never resets a
  verdict.
- `set_verdict` is write-once. Triage only touches rows with `verdict IS NULL`.
- When a ranked change is delivered, `supersede_older` marks older undelivered rows for the same `(source_id, url)` so a
  page that changed twice yields one digest line, not a stale one.
- Delivery is per `(change, channel)`, recorded in `deliveries`. Send, then record the delivery transactionally. A
  channel that already succeeded is never re-sent; a channel that failed is retried next run.
- An in-flight `digests` row is opened before sending and reconciled on the next startup: re-deliver the undelivered
  channels, then commit. This closes the crash-between-send-and-commit window.
- First run baselines silently (no "everything is new" flood); the first digest after baseline is capped.
- A fresh DB imports the bundled baseline seed (`seed/seed.sql.gz`) before detecting: `apply_seed_if_empty` loads it
  only when `snapshot_count() == 0`, via `INSERT OR IGNORE` so user data is never overwritten. The seed carries
  snapshots + feed seen-set + HTTP validators, tagged with a `seed_date` in `run_state` (never `last_successful_run`).
  The seed is generated by `scripts/build_seed.py` (the one expensive full-content crawl — run locally or via the `seed`
  workflow, never in `release.yml`) and committed; the build bundles it via the wheel `artifacts` glob. When absent,
  import is a no-op and the detectors baseline fetch-free instead. `doctor` surfaces the seed date and snapshot count.
- An empty "nothing notable" digest is sent at most once per catch-up window.
- Zero channels enabled: short-circuit before opening a digest, still mark the run successful.
- A missed cycle (machine asleep) is backfilled via `last_successful_run` and `INTERVAL_DELTA`, not dropped.

### AI / triage

- `claude_cli` shells out to `claude -p --output-format json`, strips a markdown code fence from the result before
  parsing, and on any failure returns `TriageResult(unavailable=<reason>)` without raising. The digest still goes out,
  with a visible "AI unavailable" banner.
- Fetched page content is untrusted. Wrap it in per-run nonce-fenced blocks, length-cap it, and instruct the model to
  treat it as data, never instructions.
- `noop` (AI off) marks every change substantive with no description and does not filter.

### Config and secrets

- Paths come from `platformdirs`. The config file is written `0600`.
- String values support `${ENV_VAR}` interpolation on secret-bearing fields, resolved at load. The TUI editor loads with
  `expand=False` so it preserves `${...}` literals and never crashes on an unset variable.
- Source selection: start from catalog entries with `enabled=True`; if the user's `enabled_sources` list is non-empty,
  intersect with it; an empty or absent list means "use the catalog flags," not "none." Custom sources are always
  watched; on id collision a custom source overrides the catalog. The TUI writes the reserved id `["__none__"]` to
  mean "watch no catalog sources."
- SMTP enforces TLS and fails closed. The Slack bot token is a secret.

### Conventions

- Python 3.11+. Async lives only in `fetch.py` and detectors; everything else is synchronous. `run_once` drives the
  async detectors with `asyncio.run`.
- Always `datetime.now(timezone.utc)`; never the naive `utcnow()`. The store coerces to UTC-aware at its boundary.
- Shared types, the four exceptions (`ConfigError`, `AlreadyRunning`, `Disallowed`, `NotifyError`), `Check`,
  `SignalType`, and `INTERVAL_DELTA` are defined once in `models.py` and imported everywhere, which keeps the import
  graph acyclic.
- Registries store classes. `get(name)` returns the class; callers instantiate it with no arguments. Unknown names raise
  with a message listing the available ones.
- TDD: write the failing test first with real assertions, make it pass with the minimal real code, commit. No live
  network in tests; use recorded fixtures under `tests/fixtures/`. A test must exercise the guarantee, not echo the
  implementation.

## No Stale Plan References

**IMPORTANT:** After any refactor or new feature, do NOT leave references in code or markdown to what the plan
was — no "per the plan", no "according to decision N", no plan phase or task numbers, no pointer to the
planning or design doc. Those planning docs are scratch that won't survive, so a citation to them becomes a
stale, dangling reference the moment they're deleted. State the fact or rule directly; if a decision's
rationale matters, write the rationale itself, not a pointer to where it was decided.
