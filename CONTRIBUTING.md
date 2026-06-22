# Contributing to android-watcher

Thanks for helping. The parts people most want to extend, sources, detectors,
triagers, and channels, are small and well-bounded. Everything lives in this
repo; there is no external plugin system.

## Dev setup

```sh
git clone https://github.com/krayong/android-watcher
cd android-watcher
uv sync                  # install deps from the lockfile
uv run pytest            # run the test suite (no live network; recorded fixtures only)
uv run ruff check .      # lint
uv run ruff format .     # format
uv tool install .        # install the CLI locally for manual smoke tests
uv run pre-commit install   # run ruff lint + format automatically on each commit
```

Python 3.11+ required. No live network in tests; add recorded fixtures under
`tests/fixtures/` for anything that fetches from the internet.

The linter and formatter are both [ruff](https://docs.astral.sh/ruff/),
configured in `pyproject.toml` (`[tool.ruff]`). `pre-commit` runs them on every
commit; run them across the whole tree with `uv run pre-commit run --all-files`.

## Adding a catalog source

The shipped source catalog lives in `src/android_watcher/catalog/catalog.toml`.
Add an entry following the existing shape:

```toml
[[source]]
id              = "my-source"           # unique slug
name            = "My Source"
category        = "dev-blog"            # platform-release | api-reference | tooling | guides | dev-blog | design | news
detector        = "feed"                # feed | android_sitemap | sitemap | content
url             = "https://example.com"
feed_url        = "https://example.com/feed.xml"   # feed only
path_prefix     = ""                    # android_sitemap only
content_selector = ""                   # content only (optional)
enabled         = true
default_weight  = 0                     # 0 = use category weight
```

Then run `uv run pytest tests/test_catalog_data.py`. The validator enforces
unique ids, known category and detector values, and the required field per
detector type (`android_sitemap` needs `path_prefix`; an enabled `feed` needs
`feed_url`).

Before opening a PR, run the optional live check to confirm the URL resolves and
the page renders server-side:

```sh
uv run python -m scripts.verify_catalog
```

Prefer `feed` over `content` for blogs and changelogs that publish one. Prefer
`android_sitemap` for `developer.android.com` sections.

## Adding a detector, triager, or channel

The three extension points follow the same pattern:

1. **Implement the protocol.** Each kind has a base class and a protocol in
   `src/android_watcher/`:

   | Kind | Module | Base |
         |---|---|---|
   | Detector | `detect/` | `detect.base.Detector` |
   | Triager | `triage/` | `triage.base.Triager` |
   | Channel (notifier) | `notify/` | `notify.base.Notifier` |

2. **Register the name.** At the bottom of your module, call the registry so
   the name resolves at runtime:

   ```python
   from android_watcher.registry import DETECTORS  # or TRIAGERS / NOTIFIERS
   DETECTORS.register("my-detector", MyDetector)
   ```

3. **Add a fixture-backed test.** Record a real HTTP response under
   `tests/fixtures/` and write a test that:
	- Proves a genuine change signals (the detector emits a `Change`).
	- Proves cosmetic churn does not signal (same extracted text, different
	  template noise).

   For a channel, add a render test and a send test (mock the transport).
   For a triager, add a test covering the `unavailable` fallback path.

4. **Wire up the TUI and README.** For a new channel, add it to the TUI
   channel list and document its config keys in the README.

No other wiring is required. Registration at import time is the only hook.

## Pull requests

- TDD: write a failing test first, then the implementation.
- Conventional commit prefixes: `feat:` / `fix:` / `test:` / `docs:` / `chore:`.
- `uv run ruff check .` and `uv run pytest` must pass. CI runs both.
- By contributing you agree your work is licensed under MIT.

## Code of Conduct

This project follows the [Contributor Covenant](CODE_OF_CONDUCT.md). Be
respectful and constructive.
