import pytest

from android_watcher.config import (
	Config,
	ConfigError,
	config_path,
	data_path,
	db_path,
	load_config,
)


def _write(tmp_path, text):
	p = tmp_path / "config.toml"
	p.write_text(text)
	return str(p)


def test_load_minimal_config_defaults(tmp_path):
	cfg = load_config(_write(tmp_path, ""))
	assert isinstance(cfg, Config)
	assert cfg.schedule.interval == "daily"
	assert cfg.schedule.at == "09:00"
	assert cfg.ai.mode == "claude_cli"
	assert cfg.ai.model == "claude-sonnet-4-6"
	assert cfg.digest.max_items == 10
	assert cfg.digest.empty == "send"
	assert cfg.email.enabled is False
	assert cfg.slack.enabled is False
	assert cfg.custom_sources == []
	assert cfg.enabled_source_ids == set()


def test_desktop_channel_defaults_disabled(tmp_path):
	cfg = load_config(_write(tmp_path, ""))
	assert cfg.desktop.enabled is False
	assert cfg.desktop.sound == "Glass"


def test_desktop_channel_loads_enabled(tmp_path):
	text = '[channels.desktop]\nenabled = true\nsound = "Ping"\n'
	cfg = load_config(_write(tmp_path, text))
	assert cfg.desktop.enabled is True
	assert cfg.desktop.sound == "Ping"


def test_digests_dir_under_data_path(monkeypatch, tmp_path):
	from android_watcher.config import digests_dir

	monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
	assert digests_dir().startswith(data_path())
	assert digests_dir().rstrip("/").endswith("digests")


def test_desktop_mechanism_available_reflects_binary(monkeypatch):
	import android_watcher.config as cfg_mod

	monkeypatch.setattr(cfg_mod.sys, "platform", "darwin")
	monkeypatch.setattr(cfg_mod.shutil, "which", lambda b: "/usr/local/bin/terminal-notifier")
	assert cfg_mod.desktop_mechanism_available() is True

	monkeypatch.setattr(cfg_mod.shutil, "which", lambda b: None)
	assert cfg_mod.desktop_mechanism_available() is False


def test_email_from_to_keys_map_to_sender_recipient(tmp_path):
	text = """
[channels.email]
enabled = true
smtp_host = "smtp.example.com"
smtp_port = 465
username = "me@example.com"
from = "me@example.com"
to = "you@example.com"
"""
	cfg = load_config(_write(tmp_path, text))
	assert cfg.email.sender == "me@example.com"
	assert cfg.email.recipient == "you@example.com"


def test_env_var_interpolation(tmp_path, monkeypatch):
	monkeypatch.setenv("AW_SMTP_PW", "s3cret")
	monkeypatch.setenv("AW_SLACK_TOKEN", "xoxb-real-token")
	text = """
[channels.email]
enabled = true
password = "${AW_SMTP_PW}"

[channels.slack]
enabled = true
bot_token = "${AW_SLACK_TOKEN}"
channel = "#updates"
"""
	cfg = load_config(_write(tmp_path, text))
	assert cfg.email.password == "s3cret"
	assert cfg.slack.bot_token == "xoxb-real-token"


def test_missing_env_var_raises(tmp_path):
	text = """
[channels.slack]
enabled = true
bot_token = "${DOES_NOT_EXIST_AW}"
channel = "#dev"
"""
	with pytest.raises(ConfigError):
		load_config(_write(tmp_path, text))


def test_inline_plaintext_secret_passes_through(tmp_path):
	text = """
[channels.slack]
enabled = true
bot_token = "xoxb-literal-token"
channel = "#dev"
"""
	cfg = load_config(_write(tmp_path, text))
	assert cfg.slack.bot_token == "xoxb-literal-token"


def test_literal_dollar_brace_in_url_is_not_interpolated(tmp_path):
	# A non-secret field with a "${" must never be treated as an env reference,
	# even though no such env var exists.
	text = """
[[custom_source]]
id = "tpl"
name = "Templated"
category = "guides"
detector = "content"
url = "https://e/${path}/changes"
content_selector = "main"
"""
	cfg = load_config(_write(tmp_path, text))
	assert cfg.custom_sources[0].url == "https://e/${path}/changes"


def test_expand_false_preserves_literals_and_tolerates_unset(tmp_path):
	text = """
[channels.email]
enabled = true
password = "${AW_SMTP_PW}"

[channels.slack]
enabled = true
bot_token = "${DOES_NOT_EXIST_AW}"
channel = "#dev"
"""
	# expand=False: literals preserved, no raise on unset vars (TUI editor path).
	cfg = load_config(_write(tmp_path, text), expand=False)
	assert cfg.email.password == "${AW_SMTP_PW}"
	assert cfg.slack.bot_token == "${DOES_NOT_EXIST_AW}"


def test_sort_and_enabled_sources_and_custom(tmp_path):
	text = """
enabled_sources = ["android-dev-blog", "aosp"]

[sort]
"android-dev-blog" = 90
"guides" = 5

[[custom_source]]
id = "my-wiki"
name = "Internal Wiki"
category = "guides"
detector = "content"
url = "https://wiki.internal/changes"
content_selector = "#content"
"""
	cfg = load_config(_write(tmp_path, text))
	assert cfg.enabled_source_ids == {"android-dev-blog", "aosp"}
	assert cfg.sort == {"android-dev-blog": 90, "guides": 5}
	assert len(cfg.custom_sources) == 1
	assert cfg.custom_sources[0].id == "my-wiki"
	assert cfg.custom_sources[0].detector == "content"


def test_schedule_cron_requires_cron_string(tmp_path):
	text = """
[schedule]
interval = "cron"
cron = ""
"""
	with pytest.raises(ConfigError):
		load_config(_write(tmp_path, text))


def test_schedule_cron_set_but_interval_not_cron(tmp_path):
	text = """
[schedule]
interval = "daily"
cron = "0 9 * * *"
"""
	with pytest.raises(ConfigError):
		load_config(_write(tmp_path, text))


def test_schedule_valid_cron(tmp_path):
	text = """
[schedule]
interval = "cron"
cron = "0 9 * * *"
"""
	cfg = load_config(_write(tmp_path, text))
	assert cfg.schedule.interval == "cron"
	assert cfg.schedule.cron == "0 9 * * *"


def test_invalid_interval_rejected(tmp_path):
	text = """
[schedule]
interval = "fortnightly"
"""
	with pytest.raises(ConfigError):
		load_config(_write(tmp_path, text))


def test_schedule_env_loaded(tmp_path):
	text = """
[schedule]
interval = "daily"
at = "09:00"

[schedule.env]
CLAUDE_ACCOUNT = "personal"
"""
	cfg = load_config(_write(tmp_path, text))
	assert cfg.schedule.env == {"CLAUDE_ACCOUNT": "personal"}


def test_schedule_env_defaults_empty(tmp_path):
	cfg = load_config(_write(tmp_path, '[schedule]\ninterval = "daily"\n'))
	assert cfg.schedule.env == {}


def test_paths(monkeypatch, tmp_path):
	monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
	monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
	assert config_path().endswith("android-watcher/config.toml")
	assert data_path().endswith("android-watcher")
	assert db_path().endswith("android-watcher/state.db")


def test_missing_config_returns_defaults(tmp_path):
	missing = str(tmp_path / "nope.toml")
	cfg = load_config(missing)
	assert cfg.schedule.interval == "daily"


def test_slack_bot_token_env_var_interpolation(tmp_path, monkeypatch):
	monkeypatch.setenv("AW_BOT_TOKEN", "xoxb-live-token")
	text = """
[channels.slack]
enabled = true
bot_token = "${AW_BOT_TOKEN}"
channel = "#releases"
"""
	cfg = load_config(_write(tmp_path, text))
	assert cfg.slack.bot_token == "xoxb-live-token"
	assert cfg.slack.channel == "#releases"


def test_slack_bot_token_expand_false_preserved(tmp_path):
	text = """
[channels.slack]
enabled = true
bot_token = "${AW_BOT_TOKEN}"
channel = "#releases"
"""
	cfg = load_config(_write(tmp_path, text), expand=False)
	assert cfg.slack.bot_token == "${AW_BOT_TOKEN}"
	assert cfg.slack.channel == "#releases"
