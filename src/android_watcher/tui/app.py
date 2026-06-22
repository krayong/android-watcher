from __future__ import annotations

from textual import events
from textual.app import App
from textual.binding import Binding
from textual.screen import Screen

from android_watcher.config import Config
from android_watcher.config import config_path as default_config_path
from android_watcher.tui.configio import validate_config, write_config
from android_watcher.tui.screens import (
	AIScreen,
	ChannelsScreen,
	MainMenuScreen,
	ReviewScreen,
	ScheduleScreen,
	SourcesGateScreen,
	WelcomeScreen,
)

_WIZARD_STEPS = ("welcome", "sources", "schedule", "ai", "channels", "review")


class AndroidWatcher(App):
	"""android-watcher configuration: a keyboard-driven, box-free pointer UI.

	First run walks the sections in sequence (a wizard); afterwards it opens the
	section menu so a single area can be reconfigured.
	"""

	TITLE = "Android Watcher"

	CSS = """
	Screen { align: left top; }
	#logo { width: auto; padding: 3 0 1 0; }
	Center { height: auto; }
	#title { width: 1fr; text-align: center; padding: 1 0 0 0; }
	#help { width: 1fr; text-align: center; padding: 0 0 2 0; }
	#status { color: $text-muted; padding: 0 0 0 1; }
	#hint { color: $text-muted; padding: 1 0 0 1; }
	#quit { color: $text-muted; padding: 0 0 0 1; }
	OptionList {
		border: none;
		background: transparent;
		padding: 0 1;
		height: 1fr;
		scrollbar-size-vertical: 1;
		scrollbar-background: $background;
		scrollbar-background-hover: $background;
		scrollbar-background-active: $background;
		scrollbar-color: white;
		scrollbar-color-hover: white;
		scrollbar-color-active: white;
	}
	OptionList:focus { border: none; }
	.option-list--option-highlighted {
		background: white 20%;
		color: $text;
		text-style: none;
	}
	OptionList:focus > .option-list--option-highlighted {
		background: white 32%;
		color: $text;
		text-style: bold;
	}
	#editor {
		display: none;
		border: round white;
		background: transparent;
		height: 3;
		margin: 0 1;
	}
	#editor:focus { border: round white; }
	Input > .input--cursor { background: white; color: black; }
	Input > .input--selection { background: white 30%; }
	"""

	BINDINGS = [
		Binding("ctrl+c", "quit", "quit", show=False, priority=True),
		Binding("q", "quit", "quit", show=False),
	]

	def __init__(
		self, config: Config, *, config_path: str | None = None, first_run: bool = False
	) -> None:
		super().__init__()
		self._config = config
		self._config_path = config_path or default_config_path()
		self._first_run = first_run

	def get_default_screen(self) -> Screen:
		# The first screen IS the base screen (not pushed on top of an empty one),
		# so there is nothing empty to fall back to, and it lays out immediately.
		if self._first_run:
			return self._make_step(0)
		return MainMenuScreen(self._config)

	def on_mount(self) -> None:
		# Borrow the terminal's own colors: no imposed background, no accent palette.
		self.theme = "ansi-dark"

	def _make_step(self, index: int) -> Screen:
		match _WIZARD_STEPS[index]:
			case "welcome":
				screen: Screen = WelcomeScreen()
			case "sources":
				screen = SourcesGateScreen(self._config)
			case "schedule":
				screen = ScheduleScreen(self._config, wizard=True)
			case "ai":
				screen = AIScreen(self._config, wizard=True)
			case "channels":
				screen = ChannelsScreen(self._config, wizard=True)
			case _:
				screen = ReviewScreen(self._config, self._config_path)
		# Tag each wizard screen with its step so forward/back stay in sync no
		# matter how the arrow keys are mashed (the step is read from the screen
		# on top, never from a counter that can drift out of sync with the stack).
		screen.wizard_index = index  # type: ignore[attr-defined]
		return screen

	def wizard_next(self) -> None:
		"""Advance from whichever wizard screen is on top; save after the last step."""
		current = getattr(self.screen, "wizard_index", -1)
		nxt = current + 1
		if nxt >= len(_WIZARD_STEPS):
			self.save_and_exit()
			return
		self.push_screen(self._make_step(nxt))

	def save_and_exit(self) -> list[str]:
		"""Validate, write config, install the scheduled job, then exit.

		Saving completes the whole setup: after this the scheduled job is live,
		so the user does not need to run `schedule install` separately.
		"""
		errors = validate_config(self._config)
		if errors:
			self.bell()
			self.notify("; ".join(errors), title="Cannot save", severity="error")
			return errors
		write_config(self._config, self._config_path)
		lines = [f"Saved {self._config_path}"]
		try:
			from android_watcher.schedule import install_schedule  # noqa: PLC0415

			install_schedule(self._config)
			lines.append("Scheduled job installed.")
		except Exception as exc:  # noqa: BLE001 - report any backend/OS failure, keep config
			lines.append(f"Config saved, but installing the schedule failed: {exc}")
			lines.append("Run 'android-watcher schedule install' to finish.")
		lines.append(
			"First run downloads Google's sitemap (~300 MB) and may take a few "
			"minutes; later runs use conditional requests and are fast."
		)
		self.exit(result="\n".join(lines))
		return []

	async def on_event(self, event: events.Event) -> None:
		# Keyboard-only: the mouse is intentionally inert.
		if isinstance(event, events.MouseEvent):
			return
		await super().on_event(event)
