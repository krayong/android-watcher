from __future__ import annotations

import stat
from pathlib import Path

import pytest
from textual.widgets import Input, OptionList

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
from android_watcher.tui.app import AndroidWatcher
from android_watcher.tui.screens import (
	AIScreen,
	ChannelsScreen,
	MainMenuScreen,
	ReviewScreen,
	ScheduleScreen,
	SlackScreen,
	SourcesGateScreen,
	SourcesScreen,
	WelcomeScreen,
)


def _blank_config() -> Config:
	return Config(
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


def _desktop_index(listing: OptionList) -> int:
	return next(
		i for i in range(listing.option_count) if listing.get_option_at_index(i).id == "desktop"
	)


@pytest.mark.asyncio
async def test_app_boots_on_main_menu_when_reconfiguring():
	app = AndroidWatcher(config=_blank_config(), first_run=False)
	async with app.run_test() as pilot:
		await pilot.pause()
		assert isinstance(app.screen, MainMenuScreen)


@pytest.mark.asyncio
async def test_desktop_toggle_refused_when_mechanism_unavailable(monkeypatch):
	import android_watcher.tui.screens as screens

	monkeypatch.setattr(screens, "desktop_mechanism_available", lambda: False)
	config = _blank_config()
	app = AndroidWatcher(config=config, first_run=False)
	async with app.run_test() as pilot:
		await pilot.pause()
		screen = ChannelsScreen(config)
		app.push_screen(screen)
		await pilot.pause()
		listing = screen.query_one("#ch-list", OptionList)
		listing.highlighted = _desktop_index(listing)
		screen.action_toggle()
		await pilot.pause()
		assert config.desktop.enabled is False


@pytest.mark.asyncio
async def test_desktop_toggle_enables_when_mechanism_available(monkeypatch):
	import android_watcher.tui.screens as screens

	monkeypatch.setattr(screens, "desktop_mechanism_available", lambda: True)
	config = _blank_config()
	app = AndroidWatcher(config=config, first_run=False)
	async with app.run_test() as pilot:
		await pilot.pause()
		screen = ChannelsScreen(config)
		app.push_screen(screen)
		await pilot.pause()
		listing = screen.query_one("#ch-list", OptionList)
		listing.highlighted = _desktop_index(listing)
		screen.action_toggle()
		await pilot.pause()
		assert config.desktop.enabled is True


@pytest.mark.asyncio
async def test_first_run_starts_on_welcome_screen():
	app = AndroidWatcher(config=_blank_config(), first_run=True)
	async with app.run_test() as pilot:
		await pilot.pause()
		assert isinstance(app.screen, WelcomeScreen)


@pytest.mark.asyncio
async def test_wizard_advances_through_steps_then_saves(tmp_path: Path, monkeypatch):
	monkeypatch.setattr("android_watcher.schedule.install_schedule", lambda config: None)
	cfg = _blank_config()
	out = tmp_path / "config.toml"
	app = AndroidWatcher(config=cfg, config_path=str(out), first_run=True)
	async with app.run_test() as pilot:
		await pilot.pause()
		assert isinstance(app.screen, WelcomeScreen)
		app.wizard_next()
		await pilot.pause()
		assert isinstance(app.screen, SourcesGateScreen)
		app.wizard_next()
		await pilot.pause()
		assert isinstance(app.screen, ScheduleScreen)
		app.wizard_next()
		await pilot.pause()
		assert isinstance(app.screen, AIScreen)
		app.wizard_next()
		await pilot.pause()
		assert isinstance(app.screen, ChannelsScreen)
		# A channel must be enabled for the config to be complete.
		cfg.slack.enabled = True
		cfg.slack.bot_token = "xoxb-test"
		cfg.slack.channel = "#updates"
		app.wizard_next()  # channels -> review
		await pilot.pause()
		assert isinstance(app.screen, ReviewScreen)
		app.wizard_next()  # review -> save and exit
		await pilot.pause()
	assert out.exists()


@pytest.mark.asyncio
async def test_review_screen_shows_summary_and_save_path(tmp_path: Path):
	cfg = _blank_config()
	cfg.slack.enabled = True
	cfg.slack.bot_token = "xoxb-test"
	cfg.slack.channel = "#updates"
	out = tmp_path / "config.toml"
	screen = ReviewScreen(cfg, str(out))
	lines = screen._summary_lines()  # type: ignore[attr-defined]
	joined = "\n".join(lines)
	assert "Sources" in joined
	assert "Schedule" in joined
	assert "slack" in joined
	assert str(out) in joined


@pytest.mark.asyncio
async def test_wizard_back_then_forward_stays_in_sync():
	"""Mashing left/right must not desync the wizard step (forward after back repeats)."""
	app = AndroidWatcher(config=_blank_config(), first_run=True)
	async with app.run_test() as pilot:
		await pilot.pause()
		app.wizard_next()  # welcome -> gate
		await pilot.pause()
		app.wizard_next()  # gate -> schedule
		await pilot.pause()
		assert isinstance(app.screen, ScheduleScreen)
		await pilot.press("escape")  # back -> gate
		await pilot.pause()
		assert isinstance(app.screen, SourcesGateScreen)
		app.wizard_next()  # forward again must land on schedule, not skip past it
		await pilot.pause()
		assert isinstance(app.screen, ScheduleScreen)


@pytest.mark.asyncio
async def test_sources_gate_edit_opens_list_then_returns():
	"""The wizard gate offers to edit; choosing it opens the full source list."""
	app = AndroidWatcher(config=_blank_config(), first_run=True)
	async with app.run_test() as pilot:
		await pilot.pause()
		app.wizard_next()  # welcome -> sources gate
		await pilot.pause()
		gate = app.screen
		assert isinstance(gate, SourcesGateScreen)
		gate._activate("edit")  # type: ignore[attr-defined]
		await pilot.pause()
		assert isinstance(app.screen, SourcesScreen)
		await pilot.press("escape")
		await pilot.pause()
		assert isinstance(app.screen, SourcesGateScreen)


@pytest.mark.asyncio
async def test_back_from_main_menu_is_a_noop():
	"""Escape on the root menu must not reveal an empty base screen."""
	app = AndroidWatcher(config=_blank_config(), first_run=False)
	async with app.run_test() as pilot:
		await pilot.pause()
		await pilot.press("escape")
		await pilot.pause()
		assert isinstance(app.screen, MainMenuScreen)


@pytest.mark.asyncio
async def test_sources_screen_lists_catalog():
	app = AndroidWatcher(config=_blank_config(), first_run=False)
	async with app.run_test() as pilot:
		await pilot.pause()
		app.screen._activate("sources")  # type: ignore[attr-defined]
		await pilot.pause()
		screen = app.screen
		assert isinstance(screen, SourcesScreen)
		listing = screen.query_one("#src-list", OptionList)
		prompts = [listing.get_option_at_index(i).prompt.plain for i in range(listing.option_count)]
		assert any("Android" in p for p in prompts)


@pytest.mark.asyncio
async def test_sources_screen_has_no_add_option():
	"""Adding custom sources is disabled: no '+ add source' row remains."""
	app = AndroidWatcher(config=_blank_config(), first_run=False)
	async with app.run_test() as pilot:
		await pilot.pause()
		app.screen._activate("sources")  # type: ignore[attr-defined]
		await pilot.pause()
		listing = app.screen.query_one("#src-list", OptionList)
		ids = {listing.get_option_at_index(i).id for i in range(listing.option_count)}
		assert "__add__" not in ids


@pytest.mark.asyncio
async def test_uncheck_all_catalog_writes_sentinel_not_empty():
	cfg = _blank_config()
	screen = SourcesScreen(cfg)
	screen.enabled_ids = set()  # user unchecked every catalog box
	screen.apply_to_config()
	assert cfg.enabled_source_ids == {"__none__"}


@pytest.mark.asyncio
async def test_menu_opens_each_screen():
	app = AndroidWatcher(config=_blank_config(), first_run=False)
	async with app.run_test() as pilot:
		await pilot.pause()
		for option_id, screen_type in (
			("sources", SourcesScreen),
			("schedule", ScheduleScreen),
			("ai", AIScreen),
			("channels", ChannelsScreen),
		):
			app.screen._activate(option_id)  # type: ignore[attr-defined]
			await pilot.pause()
			assert isinstance(app.screen, screen_type)
			await pilot.press("escape")
			await pilot.pause()
			assert isinstance(app.screen, MainMenuScreen)


@pytest.mark.asyncio
async def test_save_writes_valid_toml(tmp_path: Path, monkeypatch):
	monkeypatch.setattr("android_watcher.schedule.install_schedule", lambda config: None)
	cfg = _blank_config()
	cfg.slack.enabled = True  # a delivery channel is required for a complete config
	cfg.slack.bot_token = "xoxb-test"
	cfg.slack.channel = "#updates"
	out = tmp_path / "config.toml"
	app = AndroidWatcher(config=cfg, config_path=str(out), first_run=False)
	async with app.run_test() as pilot:
		await pilot.pause()
		screen = app.screen
		assert isinstance(screen, MainMenuScreen)
		assert screen.action_save() == []
		await pilot.pause()

	assert out.exists(), "config file should have been written"
	assert stat.S_IMODE(out.stat().st_mode) == 0o600, "config must be 0600"
	loaded = load_config(str(out), expand=False)
	assert loaded.schedule.interval == "daily"


@pytest.mark.asyncio
async def test_editing_channel_enables_it():
	cfg = _blank_config()
	assert cfg.slack.enabled is False
	app = AndroidWatcher(config=cfg, first_run=False)
	async with app.run_test() as pilot:
		await pilot.pause()
		app.screen._activate("channels")  # type: ignore[attr-defined]
		await pilot.pause()
		app.screen._activate("slack")  # type: ignore[attr-defined]
		await pilot.pause()
		screen = app.screen
		assert isinstance(screen, SlackScreen)
		screen._set("bot_token", "xoxb-test")
		await pilot.pause()
	assert cfg.slack.enabled is True
	assert cfg.slack.bot_token == "xoxb-test"


@pytest.mark.asyncio
async def test_channels_hub_configure_and_done_returns():
	cfg = _blank_config()
	app = AndroidWatcher(config=cfg, first_run=False)
	async with app.run_test() as pilot:
		await pilot.pause()
		app.screen._activate("channels")  # type: ignore[attr-defined]
		await pilot.pause()
		hub = app.screen
		assert isinstance(hub, ChannelsScreen)
		hub._activate("slack")  # type: ignore[attr-defined]
		await pilot.pause()
		assert isinstance(app.screen, SlackScreen)
		await pilot.press("escape")
		await pilot.pause()
		app.screen._activate("__done__")  # type: ignore[attr-defined]
		await pilot.pause()
		assert isinstance(app.screen, MainMenuScreen)


@pytest.mark.asyncio
async def test_email_and_telegram_not_offered_in_channels():
	"""Only the surfaced channels (Slack, Desktop) appear in the TUI hub."""
	cfg = _blank_config()
	app = AndroidWatcher(config=cfg, first_run=False)
	async with app.run_test() as pilot:
		await pilot.pause()
		app.screen._activate("channels")  # type: ignore[attr-defined]
		await pilot.pause()
		ids = {
			app.screen.query_one("#ch-list", OptionList).get_option_at_index(i).id
			for i in range(app.screen.query_one("#ch-list", OptionList).option_count)
		}
		assert ids.isdisjoint({"email", "telegram"})
		assert {"slack", "desktop"} <= ids


@pytest.mark.asyncio
async def test_inline_edit_sets_value_no_new_screen():
	cfg = _blank_config()
	app = AndroidWatcher(config=cfg, first_run=False)
	async with app.run_test() as pilot:
		await pilot.pause()
		app.screen._activate("channels")  # type: ignore[attr-defined]
		await pilot.pause()
		app.screen._activate("slack")  # type: ignore[attr-defined]
		await pilot.pause()
		screen = app.screen
		assert isinstance(screen, SlackScreen)
		screen._open_editor(screen._field("channel"))  # type: ignore[attr-defined]
		await pilot.pause()
		editor = screen.query_one("#editor", Input)
		assert editor.display is True  # inline editor, same screen
		screen.on_input_submitted(Input.Submitted(editor, "#updates"))
		await pilot.pause()
		assert isinstance(app.screen, SlackScreen)  # no new screen pushed
	assert cfg.slack.channel == "#updates"
	assert cfg.slack.enabled is True


@pytest.mark.asyncio
async def test_schedule_validates_time():
	cfg = _blank_config()
	screen = ScheduleScreen(cfg)
	assert screen._validate("at", "9am") is not None
	assert screen._validate("at", "09:00") is None
