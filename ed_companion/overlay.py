"""Presentation-only engineering overlay settings and window geometry."""

import json
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, Property, QRect, QTimer, Signal, Slot
from PySide6.QtGui import QGuiApplication

from ed_companion.persistence import atomic_write, load_json_file


DEFAULT_OVERLAY = {
    "visible": False,
    "locked": False,
    "click_through": False,
    "opacity": 0.92,
    "scale": 1.0,
    "geometry": {},
}


def clamp_overlay_geometry(saved, screens, fallback_size=(420, 230)):
    """Return a visible logical-pixel rectangle on an available monitor."""
    screens = [row for row in screens if isinstance(row, dict)]
    if not screens:
        return (40, 40, *fallback_size), ""
    requested_id = str((saved or {}).get("screen") or "")
    screen = next((row for row in screens if row.get("id") == requested_id), screens[0])
    available = screen["available"]
    ax, ay, aw, ah = (int(value) for value in available)
    width = max(300, min(int((saved or {}).get("width") or fallback_size[0]), aw))
    height = max(170, min(int((saved or {}).get("height") or fallback_size[1]), ah))
    x = int((saved or {}).get("x", ax + 32))
    y = int((saved or {}).get("y", ay + 32))
    x = max(ax, min(x, ax + aw - width))
    y = max(ay, min(y, ay + ah - height))
    return (x, y, width, height), str(screen.get("id") or "")


class OverlaySettings(QObject):
    changed = Signal()

    def __init__(self, path=None, parent=None):
        super().__init__(parent)
        self.path = Path(path) if path else (
            Path(__import__("os").environ.get("LOCALAPPDATA")
                 or (Path.home() / "AppData" / "Local"))
            / "EDEngineeringCompanion" / "overlay_settings.json"
        )
        loaded = load_json_file(self.path, {})
        self._data = dict(DEFAULT_OVERLAY)
        if isinstance(loaded, dict):
            self._data.update({key: loaded[key] for key in DEFAULT_OVERLAY if key in loaded})

    def _save(self):
        atomic_write(self.path, json.dumps(self._data, indent=2))

    def _set(self, key, value):
        if self._data.get(key) == value:
            return
        self._data[key] = value
        self._save()
        self.changed.emit()

    visible = Property(bool, lambda self: bool(self._data["visible"]),
                       lambda self, value: self._set("visible", bool(value)), notify=changed)
    locked = Property(bool, lambda self: bool(self._data["locked"]),
                      lambda self, value: self._set("locked", bool(value)), notify=changed)
    clickThrough = Property(bool, lambda self: bool(self._data["click_through"]),
                            lambda self, value: self._set("click_through", bool(value)), notify=changed)
    opacity = Property(float, lambda self: float(self._data["opacity"]),
                       lambda self, value: self._set("opacity", max(0.35, min(1.0, float(value)))), notify=changed)
    scale = Property(float, lambda self: float(self._data["scale"]),
                     lambda self, value: self._set("scale", max(0.75, min(1.5, float(value)))), notify=changed)

    @Slot()
    def toggleVisible(self):
        self.visible = not self.visible

    @Slot()
    def toggleLocked(self):
        self.locked = not self.locked

    @Slot()
    def toggleClickThrough(self):
        self.clickThrough = not self.clickThrough

    def geometry(self):
        value = self._data.get("geometry", {})
        return dict(value) if isinstance(value, dict) else {}

    def save_geometry(self, screen_id, rectangle):
        self._data["geometry"] = {
            "screen": str(screen_id or ""), "x": rectangle.x(), "y": rectangle.y(),
            "width": rectangle.width(), "height": rectangle.height(),
        }
        self._save()


class OverlayWindowRuntime(QObject):
    """Restore and persist one QML overlay window without domain logic."""

    def __init__(self, window, settings, parent=None):
        super().__init__(parent)
        self.window = window
        self.settings = settings
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(250)
        self.timer.timeout.connect(self._save_geometry)
        self._restore_geometry()
        window.installEventFilter(self)

    @staticmethod
    def _screen_id(screen):
        hardware = [
            screen.manufacturer().strip(),
            screen.model().strip(),
            screen.serialNumber().strip(),
        ]
        return "|".join(hardware) if any(hardware) else screen.name()

    @staticmethod
    def _screens():
        rows = []
        primary = QGuiApplication.primaryScreen()
        for screen in QGuiApplication.screens():
            area = screen.availableGeometry()
            rows.append({
                "id": OverlayWindowRuntime._screen_id(screen),
                "available": (area.x(), area.y(), area.width(), area.height()),
                "primary": screen is primary,
            })
        rows.sort(key=lambda row: not row["primary"])
        return rows

    def _restore_geometry(self):
        rectangle, _screen_id = clamp_overlay_geometry(
            self.settings.geometry(), self._screens()
        )
        self.window.setGeometry(QRect(*rectangle))

    def _save_geometry(self):
        center = self.window.geometry().center()
        screen = QGuiApplication.screenAt(center) or QGuiApplication.primaryScreen()
        screen_id = self._screen_id(screen) if screen else ""
        self.settings.save_geometry(screen_id, self.window.geometry())

    def eventFilter(self, watched, event):
        if watched is self.window and event.type() in {
            QEvent.Type.Move, QEvent.Type.Resize,
        }:
            self.timer.start()
        return super().eventFilter(watched, event)
