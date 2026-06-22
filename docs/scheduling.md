# Scheduling

`android-watcher schedule install` wires up a native scheduled job so the tool
runs unattended. The backend depends on the OS: launchd on macOS, a systemd user
timer on Linux with systemd, or a crontab block as a fallback.

## Installing the schedule

Run once after you finish the TUI setup:

```sh
android-watcher schedule install
```

The command writes the appropriate config files and activates the job
immediately. No reboot is required.

## Per-OS activation steps

### macOS: launchd

`schedule install` writes a plist to `~/Library/LaunchAgents/` and calls
`launchctl load -w` to load it immediately. The agent label is
`com.krayong.android-watcher`.

**Login-session requirement.** LaunchAgents only fire while you have an active
GUI session. If you are logged in but your session is locked, or you are
connected only over SSH, the agent does not fire. If the Mac was asleep at the
scheduled time, launchd coalesces and fires once on wake. If the Mac was fully
powered off, launchd drops the missed firing; the catch-up gate in `run.py`
(`last_successful_run` + interval) detects the gap and covers the missed cycle
on the next successful run, the same way the crontab path does.

To verify:

```sh
launchctl list | grep android-watcher
```

To load or reload manually:

```sh
launchctl load -w ~/Library/LaunchAgents/com.krayong.android-watcher.plist
```

### Linux: systemd user timer

`schedule install` writes `android-watcher.service` and `android-watcher.timer`
into `$XDG_CONFIG_HOME/systemd/user/` (defaulting to
`~/.config/systemd/user/`), reloads the daemon, and runs:

```sh
systemctl --user enable --now android-watcher.timer
```

**Linger requirement.** Systemd user timers only run while your user session is
active; they stop at logout unless lingering is enabled. Enable it with:

```sh
loginctl enable-linger $USER
```

Linger keeps the user slice alive after logout so the timer fires even when no
interactive session exists. Run `loginctl enable-linger` once; the setting
persists across reboots.

To check timer status:

```sh
systemctl --user status android-watcher.timer
```

### Linux: crontab fallback

On Linux without systemd, `schedule install` adds a marked block to your
crontab via `crontab -l` / `crontab -`. The block starts with
`# >>> android-watcher >>>` so it can be cleanly replaced or removed.

**Timezone pinning.** The block includes `CRON_TZ=<your-local-tz>` at the top
of the block. Vixie cron and cronie honour `CRON_TZ`; older or minimal cron
builds that do not support it fall back to the system timezone set in
`/etc/timezone` or `TZ`. Verify which behaviour your cron uses if the job fires
at the wrong time.

Missed runs backfill through the `last_successful_run` catch-up gate in
`run.py`; no separate cron retry mechanism is needed.

## Weekly schedule: Monday across all backends

When the schedule interval is `weekly`, all three backends fire on **Monday**:

- **launchd**: `StartCalendarInterval` with `Weekday: 1`
- **systemd**: `OnCalendar=Mon *-*-* HH:MM:00 <tz>`
- **crontab**: the cron field `1` (day-of-week, 0=Sunday)

The day-of-week is not configurable separately; change the interval to `cron`
and write a custom expression if you need a different day.

## DST edge note

The systemd timer pins its `OnCalendar` expression to the local IANA timezone
at install time (e.g. `America/New_York`). When clocks spring forward and an
hour is skipped, the scheduled time may fall in the gap.

`Persistent=true` in the timer unit tells systemd to fire the missed activation
as soon as the clock catches up. The run pipeline also has a catch-up gate
(`INTERVAL_DELTA` window in `run.py`) that detects a missed cycle from
`last_successful_run` and re-runs if the machine was asleep or off at the
scheduled time.

A DST-skipped hour causes at most one delayed run, never a dropped run.

The launchd backend uses `StartCalendarInterval`, which uses local wall-clock
time. If the system was asleep when the schedule triggered, launchd fires once
on wake. A DST-skipped hour where the Mac was on but the slot fell in the gap
may be dropped by launchd; the catch-up gate in `run.py` covers it the same
way it does for the crontab path.

The crontab backend uses `CRON_TZ` to pin the expression; whether DST-skipped
minutes cause a missed cron entry depends on the cron daemon. The catch-up gate
in `run.py` covers the missed run regardless.

## Source-selection persistence

The TUI writes the explicit `enabled_sources` list to the config file when you
save. This has two implications:

1. **Freezing the set.** If you check every catalog source and save, the config
   records all current source IDs explicitly. A new source added to the catalog
   in a future release is not watched until you re-open the TUI and re-save.
   An absent or empty `enabled_sources` means "use catalog defaults". Once you
   save from the TUI, that implicit state is replaced by an explicit list.

2. **The `__none__` sentinel.** If you uncheck every catalog source and save,
   the TUI writes `enabled_sources = ["__none__"]` instead of an empty list.
   The empty list `[]` is the implicit "watch all catalog defaults" default, so
   the sentinel is needed to express "watch no catalog sources" without
   ambiguity. Custom sources you add directly to the config are always watched
   regardless of `enabled_sources`.

## Verifying the schedule

After installation, confirm the job is active:

```sh
android-watcher schedule status
```

Output is `OK` when the job is loaded or active, `FAIL` when it is not, with a
descriptive message. The command exits 0 on `OK` and non-zero on `FAIL`, so you
can use it in scripts.

To remove the schedule:

```sh
android-watcher schedule remove
```
