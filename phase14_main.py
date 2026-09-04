"""Qt Quick entry point for the GPU Cockpit."""

import json
import os
import sys
import time
import traceback
from pathlib import Path

from ed_companion.persistence import atomic_write, cleanup_stale_atomic_temps


CONFIG_DIR = Path(
    os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")
) / "EDEngineeringCompanion"
CONFIG_FILE = CONFIG_DIR / "phase14_graphics.json"


def configured_mode():
    try:
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        mode = str(data.get("renderer_mode") or "auto").lower()
        return mode if mode in {"auto", "gpu", "software"} else "auto"
    except (OSError, ValueError, TypeError):
        return "auto"


def configure_renderer(mode):
    os.environ["QT_QUICK_CONTROLS_STYLE"] = "Basic"
    os.environ.pop("QSG_RHI_BACKEND", None)
    os.environ.pop("QT_QUICK_BACKEND", None)
    if mode == "gpu":
        os.environ["QSG_RHI_BACKEND"] = "d3d11"
    elif mode == "software":
        os.environ["QT_QUICK_BACKEND"] = "software"


configure_renderer(configured_mode())

from PySide6.QtCore import (
    QEvent, QMetaObject, QObject, QProcess, QTimer, QUrl, Signal,
    qInstallMessageHandler,
)
from PySide6.QtGui import QAction, QFont
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMenu, QStyle, QSystemTrayIcon

from ed_companion import APP_VERSION

from ed_companion.phase14 import CockpitController
from ed_companion.overlay import OverlaySettings, OverlayWindowRuntime
from ed_companion.diagnostics import (
    clean_diagnostic_log,
    is_benign_qt_message,
)


SINGLE_INSTANCE_NAME = os.environ.get(
    "EDEC_SINGLE_INSTANCE_NAME", "EDEC-single-instance"
)


class SingleInstanceRuntime(QObject):
    """Keep one EDEC process and ask the existing window to foreground."""

    activationRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.server = QLocalServer(self)
        self.server.newConnection.connect(self._accept_connections)

    @staticmethod
    def notify_existing(timeout_ms=700):
        socket = QLocalSocket()
        socket.connectToServer(SINGLE_INSTANCE_NAME)
        if not socket.waitForConnected(timeout_ms):
            return False
        socket.write(b"SHOW\n")
        socket.flush()
        socket.waitForBytesWritten(timeout_ms)
        socket.disconnectFromServer()
        return True

    def listen(self):
        if self.server.listen(SINGLE_INSTANCE_NAME):
            return True
        # A crashed process may leave a stale Windows local-server endpoint.
        QLocalServer.removeServer(SINGLE_INSTANCE_NAME)
        return self.server.listen(SINGLE_INSTANCE_NAME)

    def _accept_connections(self):
        while self.server.hasPendingConnections():
            socket = self.server.nextPendingConnection()
            socket.waitForReadyRead(100)
            if bytes(socket.readAll()).strip().upper() == b"SHOW":
                self.activationRequested.emit()
            socket.disconnectFromServer()


def diagnostics_dir():
    return Path(
        os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")
    ) / "EDEngineeringCompanion"


def install_diagnostics(smoke_messages=None):
    directory = diagnostics_dir()
    clean_diagnostic_log(directory / "phase14.log")

    def crash_hook(exc_type, exc_value, exc_traceback):
        try:
            crash_dir = directory / "crashes"
            crash_dir.mkdir(parents=True, exist_ok=True)
            path = crash_dir / f"crash-{time.strftime('%Y%m%d-%H%M%S')}.log"
            atomic_write(
                path,
                "".join(traceback.format_exception(
                    exc_type, exc_value, exc_traceback
                )),
            )
        finally:
            sys.__excepthook__(exc_type, exc_value, exc_traceback)

    def qt_message_handler(_mode, context, message):
        if is_benign_qt_message(message):
            return
        source = getattr(context, "file", "") or "QML"
        line = getattr(context, "line", 0) or 0
        is_qml = str(source).lower().endswith(".qml") or ".qml:" in str(message).lower()
        if smoke_messages is not None and is_qml:
            smoke_messages.append({
                "source": str(source), "line": int(line), "message": str(message),
            })
        try:
            directory.mkdir(parents=True, exist_ok=True)
            with (directory / "phase14.log").open(
                "a", encoding="utf-8"
            ) as handle:
                handle.write(
                    f"{time.strftime('%Y-%m-%d %H:%M:%S')} · "
                    f"{source}:{line} · {message}\n"
                )
        except OSError:
            pass

    sys.excepthook = crash_hook
    qInstallMessageHandler(qt_message_handler)


class SmokeTestRunner(QObject):
    """Exercise QML surfaces and turn every diagnostic failure into exit 1."""

    PAGE_STEPS = [
        ("operations", 0, "qa-page-operations"),
        ("wishlist", 1, "qa-page-wishlist"),
        ("materials", 2, "qa-page-materials"),
        ("engineering", 3, "qa-page-engineering"),
        ("engineers", 4, "qa-page-engineers"),
        ("settings", 5, "qa-page-settings"),
        ("connections", 6, "qa-page-connections"),
        ("diagnostics", 7, "qa-page-diagnostics"),
        ("state-finds", 8, "qa-page-state-finds"),
        ("logbook", 9, "qa-page-logbook"),
        ("cmdr", 10, "qa-page-cmdr"),
        ("powerplay", 11, "qa-page-powerplay"),
        ("mining-finder", 12, "qa-page-mining-finder"),
    ]
    DIALOG_STEPS = [
        ("dialog-build-import", "qa-dialog-build-import"),
        ("dialog-logbook-detail", "qa-dialog-logbook-detail"),
        ("dialog-shortcuts", "qa-dialog-shortcuts"),
        ("dialog-global-search", "qa-dialog-global-search"),
        ("dialog-onboarding", "qa-dialog-onboarding"),
        ("dialog-about", "qa-dialog-about"),
    ]

    def __init__(
        self, app, window, qml_messages, screenshot=None,
        overlay_window=None, controller=None, parent=None,
    ):
        super().__init__(parent)
        self.app = app
        self.window = window
        self.qml_messages = qml_messages
        self.screenshot = screenshot
        self.overlay_window = overlay_window
        self.controller = controller
        self.results = []
        self.steps = []
        self.step_index = 0
        self.deadline = 0.0
        self.current = None
        self.persistence_phase = 0
        for label, page, object_name in self.PAGE_STEPS:
            self.steps.append((label, lambda p=page, n=object_name: self._page(p, n)))
        self.steps.extend([
            ("materials-farm-missing", self._materials_state),
            ("engineers-unlock-guide", lambda: self._engineer_state(True, False)),
            ("engineers-tech-brokers", lambda: self._engineer_state(False, True)),
            ("connections-preview", self._connection_states),
            ("lazy-page-state-persistence", self._lazy_page_state),
            ("engineering-overlay", self._engineering_overlay),
        ])
        for label, object_name in self.DIALOG_STEPS:
            self.steps.append((label, lambda n=object_name: self._dialog(n)))
        self.steps.append(("screenshot", self._capture))

    def start(self):
        QTimer.singleShot(0, self._next)

    def _find(self, object_name):
        return self.window.findChild(QObject, object_name)

    def _page(self, page, object_name):
        self.window.setProperty("currentPage", page)
        target = self._find(object_name)
        return bool(target and target.property("visible"))

    def _materials_state(self):
        self.window.setProperty("currentPage", 2)
        target = self._find("qa-page-materials")
        if not target:
            return False
        target.setProperty("farmMissing", True)
        return bool(target.property("visible") and target.property("farmMissing"))

    def _engineer_state(self, unlock_mode, guardian_mode):
        self.window.setProperty("currentPage", 4)
        target = self._find("qa-page-engineers")
        if not target:
            return False
        target.setProperty("unlockMode", unlock_mode)
        target.setProperty("guardianMode", guardian_mode)
        return bool(
            target.property("visible")
            and bool(target.property("unlockMode")) == unlock_mode
            and bool(target.property("guardianMode")) == guardian_mode
        )

    def _connection_states(self):
        self.window.setProperty("currentPage", 6)
        target = self._find("qa-page-connections")
        if not target or not target.property("visible"):
            return False
        for mode in (0, 1, 2):
            self.window.setProperty("connectionPreviewMode", mode)
            if int(self.window.property("connectionPreviewMode")) != mode:
                return False
        return True

    def _engineering_overlay(self):
        if not self.overlay_window or not self.controller:
            return False
        self.overlay_window.show()
        action = self.overlay_window.findChild(QObject, "overlay-next-action")
        readiness = self.overlay_window.findChild(
            QObject, "overlay-material-readiness"
        )
        expected_action = (
            self.controller.operationAction.get("title")
            or self.controller.nextAction
        )
        expected_readiness = (
            f"{self.controller.materialStatus} · "
            f"{self.controller.covered} / {self.controller.required}"
        )
        valid = bool(
            action and readiness
            and action.property("text") == expected_action
            and readiness.property("text") == expected_readiness
        )
        self.overlay_window.hide()
        return valid

    def _lazy_page_state(self):
        """Prove an unloaded page restores its transient controls."""
        if self.persistence_phase == 0:
            self.window.setProperty("currentPage", 2)
            target = self._find("qa-page-materials")
            if not target:
                return False
            target.setProperty("neededOnly", True)
            target.setProperty("statusFilter", "missing")
            self.window.setProperty("currentPage", 0)
            self.persistence_phase = 1
            return False
        if self.persistence_phase == 1:
            if self._find("qa-page-materials") is not None:
                return False
            self.window.setProperty("currentPage", 2)
            self.persistence_phase = 2
            return False
        target = self._find("qa-page-materials")
        return bool(
            target
            and target.property("visible")
            and bool(target.property("neededOnly"))
            and str(target.property("statusFilter")) == "missing"
        )

    def _dialog(self, object_name):
        target = self._find(object_name)
        if not target:
            return False
        if not bool(target.property("visible")):
            QMetaObject.invokeMethod(target, "open")
            return False
        QMetaObject.invokeMethod(target, "close")
        return True

    def _capture(self):
        if not self.screenshot:
            return True
        if not self.window.grabWindow().save(self.screenshot):
            raise RuntimeError(f"screenshot save failed: {self.screenshot}")
        return True

    def _next(self):
        if self.step_index >= len(self.steps):
            self._finish()
            return
        self.current = self.steps[self.step_index]
        self.deadline = time.monotonic() + 3.0
        self._poll()

    def _poll(self):
        label, check = self.current
        try:
            ready = bool(check())
        except Exception as exc:
            self.results.append({"area": label, "status": "FAIL", "error": str(exc)})
            self.step_index += 1
            QTimer.singleShot(0, self._next)
            return
        if ready:
            self.results.append({"area": label, "status": "PASS"})
            self.step_index += 1
            # Let delegates and image providers finish incubation before the
            # next lazy page unloads their Loader hierarchy.
            QTimer.singleShot(750, self._next)
        elif time.monotonic() >= self.deadline:
            self.results.append({
                "area": label, "status": "FAIL",
                "error": "ready-state timeout after 3000 ms",
            })
            self.step_index += 1
            QTimer.singleShot(0, self._next)
        else:
            QTimer.singleShot(50, self._poll)

    def _finish(self):
        for message in self.qml_messages:
            self.results.append({
                "area": "qml-runtime", "status": "FAIL",
                "error": f"{message['source']}:{message['line']}: {message['message']}",
            })
        failed = any(row["status"] == "FAIL" for row in self.results)
        report = {"status": "FAIL" if failed else "PASS", "areas": self.results}
        print("PHASE14_SMOKE_REPORT=" + json.dumps(report, ensure_ascii=False), flush=True)
        self.app.exit(1 if failed else 0)


def cleanup_startup_persistence_temps():
    """Clean stale atomic-write artifacts without touching active writers."""
    directories = [CONFIG_DIR]
    try:
        profiles = [
            path for path in CONFIG_DIR.glob("profile-*") if path.is_dir()
        ]
    except OSError:
        profiles = []
    directories.extend(profiles)
    directories.extend(
        path / "crashes" for path in [CONFIG_DIR, *profiles]
    )
    for directory in directories:
        cleanup_stale_atomic_temps(directory)


class TrayRuntime(QObject):
    """Own the optional Windows tray lifecycle without changing app logic."""

    def __init__(self, app, window, controller, overlay_settings):
        super().__init__(app)
        self.app = app
        self.window = window
        self.controller = controller
        self.overlay_settings = overlay_settings
        self.quitting = False
        self.available = QSystemTrayIcon.isSystemTrayAvailable()
        self.controller.setSystemTrayAvailable(self.available)
        self.tray = QSystemTrayIcon(self)
        self.tray.setToolTip("ED\u00b7OPS · Journal and EDDN monitor")
        self.tray.setIcon(app.style().standardIcon(QStyle.SP_ComputerIcon))
        self.menu = QMenu()
        self.open_action = QAction("Open ED\u00b7OPS", self.menu)
        self.refresh_action = QAction("Refresh Journal now", self.menu)
        self.overlay_action = QAction("Show Engineering Overlay", self.menu)
        self.overlay_action.setCheckable(True)
        self.overlay_lock_action = QAction("Lock Overlay", self.menu)
        self.overlay_lock_action.setCheckable(True)
        self.overlay_click_action = QAction("Click-through Overlay", self.menu)
        self.overlay_click_action.setCheckable(True)
        self.status_action = QAction("Status", self.menu)
        self.status_action.setEnabled(False)
        self.exit_action = QAction("Exit ED\u00b7OPS", self.menu)
        self.restart_action = QAction("Restart ED\u00b7OPS", self.menu)
        self.menu.addAction(self.open_action)
        self.menu.addAction(self.refresh_action)
        self.menu.addSeparator()
        self.menu.addAction(self.overlay_action)
        self.menu.addAction(self.overlay_lock_action)
        self.menu.addAction(self.overlay_click_action)
        self.menu.addSeparator()
        self.menu.addAction(self.status_action)
        self.menu.addSeparator()
        self.menu.addAction(self.restart_action)
        self.menu.addAction(self.exit_action)
        self.tray.setContextMenu(self.menu)
        self.open_action.triggered.connect(self.show_window)
        self.refresh_action.triggered.connect(controller.reloadJournalNow)
        self.overlay_action.triggered.connect(overlay_settings.toggleVisible)
        self.overlay_lock_action.triggered.connect(overlay_settings.toggleLocked)
        self.overlay_click_action.triggered.connect(
            overlay_settings.toggleClickThrough
        )
        self.exit_action.triggered.connect(self.exit_app)
        self.restart_action.triggered.connect(self.restart_app)
        self.menu.aboutToShow.connect(self.update_status)
        self.tray.activated.connect(self._activated)
        self.window.installEventFilter(self)
        self.controller.uiChanged.connect(self.sync)
        self.controller.activityChanged.connect(self.update_status)
        self.overlay_settings.changed.connect(self.update_status)
        self.sync()
        self.restart_requested = False

    def enabled(self):
        return bool(self.controller.backgroundMode and self.available)

    def sync(self):
        active = self.enabled()
        self.tray.setVisible(active)
        self.app.setQuitOnLastWindowClosed(not active)
        self.update_status()

    def update_status(self):
        self.overlay_action.setChecked(self.overlay_settings.visible)
        self.overlay_lock_action.setChecked(self.overlay_settings.locked)
        self.overlay_click_action.setChecked(self.overlay_settings.clickThrough)
        if not self.available:
            mode = "TRAY UNAVAILABLE"
        else:
            mode = "RUNNING IN BACKGROUND" if self.enabled() and not self.window.isVisible() else "WINDOW OPEN"
        self.controller.setBackgroundRuntimeStatus(mode)
        self.status_action.setText(f"{mode} · {self.controller.activity}")

    def show_window(self):
        self.window.show()
        self.window.raise_()
        self.window.requestActivate()
        self.update_status()

    def exit_app(self):
        self.quitting = True
        self.app.quit()

    def restart_app(self):
        self.restart_requested = True
        self.exit_app()

    def _activated(self, reason):
        if reason in {
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        }:
            self.show_window()

    def eventFilter(self, watched, event):
        if (
            watched is self.window and event.type() == QEvent.Type.Close
            and self.enabled() and not self.quitting
        ):
            event.ignore()
            self.window.hide()
            self.tray.showMessage(
                "ED\u00b7OPS is still running",
                "Journal, inventory and EDDN monitoring continue in the tray.",
                QSystemTrayIcon.MessageIcon.Information,
                3500,
            )
            self.update_status()
            return True
        return super().eventFilter(watched, event)


def run():
    cleanup_startup_persistence_temps()
    smoke_test = os.environ.get("PHASE14_SMOKE_TEST") == "1"
    smoke_messages = [] if smoke_test else None
    install_diagnostics(smoke_messages)
    app = QApplication(sys.argv)
    app.setFont(QFont("Segoe UI", 12))
    # Keep Qt's writable cache location aligned with EDEC's existing runtime
    # data directory. The display name remains human-readable in Windows.
    app.setApplicationName("EDEngineeringCompanion")
    app.setApplicationDisplayName("ED Engineering Companion")
    app.setApplicationVersion(APP_VERSION)

    if SingleInstanceRuntime.notify_existing():
        return 0
    single_instance = SingleInstanceRuntime(app)
    if not single_instance.listen():
        return 3

    controller = CockpitController()
    overlay_settings = OverlaySettings(parent=app)
    app.aboutToQuit.connect(controller.shutdown)
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("cockpit", controller)
    engine.rootContext().setContextProperty("overlaySettings", overlay_settings)
    engine.rootContext().setContextProperty(
        "smokeInjectQmlError",
        smoke_test and os.environ.get("PHASE14_SMOKE_INJECT_QML_ERROR") == "1",
    )
    engine.rootContext().setContextProperty("smokeTest", smoke_test)
    qml = Path(__file__).resolve().parent / "Main.qml"
    engine.load(QUrl.fromLocalFile(str(qml)))
    if not engine.rootObjects():
        return 2
    window = engine.rootObjects()[0]
    overlay_qml = Path(__file__).resolve().parent / "qml" / "Overlay.qml"
    engine.load(QUrl.fromLocalFile(str(overlay_qml)))
    overlay_window = next((
        root for root in engine.rootObjects()
        if root.objectName() == "engineering-overlay-window"
    ), None)
    if overlay_window is None:
        return 2
    overlay_runtime = OverlayWindowRuntime(
        overlay_window, overlay_settings, parent=app
    )
    tray_runtime = TrayRuntime(app, window, controller, overlay_settings)
    single_instance.activationRequested.connect(tray_runtime.show_window)
    controller.exitRequested.connect(tray_runtime.exit_app)
    controller.restartRequested.connect(tray_runtime.restart_app)
    if "--background" in sys.argv and tray_runtime.enabled():
        window.hide()
    preview_width = os.environ.get("PHASE14_PREVIEW_WIDTH")
    preview_height = os.environ.get("PHASE14_PREVIEW_HEIGHT")
    try:
        if preview_width:
            window.setWidth(int(preview_width))
        if preview_height:
            window.setHeight(int(preview_height))
    except ValueError:
        pass
    preview_page = os.environ.get("PHASE14_PREVIEW_PAGE")
    if preview_page is not None:
        try:
            selected_preview_page = int(preview_page)
            window.setProperty("currentPage", selected_preview_page)
            # Journal bootstrap may restore the persisted page shortly after
            # the QML root appears. Reassert explicit preview intent before a
            # scheduled screenshot so documentation captures stay stable.
            QTimer.singleShot(
                500,
                lambda page=selected_preview_page: window.setProperty(
                    "currentPage", page
                ),
            )
        except ValueError:
            pass
    preview_language = os.environ.get("PHASE14_PREVIEW_LANGUAGE")
    if preview_language:
        controller.setInterfaceLanguage(preview_language)
    preview_theme = os.environ.get("PHASE14_PREVIEW_THEME")
    if preview_theme:
        controller.setTheme(preview_theme)
    preview_connection = os.environ.get("PHASE14_PREVIEW_CONNECTION")
    if preview_connection is not None:
        try:
            window.setProperty(
                "connectionPreviewMode", int(preview_connection)
            )
        except ValueError:
            pass
    preview_engineers_mode = os.environ.get("PHASE14_PREVIEW_ENGINEERS_MODE")
    if preview_engineers_mode is not None:
        try:
            window.setProperty("previewEngineersMode", int(preview_engineers_mode))
        except ValueError:
            pass
    preview_material = os.environ.get("PHASE14_PREVIEW_MATERIAL")
    if preview_material:
        controller.selectMaterial(preview_material)
    preview_blueprint = os.environ.get("PHASE14_PREVIEW_BLUEPRINT")
    if preview_blueprint:
        controller.selectBlueprint(preview_blueprint)
    preview_module_slot = os.environ.get("PHASE14_PREVIEW_MODULE_SLOT")
    if preview_blueprint and preview_module_slot:
        def select_preview_module():
            controller.selectBlueprint(preview_blueprint)
            controller.setSelectedModuleSlot(preview_module_slot)
        QTimer.singleShot(500, select_preview_module)
    preview_plan_mode = os.environ.get("PHASE14_PREVIEW_PLAN_MODE")
    if preview_plan_mode:
        def select_preview_plan_mode():
            controller.setPlanMode(preview_plan_mode)
            if os.environ.get("PHASE14_PREVIEW_EXPERIMENTAL") == "first":
                rows = controller._selected_blueprint.get("experimentals", [])
                if rows:
                    controller.setSelectedExperimental(rows[0].get("id", ""))
        QTimer.singleShot(600, select_preview_plan_mode)
    screenshot = os.environ.get("PHASE14_SCREENSHOT")
    smoke_runner = None
    if smoke_test:
        smoke_runner = SmokeTestRunner(
            app, window, smoke_messages, screenshot=screenshot,
            overlay_window=overlay_window, controller=controller, parent=app,
        )
        smoke_runner.start()
    elif screenshot:
        def capture():
            app.exit(0 if window.grabWindow().save(screenshot) else 1)
        QTimer.singleShot(800, capture)
    exit_code = app.exec()
    # Qt invalidates context properties while tearing down the engine. Those
    # expected post-event-loop binding reevaluations are not runtime errors and
    # must not pollute the user-facing diagnostics log.
    qInstallMessageHandler(lambda _mode, _context, _message: None)
    # Destroy QML roots while the context controller is still alive. This
    # prevents shutdown-time bindings from observing a null context property.
    for root in engine.rootObjects():
        root.setVisible(False)
        root.deleteLater()
    app.processEvents()
    engine.clearComponentCache()
    single_instance.server.close()
    QLocalServer.removeServer(SINGLE_INSTANCE_NAME)
    if tray_runtime.restart_requested:
        if getattr(sys, "frozen", False):
            QProcess.startDetached(sys.executable, sys.argv[1:])
        else:
            QProcess.startDetached(
                sys.executable, sys.argv, str(Path(__file__).resolve().parent)
            )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(run())
