"""System notifications from the desktop app: the bridge, the click route and each platform adapter.

Owner decisions 1A (the OS owns the sound), 2A (shown while the window is in front) and 3A (an
older app falls back and says so) — DESIGN §9. Every operating-system surface here is a stand-in:
no test asks for a permission, shows a banner or plays a sound. The one real-PyObjC check loads the
UserNotifications framework and builds objects, but never touches the notification center.
"""
from __future__ import annotations

import inspect
import json
import pathlib
import re
import sys
import threading
import types
from types import SimpleNamespace

import pytest

from ouroboros import desktop_notifications as dn
from ouroboros import launcher_background as lb

REPO = pathlib.Path(__file__).resolve().parents[1]


def wait_for(predicate, timeout=5.0):
    done = threading.Event()
    for _ in range(int(timeout / 0.01)):
        if predicate():
            return
        done.wait(0.01)
    raise AssertionError("condition not reached")


class Recorder(dn.NativeNotifier):
    platform = "test"

    def __init__(self, status="authorized", deliver=None, on_click=None):
        super().__init__(on_click or (lambda token: None))
        self.answer, self.deliver_with, self.calls = status, deliver, []

    def _status(self):
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer

    def _deliver(self, title, body, sound, token):
        self.calls.append((title, body, sound, token))
        if self.deliver_with is not None:
            return self.deliver_with()
        return dn.delivered(self.platform, "os" if sound else "off")


# ------------------------------------------------------------------------------- the bridge ---

def bridge(notifier):
    class Api(lb.DesktopApi):
        _background = SimpleNamespace(notifications=notifier, attention=lambda *a: {"ok": True, "args": a})

    return Api()


def pywebview_exposed(api):
    """pywebview 5.4 ``inject_pywebview.get_functions``: public bound methods of the js_api object."""
    return sorted(name for name in dir(api) if not name.startswith("_") and inspect.ismethod(getattr(api, name)))


def test_the_bridge_exposes_the_alert_methods_and_hides_its_background():
    api = bridge(Recorder())
    assert pywebview_exposed(api) == ["notify_owner", "request_attention", "request_native_notifications",
                                      "shell_info", "show_native_notification"]
    assert api.notify_owner(True, "Task finished", "", False) == {"ok": True, "args": (True, "Task finished", "", False)}


def test_launcher_main_api_inherits_the_bridge():
    source = (REPO / "launcher.py").read_text(encoding="utf-8")
    main_api = source[source.index("class MainApi("):]
    assert main_api.startswith("class MainApi(DesktopApi):"), "the frozen launcher must expose the bridge"
    assert "_background = background" in main_api.split("def ", 1)[0]
    assert "def request_attention" not in main_api, "one definition: launcher_background.DesktopApi"


def test_shell_info_is_the_apps_own_facts():
    info = bridge(Recorder(status="not_determined")).shell_info()
    assert info["shell_version"] == (REPO / "VERSION").read_text(encoding="utf-8").strip()
    assert info["persistent_storage"] is True
    assert info["native_notifications"] == {"available": True, "status": "not_determined", "platform": "test",
                                            "reason": ""}


def test_the_persistent_storage_claim_is_the_webview_flag():
    """``shell_info`` says the WebView keeps website data; that is true only while every window asks for it."""
    assert lb.PERSISTENT_WEBVIEW_STORAGE is True
    for rel in ("launcher.py", "ouroboros/launcher_onboarding.py"):
        calls = re.findall(r"^\s*webview\.start\((.*)\)\s*$", (REPO / rel).read_text(encoding="utf-8"), re.M)
        assert calls and all("private_mode=False" in args for args in calls), rel


def test_no_adapter_is_a_typed_unavailable_answer():
    api = bridge(None)
    assert api.shell_info()["native_notifications"]["status"] == "unavailable"
    assert api.request_native_notifications() == {"available": False, "status": "unavailable",
                                                  "platform": dn.platform_name(), "reason": "no_platform_adapter"}
    answer = api.show_native_notification("Task finished", "", True, "n1-a")
    assert answer["ok"] is False and answer["status"] == "unavailable" and answer["banner"] is False


def test_inputs_are_cleaned_and_failures_are_typed():
    notifier = Recorder()
    api = bridge(notifier)
    assert api.show_native_notification("  Task\nfinished ", "", True, "n4-k")["status"] == "delivered"
    assert notifier.calls[-1] == ("Task finished", "", True, "n4-k")
    api.show_native_notification("", "x" * 1000, False, "window.alert(1)")
    title, body, sound, token = notifier.calls[-1]
    assert title == "Ouroboros" and len(body) == 400 and sound is False
    assert re.fullmatch(r"n\d+-\d+", token), "a token that is not the page's shape is replaced, never echoed"

    notifier.deliver_with = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
    assert api.show_native_notification("t", "", True, "n5-k") == {
        "ok": False, "status": "failed", "banner": False, "platform": "test", "reason": "RuntimeError: boom"}
    notifier.deliver_with = lambda: (_ for _ in ()).throw(dn.Unavailable("no_service"))
    assert api.show_native_notification("t", "", True, "n6-k")["status"] == "unavailable"
    notifier.answer = RuntimeError("probe failed")
    assert api.request_native_notifications()["reason"] == "RuntimeError: probe failed"


# --------------------------------------------------------------------------- the click route ---

class Window:
    def __init__(self, fail=False):
        self.calls, self.fail = [], fail

    def show(self):
        self.calls.append("show")

    def evaluate_js(self, script):
        if self.fail:
            raise RuntimeError("page gone")
        self.calls.append(("js", script))


def background_with(window, monkeypatch):
    monkeypatch.setattr(lb, "indicator_class", lambda: None)
    background = lb.Background(lambda: None, lambda: 8765, threading.Event())
    background.window = window
    background._can_show = True
    return background


def test_a_click_opens_the_window_and_hands_the_page_its_token(monkeypatch):
    window = Window()
    background_with(window, monkeypatch).open_notification("n3-abc")
    assert window.calls[0] == "show", "the window opens first, as it was left"
    kind, script = window.calls[1]
    assert script == f"window.ouroNotifications && window.ouroNotifications.activate({json.dumps('n3-abc')})"


def test_a_click_without_a_token_or_a_page_only_opens(monkeypatch, caplog):
    window = Window()
    background_with(window, monkeypatch).open_notification("")
    assert window.calls == ["show"]
    broken = Window(fail=True)
    background_with(broken, monkeypatch).open_notification("n3-abc")
    assert broken.calls == ["show"]
    assert "could not be told" in caplog.text


def test_the_platform_click_never_waits_on_the_callback_thread():
    clicked, gate = [], threading.Event()

    def on_click(token):
        gate.wait(5)
        clicked.append((token, threading.current_thread().name))

    Recorder(on_click=on_click)._clicked("n9-z")  # returns although on_click blocks
    gate.set()
    wait_for(lambda: clicked)
    assert clicked == [("n9-z", "ouroboros-notification-click")]


def test_the_background_owns_the_platform_adapter(monkeypatch):
    seen = {}
    monkeypatch.setattr(lb, "indicator_class", lambda: None)
    monkeypatch.setattr(lb, "native_notifier",
                        lambda background, on_click, play_sound: seen.update(b=background, click=on_click) or "adapter")
    background = lb.Background(lambda: None, lambda: 8765, threading.Event())
    assert background.notifications == "adapter"
    assert seen["b"] is background and seen["click"] == background.open_notification


@pytest.mark.parametrize(("platform", "kind"), [("darwin", dn.MacNotifier), ("win32", dn.WindowsNotifier),
                                                ("linux", dn.FreedesktopNotifier), ("freebsd14", type(None))])
def test_one_adapter_per_platform(monkeypatch, platform, kind):
    monkeypatch.setattr(dn.sys, "platform", platform)
    assert isinstance(dn.native_notifier(object(), lambda token: None), kind)


# ------------------------------------------------------------------------------------- macOS ---

class Settings:
    def __init__(self, status):
        self.status = status

    def authorizationStatus(self):
        return self.status


class Center:
    def __init__(self, status=0, grant=True, error=None):
        self.status, self.grant, self.error, self.asked, self.added = status, grant, error, [], []

    def getNotificationSettingsWithCompletionHandler_(self, handler):
        handler(Settings(self.status))

    def requestAuthorizationWithOptions_completionHandler_(self, options, handler):
        self.asked.append(options)
        self.status = 2 if self.grant else 1
        handler(self.grant, None)

    def addNotificationRequest_withCompletionHandler_(self, request, handler):
        self.added.append(request)
        handler(self.error)


class Content:
    def __init__(self):
        self.fields = {}

    @classmethod
    def alloc(cls):
        return cls()

    def init(self):
        return self

    def __getattr__(self, name):
        if name.startswith("set") and name.endswith("_"):
            return lambda value: self.fields.__setitem__(name[3:-1].lower(), value)
        raise AttributeError(name)


MAC_CLASSES = {
    "UNMutableNotificationContent": Content,
    "UNNotificationSound": SimpleNamespace(defaultSound=lambda: "default-sound"),
    "UNNotificationRequest": SimpleNamespace(
        requestWithIdentifier_content_trigger_=lambda identifier, content, trigger: (identifier, content, trigger)),
}


def mac(monkeypatch, center):
    built = []
    monkeypatch.setattr(dn, "_mac_center", lambda clicked: built.append(clicked) or (center, MAC_CLASSES))
    return dn.MacNotifier(lambda token: None), built


def test_macos_reads_the_permission_without_asking(monkeypatch):
    for status, expected in ((0, "not_determined"), (1, "denied"), (2, "authorized"), (3, "authorized")):
        center = Center(status=status)
        notifier, _ = mac(monkeypatch, center)
        assert notifier.status()["status"] == expected
        assert center.asked == []


def test_macos_asks_once_for_alert_and_sound_never_a_badge(monkeypatch):
    center = Center(status=0)
    notifier, _ = mac(monkeypatch, center)
    assert notifier.ask()["status"] == "authorized"
    assert center.asked == [2 | 4]
    assert notifier.ask()["status"] == "authorized" and center.asked == [2 | 4], "decided: never asked again"


def test_macos_delivers_with_the_system_sound_and_the_pages_token(monkeypatch):
    center = Center(status=2)
    notifier, _ = mac(monkeypatch, center)
    answer = notifier.notify("Task finished", "Report ready", True, "n1-abc")
    assert answer == {"ok": True, "status": "delivered", "banner": True, "platform": "macos", "sound": "os"}
    identifier, content, trigger = center.added[0]
    assert (identifier, trigger) == ("n1-abc", None), "immediate, identified by the page's token"
    assert content.fields == {"title": "Task finished", "body": "Report ready", "sound": "default-sound"}

    silent = notifier.notify("Task finished", "", False, "n2-abc")
    assert silent["sound"] == "off"
    assert center.added[1][1].fields == {"title": "Task finished"}, "no body, no sound: private and silent"


def test_macos_denial_and_errors_are_typed_fallbacks(monkeypatch):
    denied = Center(status=1)
    notifier, _ = mac(monkeypatch, denied)
    assert notifier.notify("t", "", True, "n1-a") == {"ok": False, "status": "denied", "banner": False,
                                                       "platform": "macos", "reason": ""}
    assert denied.added == []

    refused_now = Center(status=0, grant=False)
    notifier, _ = mac(monkeypatch, refused_now)
    assert notifier.notify("t", "", True, "n1-a")["status"] == "denied"
    assert refused_now.asked == [6], "an owner who enabled notifications is asked at the first alert"

    error = SimpleNamespace(localizedDescription=lambda: "Notifications are not allowed")
    notifier, _ = mac(monkeypatch, Center(status=2, error=error))
    assert notifier.notify("t", "", True, "n1-a")["reason"] == "Notifications are not allowed"


def test_macos_outside_an_app_bundle_is_unavailable_once(monkeypatch):
    attempts = []

    def no_bundle(clicked):
        attempts.append(1)
        raise dn.Unavailable("not_an_app_bundle")

    monkeypatch.setattr(dn, "_mac_center", no_bundle)
    notifier = dn.MacNotifier(lambda token: None)
    assert notifier.status() == {"available": False, "status": "unavailable", "platform": "macos",
                                 "reason": "not_an_app_bundle"}
    assert notifier.notify("t", "", True, "n1-a")["status"] == "unavailable"
    assert attempts == [1], "no retry storm against a missing identity"


def test_macos_silent_service_is_a_failure_not_a_hang(monkeypatch):
    class Mute(Center):
        def addNotificationRequest_withCompletionHandler_(self, request, handler):
            pass

    monkeypatch.setattr(dn, "_REPLY_WAIT", 0.05)
    notifier, _ = mac(monkeypatch, Mute(status=2))
    assert notifier.notify("t", "", True, "n1-a")["status"] == "failed"


@pytest.mark.skipif(sys.platform != "darwin", reason="PyObjC and the UserNotifications framework are macOS-only")
def test_macos_block_metadata_through_real_pyobjc():
    """The registered metadata equals pyobjc-framework-UserNotifications 12.2.1's (compared when written):
    blocks cross the bridge with the right types, and the delegate shows a frontmost app's notification."""
    objc = pytest.importorskip("objc")
    objc.loadBundle("UserNotifications", {}, bundle_path=dn._MAC_FRAMEWORK)
    delegate_class = dn._mac_delegate_class()
    center = objc.lookUpClass("UNUserNotificationCenter")
    callable_of = lambda method: method.__metadata__()["arguments"][-1]["callable"]["arguments"]  # noqa: E731
    assert [arg["type"] for arg in callable_of(center.requestAuthorizationWithOptions_completionHandler_)] == \
        [b"^v", b"Z", b"@"]
    assert [arg["type"] for arg in callable_of(center.addNotificationRequest_withCompletionHandler_)] == [b"^v", b"@"]
    assert delegate_class.userNotificationCenter_willPresentNotification_withCompletionHandler_.signature == \
        b"v@:@@@?"

    content = objc.lookUpClass("UNMutableNotificationContent").alloc().init()
    content.setTitle_("Task finished")
    request = objc.lookUpClass("UNNotificationRequest").requestWithIdentifier_content_trigger_("n1-abc", content, None)
    assert (request.identifier(), request.content().title(), request.trigger()) == ("n1-abc", "Task finished", None)

    delegate, seen = delegate_class.alloc().init(), []
    delegate.userNotificationCenter_willPresentNotification_withCompletionHandler_(None, None, seen.append)
    assert seen == [2 | 4 | 8 | 16], "Sound | Alert | List | Banner: shown in front too (2A)"

    class Response:
        def __init__(self, action):
            self.action = action

        def actionIdentifier(self):
            return self.action

        def notification(self):
            return SimpleNamespace(request=lambda: SimpleNamespace(identifier=lambda: "n7-tok"))

    clicked, finished = [], []
    saved = dn._mac_shared.get("clicked")
    dn._mac_shared["clicked"] = clicked.append
    try:
        for action in (dn._MAC_DEFAULT_ACTION, "com.apple.UNNotificationDismissActionIdentifier"):
            delegate.userNotificationCenter_didReceiveNotificationResponse_withCompletionHandler_(
                None, Response(action), lambda: finished.append(action))
    finally:
        dn._mac_shared["clicked"] = saved
    assert clicked == ["n7-tok"], "only the notification's own click opens its source"
    assert len(finished) == 2, "the system's completion handler is always called"


# ----------------------------------------------------------------------------------- Windows ---

class TrayIcon:
    def __init__(self, ready=True, accepts=True):
        self.ready, self.accepts, self.balloons = threading.Event(), accepts, []
        if ready:
            self.ready.set()

    def notify(self, title, body, token=""):
        self.balloons.append((title, body, token))
        return self.accepts


class Balloon:
    shown: list = []
    works = True

    def __init__(self, on_click):
        self.on_click = on_click

    def show(self, title, body, token):
        Balloon.shown.append((title, body, token))
        return Balloon.works


@pytest.fixture
def windows(monkeypatch):
    monkeypatch.setattr(dn.sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "clr", types.ModuleType("clr"))
    tray = types.ModuleType("ouroboros.launcher_tray")
    tray.NotificationIcon = Balloon
    monkeypatch.setitem(sys.modules, "ouroboros.launcher_tray", tray)
    Balloon.shown, Balloon.works = [], True
    return lambda indicator=None: dn.WindowsNotifier(lambda token: None, SimpleNamespace(indicator=indicator))


def test_windows_uses_the_live_background_icon_with_the_token(windows):
    icon = TrayIcon()
    answer = windows(icon).notify("Task finished", "", True, "n1-a")
    assert answer == {"ok": True, "status": "delivered", "banner": True, "platform": "windows", "sound": "os"}
    assert icon.balloons == [("Task finished", "Open Ouroboros to see it.", "n1-a")], "a balloon needs text"
    assert Balloon.shown == []


def test_windows_without_a_live_icon_shows_a_balloon_icon_of_its_own(windows):
    notifier = windows(TrayIcon(ready=False))
    answer = notifier.notify("Task finished", "Report ready", False, "n2-a")
    assert answer["status"] == "delivered"
    assert answer["sound"] == "os_settings", "a balloon cannot be silenced: Windows' settings decide"
    assert Balloon.shown == [("Task finished", "Report ready", "n2-a")]
    Balloon.works = False
    assert notifier.notify("t", "", True, "n3-a") == {"ok": False, "status": "unavailable", "banner": False,
                                                      "platform": "windows",
                                                      "reason": "notification_area_unavailable"}


def test_windows_without_winforms_is_unavailable(monkeypatch):
    monkeypatch.setattr(dn.sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "clr", None)  # import clr raises ImportError
    notifier = dn.WindowsNotifier(lambda token: None, SimpleNamespace(indicator=None))
    assert notifier.status()["status"] == "unavailable"
    assert notifier.status()["reason"].startswith("no_winforms")


# ------------------------------------------------------------------------------------- Linux ---

class Variant:
    def __init__(self, kind, value):
        self.kind, self.value = kind, value

    def __eq__(self, other):
        return isinstance(other, Variant) and (self.kind, self.value) == (other.kind, other.value)

    def __repr__(self):
        return f"Variant({self.kind!r}, {self.value!r})"

    def unpack(self):
        return self.value


class Bus:
    def __init__(self, caps):
        self.caps, self.sent, self.subscribed = caps, [], {}

    def call_sync(self, name, path, interface, method, parameters, reply_type, flags, timeout, cancellable):
        assert (name, path, interface) == (dn._FD_NAME, dn._FD_PATH, dn._FD_NAME)
        if method == "GetCapabilities":
            return Variant("(as)", (list(self.caps),))
        self.sent.append(parameters.value)
        return Variant("(u)", (40 + len(self.sent),))

    def signal_subscribe(self, sender, interface, member, path, arg0, flags, callback):
        self.subscribed[member] = callback


@pytest.fixture
def freedesktop(monkeypatch):
    def install(caps, bus_error=None):
        bus = Bus(caps)
        gio = SimpleNamespace(BusType=SimpleNamespace(SESSION="session"), DBusCallFlags=SimpleNamespace(NONE=0),
                              DBusSignalFlags=SimpleNamespace(NONE=0))

        def bus_get_sync(kind, cancellable):
            if bus_error:
                raise bus_error
            return bus

        gio.bus_get_sync = bus_get_sync
        glib = SimpleNamespace(Variant=Variant, VariantType=SimpleNamespace(new=lambda signature: signature),
                               markup_escape_text=lambda value: value.replace("&", "&amp;").replace("<", "&lt;"))
        repository = types.ModuleType("gi.repository")
        repository.Gio, repository.GLib = gio, glib
        gi = types.ModuleType("gi")
        gi.repository = repository
        monkeypatch.setitem(sys.modules, "gi", gi)
        monkeypatch.setitem(sys.modules, "gi.repository", repository)
        return bus

    return install


def test_linux_uses_the_servers_sound_and_its_default_action(freedesktop):
    bus = freedesktop(["actions", "body", "body-markup", "sound"])
    clicked, sounds = [], []
    notifier = dn.FreedesktopNotifier(clicked.append, lambda: sounds.append(1) or {"sound_played": True})
    assert notifier.status()["status"] == "authorized"
    answer = notifier.notify("Task finished", "a < b & c", True, "n1-a")
    assert answer == {"ok": True, "status": "delivered", "banner": True, "platform": "linux", "sound": "os"}
    app, replaces, icon, title, body, actions, hints, expire = bus.sent[0]
    assert (app, replaces, title, body, actions, expire) == (
        "Ouroboros", 0, "Task finished", "a &lt; b &amp; c", ["default", "Open"], -1)
    assert hints["sound-name"] == Variant("s", "message-new-instant")
    assert sounds == [], "the server plays it: one sound"

    notifier._clicked = clicked.append  # synchronous here; the real one starts a thread
    bus.subscribed["ActionInvoked"](None, None, None, None, None, Variant("(us)", (41, "default")))
    bus.subscribed["ActionInvoked"](None, None, None, None, None, Variant("(us)", (41, "default")))
    assert clicked == ["n1-a"], "one click, one source"


def test_linux_without_server_sound_plays_the_desktop_sound_once(freedesktop):
    bus = freedesktop(["body"])
    sounds = []
    notifier = dn.FreedesktopNotifier(lambda token: None, lambda: sounds.append(1) or {"sound_played": True})
    answer = notifier.notify("Task finished", "x<y", True, "n2-a")
    assert answer["sound"] == "os" and sounds == [1]
    _app, _r, _i, _t, body, actions, hints, _e = bus.sent[0]
    assert (body, actions) == ("x<y", []), "no markup and no actions where the server offers none"
    assert "sound-name" not in hints

    silent = notifier.notify("Task finished", "", False, "n3-a")
    assert silent["sound"] == "off" and sounds == [1]
    assert bus.sent[1][6]["suppress-sound"] == Variant("b", True)

    mute = dn.FreedesktopNotifier(lambda token: None, lambda: {"sound_played": False})
    assert mute.notify("Task finished", "", True, "n4-a")["sound"] == "none"


def test_linux_closed_notifications_are_forgotten(freedesktop):
    bus = freedesktop(["actions"])
    clicked = []
    notifier = dn.FreedesktopNotifier(clicked.append)
    notifier._clicked = clicked.append
    notifier.notify("t", "", False, "n1-a")
    bus.subscribed["NotificationClosed"](None, None, None, None, None, Variant("(uu)", (41, 2)))
    bus.subscribed["ActionInvoked"](None, None, None, None, None, Variant("(us)", (41, "default")))
    assert clicked == []


def test_linux_without_a_notification_server_is_unavailable(freedesktop):
    freedesktop([], bus_error=RuntimeError("no session bus"))
    notifier = dn.FreedesktopNotifier(lambda token: None)
    assert notifier.status() == {"available": False, "status": "unavailable", "platform": "linux",
                                 "reason": "RuntimeError: no session bus"}
    assert notifier.notify("t", "", True, "n1-a")["status"] == "unavailable"
