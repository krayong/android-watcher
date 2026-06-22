from __future__ import annotations

import re
from dataclasses import dataclass

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Center
from textual.screen import Screen
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option

from android_watcher.catalog import load_catalog
from android_watcher.config import Config
from android_watcher.models import Source

NONE_SENTINEL = "__none__"

_DETECTORS = ("content", "feed", "sitemap", "android_sitemap")
_CATEGORIES = (
	"platform-release",
	"api-reference",
	"tooling",
	"guides",
	"dev-blog",
	"design",
	"news",
)

_SECRET_NOTE = "Saved to a 0600 file. Use ${ENV_VAR} to reference a secret without storing it"

_WEEKDAYS = (
	("mon", "Monday"),
	("tue", "Tuesday"),
	("wed", "Wednesday"),
	("thu", "Thursday"),
	("fri", "Friday"),
	("sat", "Saturday"),
	("sun", "Sunday"),
)


@dataclass(frozen=True)
class Field:
	"""One editable row in a field menu."""

	key: str
	label: str
	kind: str  # "text" | "int" | "secret" | "enum"
	choices: tuple[str, ...] = ()
	help: str = ""


def _bold(text: str) -> Text:
	return Text(text, style="bold")


def _heading(title: str, subtitle: str) -> list[Static]:
	"""The centered heading + subheading shared by every screen."""
	return [
		Static(Text(title, justify="center", style="bold"), id="title"),
		Static(Text(subtitle, justify="center"), id="help"),
	]


def _quit_hint() -> Static:
	"""The persistent bottom line shown on every screen."""
	return Static("q or ctrl+c to quit", id="quit")


def _add_trailing(listing: OptionList, label: str, option_id: str) -> None:
	"""Append two blank spacer rows, then a bold action row (Done / Next / Submit)."""
	listing.add_option(Option(Text(" "), id="__sp1__", disabled=True))
	listing.add_option(Option(Text(" "), id="__sp2__", disabled=True))
	listing.add_option(Option(_bold(label), id=option_id))


def _focus_first(listing: OptionList) -> None:
	"""Highlight the first selectable option so the user never starts on nothing."""
	if listing.highlighted is not None:
		return
	for i in range(listing.option_count):
		if not listing.get_option_at_index(i).disabled:
			listing.highlighted = i
			return


def resolve_enabled_ids(config: Config) -> set[str]:
	"""The catalog source ids currently watched, given the config override rules."""
	catalog = load_catalog()
	ids = set(config.enabled_source_ids)
	if not ids:
		return {s.id for s in catalog if s.enabled}
	if ids == {NONE_SENTINEL}:
		return set()
	return {s.id for s in catalog if s.id in ids}


def watched_count(config: Config) -> int:
	"""Number of sources actually watched: resolved catalog ids plus customs."""
	return len(resolve_enabled_ids(config)) + len(config.custom_sources)


class _Nav(Screen):
	"""Shared back navigation: left arrow or escape returns to the previous screen.

	The first screen is the app's default (base) screen, so popping is safe only
	when something was pushed on top of it.
	"""

	BINDINGS = [
		Binding("left", "back", "back", show=False),
		Binding("escape", "back", "back", show=False),
	]

	def action_back(self) -> None:
		if len(self.app.screen_stack) > 1:
			self.app.pop_screen()


class FieldMenuScreen(_Nav):
	"""A pointer list of fields. Enums cycle in place; text edits inline.

	Enter selects the highlighted row; the right arrow moves forward. Editing
	happens in an inline input on the same screen, never a new one.
	"""

	TITLE = ""
	HELP = ""

	BINDINGS = [
		Binding("right", "forward", "forward", show=False),
		Binding("space", "select", "select", show=False),
	]

	def __init__(self, config: Config, *, wizard: bool = False) -> None:
		super().__init__()
		self._config = config
		self._wizard = wizard
		self._editing: str | None = None

	# --- subclass hooks ---------------------------------------------------
	def _fields(self) -> list[Field]:
		raise NotImplementedError

	def _slot(self, key: str) -> tuple[object, str]:
		raise NotImplementedError

	def _after_set(self) -> None:
		pass

	def _validate(self, key: str, value: str) -> str | None:
		return None

	# --- value access -----------------------------------------------------
	def _field(self, key: str) -> Field:
		return next(f for f in self._fields() if f.key == key)

	def _get(self, key: str) -> str:
		obj, attr = self._slot(key)
		return str(getattr(obj, attr))

	def _set(self, key: str, value: str) -> None:
		field = self._field(key)
		obj, attr = self._slot(key)
		if field.kind == "int":
			try:
				setattr(obj, attr, int(value))
			except ValueError:
				return
		else:
			setattr(obj, attr, value)
		self._after_set()

	# --- rendering --------------------------------------------------------
	def _hint(self) -> str:
		if self._wizard:
			return "↑/↓ move · enter select · → next"
		return "↑/↓ move · enter select · esc back"

	def compose(self) -> ComposeResult:
		yield from _heading(self.TITLE, self.HELP)
		yield OptionList(id="fields")
		yield Input(id="editor")
		yield Static("", id="status")
		yield Static(self._hint(), id="hint")
		yield _quit_hint()

	def on_mount(self) -> None:
		self._populate()

	def _display(self, field: Field) -> str:
		if field.kind == "secret":
			return "••••" if self._get(field.key) else "—"
		return self._get(field.key) or "—"

	def _populate(self) -> None:
		listing = self.query_one("#fields", OptionList)
		index = listing.highlighted
		listing.clear_options()
		for field in self._fields():
			if field.kind == "sep":
				listing.add_option(
					Option(Text(f"── {field.label} ──", style="dim"), id=field.key, disabled=True)
				)
				continue
			if field.kind == "toggle":
				row = Text()
				if self._get(field.key) == "on":
					row.append("[")
					row.append("✓", style="bold green")
					row.append("] ")
				else:
					row.append("[ ] ")
				row.append(field.label)
				listing.add_option(Option(row, id=field.key))
				continue
			row = Text()
			row.append(f"{field.label}  ")
			row.append(self._display(field), style="dim")
			listing.add_option(Option(row, id=field.key))
		_add_trailing(listing, "Next →" if self._wizard else "Done", "__done__")
		if index is not None and index < listing.option_count:
			listing.highlighted = index
		_focus_first(listing)

	# --- interaction ------------------------------------------------------
	def action_forward(self) -> None:
		self._forward()

	def action_select(self) -> None:
		listing = self.query_one("#fields", OptionList)
		if listing.highlighted is not None:
			self._activate(listing.get_option_at_index(listing.highlighted).id)

	def _forward(self) -> None:
		if self._wizard:
			self.app.wizard_next()
		else:
			self.action_back()

	def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
		self._activate(event.option.id)

	def _activate(self, key: str | None) -> None:
		if key is None:
			return
		if key == "__done__":
			self._forward()
			return
		field = self._field(key)
		if field.kind == "sep":
			return
		if field.kind == "toggle":
			self._set(key, "off" if self._get(key) == "on" else "on")
			self._populate()
			return
		if field.kind == "enum":
			cur = self._get(key)
			idx = (field.choices.index(cur) + 1) % len(field.choices) if cur in field.choices else 0
			self._set(key, field.choices[idx])
			self._populate()
			return
		self._open_editor(field)

	def _open_editor(self, field: Field) -> None:
		self._editing = field.key
		editor = self.query_one("#editor", Input)
		editor.password = field.kind == "secret"
		editor.value = self._get(field.key)
		editor.display = True
		note = _SECRET_NOTE if field.kind == "secret" else field.help
		self.query_one("#status", Static).update(note)
		self.query_one("#hint", Static).update("enter save · esc cancel")
		editor.focus()

	def _close_editor(self) -> None:
		self._editing = None
		editor = self.query_one("#editor", Input)
		editor.display = False
		editor.value = ""
		self.query_one("#status", Static).update("")
		self.query_one("#hint", Static).update(self._hint())
		self._populate()
		self.query_one("#fields", OptionList).focus()

	def on_input_submitted(self, event: Input.Submitted) -> None:
		if self._editing is None:
			return
		key = self._editing
		error = self._validate(key, event.value)
		if error:
			self.query_one("#status", Static).update(f"⚠ {error}")
			return
		self._set(key, event.value)
		self._close_editor()

	def action_back(self) -> None:
		if self._editing is not None:
			self._close_editor()
			return
		if len(self.app.screen_stack) > 1:
			self.app.pop_screen()


def _logo() -> Text:
	"""Broadcast ripples rising from a source dot, with a detected-change dot.

	An ASCII echo of assets/logo.svg: green monitoring waves, orange change.
	"""
	green = "bold #3ddc84"
	orange = "bold #ff7043"
	logo = Text()
	logo.append(" .·°°°°°°°°°·.", style=green)
	logo.append("     ")
	logo.append("●\n", style=orange)
	logo.append("    ·°°°°°·\n", style=green)
	logo.append("      ·°·\n", style=green)
	logo.append("       ●", style=green)
	return logo


class WelcomeScreen(_Nav):
	"""First-run splash: logo, tagline, and a prompt to begin."""

	BINDINGS = [
		Binding("enter", "begin", "begin", show=False),
		Binding("right", "begin", "begin", show=False),
		Binding("space", "begin", "begin", show=False),
	]

	def compose(self) -> ComposeResult:
		yield Center(Static(_logo(), id="logo"))
		yield Static(Text("android-watcher", justify="center", style="bold"), id="title")
		yield Static(
			Text("Watch Google's Android sites. Get an AI-triaged digest", justify="center"),
			id="help",
		)
		yield Static("press enter to begin", id="hint")
		yield _quit_hint()

	def action_begin(self) -> None:
		self.app.wizard_next()

	def action_back(self) -> None:
		return


class SourcesGateScreen(_Nav):
	"""Wizard gate: show the selected-source count and offer to edit, or move on."""

	BINDINGS = [
		Binding("right", "forward", "forward", show=False),
		Binding("space", "select", "select", show=False),
	]

	def __init__(self, config: Config) -> None:
		super().__init__()
		self._config = config

	def compose(self) -> ComposeResult:
		yield from _heading("Sources", "")
		yield OptionList(id="gate")
		yield Static("↑/↓ move · enter select · → next", id="hint")
		yield _quit_hint()

	def on_mount(self) -> None:
		self._refresh()

	def on_screen_resume(self) -> None:
		self._refresh()

	def _refresh(self) -> None:
		count = watched_count(self._config)
		self.query_one("#help", Static).update(Text(f"{count} sources selected", justify="center"))
		listing = self.query_one("#gate", OptionList)
		index = listing.highlighted
		listing.clear_options()
		listing.add_option(Option(_bold("Review / edit sources"), id="edit"))
		listing.add_option(Option(Text(" "), id="__sp__", disabled=True))
		listing.add_option(Option(_bold("Next →"), id="next"))
		if index is not None and index < listing.option_count:
			listing.highlighted = index
		_focus_first(listing)

	def action_forward(self) -> None:
		self.app.wizard_next()

	def action_select(self) -> None:
		listing = self.query_one("#gate", OptionList)
		if listing.highlighted is not None:
			self._activate(listing.get_option_at_index(listing.highlighted).id)

	def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
		self._activate(event.option.id)

	def _activate(self, option_id: str | None) -> None:
		if option_id == "edit":
			self.app.push_screen(SourcesScreen(self._config))
		elif option_id == "next":
			self.app.wizard_next()


class MainMenuScreen(_Nav):
	"""Top-level pointer menu (reconfigure mode): pick a section, or save and exit."""

	BINDINGS = [
		Binding("right", "forward", "forward", show=False),
		Binding("space", "forward", "forward", show=False),
	]

	def __init__(self, config: Config) -> None:
		super().__init__()
		self._config = config

	def compose(self) -> ComposeResult:
		yield from _heading("android-watcher", "Configure what to watch and where digests go")
		yield OptionList(id="menu")
		yield Static("↑/↓ move · enter open", id="hint")
		yield _quit_hint()

	def on_mount(self) -> None:
		self._refresh()

	def on_screen_resume(self) -> None:
		self._refresh()

	def action_back(self) -> None:
		return

	def _summaries(self) -> list[tuple[str, str, str]]:
		c = self._config
		channels = [
			name for name, ch in (("slack", c.slack), ("telegram", c.telegram)) if ch.enabled
		]
		sched = c.schedule
		when = sched.cron if sched.interval == "cron" else f"{sched.interval} {sched.at}".strip()
		return [
			("sources", "Sources", f"{watched_count(c)} watched"),
			("schedule", "Schedule", when),
			("ai", "AI & Digest", f"{c.ai.mode} · max {c.digest.max_items}"),
			("channels", "Channels", ", ".join(channels) or "none"),
			("save", "Save & Exit", ""),
		]

	def _refresh(self) -> None:
		menu = self.query_one("#menu", OptionList)
		index = menu.highlighted
		menu.clear_options()
		for oid, name, summary in self._summaries():
			row = Text()
			row.append(f"{name:<14}")
			if summary:
				row.append(summary, style="dim")
			menu.add_option(Option(row, id=oid))
		if index is not None:
			menu.highlighted = index
		_focus_first(menu)

	def action_forward(self) -> None:
		menu = self.query_one("#menu", OptionList)
		if menu.highlighted is not None:
			self._activate(menu.get_option_at_index(menu.highlighted).id)

	def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
		self._activate(event.option.id)

	def _activate(self, option_id: str | None) -> None:
		match option_id:
			case "sources":
				self.app.push_screen(SourcesScreen(self._config))
			case "schedule":
				self.app.push_screen(ScheduleScreen(self._config))
			case "ai":
				self.app.push_screen(AIScreen(self._config))
			case "channels":
				self.app.push_screen(ChannelsScreen(self._config))
			case "save":
				self.action_save()

	def action_save(self) -> list[str]:
		return self.app.save_and_exit()


class SourcesScreen(_Nav):
	"""Pointer list of sources: space toggles, enter/right moves on, 'a' adds."""

	BINDINGS = [
		Binding("space", "toggle", "toggle", show=False),
		Binding("a", "toggle_all", "toggle all", show=False),
		Binding("right", "forward", "forward", show=False),
	]

	def __init__(self, config: Config, *, wizard: bool = False) -> None:
		super().__init__()
		self._config = config
		self._wizard = wizard
		self.enabled_ids: set[str] = resolve_enabled_ids(config) | {
			s.id for s in config.custom_sources
		}
		self._by_id: dict[str, Source] = {}

	def _all_sources(self) -> list[Source]:
		return [*load_catalog(), *self._config.custom_sources]

	def _row(self, src: Source) -> Text:
		on = src.id in self.enabled_ids
		row = Text()
		if on:
			row.append("[")
			row.append("✓", style="bold green")
			row.append("] ")
		else:
			row.append("[ ] ")
		row.append(src.name)
		row.append(f"  {src.url}", style="dim")
		return row

	def compose(self) -> ComposeResult:
		yield Static(Text("Sources", justify="center", style="bold"), id="title")
		yield Static(Text("", justify="center"), id="help")
		yield OptionList(id="src-list")
		hint = (
			"↑/↓ move · space toggle · a all · → next"
			if self._wizard
			else "↑/↓ move · space toggle · a all · esc back"
		)
		yield Static(hint, id="hint")
		yield _quit_hint()

	def on_mount(self) -> None:
		self._populate()

	def on_screen_resume(self) -> None:
		self.enabled_ids |= {s.id for s in self._config.custom_sources}
		self._populate()

	def _populate(self) -> None:
		listing = self.query_one("#src-list", OptionList)
		index = listing.highlighted
		listing.clear_options()
		self._by_id = {}
		for src in self._all_sources():
			self._by_id[src.id] = src
			listing.add_option(Option(self._row(src), id=src.id))
		_add_trailing(listing, "Next →" if self._wizard else "Done", "__done__")
		if index is not None and index < listing.option_count:
			listing.highlighted = index
		_focus_first(listing)
		self._update_count()

	def _update_count(self) -> None:
		total = len(self._by_id)
		selected = sum(1 for sid in self._by_id if sid in self.enabled_ids)
		self.query_one("#help", Static).update(
			Text(f"{selected} of {total} selected", justify="center")
		)

	def _toggle(self, sid: str | None) -> None:
		if sid is None or sid not in self._by_id:
			return
		if sid in self.enabled_ids:
			self.enabled_ids.discard(sid)
		else:
			self.enabled_ids.add(sid)
		listing = self.query_one("#src-list", OptionList)
		listing.replace_option_prompt(sid, self._row(self._by_id[sid]))
		self._update_count()

	def action_toggle(self) -> None:
		listing = self.query_one("#src-list", OptionList)
		if listing.highlighted is not None:
			self._toggle(listing.get_option_at_index(listing.highlighted).id)

	def action_toggle_all(self) -> None:
		all_ids = set(self._by_id)
		if all_ids and all_ids <= self.enabled_ids:
			self.enabled_ids -= all_ids
		else:
			self.enabled_ids |= all_ids
		self._populate()

	def action_forward(self) -> None:
		self._forward()

	def _forward(self) -> None:
		if self._wizard:
			self.app.wizard_next()
		else:
			self.action_back()

	def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
		self._forward()

	def apply_to_config(self) -> None:
		catalog_ids = {s.id for s in load_catalog()}
		chosen = set(self.enabled_ids)
		if not (chosen & catalog_ids):
			chosen = (chosen - {NONE_SENTINEL}) | {NONE_SENTINEL}
		self._config.enabled_source_ids = chosen

	def on_screen_suspend(self) -> None:
		self.apply_to_config()


class ScheduleScreen(FieldMenuScreen):
	"""Configure when android-watcher checks for changes."""

	TITLE = "Schedule"
	HELP = "How often android-watcher checks the sites for changes"

	def _fields(self) -> list[Field]:
		s = self._config.schedule
		fields = [
			Field(
				"interval",
				"Interval",
				"enum",
				("daily", "weekly", "cron"),
				help="How frequently to run?",
			)
		]
		if s.interval == "cron":
			fields.append(
				Field("cron", "Cron expression", "text", help="5 fields, e.g. '0 9 * * 1-5'")
			)
			return fields
		if s.interval == "weekly":
			for wd, label in _WEEKDAYS:
				fields.append(Field(f"day_{wd}", label, "toggle"))
		fields.append(
			Field(
				"at",
				"Times (24h HH:MM)",
				"text",
				help="One or more, comma-separated, e.g. 09:00,18:30",
			)
		)
		return fields

	def _slot(self, key: str) -> tuple[object, str]:
		return (self._config.schedule, key)

	def _days(self) -> set[str]:
		return {d.strip().lower()[:3] for d in self._config.schedule.days.split(",") if d.strip()}

	def _get(self, key: str) -> str:
		if key.startswith("day_"):
			return "on" if key[4:] in self._days() else "off"
		return super()._get(key)

	def _set(self, key: str, value: str) -> None:
		if key.startswith("day_"):
			days = self._days()
			if value == "on":
				days.add(key[4:])
			else:
				days.discard(key[4:])
			self._config.schedule.days = ",".join(wd for wd, _ in _WEEKDAYS if wd in days)
			return
		super()._set(key, value)

	def _after_set(self) -> None:
		# Keep cron and interval consistent so save never trips the cross-check.
		if self._config.schedule.interval != "cron":
			self._config.schedule.cron = ""

	def _validate(self, key: str, value: str) -> str | None:
		if key == "at":
			parts = [p.strip() for p in value.split(",") if p.strip()]
			if not parts:
				return "Enter at least one time."
			bad = [p for p in parts if not re.match(r"^([01]\d|2[0-3]):[0-5]\d$", p)]
			if bad:
				return f"Use 24-hour HH:MM: {', '.join(bad)}"
		if key == "cron" and len(value.split()) != 5:
			return "Cron needs 5 space-separated fields"
		return None


class AIScreen(FieldMenuScreen):
	"""Configure AI triage and digest settings."""

	TITLE = "AI & Digest"
	HELP = "Claude reads each change and writes the digest summary"

	def __init__(self, config: Config, *, wizard: bool = False) -> None:
		super().__init__(config, wizard=wizard)
		if config.ai.model not in ("sonnet", "opus"):
			config.ai.model = "sonnet"

	def _fields(self) -> list[Field]:
		mode_help = "'off' skips summaries"
		fields = [Field("mode", "AI triage", "enum", ("claude_cli", "off"), help=mode_help)]
		if self._config.ai.mode == "claude_cli":
			fields.append(
				Field(
					"model",
					"Model",
					"enum",
					("sonnet", "opus"),
					help="sonnet is faster and cheaper; opus is most capable",
				)
			)
		max_help = "Cap total digest items (1-50)"
		fields.append(Field("max", "Max digest items", "int", help=max_help))
		empty_help = "Send 'nothing notable' or skip"
		fields.append(Field("empty", "Empty digest", "enum", ("send", "skip"), help=empty_help))
		return fields

	def _slot(self, key: str) -> tuple[object, str]:
		match key:
			case "mode":
				return (self._config.ai, "mode")
			case "model":
				return (self._config.ai, "model")
			case "max":
				return (self._config.digest, "max_items")
			case _:
				return (self._config.digest, "empty")

	def _validate(self, key: str, value: str) -> str | None:
		if key == "max":
			try:
				n = int(value)
			except ValueError:
				return "Enter a whole number"
			if not (1 <= n <= 50):
				return "Must be between 1 and 50"
		return None


class ChannelsScreen(_Nav):
	"""Channel hub: tick channels on/off, configure each, then move on."""

	BINDINGS = [
		Binding("space", "toggle", "toggle", show=False),
		Binding("right", "forward", "forward", show=False),
	]

	def __init__(self, config: Config, *, wizard: bool = False) -> None:
		super().__init__()
		self._config = config
		self._wizard = wizard

	def _channels(self):
		c = self._config
		return (("slack", "Slack", c.slack), ("telegram", "Telegram", c.telegram))

	def compose(self) -> ComposeResult:
		yield from _heading("Channels", "Where digests are delivered")
		yield OptionList(id="ch-list")
		hint = (
			"↑/↓ move · space on/off · enter configure · → next"
			if self._wizard
			else "↑/↓ move · space on/off · enter configure · esc back"
		)
		yield Static(hint, id="hint")
		yield _quit_hint()

	def on_mount(self) -> None:
		self._populate()

	def on_screen_resume(self) -> None:
		self._populate()

	def _populate(self) -> None:
		listing = self.query_one("#ch-list", OptionList)
		index = listing.highlighted
		listing.clear_options()
		for cid, name, ch in self._channels():
			row = Text()
			row.append("[x] " if ch.enabled else "[ ] ")
			row.append(name)
			listing.add_option(Option(row, id=cid))
		_add_trailing(listing, "Next →" if self._wizard else "Done", "__done__")
		if index is not None and index < listing.option_count:
			listing.highlighted = index
		_focus_first(listing)

	def action_toggle(self) -> None:
		listing = self.query_one("#ch-list", OptionList)
		if listing.highlighted is None:
			return
		cid = listing.get_option_at_index(listing.highlighted).id
		for oid, _name, ch in self._channels():
			if oid == cid:
				ch.enabled = not ch.enabled
				self._populate()
				return

	def action_forward(self) -> None:
		self._forward()

	def _forward(self) -> None:
		if self._wizard:
			self.app.wizard_next()
		else:
			self.action_back()

	def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
		self._activate(event.option.id)

	def _activate(self, option_id: str | None) -> None:
		match option_id:
			case "slack":
				self.app.push_screen(SlackScreen(self._config))
			case "telegram":
				self.app.push_screen(TelegramScreen(self._config))
			case "__done__":
				self._forward()


class _ChannelScreen(FieldMenuScreen):
	"""A per-channel field menu; editing any field enables the channel."""

	HELP = "Tokens are saved securely"

	def _channel(self):
		raise NotImplementedError

	def _slot(self, key: str) -> tuple[object, str]:
		return (self._channel(), key)

	def _after_set(self) -> None:
		self._channel().enabled = True


class SlackScreen(_ChannelScreen):
	TITLE = "Slack"

	def _channel(self):
		return self._config.slack

	def _fields(self) -> list[Field]:
		return [
			Field(
				"bot_token",
				"Bot token",
				"secret",
				help="Bot token (scopes: chat:write, files:write)",
			),
			Field(
				"channel",
				"Channels / DMs",
				"text",
				help="Comma-separated: #channel or a user id (Uxxxx) for a DM",
			),
		]


class TelegramScreen(_ChannelScreen):
	TITLE = "Telegram"

	def _channel(self):
		return self._config.telegram

	def _fields(self) -> list[Field]:
		return [
			Field("bot_token", "Bot token", "secret", help="From @BotFather"),
			Field("chat_id", "Chat IDs", "text", help="Comma-separated user or group chat ids"),
		]


class ReviewScreen(_Nav):
	"""Final wizard step: review every choice, show where it saves, then save."""

	BINDINGS = [
		Binding("right", "save", "save", show=False),
		Binding("space", "save", "save", show=False),
	]

	def __init__(self, config: Config, config_path: str) -> None:
		super().__init__()
		self._config = config
		self._config_path = config_path

	def compose(self) -> ComposeResult:
		yield from _heading("Review", "Confirm your configuration, then save")
		yield OptionList(id="review")
		yield Static("enter to save · esc back", id="hint")
		yield _quit_hint()

	def on_mount(self) -> None:
		self._refresh()

	def on_screen_resume(self) -> None:
		self._refresh()

	def _summary_lines(self) -> list[str]:
		c = self._config
		s = c.schedule
		if s.interval == "cron":
			when = f"cron  {s.cron}"
		elif s.interval == "weekly":
			chosen = {d.strip().lower()[:3] for d in s.days.split(",") if d.strip()}
			days = ", ".join(lbl for wd, lbl in _WEEKDAYS if wd in chosen) or "Monday"
			when = f"weekly on {days} at {s.at}"
		else:
			when = f"daily at {s.at}"
		ai = "off" if c.ai.mode == "off" else f"claude ({c.ai.model})"
		channels = [n for n, ch in (("slack", c.slack), ("telegram", c.telegram)) if ch.enabled]
		channels_str = ", ".join(channels) if channels else "none — pick one to finish!"
		return [
			f"Sources    {watched_count(c)} selected",
			f"Schedule   {when}",
			f"AI         {ai} · max {c.digest.max_items} · empty: {c.digest.empty}",
			f"Channels   {channels_str}",
			f"Saved to   {self._config_path}",
		]

	def _refresh(self) -> None:
		listing = self.query_one("#review", OptionList)
		listing.clear_options()
		for line in self._summary_lines():
			listing.add_option(Option(Text(line, style="dim"), disabled=True))
		listing.add_option(Option(Text(" "), id="__sp__", disabled=True))
		listing.add_option(Option(_bold("Save & finish"), id="save"))
		_focus_first(listing)

	def action_save(self) -> None:
		self.app.save_and_exit()

	def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
		if event.option.id == "save":
			self.action_save()
