<p align="center">
  <img src="assets/logo.svg" alt="Android Watcher" width="160">
</p>

<h1 align="center">Android Watcher</h1>

<p align="center">
  Watch every official Google site for Android and Android developers, and get a
  ranked, AI-triaged digest when something actually changes.
</p>

<p align="center">
  <a href="https://github.com/krayong/android-watcher/actions/workflows/ci.yml"><img src="https://github.com/krayong/android-watcher/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="https://pypi.org/project/android-watcher/"><img src="https://img.shields.io/pypi/v/android-watcher.svg" alt="PyPI"></a>
  <img src="https://img.shields.io/badge/python-3.11%2B-blue.svg" alt="Python 3.11+">
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-yellow.svg" alt="License: MIT"></a>
  <a href="https://github.com/astral-sh/ruff"><img src="https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json" alt="Ruff"></a>
  <a href="https://github.com/pre-commit/pre-commit"><img src="https://img.shields.io/badge/pre--commit-enabled-brightgreen?logo=pre-commit" alt="pre-commit"></a>
</p>

<p align="center">
  Self-hosted CLI. No cloud subscription. MIT.
</p>

---

## What it does

Google ships Android news across dozens of properties: platform and AOSP release
notes, Android Studio and Gradle Plugin changelogs, the AndroidX feeds, the
Developers Blog, Material Design, YouTube, and many sections of
`developer.android.com`. Keeping up means babysitting a pile of RSS readers and
bookmarks, and most of what changes is a typo fix or a template reflow.

`android-watcher` checks a curated catalog of official sources on a schedule,
detects real changes (not cosmetic churn), uses Claude to decide what is worth
your attention and write a short description, ranks the result, and delivers a
digest to email, Slack, or Telegram. When nothing substantive changed, it says so
instead of padding a digest with noise.

Install it once, run the interactive setup wizard, and receive a daily (or
hourly, or weekly) digest.

---

## Install

**Homebrew** (recommended):

```sh
brew tap krayong/android-watcher https://github.com/krayong/android-watcher
brew install android-watcher
```

The tap points straight at this repo's `Formula/`, so `brew upgrade` tracks new
releases. (A bare `brew install android-watcher` without the tap only works once a
formula is accepted into homebrew-core.)

**PyPI** (via uv or pipx):

```sh
uv tool install android-watcher     # via uv
pipx install android-watcher        # via pipx
```

**From source:**

```sh
git clone https://github.com/krayong/android-watcher
cd android-watcher
uv tool install .
```

Requires **Python 3.11+**.

**AI prerequisite:** The triage backend calls `claude -p` (the Claude CLI). No
API key is required; the CLI handles authentication. Install and authenticate
`claude` separately if you want AI triage. To skip it, set
`[ai] mode = "off"` and android-watcher still delivers a digest (every detected
change is marked substantive, with no description).

---

## Quickstart

```sh
android-watcher                        # open the Textual setup wizard
android-watcher test                   # dry run: render the digest to stdout, send nothing
android-watcher run                    # one detection-triage-notify pass
android-watcher schedule install       # install the native launchd / systemd / cron entry
android-watcher schedule status        # confirm the entry is loaded and active
android-watcher schedule remove        # uninstall the scheduled job
android-watcher doctor                 # health checks: prefixes, AI reachable, schedule active
android-watcher catalog                # list and inspect the shipped source catalog
android-watcher --help                 # full flag reference
```

Bare `android-watcher` opens the Textual setup wizard. It walks you through config
and writes a native scheduled job. The first pipeline run baselines each source
silently. It does not send a digest claiming every page is new.

---

## Configuration

Config lives at `~/.config/android-watcher/config.toml` (honoring
`$XDG_CONFIG_HOME`) and is written with `0600` permissions. State (snapshots,
change history, delivery ledger) lives in a SQLite database under
`~/.local/share/android-watcher/`. Override the config path with `--config PATH`.

The setup wizard writes the file for you. To edit manually:

```toml
[schedule]
interval = "daily"     # hourly | daily | weekly | cron
at = "09:00"           # local time; ignored when interval = "cron"
cron = ""              # raw cron expression; only used when interval = "cron"

[ai]
mode = "claude_cli"    # claude_cli | off
model = "claude-sonnet-4-6"

[digest]
max_items = 10         # cap on change-groups shown in the message; the rest collapse into the full digest page
empty = "send"         # send | skip  (what to do when nothing substantive changed)

[sort]
# Optional per-source or per-category priority overrides (higher = ranked first).
# "android-developers-blog" = 90

[channels.email]
enabled = true
smtp_host = "smtp.example.com"
smtp_port = 465                                    # TLS required: implicit (465) or STARTTLS
username = "you@example.com"
password = "${ANDROID_WATCH_SMTP_PASSWORD}"        # env-var ref recommended; see Security
from = "you@example.com"
to = "you@example.com"

[channels.slack]
enabled = true
bot_token = "${ANDROID_WATCH_SLACK_TOKEN}"        # env-var ref recommended (bot token is a secret)
channel = "C0123456789"                           # channel ID (or #channel-name)

[channels.telegram]
enabled = false
bot_token = "${ANDROID_WATCH_TELEGRAM_TOKEN}"     # env-var ref recommended (bot token is a secret)
chat_id = "123456789"

# Add your own URLs (same shape as catalog entries):
# [[custom_source]]
# id = "my-blog"
# name = "My Blog"
# category = "dev-blog"
# detector = "feed"
# url = "https://example.com"
# feed_url = "https://example.com/feed.xml"
# enabled = true
```

### Secrets

The secrets android-watcher uses are the SMTP password, the Slack bot token, and
the Telegram bot token. Use **environment-variable references** so those values
are never written into the config file:

```sh
export ANDROID_WATCH_SMTP_PASSWORD="hunter2"
export ANDROID_WATCH_SLACK_TOKEN="xoxb-..."
export ANDROID_WATCH_TELEGRAM_TOKEN="1234567890:AAF..."
```

The config stores `${ANDROID_WATCH_SMTP_PASSWORD}` literally; the value is
resolved at runtime. Inline plaintext works but is discouraged.

---

## The catalog

A curated catalog of 41 official Android and Android-developer sources ships
inside the package. `android-watcher catalog` lists it. The setup wizard lets you
enable or disable catalog entries and add custom sources.

To propose a new official source, edit `src/android_watcher/catalog/catalog.toml`
and open a pull request (see [CONTRIBUTING.md](CONTRIBUTING.md)).

---

## Scheduling

After the wizard completes, install the native scheduled job:

```sh
android-watcher schedule install
```

On macOS this writes a launchd plist to `~/Library/LaunchAgents/` and loads it
via `launchctl`. On Linux with systemd it writes a user timer and enables it;
run `loginctl enable-linger $USER` once so the timer fires after logout. On
Linux without systemd it writes a marked crontab block.

Confirm the job is active:

```sh
android-watcher schedule status
```

If the machine was asleep during a scheduled cycle, android-watcher detects the
missed run on the next wake and catches up automatically.

---

## Security and privacy

**Secrets.** The secrets are the SMTP password, the Slack bot token, and the
Telegram bot token. The config file is written `0600`. Prefer environment-variable
references (`password = "${ANDROID_WATCH_SMTP_PASSWORD}"`) so plaintext values
are never written to disk.

**Keep config out of git.** If you keep your config under version control, never
commit a file with inline secrets. Add to `.gitignore`:

```gitignore
# android-watcher
config.toml
*.android-watcher.toml
```

The TUI and `--config` warn when the config path is inside a git work tree,
because an accidental commit would expose your SMTP password, Slack bot token, or
Telegram bot token.

**AI data egress.** When `[ai] mode = "claude_cli"`, the **content of changed
pages is sent to the `claude` CLI** for triage and description. For the shipped
catalog this is public Google documentation. If you add **custom sources** (such
as an internal wiki), that page content is also sent to `claude`. Set
`[ai] mode = "off"` for the no-egress path: no triage, no descriptions, no
page content leaves your machine.

**SMTP transport.** SMTP enforces TLS (implicit on port 465 or mandatory
STARTTLS) with certificate verification. It fails closed rather than downgrading
to plaintext.

**Slack and Telegram.** The Slack bot token and Telegram bot token are treated
as bearer secrets and are never logged.

---

## Commands

| Command                            | What it does                                           |
|------------------------------------|--------------------------------------------------------|
| `android-watcher`                  | Open the Textual setup wizard                          |
| `android-watcher run [--force]`    | One detection-triage-notify pass                       |
| `android-watcher test`             | Dry run: render digest to stdout, send nothing         |
| `android-watcher doctor`           | Health checks: prefixes, AI reachable, schedule active |
| `android-watcher catalog`          | List and inspect the shipped source catalog            |
| `android-watcher schedule install` | Install the native scheduled job                       |
| `android-watcher schedule status`  | Show whether the job is loaded and active              |
| `android-watcher schedule remove`  | Uninstall the scheduled job                            |
| `android-watcher --config PATH`    | Use a specific config file                             |
| `android-watcher --version`        | Print version                                          |

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Short version: add a source by editing
`catalog.toml`; add a detector, triager, or channel by implementing the
protocol, registering the name, and adding a fixture-backed test. TDD, `ruff`,
and `pytest` must pass.

---

## Contact

For questions or support, open an issue on [GitHub](https://github.com/krayong/android-watcher/issues)
or email **androidwatcher@krayong.com**. Security reports: see [SECURITY.md](SECURITY.md).

---

## License

[MIT](LICENSE).
