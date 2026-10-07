# Changelog

All notable changes are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.2.0] - 2026-10-07

### Added

- Optional top-bar icon (StatusNotifierItem) showing the next break, with a menu to pause,
  resume, open or quit. Works with the "AppIndicator and KStatusNotifierItem Support"
  extension, included in Ubuntu. Toggle it in Preferences.
- `movebreak doctor` reports whether a top-bar icon host is available.

### Changed

- The process is named "Movebreak" instead of "python3" in GNOME System Monitor and `top`.

## [0.1.0] - 2026-10-07

### Added

- Built-in, evidence-based activities: walk break, eye break (20-20-20), stretch and
  switch position (on by default); strength snack, water, breathing reset and sit–stand
  switch (opt-in).
- Custom activities with their own name, icon, tips and schedule.
- Profiles: Recommended, Light and Eyes first, plus your own (create, rename, delete,
  reset).
- Scheduler that counts active time only: idle (Mutter IdleMonitor), screen lock and
  suspend pause the timers, and enough time away counts as done.
- Reminder merging, minimum gap, "also counts as" between activities, snooze limit,
  expiry of unanswered reminders.
- Notifications with Done, Snooze and Skip buttons.
- Pause for a while, until tomorrow or until resumed.
- Start at login (autostart file natively, Background portal in Flatpak), with a status
  line in Quick Settings › Background Apps for the Flatpak build.
- Command line: `pause`, `resume`, `status`, `profile`, `doctor`.
- `movebreak doctor` system checks, including an interactive notification test.
- User installer that needs no administrator rights, and a Flatpak manifest.

[Unreleased]: https://github.com/isreb27/movebreak/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/isreb27/movebreak/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/isreb27/movebreak/releases/tag/v0.1.0
