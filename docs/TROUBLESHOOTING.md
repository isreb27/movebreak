# Troubleshooting and system checks

Start with:

```sh
movebreak doctor          # all checks + an interactive notification test
movebreak doctor quick    # checks only
```

With the Flatpak build: `flatpak run io.github.isreb27.Movebreak doctor`. Running it
inside the sandbox matters: that is where permissions differ.

Each section below explains one line of the report, how to check it by hand, and what
to do if it fails.

## Versions

Movebreak needs GTK 4 and libadwaita 1.5 or newer (Ubuntu 24.04 and later, Fedora 40
and later).

Manual check:

```sh
python3 -c "import gi; gi.require_version('Gtk','4.0'); gi.require_version('Adw','1'); \
from gi.repository import Adw; print('libadwaita', Adw.get_major_version(), Adw.get_minor_version())"
```

If that fails: `sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1` (Ubuntu) or
`sudo dnf install python3-gobject gtk4 libadwaita` (Fedora). Without administrator
rights, ask your IT team, or use the Flatpak build if Flatpak is installed.

## Desktop file

GNOME only shows notifications from apps that have a `.desktop` file named after their
application ID. `scripts/install.py` installs
`~/.local/share/applications/io.github.isreb27.Movebreak.desktop`.

If you run Movebreak straight from a checkout, install the desktop file with
`python3 scripts/install.py --dev`.

## Idle detection

Movebreak asks GNOME's compositor, Mutter, to tell it when you have been idle for
60 seconds and when you come back. Without it, time away is only noticed when the
screen locks.

Manual check, native install:

```sh
gdbus call --session --dest org.gnome.Mutter.IdleMonitor \
  --object-path /org/gnome/Mutter/IdleMonitor/Core \
  --method org.gnome.Mutter.IdleMonitor.GetIdletime
```

It prints `(uint64 1234,)`: milliseconds since your last input.

Manual check from inside the Flatpak sandbox (the manifest grants this name):

```sh
flatpak run --command=gdbus io.github.isreb27.Movebreak call --session \
  --dest org.gnome.Mutter.IdleMonitor --object-path /org/gnome/Mutter/IdleMonitor/Core \
  --method org.gnome.Mutter.IdleMonitor.GetIdletime
```

An `AccessDenied` or `ServiceUnknown` error means the check failed: you are probably
not in a GNOME session, or the Flatpak was built without
`--talk-name=org.gnome.Mutter.IdleMonitor`.

## Screen lock

```sh
gdbus call --session --dest org.gnome.ScreenSaver --object-path /org/gnome/ScreenSaver \
  --method org.gnome.ScreenSaver.GetActive
```

Prints `(false,)` while unlocked.

## Do Not Disturb

Movebreak 0.1 does not change its behaviour during Do Not Disturb: GNOME already hides
banners then, and reminders wait in the message tray. This check tells us whether a
future "pause during Do Not Disturb" option can work on your machine.

Native install:

```sh
gsettings get org.gnome.desktop.notifications show-banners
```

`true` means Do Not Disturb is off.

Inside the Flatpak sandbox, through the Settings portal:

```sh
flatpak run --command=gdbus io.github.isreb27.Movebreak call --session \
  --dest org.freedesktop.portal.Desktop --object-path /org/freedesktop/portal/desktop \
  --method org.freedesktop.portal.Settings.ReadOne \
  org.gnome.desktop.notifications show-banners
```

An error such as `Requested setting not found` means the portal does not expose it.

## Notifications and buttons

The full `movebreak doctor` sends a test notification with Done, Snooze and Skip
buttons and waits 60 seconds for a click. You can also use Preferences › **Send a Test
Notification**. While it is on screen, note:

- whether a banner appears at all;
- how long it stays (GNOME hides normal banners after a few seconds; they remain in the
  message tray, opened with <kbd>Super</kbd>+<kbd>V</kbd>);
- whether clicking a button is reported back.

No banner? Check Settings › Notifications: the global switch, Do Not Disturb, and the
**Movebreak** entry. Also check the desktop file (above).

## Background portal (Flatpak only)

GNOME lists sandboxed apps that run without a window in **Quick Settings › Background
Apps**, with a status line the app can set. Run `flatpak run io.github.isreb27.Movebreak
doctor quick`, then open Quick Settings: Movebreak should be listed with the text
"Movebreak doctor: status line works". In normal use the line shows what is due next.

Native installs are never listed there; that is expected.

## Top-bar icon

GNOME has no tray of its own. The icon needs the GNOME Shell extension
"AppIndicator and KStatusNotifierItem Support", which Ubuntu enables by default. On
Fedora, install it from GNOME Extensions (or `sudo dnf install
gnome-shell-extension-appindicator`), then log out and back in.

Manual check that a host is running:

```sh
gdbus call --session --dest org.kde.StatusNotifierWatcher \
  --object-path /StatusNotifierWatcher \
  --method org.freedesktop.DBus.Properties.Get \
  org.kde.StatusNotifierWatcher IsStatusNotifierHostRegistered
```

`(<true>,)` means icons can be shown. Turn Movebreak's icon on or off in Preferences ›
Show Icon in Top Bar.

## Common problems

**It shows as "python3" in `ps aux`.** `ps aux` shows the full command line
(`python3 -m movebreak`); System Monitor, `top` and `ps -o comm` show **Movebreak**. To
find it: `pgrep -a Movebreak`.

**"Movebreak is not running" from the command line.** Start it with
`movebreak --background` or open it from the app grid.

**It does not start at login.** Turn on Preferences › Start at Login. Natively this
writes `~/.config/autostart/io.github.isreb27.Movebreak.desktop`; check that the `Exec`
line points to an existing `movebreak` launcher (reinstalling rewrites it).

**Two kinds of break reminders.** GNOME 48 and later have their own; turn them off in
Settings › Wellbeing.

**"The database uses schema N".** You opened your data with an older Movebreak after a
newer one. Update Movebreak.

**Debug logging.** `MOVEBREAK_LOG_LEVEL=debug movebreak` prints presence changes and
other details. Quit the running instance first so the new one becomes the main one.
