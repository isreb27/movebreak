# Architecture

Movebreak is a single-instance GTK application that keeps running in the background.
One scheduler decides when to remind; everything else feeds it or carries out its
decisions.

```text
 Entry points     login autostart (--background) · window · CLI and D-Bus actions
       │
       ▼
 Core (no GTK)    Scheduler ◀──── Store (SQLite: activities, profiles, history)
       │   ▲
       │   │ idle, lock and resume events
       ▼   │
 GNOME adapters   Notifier (Gio.Notification) · PresenceMonitor (Mutter, ScreenSaver)
                  Autostart (autostart file or Background portal) · portals
```

## Source layout

```text
src/movebreak/
├── main.py              entry point: dependency check, then run the app
├── application.py       Adw.Application: wires store, scheduler and adapters; CLI commands
├── config.py            APP_ID (single source of truth) and file locations
├── doctor.py            `movebreak doctor` system checks
├── i18n.py              gettext helpers
├── process_name.py      shows "Movebreak" instead of "python3" in process lists
├── core/                no GTK, no D-Bus: fully unit-tested
│   ├── models.py        Activity, ActivitySettings, Profile, PlannedActivity, Outcome
│   ├── presets.py       built-in activities and profiles (the evidence-based defaults)
│   ├── scheduler.py     active-time clocks and reminder rules
│   ├── store.py         SQLite persistence with migrations
│   ├── clock.py         time sources (boot time vs monotonic time)
│   └── text.py          durations, schedules and reminder wording
├── desktop/             adapters to GNOME
│   ├── presence.py      idle and screen-lock detection
│   ├── notifier.py      reminders as notifications with buttons
│   ├── tray.py          optional top-bar icon (StatusNotifierItem + dbusmenu)
│   ├── dnd.py           whether Do Not Disturb is on
│   ├── autostart.py     start at login (autostart file or Background portal)
│   ├── portal.py        XDG desktop portal calls
│   └── dbus.py          GDBus helpers
├── ui/                  GTK 4 / libadwaita widgets, built in code (incl. the break screen)
└── icons/               symbolic activity icons, loaded at start-up
```

## Data model

An activity is split in two:

- `Activity`: what it is (name, icon, tips, which other activities it covers). Shared by
  every profile.
- `ActivitySettings`: how it runs in one profile (enabled, interval, break length,
  style, "counts as done if away for").

A `Profile` is a named set of `ActivitySettings`. Switching profiles changes schedules
without losing custom activities. A new custom activity is enabled in the active profile
and disabled in the others.

## Scheduler rules

The scheduler receives presence events and is ticked every 10 seconds. It returns
events (`PromptOpened`, `PromptClosed`, `ActivityCompleted`, `PauseChanged`) that the
application turns into notifications and history rows.

1. Time only counts while you are present and reminders are not paused.
2. Idle is reported after 60 seconds without input; the scheduler back-dates it, so
   those 60 seconds do not count.
3. A suspend is detected without any permission: `CLOCK_BOOTTIME` keeps running while
   asleep and `CLOCK_MONOTONIC` does not, so a gap between them is time asleep.
4. Time away that reaches an activity's threshold counts as done. Nothing piles up.
5. One reminder at a time, at least 5 minutes apart.
6. Activities due within 3 minutes are merged into one reminder; an activity covered by
   another one in the same reminder is not shown separately.
7. Done resets the activity and everything it covers. Snooze re-arms it 10 minutes
   later, at most twice. Skip resets it without credit.
8. Unanswered reminders expire after 5 minutes of active time (logged as missed);
   gentle ones disappear after 30 seconds.
9. Pausing freezes every clock; resuming continues where they stopped.

The timings in rules 5 to 8 are configurable in Preferences.

## Storage

One SQLite file, `~/.local/share/movebreak/movebreak.sqlite3` (inside
`~/.var/app/io.github.isreb27.Movebreak/` for the Flatpak). Schema changes are append-only
migrations in `core/store.py`, tracked with `PRAGMA user_version`. Built-in activities
and profiles are added on every start if missing, so new versions can ship new defaults
without touching user data.

## Decisions

| Decision | Why | Trade-off |
| --- | --- | --- |
| Python + PyGObject | Fast iteration; first-class GNOME bindings | More memory than Rust for an all-day process |
| UI built in code, no Blueprint/GtkBuilder | Runs from source with no build step, which matters when you cannot install tools | Less separation between layout and logic |
| SQLite instead of GSettings | Profiles are data, not flat keys; no schema compilation; identical native and in Flatpak | Settings are not editable with `gsettings` |
| Tick every 10 s instead of exact timers | Simple, and makes suspend detection trivial | Reminders fire up to 10 s late |
| `org.gnome.ScreenSaver` for lock state | One code path natively and in Flatpak | One extra `--talk-name` in the Flatpak |
| Top-bar icon over D-Bus, without libappindicator | libappindicator needs GTK 3, which cannot share a process with GTK 4 | About 300 lines implementing two small D-Bus interfaces |
| Break screen is a translucent full-screen window | Wayland lets no app position a window, so full screen is the only way to be centred | One monitor only; GNOME may keep it behind others, hence the banner fallback |
| "Stay on screen" uses urgent notifications | The only priority GNOME Shell never hides automatically | Urgent also breaks through Do Not Disturb, so Movebreak checks it first |
| Single instance with command-line forwarding | `movebreak pause 30` talks to the running app and prints its answer | Commands need the app to be running |
| Custom user installer (`scripts/install.py`) | Works without sudo, pip, venv or meson | Not a standard Python packaging flow; `pipx install --system-site-packages .` also works |
