"""Tests for tui/configio.py — pure Config<->TOML (de)serialization."""

from __future__ import annotations

from pathlib import Path
from stat import S_IMODE

import pytest

from android_watcher.config import (
	AIConfig,
	Config,
	DigestConfig,
	EmailChannel,
	ScheduleConfig,
	SlackChannel,
	TelegramChannel,
	load_config,
)
from android_watcher.models import Source
from android_watcher.tui.configio import (
	config_to_toml,
	load_or_default,
	validate_config,
	write_config,
)

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _make_full_config() -> Config:
	"""A Config with every field populated, using ${...} secret refs."""
	return Config(
		schedule=ScheduleConfig(interval="daily", at="09:00"),
		ai=AIConfig(mode="claude_cli", model="claude-opus-4-8"),
		digest=DigestConfig(max_items=5, empty="send"),
		sort={"security": 10, "releases": 5},
		email=EmailChannel(
			enabled=True,
			smtp_host="smtp.example.com",
			smtp_port=465,
			username="user@example.com",
			password="${SMTP_PW}",
			sender="from@example.com",
			recipient="to@example.com",
		),
		slack=SlackChannel(enabled=True, bot_token="${SLACK_BOT}", channel="#dev"),
		telegram=TelegramChannel(enabled=True, bot_token="${TG_TOKEN}", chat_id="-100123"),
		custom_sources=[
			Source(
				id="custom-1",
				name="Custom One",
				category="releases",
				detector="content",
				url="https://example.com/page",
				enabled=True,
				path_prefix="/blog",
				feed_url="https://example.com/feed.xml",
				content_selector="main",
				default_weight=3,
			)
		],
		enabled_source_ids={"android-versions", "compose-releases"},
	)


# ---------------------------------------------------------------------------
# Commit 2 tests
# ---------------------------------------------------------------------------


def test_write_perms_0600(tmp_path: Path) -> None:
	"""write_config must chmod the file to 0600."""
	cfg = _make_full_config()
	p = tmp_path / "config.toml"
	write_config(cfg, str(p))
	assert S_IMODE(p.stat().st_mode) == 0o600


def test_roundtrip_preserves_fields(tmp_path: Path) -> None:
	"""Round-trip: Config -> write_config -> load_config(expand=False) -> equal fields."""
	cfg = _make_full_config()
	p = tmp_path / "config.toml"
	write_config(cfg, str(p))

	loaded = load_config(str(p), expand=False)

	assert loaded.schedule.interval == "daily"
	assert loaded.schedule.at == "09:00"
	assert loaded.ai.mode == "claude_cli"
	assert loaded.ai.model == "claude-opus-4-8"
	assert loaded.digest.max_items == 5
	assert loaded.digest.empty == "send"
	# Secret refs preserved verbatim (not expanded)
	assert loaded.email.password == "${SMTP_PW}"
	assert loaded.slack.bot_token == "${SLACK_BOT}"
	assert loaded.slack.channel == "#dev"
	assert loaded.telegram.bot_token == "${TG_TOKEN}"
	assert loaded.telegram.chat_id == "-100123"
	# sender/recipient mapping
	assert loaded.email.sender == "from@example.com"
	assert loaded.email.recipient == "to@example.com"
	# Custom source
	assert len(loaded.custom_sources) == 1
	src = loaded.custom_sources[0]
	assert src.id == "custom-1"
	assert src.name == "Custom One"
	assert src.feed_url == "https://example.com/feed.xml"
	assert src.content_selector == "main"
	assert src.default_weight == 3
	# enabled_source_ids
	assert loaded.enabled_source_ids == {"android-versions", "compose-releases"}
	# sort
	assert loaded.sort == {"security": 10, "releases": 5}


def test_env_refs_not_expanded_on_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	"""config_to_toml must write ${SMTP_PW} literally, even when the var is set."""
	monkeypatch.setenv("SMTP_PW", "secret")
	cfg = Config(
		schedule=ScheduleConfig(),
		ai=AIConfig(),
		digest=DigestConfig(),
		sort={},
		email=EmailChannel(enabled=True, smtp_host="smtp.example.com", password="${SMTP_PW}"),
		slack=SlackChannel(),
		telegram=TelegramChannel(),
		custom_sources=[],
		enabled_source_ids=set(),
	)
	toml_text = config_to_toml(cfg)
	assert "${SMTP_PW}" in toml_text
	assert "secret" not in toml_text


def test_from_to_key_mapping(tmp_path: Path) -> None:
	"""TOML output uses keys 'from' and 'to', not 'sender'/'recipient'."""
	cfg = Config(
		schedule=ScheduleConfig(),
		ai=AIConfig(),
		digest=DigestConfig(),
		sort={},
		email=EmailChannel(sender="a@b.com", recipient="c@d.com"),
		slack=SlackChannel(),
		telegram=TelegramChannel(),
		custom_sources=[],
		enabled_source_ids=set(),
	)
	toml_text = config_to_toml(cfg)
	assert 'from = "a@b.com"' in toml_text
	assert 'to = "c@d.com"' in toml_text
	assert "sender" not in toml_text
	assert "recipient" not in toml_text

	# Round-trip: load_config must map them back to sender/recipient
	p = tmp_path / "config.toml"
	write_config(cfg, str(p))
	loaded = load_config(str(p), expand=False)
	assert loaded.email.sender == "a@b.com"
	assert loaded.email.recipient == "c@d.com"


def test_open_existing_preserves_env_ref_with_var_set(
	tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
	"""load_or_default with expand=False keeps ${AW_SMTP_PW} literal when var is set."""
	config_file = tmp_path / "config.toml"
	config_file.write_text(
		'[channels.email]\nenabled = true\nsmtp_host = "smtp.example.com"\n'
		'password = "${AW_SMTP_PW}"\nfrom = ""\nto = ""\n',
		encoding="utf-8",
	)
	monkeypatch.setenv("AW_SMTP_PW", "topsecret")
	monkeypatch.setattr("android_watcher.tui.configio.config_path", lambda: str(config_file))

	cfg, existed = load_or_default()
	assert existed is True
	assert cfg.email.password == "${AW_SMTP_PW}"

	toml_text = config_to_toml(cfg)
	assert "${AW_SMTP_PW}" in toml_text
	assert "topsecret" not in toml_text


def test_open_existing_preserves_env_ref_with_var_unset(
	tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
	"""load_or_default must not raise when the referenced env var is unset."""
	config_file = tmp_path / "config.toml"
	config_file.write_text(
		'[channels.email]\nenabled = true\nsmtp_host = "smtp.example.com"\n'
		'password = "${AW_SMTP_PW}"\nfrom = ""\nto = ""\n',
		encoding="utf-8",
	)
	monkeypatch.delenv("AW_SMTP_PW", raising=False)
	monkeypatch.setattr("android_watcher.tui.configio.config_path", lambda: str(config_file))

	cfg, existed = load_or_default()  # must not raise
	assert existed is True
	assert cfg.email.password == "${AW_SMTP_PW}"


def test_enabled_source_ids_persist(tmp_path: Path) -> None:
	"""enabled_source_ids round-trips through write_config/load_config."""
	cfg = Config(
		schedule=ScheduleConfig(),
		ai=AIConfig(),
		digest=DigestConfig(),
		sort={},
		email=EmailChannel(),
		slack=SlackChannel(),
		telegram=TelegramChannel(),
		custom_sources=[],
		enabled_source_ids={"android-versions", "compose-releases"},
	)
	p = tmp_path / "config.toml"
	write_config(cfg, str(p))
	loaded = load_config(str(p), expand=False)
	assert loaded.enabled_source_ids == {"android-versions", "compose-releases"}


def test_empty_enabled_source_ids_serializes_to_empty_array(tmp_path: Path) -> None:
	"""enabled_source_ids=set() serializes to enabled_sources = [] and loads back as set()."""
	cfg = Config(
		schedule=ScheduleConfig(),
		ai=AIConfig(),
		digest=DigestConfig(),
		sort={},
		email=EmailChannel(),
		slack=SlackChannel(),
		telegram=TelegramChannel(),
		custom_sources=[],
		enabled_source_ids=set(),
	)
	toml_text = config_to_toml(cfg)
	assert "enabled_sources = []" in toml_text

	p = tmp_path / "config.toml"
	write_config(cfg, str(p))
	loaded = load_config(str(p), expand=False)
	assert loaded.enabled_source_ids == set()


def test_sentinel_enabled_source_ids_roundtrip(tmp_path: Path) -> None:
	"""enabled_source_ids={"__none__"} round-trips to {"__none__"} (the watch-nothing sentinel)."""
	cfg = Config(
		schedule=ScheduleConfig(),
		ai=AIConfig(),
		digest=DigestConfig(),
		sort={},
		email=EmailChannel(),
		slack=SlackChannel(),
		telegram=TelegramChannel(),
		custom_sources=[],
		enabled_source_ids={"__none__"},
	)
	toml_text = config_to_toml(cfg)
	assert '"__none__"' in toml_text

	p = tmp_path / "config.toml"
	write_config(cfg, str(p))
	loaded = load_config(str(p), expand=False)
	assert loaded.enabled_source_ids == {"__none__"}


def test_validate_rejects_contradictory_schedule() -> None:
	"""interval=daily with a non-empty cron string is a contradiction."""
	cfg = Config(
		schedule=ScheduleConfig(interval="daily", cron="0 9 * * *"),
		ai=AIConfig(),
		digest=DigestConfig(),
		sort={},
		email=EmailChannel(),
		slack=SlackChannel(),
		telegram=TelegramChannel(),
		custom_sources=[],
		enabled_source_ids=set(),
	)
	errors = validate_config(cfg)
	assert errors
	assert any("cron" in e for e in errors)


def test_validate_rejects_cron_without_expr() -> None:
	"""interval=cron with empty cron string is invalid."""
	cfg = Config(
		schedule=ScheduleConfig(interval="cron", cron=""),
		ai=AIConfig(),
		digest=DigestConfig(),
		sort={},
		email=EmailChannel(),
		slack=SlackChannel(),
		telegram=TelegramChannel(),
		custom_sources=[],
		enabled_source_ids=set(),
	)
	errors = validate_config(cfg)
	assert errors


def test_validate_ok() -> None:
	"""A well-formed config returns no errors."""
	cfg = Config(
		schedule=ScheduleConfig(interval="daily", at="09:00"),
		ai=AIConfig(),
		digest=DigestConfig(),
		sort={},
		email=EmailChannel(),
		slack=SlackChannel(enabled=True, bot_token="xoxb-tok", channel="#updates"),
		telegram=TelegramChannel(),
		custom_sources=[],
		enabled_source_ids=set(),
	)
	errors = validate_config(cfg)
	assert errors == []


def test_load_or_default_blank(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	"""load_or_default returns (Config, False) with sane defaults when no file exists."""
	nonexistent = str(tmp_path / "no-file.toml")
	monkeypatch.setattr("android_watcher.tui.configio.config_path", lambda: nonexistent)

	cfg, existed = load_or_default()
	assert existed is False
	assert cfg.schedule.interval == "daily"
	assert cfg.ai.mode == "claude_cli"
	assert cfg.email.enabled is False
	assert cfg.slack.enabled is False
	assert cfg.telegram.enabled is False
	assert cfg.custom_sources == []
	assert cfg.enabled_source_ids == set()


def test_load_or_default_existing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	"""load_or_default returns (Config, True) when the config file exists."""
	config_file = tmp_path / "config.toml"
	src_cfg = Config(
		schedule=ScheduleConfig(interval="weekly", at="08:00"),
		ai=AIConfig(),
		digest=DigestConfig(),
		sort={},
		email=EmailChannel(),
		slack=SlackChannel(),
		telegram=TelegramChannel(),
		custom_sources=[],
		enabled_source_ids=set(),
	)
	write_config(src_cfg, str(config_file))
	monkeypatch.setattr("android_watcher.tui.configio.config_path", lambda: str(config_file))

	cfg, existed = load_or_default()
	assert existed is True
	assert cfg.schedule.interval == "weekly"


def test_telegram_roundtrip_preserves_env_ref(tmp_path: Path) -> None:
	"""[channels.telegram] round-trips with ${ENV} bot_token preserved verbatim."""
	cfg = Config(
		schedule=ScheduleConfig(),
		ai=AIConfig(),
		digest=DigestConfig(),
		sort={},
		email=EmailChannel(),
		slack=SlackChannel(),
		telegram=TelegramChannel(enabled=True, bot_token="${TG_TOKEN}", chat_id="-100123"),
		custom_sources=[],
		enabled_source_ids=set(),
	)
	toml_text = config_to_toml(cfg)
	assert "[channels.telegram]" in toml_text
	assert "${TG_TOKEN}" in toml_text

	p = tmp_path / "config.toml"
	write_config(cfg, str(p))
	loaded = load_config(str(p), expand=False)
	assert loaded.telegram.enabled is True
	assert loaded.telegram.bot_token == "${TG_TOKEN}"
	assert loaded.telegram.chat_id == "-100123"


def test_slack_bot_token_roundtrip_preserves_env_ref(tmp_path: Path) -> None:
	"""[channels.slack] round-trips with ${ENV} bot_token preserved verbatim."""
	cfg = Config(
		schedule=ScheduleConfig(),
		ai=AIConfig(),
		digest=DigestConfig(),
		sort={},
		email=EmailChannel(),
		slack=SlackChannel(enabled=True, bot_token="${SLACK_TOKEN}", channel="#updates"),
		telegram=TelegramChannel(),
		custom_sources=[],
		enabled_source_ids=set(),
	)
	toml_text = config_to_toml(cfg)
	assert "[channels.slack]" in toml_text
	assert "${SLACK_TOKEN}" in toml_text
	assert 'channel = "#updates"' in toml_text

	p = tmp_path / "config.toml"
	write_config(cfg, str(p))
	loaded = load_config(str(p), expand=False)
	assert loaded.slack.enabled is True
	assert loaded.slack.bot_token == "${SLACK_TOKEN}"
	assert loaded.slack.channel == "#updates"


def test_validate_accepts_slack_bot_token_and_channel(tmp_path: Path) -> None:
	"""slack enabled with bot_token+channel is valid."""
	cfg = Config(
		schedule=ScheduleConfig(),
		ai=AIConfig(),
		digest=DigestConfig(),
		sort={},
		email=EmailChannel(),
		slack=SlackChannel(enabled=True, bot_token="${SLACK_TOKEN}", channel="#updates"),
		telegram=TelegramChannel(),
		custom_sources=[],
		enabled_source_ids=set(),
	)
	errors = validate_config(cfg)
	assert not any("slack" in e for e in errors)


def test_validate_rejects_slack_enabled_with_neither(tmp_path: Path) -> None:
	"""slack enabled with no bot_token+channel is invalid."""
	cfg = Config(
		schedule=ScheduleConfig(),
		ai=AIConfig(),
		digest=DigestConfig(),
		sort={},
		email=EmailChannel(),
		slack=SlackChannel(enabled=True),
		telegram=TelegramChannel(),
		custom_sources=[],
		enabled_source_ids=set(),
	)
	errors = validate_config(cfg)
	assert any("slack" in e for e in errors)


def test_validate_rejects_slack_bot_token_without_channel(tmp_path: Path) -> None:
	"""slack enabled with bot_token but no channel is treated as neither configured."""
	cfg = Config(
		schedule=ScheduleConfig(),
		ai=AIConfig(),
		digest=DigestConfig(),
		sort={},
		email=EmailChannel(),
		slack=SlackChannel(enabled=True, bot_token="${SLACK_TOKEN}"),
		telegram=TelegramChannel(),
		custom_sources=[],
		enabled_source_ids=set(),
	)
	errors = validate_config(cfg)
	assert any("slack" in e for e in errors)
