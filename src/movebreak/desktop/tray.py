# SPDX-FileCopyrightText: 2026 The Movebreak Authors
# SPDX-License-Identifier: GPL-3.0-or-later
"""An optional top-bar icon with a menu (StatusNotifierItem + dbusmenu).

GNOME has no tray of its own. Ubuntu enables the "AppIndicator and
KStatusNotifierItem Support" extension by default; on Fedora it is an optional
extension. That extension acts as the *StatusNotifierWatcher*; this module
exports two D-Bus objects on the app's own connection and registers them:

* ``/StatusNotifierItem`` implements ``org.kde.StatusNotifierItem`` (the icon);
* ``/StatusNotifierItem/Menu`` implements ``com.canonical.dbusmenu`` (its menu).

No GTK 3 or libappindicator is involved, so it works next to GTK 4.
Hiding the icon sets its status to ``Passive``, which hosts hide.

Specifications:
https://www.freedesktop.org/wiki/Specifications/StatusNotifierItem/
https://github.com/AyatanaIndicators/libdbusmenu/blob/master/libdbusmenu-glib/dbus-menu.xml
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import gi

gi.require_version("GdkPixbuf", "2.0")

from gi.repository import GdkPixbuf, Gio, GLib  # noqa: E402

from movebreak import config  # noqa: E402

log = logging.getLogger(__name__)

WATCHER_NAME = "org.kde.StatusNotifierWatcher"
WATCHER_PATH = "/StatusNotifierWatcher"
ITEM_INTERFACE = "org.kde.StatusNotifierItem"
MENU_INTERFACE = "com.canonical.dbusmenu"
ITEM_PATH = "/StatusNotifierItem"
MENU_PATH = "/StatusNotifierItem/Menu"

ICON = "movebreak-walk-symbolic"
PAUSED_ICON = "movebreak-paused-symbolic"
PIXMAP_SIZES = (16, 22, 32, 48)

_INTROSPECTION = f"""
<node>
  <interface name="{ITEM_INTERFACE}">
    <property name="Category" type="s" access="read"/>
    <property name="Id" type="s" access="read"/>
    <property name="Title" type="s" access="read"/>
    <property name="Status" type="s" access="read"/>
    <property name="WindowId" type="i" access="read"/>
    <property name="IconThemePath" type="s" access="read"/>
    <property name="IconName" type="s" access="read"/>
    <property name="IconPixmap" type="a(iiay)" access="read"/>
    <property name="OverlayIconName" type="s" access="read"/>
    <property name="OverlayIconPixmap" type="a(iiay)" access="read"/>
    <property name="AttentionIconName" type="s" access="read"/>
    <property name="AttentionIconPixmap" type="a(iiay)" access="read"/>
    <property name="AttentionMovieName" type="s" access="read"/>
    <property name="ToolTip" type="(sa(iiay)ss)" access="read"/>
    <property name="ItemIsMenu" type="b" access="read"/>
    <property name="Menu" type="o" access="read"/>
    <method name="ContextMenu"><arg name="x" type="i" direction="in"/>
      <arg name="y" type="i" direction="in"/></method>
    <method name="Activate"><arg name="x" type="i" direction="in"/>
      <arg name="y" type="i" direction="in"/></method>
    <method name="SecondaryActivate"><arg name="x" type="i" direction="in"/>
      <arg name="y" type="i" direction="in"/></method>
    <method name="Scroll"><arg name="delta" type="i" direction="in"/>
      <arg name="orientation" type="s" direction="in"/></method>
    <signal name="NewTitle"/>
    <signal name="NewIcon"/>
    <signal name="NewToolTip"/>
    <signal name="NewStatus"><arg name="status" type="s"/></signal>
  </interface>
  <interface name="{MENU_INTERFACE}">
    <property name="Version" type="u" access="read"/>
    <property name="TextDirection" type="s" access="read"/>
    <property name="Status" type="s" access="read"/>
    <property name="IconThemePath" type="as" access="read"/>
    <method name="GetLayout">
      <arg name="parentId" type="i" direction="in"/>
      <arg name="recursionDepth" type="i" direction="in"/>
      <arg name="propertyNames" type="as" direction="in"/>
      <arg name="revision" type="u" direction="out"/>
      <arg name="layout" type="(ia{{sv}}av)" direction="out"/>
    </method>
    <method name="GetGroupProperties">
      <arg name="ids" type="ai" direction="in"/>
      <arg name="propertyNames" type="as" direction="in"/>
      <arg name="properties" type="a(ia{{sv}})" direction="out"/>
    </method>
    <method name="GetProperty">
      <arg name="id" type="i" direction="in"/>
      <arg name="name" type="s" direction="in"/>
      <arg name="value" type="v" direction="out"/>
    </method>
    <method name="Event">
      <arg name="id" type="i" direction="in"/>
      <arg name="eventId" type="s" direction="in"/>
      <arg name="data" type="v" direction="in"/>
      <arg name="timestamp" type="u" direction="in"/>
    </method>
    <method name="EventGroup">
      <arg name="events" type="a(isvu)" direction="in"/>
      <arg name="idErrors" type="ai" direction="out"/>
    </method>
    <method name="AboutToShow">
      <arg name="id" type="i" direction="in"/>
      <arg name="needUpdate" type="b" direction="out"/>
    </method>
    <method name="AboutToShowGroup">
      <arg name="ids" type="ai" direction="in"/>
      <arg name="updatesNeeded" type="ai" direction="out"/>
      <arg name="idErrors" type="ai" direction="out"/>
    </method>
    <signal name="ItemsPropertiesUpdated">
      <arg name="updatedProps" type="a(ia{{sv}})"/>
      <arg name="removedProps" type="a(ias)"/>
    </signal>
    <signal name="LayoutUpdated">
      <arg name="revision" type="u"/>
      <arg name="parent" type="i"/>
    </signal>
  </interface>
</node>
"""


@dataclass(frozen=True, slots=True)
class MenuEntry:
    """One menu line. A ``label`` of ``None`` is a separator; no action means disabled."""

    label: str | None
    action: Callable[[], None] | None = None

    @property
    def enabled(self) -> bool:
        return self.action is not None

    def same_look(self, other: MenuEntry) -> bool:
        return self.label == other.label and self.enabled == other.enabled


def _render_pixmaps(icons_dir: Path, icon: str) -> list[tuple[int, int, bytes]]:
    """White ARGB32 renders of a symbolic icon, for hosts that cannot see our icon files
    (for example, GNOME Shell looking into a Flatpak sandbox)."""
    path = icons_dir / f"{icon}.svg"
    try:
        svg = path.read_text(encoding="utf-8").replace("#2e3436", "#ffffff").encode()
    except OSError:
        return []
    pixmaps = []
    for size in PIXMAP_SIZES:
        loader = GdkPixbuf.PixbufLoader.new_with_type("svg")
        loader.connect("size-prepared", lambda ldr, _w, _h, s=size: ldr.set_size(s, s))
        try:
            loader.write(svg)
            loader.close()
        except GLib.Error as error:
            log.debug("Cannot render %s: %s", path, error.message)
            return []
        pixbuf = loader.get_pixbuf().add_alpha(False, 0, 0, 0)
        width, height, stride = pixbuf.get_width(), pixbuf.get_height(), pixbuf.get_rowstride()
        rgba = pixbuf.get_pixels()
        argb = bytearray()
        for row in range(height):
            line = rgba[row * stride : row * stride + width * 4]
            for i in range(0, len(line), 4):
                red, green, blue, alpha = line[i : i + 4]
                argb += bytes((alpha, red, green, blue))  # Network byte order ARGB32.
        pixmaps.append((width, height, bytes(argb)))
    return pixmaps


class TrayIcon:
    """The top-bar icon. Call :meth:`update` whenever the state may have changed."""

    def __init__(
        self, bus: Gio.DBusConnection, *, icons_dir: Path, on_activate: Callable[[], None]
    ) -> None:
        self._bus = bus
        self._icons_dir = icons_dir
        self._on_activate = on_activate
        self._registrations: list[int] = []
        self._name_watch: int | None = None
        self._host_available = False
        self._visible = True
        self._paused = False
        self._status_text = config.APP_NAME
        self._entries: list[MenuEntry] = []
        self._revision = 1
        self._pixmaps: dict[str, list[tuple[int, int, bytes]]] = {}

    # ------------------------------------------------------------------

    @property
    def host_available(self) -> bool:
        """Whether a tray host (the AppIndicator extension) is running."""
        return self._host_available

    def start(self) -> None:
        info = Gio.DBusNodeInfo.new_for_xml(_INTROSPECTION)
        for path, interface in ((ITEM_PATH, info.interfaces[0]), (MENU_PATH, info.interfaces[1])):
            try:
                self._registrations.append(
                    self._bus.register_object(
                        path, interface, self._on_method_call, self._on_get_property, None
                    )
                )
            except GLib.Error as error:
                log.warning("Cannot export the top-bar icon: %s", error.message)
                self.stop()
                return
        self._name_watch = Gio.bus_watch_name_on_connection(
            self._bus,
            WATCHER_NAME,
            Gio.BusNameWatcherFlags.NONE,
            self._on_watcher_appeared,
            self._on_watcher_vanished,
        )

    def stop(self) -> None:
        if self._name_watch is not None:
            Gio.bus_unwatch_name(self._name_watch)
            self._name_watch = None
        for registration in self._registrations:
            self._bus.unregister_object(registration)
        self._registrations.clear()

    def update(
        self, *, visible: bool, paused: bool, status_text: str, entries: Sequence[MenuEntry]
    ) -> None:
        if visible != self._visible:
            self._visible = visible
            self._emit_item("NewStatus", GLib.Variant("(s)", (self._status(),)))
        if paused != self._paused:
            self._paused = paused
            self._emit_item("NewIcon", None)
        if status_text != self._status_text:
            self._status_text = status_text
            self._emit_item("NewToolTip", None)
        new_entries = list(entries)
        changed = len(new_entries) != len(self._entries) or any(
            not new.same_look(old) for new, old in zip(new_entries, self._entries, strict=False)
        )
        self._entries = new_entries  # Always keep the newest actions.
        if changed:
            self._revision += 1
            self._emit(
                MENU_PATH,
                MENU_INTERFACE,
                "LayoutUpdated",
                GLib.Variant("(ui)", (self._revision, 0)),
            )

    # ------------------------------------------------------------------
    # Registration with the host
    # ------------------------------------------------------------------

    def _on_watcher_appeared(self, _bus: Gio.DBusConnection, _name: str, _owner: str) -> None:
        self._host_available = True

        def registered(_connection: Gio.DBusConnection, result: Gio.AsyncResult) -> None:
            try:
                self._bus.call_finish(result)
                log.debug("Top-bar icon registered")
            except GLib.Error as error:
                log.warning("The tray host refused the icon: %s", error.message)

        self._bus.call(
            WATCHER_NAME,
            WATCHER_PATH,
            WATCHER_NAME,
            "RegisterStatusNotifierItem",
            GLib.Variant("(s)", (ITEM_PATH,)),
            None,
            Gio.DBusCallFlags.NONE,
            5000,
            None,
            registered,
        )

    def _on_watcher_vanished(self, _bus: Gio.DBusConnection, _name: str) -> None:
        self._host_available = False

    # ------------------------------------------------------------------
    # D-Bus plumbing
    # ------------------------------------------------------------------

    def _status(self) -> str:
        return "Active" if self._visible else "Passive"

    def _icon(self) -> str:
        return PAUSED_ICON if self._paused else ICON

    def _pixmap_variant(self) -> GLib.Variant:
        icon = self._icon()
        if icon not in self._pixmaps:
            self._pixmaps[icon] = _render_pixmaps(self._icons_dir, icon)
        return GLib.Variant("a(iiay)", self._pixmaps[icon])

    def _emit(
        self, path: str, interface: str, signal: str, parameters: GLib.Variant | None
    ) -> None:
        if not self._registrations:
            return
        try:
            self._bus.emit_signal(None, path, interface, signal, parameters)
        except GLib.Error as error:
            log.debug("Cannot emit %s: %s", signal, error.message)

    def _emit_item(self, signal: str, parameters: GLib.Variant | None) -> None:
        self._emit(ITEM_PATH, ITEM_INTERFACE, signal, parameters)

    def _on_get_property(
        self,
        _connection: Gio.DBusConnection,
        _sender: str,
        _path: str,
        interface: str,
        name: str,
    ) -> GLib.Variant | None:
        if interface == MENU_INTERFACE:
            return {
                "Version": GLib.Variant("u", 3),
                "TextDirection": GLib.Variant("s", "ltr"),
                "Status": GLib.Variant("s", "normal"),
                "IconThemePath": GLib.Variant("as", []),
            }.get(name)
        empty_pixmap = GLib.Variant("a(iiay)", [])
        values: dict[str, Any] = {
            "Category": GLib.Variant("s", "ApplicationStatus"),
            "Id": GLib.Variant("s", "movebreak"),
            "Title": GLib.Variant("s", config.APP_NAME),
            "Status": GLib.Variant("s", self._status()),
            "WindowId": GLib.Variant("i", 0),
            "IconThemePath": GLib.Variant("s", str(self._icons_dir)),
            "IconName": GLib.Variant("s", self._icon()),
            "OverlayIconName": GLib.Variant("s", ""),
            "OverlayIconPixmap": empty_pixmap,
            "AttentionIconName": GLib.Variant("s", ""),
            "AttentionIconPixmap": empty_pixmap,
            "AttentionMovieName": GLib.Variant("s", ""),
            "ItemIsMenu": GLib.Variant("b", True),
            "Menu": GLib.Variant("o", MENU_PATH),
        }
        if name == "IconPixmap":
            return self._pixmap_variant()
        if name == "ToolTip":
            return GLib.Variant(
                "(sa(iiay)ss)", (self._icon(), [], config.APP_NAME, self._status_text)
            )
        return values.get(name)

    def _on_method_call(
        self,
        _connection: Gio.DBusConnection,
        _sender: str,
        _path: str,
        interface: str,
        method: str,
        parameters: GLib.Variant,
        invocation: Gio.DBusMethodInvocation,
    ) -> None:
        try:
            if interface == ITEM_INTERFACE:
                if method in ("Activate", "SecondaryActivate"):
                    GLib.idle_add(self._run, self._on_activate)
                invocation.return_value(None)
                return
            reply = self._menu_call(method, parameters.unpack())
            invocation.return_value(reply)
        except Exception as error:  # Never leave a D-Bus caller hanging.
            log.exception("Top-bar menu call %s failed", method)
            invocation.return_dbus_error("org.freedesktop.DBus.Error.Failed", str(error))

    def _menu_call(self, method: str, args: tuple[Any, ...]) -> GLib.Variant | None:
        if method == "GetLayout":
            parent_id = args[0]
            if parent_id == 0:
                children = [GLib.Variant("(ia{sv}av)", self._layout(i)) for i in self._ids()]
                root = (0, {"children-display": GLib.Variant("s", "submenu")}, children)
                return GLib.Variant("(u(ia{sv}av))", (self._revision, root))
            return GLib.Variant("(u(ia{sv}av))", (self._revision, self._layout(parent_id)))
        if method == "GetGroupProperties":
            ids = args[0] or self._ids()
            items = [(i, self._properties(i)) for i in ids if i in self._ids()]
            return GLib.Variant("(a(ia{sv}))", (items,))
        if method == "GetProperty":
            item_id, name = args
            return GLib.Variant("(v)", (self._properties(item_id)[name],))
        if method == "Event":
            item_id, event_id, _data, _timestamp = args
            if event_id == "clicked":
                self._activate_entry(item_id)
            return None
        if method == "EventGroup":
            for item_id, event_id, _data, _timestamp in args[0]:
                if event_id == "clicked":
                    self._activate_entry(item_id)
            return GLib.Variant("(ai)", ([],))
        if method == "AboutToShow":
            return GLib.Variant("(b)", (False,))
        if method == "AboutToShowGroup":
            return GLib.Variant("(aiai)", ([], []))
        raise ValueError(f"unknown method {method}")

    def _ids(self) -> list[int]:
        return list(range(1, len(self._entries) + 1))

    def _properties(self, item_id: int) -> dict[str, GLib.Variant]:
        entry = self._entries[item_id - 1]
        if entry.label is None:
            return {"type": GLib.Variant("s", "separator")}
        return {
            "label": GLib.Variant("s", entry.label.replace("_", "__")),
            "enabled": GLib.Variant("b", entry.enabled),
            "visible": GLib.Variant("b", True),
        }

    def _layout(self, item_id: int) -> tuple[int, dict[str, GLib.Variant], list[GLib.Variant]]:
        return (item_id, self._properties(item_id), [])

    def _activate_entry(self, item_id: int) -> None:
        if 1 <= item_id <= len(self._entries):
            action = self._entries[item_id - 1].action
            if action is not None:
                # Reply to the host first; the action may rebuild the menu.
                GLib.idle_add(self._run, action)

    @staticmethod
    def _run(action: Callable[[], None]) -> bool:
        action()
        return GLib.SOURCE_REMOVE
