# Copyright (c) 2026 chatplus-core contributors
"""Drive the running ChattingPlus window on macOS through Accessibility, as a person would.

The macOS client is the same Flutter app as on Windows, at the same 768x680
layout, and it exposes its semantics tree to Accessibility: buttons are pressed
and fields read without moving the pointer or activating the app. Flutter ignores
an `AXValue` written into its fields, so characters are posted to the client's
process as key events. The client exposes neither the room header nor the message
bubbles, so the room is proven before it opens: exactly one recipient search
result must read as the number digit for digit. The open-file sheet is the
system one, so the picture is staged under Downloads (which the sandboxed client
may read) and chosen there. Every refusal before the final press on send has sent
nothing.
"""

import ctypes
import subprocess
import time
from collections.abc import Callable, Generator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Final, TypeAlias, TypeVar, cast, final

import anyio

from chatplus_core.envelope import ErrorCode
from chatplus_core.login import LockPassword
from chatplus_core.sender import ClientUnavailableError, RoomUnverifiedError, SendError
from chatplus_core.winclient import (
    ClientAppLockedError,
    ClientBusyError,
    DraftPresentError,
    Step,
    digits,
)

FRAMEWORKS: Final = "/System/Library/Frameworks"
EXECUTABLE: Final = "/ChattingPlus.app/Contents/MacOS/ChattingPlus"
PGREP: Final = "/usr/bin/pgrep"
WINDOW_TITLE: Final = "채팅+"
CLIENT_SIZE: Final = (768, 680)
DOWNLOADS: Final = Path.home() / "Downloads"
STAGING: Final = DOWNLOADS / "chatplus-core-uploads"
DOWNLOADS_LABELS: Final = frozenset({"다운로드", "Downloads"})
OPEN_SHEET_TITLES: Final = frozenset({"열기", "Open"})
CANCEL_TITLES: Final = frozenset({"취소", "Cancel"})
LOCK_TITLE: Final = "잠금모드"

Box: TypeAlias = tuple[int, int, int, int]
NEW_CHAT: Final[Box] = (320, 604, 48, 48)
CLOSE_ROOM: Final[Box] = (364, 42, 22, 40)
DIALOG_CLOSE: Final[Box] = (496, 50, 36, 36)
SEARCH: Final[Box] = (279, 121, 230, 18)
RESULT_LEFT: Final = 225
RESULT_WIDTH: Final = 318
COMPOSER_LEFT: Final = 385
COMPOSER_WIDTH: Final = 380
ATTACH: Final[Box] = (394, 638, 36, 36)
SEND: Final[Box] = (683, 640, 38, 32)
STAGED_TOP: Final = 400
TOOLBAR_TOP: Final = 638
TOLERANCE: Final = 2

STEP_SECONDS: Final = 10.0
POLL_SECONDS: Final = 0.1
KEY_SECONDS: Final = 0.02
UTF8: Final = 0x08000100
AX_POINT: Final = 1
AX_SIZE: Final = 2
AX_SUCCESS: Final = 0
PATH_BYTES: Final = 4096
TEXT_BYTES: Final = 65536

Ref: TypeAlias = int
Call: TypeAlias = Callable[..., int]
Void: TypeAlias = Callable[..., None]
T = TypeVar("T")
Frame: TypeAlias = tuple[int, int, int, int]


class ScreenLockedError(SendError):
    """macOS is locked or this session is not on the console; nothing was sent."""

    def __init__(self) -> None:
        """Refuse with the reauth-required code: a person unlocks the Mac."""
        super().__init__(
            ErrorCode.REAUTH_REQUIRED,
            "macOS is locked or not on the console where the client runs; nothing was sent",
        )


class AccessibilityDeniedError(SendError):
    """The adapter may not drive other apps; nothing was sent."""

    def __init__(self) -> None:
        """Refuse with the reauth-required code: a person grants Accessibility."""
        super().__init__(
            ErrorCode.REAUTH_REQUIRED,
            "macOS has not granted this process Accessibility; nothing was sent",
        )


def near(frame: Frame | None, box: Box) -> bool:
    """Whether a window-relative frame is the measured box, give or take a point."""
    return frame is not None and all(
        abs(a - b) <= TOLERANCE for a, b in zip(frame, box, strict=True)
    )


def reads_as(texts: Sequence[str], number: str) -> bool:
    """Whether any line of an element's texts reads exactly as the number."""
    return any(digits(line) == number for text in texts for line in text.split("\n"))


def lock_screen(texts: Sequence[str]) -> bool:
    """Tell the client's lock-mode screen by its title, however it is spaced."""
    return any(LOCK_TITLE in text.replace(" ", "") for text in texts)


Pair = ctypes.c_double * 2


def doubles(pair: ctypes.Array[ctypes.c_double]) -> tuple[float, float]:
    """Read a CGPoint or CGSize filled in by Accessibility."""
    first, second = memoryview(pair).cast("B").cast("d").tolist()
    return (first, second)


def _function(
    library: ctypes.CDLL, name: str, argtypes: list[type], restype: type | None
) -> object:
    function = library.__getattr__(name)
    function.argtypes = argtypes
    function.restype = restype
    return function


def _bind(library: ctypes.CDLL, name: str, argtypes: list[type], restype: type) -> Call:
    return cast("Call", _function(library, name, argtypes, restype))


def _bind_void(library: ctypes.CDLL, name: str, argtypes: list[type]) -> Void:
    return cast("Void", _function(library, name, argtypes, None))


@final
class Quartz:
    """The CoreFoundation and ApplicationServices functions this driver calls."""

    def __init__(self) -> None:
        """Load the frameworks and declare every prototype before any call."""
        core = ctypes.CDLL(f"{FRAMEWORKS}/CoreFoundation.framework/CoreFoundation")
        services = ctypes.CDLL(f"{FRAMEWORKS}/ApplicationServices.framework/ApplicationServices")
        libproc = ctypes.CDLL("/usr/lib/libproc.dylib")
        ref, flag, out = ctypes.c_void_p, ctypes.c_bool, ctypes.POINTER(ctypes.c_void_p)
        self.true: Ref = cast("int", ctypes.c_void_p.in_dll(core, "kCFBooleanTrue").value)
        self.false: Ref = cast("int", ctypes.c_void_p.in_dll(core, "kCFBooleanFalse").value)
        self.release: Void = _bind_void(core, "CFRelease", [ref])
        self.type_of: Call = _bind(core, "CFGetTypeID", [ref], ctypes.c_ulong)
        self.string_type: Call = _bind(core, "CFStringGetTypeID", [], ctypes.c_ulong)
        self.boolean_type: Call = _bind(core, "CFBooleanGetTypeID", [], ctypes.c_ulong)
        self.array_type: Call = _bind(core, "CFArrayGetTypeID", [], ctypes.c_ulong)
        self.boolean: Call = _bind(core, "CFBooleanGetValue", [ref], flag)
        self.count: Call = _bind(core, "CFArrayGetCount", [ref], ctypes.c_long)
        self.item: Call = _bind(core, "CFArrayGetValueAtIndex", [ref, ctypes.c_long], ref)
        self.lookup: Call = _bind(core, "CFDictionaryGetValue", [ref, ref], ref)
        self.new_string: Call = _bind(
            core, "CFStringCreateWithCString", [ref, ctypes.c_char_p, ctypes.c_uint32], ref
        )
        self.read_string: Call = _bind(
            core,
            "CFStringGetCString",
            [ref, ctypes.c_char_p, ctypes.c_long, ctypes.c_uint32],
            flag,
        )
        self.trusted: Call = _bind(services, "AXIsProcessTrusted", [], flag)
        self.application: Call = _bind(
            services, "AXUIElementCreateApplication", [ctypes.c_int], ref
        )
        self.copy: Call = _bind(
            services, "AXUIElementCopyAttributeValue", [ref, ref, out], ctypes.c_int32
        )
        self.assign: Call = _bind(
            services, "AXUIElementSetAttributeValue", [ref, ref, ref], ctypes.c_int32
        )
        self.act: Call = _bind(services, "AXUIElementPerformAction", [ref, ref], ctypes.c_int32)
        self.unpack: Call = _bind(services, "AXValueGetValue", [ref, ctypes.c_int, ref], flag)
        self.pack: Call = _bind(services, "AXValueCreate", [ctypes.c_int, ref], ref)
        self.session: Call = _bind(services, "CGSessionCopyCurrentDictionary", [], ref)
        self.key_event: Call = _bind(
            services, "CGEventCreateKeyboardEvent", [ref, ctypes.c_uint16, flag], ref
        )
        self.key_text: Void = _bind_void(
            services,
            "CGEventKeyboardSetUnicodeString",
            [ref, ctypes.c_ulong, ctypes.POINTER(ctypes.c_uint16)],
        )
        self.post: Void = _bind_void(services, "CGEventPostToPid", [ctypes.c_int, ref])
        self.process_path: Call = _bind(
            libproc, "proc_pidpath", [ctypes.c_int, ctypes.c_char_p, ctypes.c_uint32], ctypes.c_int
        )


@final
class Session:
    """One drive of the client: every Core Foundation object it copies is released at the end."""

    def __init__(self, api: Quartz, pid: int) -> None:
        """Hold the client's application element for this drive."""
        self._api: Quartz = api
        self._held: list[Ref] = []
        self._names: dict[str, Ref] = {}
        self.pid: int = pid
        self.app: Ref = self._hold(api.application(pid))

    def close(self) -> None:
        """Release everything this drive copied, newest first."""
        for ref in reversed(self._held):
            self._api.release(ref)
        self._held.clear()
        self._names.clear()

    def _hold(self, ref: Ref | None) -> Ref:
        if not ref:
            raise ClientUnavailableError
        self._held.append(ref)
        return ref

    def _name(self, name: str) -> Ref:
        if name not in self._names:
            self._names[name] = self._hold(self._api.new_string(None, name.encode(), UTF8))
        return self._names[name]

    def attribute(self, element: Ref, name: str) -> Ref | None:
        """Copy one attribute value, or None when the element does not have it."""
        out = ctypes.c_void_p()
        if self._api.copy(element, self._name(name), ctypes.byref(out)) != AX_SUCCESS:
            return None
        return self._hold(out.value) if out.value else None

    def string(self, element: Ref, name: str) -> str | None:
        """Read a string attribute, or None when it is absent or not a string."""
        value = self.attribute(element, name)
        if value is None or self._api.type_of(value) != self._api.string_type():
            return None
        buffer = ctypes.create_string_buffer(TEXT_BYTES)
        if not self._api.read_string(value, buffer, TEXT_BYTES, UTF8):
            return None
        return bytes(buffer).split(b"\0", 1)[0].decode("utf-8")

    def flag(self, element: Ref, name: str) -> bool | None:
        """Read a boolean attribute, or None when it is absent or not a boolean."""
        value = self.attribute(element, name)
        if value is None or self._api.type_of(value) != self._api.boolean_type():
            return None
        return bool(self._api.boolean(value))

    def children(self, element: Ref) -> list[Ref]:
        """Return an element's children; they live as long as this drive."""
        value = self.attribute(element, "AXChildren")
        if value is None or self._api.type_of(value) != self._api.array_type():
            return []
        return [self._api.item(value, index) for index in range(self._api.count(value))]

    def texts(self, element: Ref) -> list[str]:
        """Every title, description and value under an element, itself included."""
        found: list[str] = []
        for _, node in self.walk(element):
            for name in ("AXTitle", "AXDescription", "AXValue"):
                text = self.string(node, name)
                if text:
                    found.append(text)
        return found

    def walk(self, root: Ref) -> list[tuple[str, Ref]]:
        """Return (role, element) for the root and every descendant, in tree order."""
        found: list[tuple[str, Ref]] = []
        pending = [root]
        while pending:
            element = pending.pop()
            role = self.string(element, "AXRole") or ""
            if role == "AXMenuBar":
                continue
            found.append((role, element))
            pending.extend(reversed(self.children(element)))
        return found

    def frame(self, element: Ref) -> Frame | None:
        """Return the element's screen frame in points."""
        position, size = self.attribute(element, "AXPosition"), self.attribute(element, "AXSize")
        origin, extent = Pair(), Pair()
        if position is None or size is None:
            return None
        if not self._api.unpack(position, AX_POINT, ctypes.byref(origin)):
            return None
        if not self._api.unpack(size, AX_SIZE, ctypes.byref(extent)):
            return None
        (x, y), (width, height) = doubles(origin), doubles(extent)
        return (round(x), round(y), round(width), round(height))

    def press(self, element: Ref) -> None:
        """Perform the element's press action, as a tap."""
        _ = self._api.act(element, self._name("AXPress"))

    def open(self, element: Ref) -> None:
        """Perform the element's open action, as a double click; the result is read after."""
        _ = self._api.act(element, self._name("AXOpen"))

    def assign(self, element: Ref, name: str, *, value: bool) -> bool:
        """Set a boolean attribute."""
        flag = self._api.true if value else self._api.false
        return self._api.assign(element, self._name(name), flag) == AX_SUCCESS

    def resize(self, window: Ref, width: int, height: int) -> bool:
        """Set the window's size in points."""
        extent = Pair(width, height)
        value = self._hold(self._api.pack(AX_SIZE, ctypes.byref(extent)))
        return self._api.assign(window, self._name("AXSize"), value) == AX_SUCCESS

    def type(self, text: str) -> None:
        """Post each character to the client as a key press, without activating it."""
        for character in text:
            raw = character.encode("utf-16-le")
            units = (ctypes.c_uint16 * (len(raw) // 2)).from_buffer_copy(raw)
            for down in (True, False):
                event = self._api.key_event(None, 0, down)
                if not event:
                    raise ClientBusyError(Step.TYPING)
                self._api.key_text(event, len(units), units)
                self._api.post(self.pid, event)
                self._api.release(event)
            time.sleep(KEY_SECONDS)


def until(probe: Callable[[], T | None], seconds: float = STEP_SECONDS) -> T | None:
    """Poll until the probe answers something, or None at the deadline."""
    deadline = time.monotonic() + seconds
    while True:
        found = probe()
        if found is not None:
            return found
        if time.monotonic() >= deadline:
            return None
        time.sleep(POLL_SECONDS)


@final
class MacClient:
    """The running client's main window, driven and read through Accessibility."""

    def __init__(self, lock_password: LockPassword | None = None) -> None:
        """Bind the macOS functions; nothing touches the client until a send."""
        self._api: Quartz = Quartz()
        self._lock_password: LockPassword | None = lock_password

    async def send_text(self, number: str, text: str) -> None:
        """Type the text into the number's conversation and send it, or refuse sending nothing."""
        await anyio.to_thread.run_sync(self._send_text, number, text)

    async def upload(self, number: str, picture: Path) -> None:
        """Send the picture to the number's conversation, or refuse having sent nothing."""
        await anyio.to_thread.run_sync(self._upload, number, picture)

    def _send_text(self, number: str, text: str) -> None:
        with self._drive() as (ui, window):
            composer = self._open_room(ui, window, number)
            ui.type(text)
            if until(lambda: True if ui.string(composer, "AXValue") == text else None) is None:
                raise ClientBusyError(Step.MESSAGE)
            self._press_send(ui, window)

    def _upload(self, number: str, picture: Path) -> None:
        if not picture.is_relative_to(DOWNLOADS):
            raise ClientBusyError(Step.FILE_NAME)
        with self._drive() as (ui, window):
            _ = self._open_room(ui, window, number)
            ui.press(self._one(ui, window, "AXButton", ATTACH))
            sheet = until(lambda: self._sheet(ui, window))
            if sheet is None:
                raise ClientBusyError(Step.DIALOG)
            try:
                self._choose(ui, sheet, picture.relative_to(DOWNLOADS).parts)
            except SendError:
                self._cancel(ui, window)
                raise
            if until(lambda: True if self._staged(ui, window) else None) is None:
                self._cancel(ui, window)
                raise ClientBusyError(Step.STAGED)
            self._press_send(ui, window)

    @contextmanager
    def _drive(self) -> Generator[tuple[Session, Ref]]:
        if self._screen_locked():
            raise ScreenLockedError
        if not self._api.trusted():
            raise AccessibilityDeniedError
        ui = Session(self._api, self._pid())
        try:
            window = self._window(ui)
            with self._placed(ui, window):
                self._unlock(ui, window)
                yield ui, window
        finally:
            ui.close()

    def _screen_locked(self) -> bool:
        session = self._api.session()
        if not session:
            return True
        try:
            names = {
                key: self._api.new_string(None, key.encode(), UTF8)
                for key in ("CGSSessionScreenIsLocked", "kCGSSessionOnConsoleKey")
            }
            try:
                locked = self._api.lookup(session, names["CGSSessionScreenIsLocked"])
                console = self._api.lookup(session, names["kCGSSessionOnConsoleKey"])
                return bool(locked and self._api.boolean(locked)) or not (
                    console and self._api.boolean(console)
                )
            finally:
                for name in names.values():
                    self._api.release(name)
        finally:
            self._api.release(session)

    def _pid(self) -> int:
        found = subprocess.run(  # noqa: S603 - fixed system pgrep, no shell
            [PGREP, "-x", "ChattingPlus"], capture_output=True, text=True, check=False
        )
        pids = [int(line) for line in found.stdout.split() if line.isdigit()]
        owned = [pid for pid in pids if self._executable(pid).endswith(EXECUTABLE)]
        if len(owned) != 1:
            raise ClientUnavailableError
        return owned[0]

    def _executable(self, pid: int) -> str:
        buffer = ctypes.create_string_buffer(PATH_BYTES)
        size = self._api.process_path(pid, buffer, PATH_BYTES)
        return buffer.raw[:size].decode("utf-8", "replace") if size > 0 else ""

    def _window(self, ui: Session) -> Ref:
        windows = [
            element
            for element in ui.children(ui.app)
            if ui.string(element, "AXRole") == "AXWindow"
            and ui.string(element, "AXTitle") == WINDOW_TITLE
        ]
        if len(windows) != 1:
            raise ClientUnavailableError
        return windows[0]

    @contextmanager
    def _placed(self, ui: Session, window: Ref) -> Generator[None]:
        if ui.flag(ui.app, "AXHidden"):
            _ = ui.assign(ui.app, "AXHidden", value=False)
        minimized = bool(ui.flag(window, "AXMinimized"))
        if minimized:
            _ = ui.assign(window, "AXMinimized", value=False)
            if until(lambda: True if ui.flag(window, "AXMinimized") is False else None) is None:
                raise ClientBusyError(Step.RESTORE)
        original = ui.frame(window)
        if original is None:
            raise ClientBusyError(Step.RESTORE)
        resized = original[2:] != CLIENT_SIZE
        if resized and not self._sized(ui, window):
            raise ClientBusyError(Step.SIZE)
        try:
            yield
        finally:
            if resized:
                _ = ui.resize(window, original[2], original[3])
            if minimized:
                _ = ui.assign(window, "AXMinimized", value=True)

    def _sized(self, ui: Session, window: Ref) -> bool:
        # The client animates its own size changes; a resize asked mid-animation is
        # refused or undone, so it is asked again until the measured size holds.
        frame = until(
            lambda: (
                frame
                if ui.resize(window, *CLIENT_SIZE)
                and (frame := ui.frame(window))
                and frame[2:] == CLIENT_SIZE
                else None
            )
        )
        return frame is not None

    def _folded(self, ui: Session, window: Ref) -> bool:
        frame = ui.frame(window)
        return frame is not None and frame[2] < CLIENT_SIZE[0]

    def _elements(self, ui: Session, window: Ref, role: str, box: Box) -> list[Ref]:
        origin = ui.frame(window)
        if origin is None:
            return []
        found: list[Ref] = []
        for kind, element in ui.walk(window):
            frame = ui.frame(element) if kind == role else None
            if frame is not None:
                relative = (frame[0] - origin[0], frame[1] - origin[1], frame[2], frame[3])
                if near(relative, box):
                    found.append(element)
        return found

    def _one(self, ui: Session, window: Ref, role: str, box: Box) -> Ref:
        found = until(
            lambda: found[0] if len(found := self._elements(ui, window, role, box)) == 1 else None
        )
        if found is None:
            raise ClientBusyError(Step.MESSAGES)
        return found

    def _relative(self, ui: Session, window: Ref) -> list[tuple[str, Ref, Frame]]:
        origin = ui.frame(window)
        if origin is None:
            return []
        placed: list[tuple[str, Ref, Frame]] = []
        for role, element in ui.walk(window):
            frame = ui.frame(element)
            if frame is not None:
                placed.append(
                    (
                        role,
                        element,
                        (frame[0] - origin[0], frame[1] - origin[1], frame[2], frame[3]),
                    )
                )
        return placed

    def _unlock(self, ui: Session, window: Ref) -> None:
        if not lock_screen(ui.texts(window)):
            return
        if self._lock_password is None:
            raise ClientAppLockedError
        fields = [element for role, element in ui.walk(window) if role == "AXTextField"]
        if len(fields) != 1:
            raise ClientAppLockedError
        _ = ui.assign(fields[0], "AXFocused", value=True)
        ui.type(self._lock_password.value)
        if until(lambda: None if lock_screen(ui.texts(window)) else True) is None:
            raise ClientAppLockedError

    def _open_room(self, ui: Session, window: Ref, number: str) -> Ref:
        self._close_dialog(ui, window)
        if not self._elements(ui, window, "AXButton", NEW_CHAT):
            # An open room hides the list; its header button folds the window to the
            # list alone, and the measured size brings the list back with new chat.
            closers = self._elements(ui, window, "AXButton", CLOSE_ROOM)
            if len(closers) == 1:
                ui.press(closers[0])
                if until(lambda: True if self._folded(ui, window) else None) is None:
                    raise ClientBusyError(Step.SIZE)
                if not self._sized(ui, window):
                    raise ClientBusyError(Step.SIZE)
        ui.press(self._one(ui, window, "AXButton", NEW_CHAT))
        search = until(
            lambda: (
                found[0]
                if len(found := self._elements(ui, window, "AXTextField", SEARCH)) == 1
                else None
            )
        )
        if search is None:
            self._close_dialog(ui, window)
            raise RoomUnverifiedError
        _ = ui.assign(search, "AXFocused", value=True)
        ui.type(number)
        row = until(lambda: self._result(ui, window, number))
        if row is None:
            self._close_dialog(ui, window)
            raise RoomUnverifiedError
        ui.press(row)
        composer = until(lambda: self._composer(ui, window))
        if composer is None:
            self._close_dialog(ui, window)
            raise RoomUnverifiedError
        if ui.string(composer, "AXValue") or self._staged(ui, window):
            raise DraftPresentError
        return composer

    def _result(self, ui: Session, window: Ref, number: str) -> Ref | None:
        rows = [
            element
            for role, element, frame in self._relative(ui, window)
            if role == "AXButton" and frame[0] == RESULT_LEFT and frame[2] == RESULT_WIDTH
        ]
        matching = [row for row in rows if reads_as(ui.texts(row), number)]
        return matching[0] if len(matching) == 1 else None

    def _composer(self, ui: Session, window: Ref) -> Ref | None:
        if self._elements(ui, window, "AXTextField", SEARCH):
            return None
        boxes = [
            element
            for role, element, frame in self._relative(ui, window)
            if role == "AXTextField" and frame[0] == COMPOSER_LEFT and frame[2] == COMPOSER_WIDTH
        ]
        return boxes[0] if len(boxes) == 1 else None

    def _staged(self, ui: Session, window: Ref) -> bool:
        return any(
            role == "AXImage" and frame[0] >= COMPOSER_LEFT and STAGED_TOP < frame[1] < TOOLBAR_TOP
            for role, _, frame in self._relative(ui, window)
        )

    def _close_dialog(self, ui: Session, window: Ref) -> None:
        closers = self._elements(ui, window, "AXButton", DIALOG_CLOSE)
        if len(closers) != 1:
            return
        ui.press(closers[0])
        if (
            until(lambda: None if self._elements(ui, window, "AXButton", DIALOG_CLOSE) else True)
            is None
        ):
            raise ClientBusyError(Step.DIALOG_CLOSE)

    def _sheet(self, ui: Session, window: Ref) -> Ref | None:
        sheets = [
            element
            for role, element in ui.walk(window)
            if role == "AXSheet"
            and OPEN_SHEET_TITLES.intersection(
                (ui.string(element, "AXTitle"), ui.string(element, "AXDescription"))
            )
        ]
        return sheets[0] if len(sheets) == 1 else None

    def _choose(self, ui: Session, sheet: Ref, parts: tuple[str, ...]) -> None:
        rows = [
            element
            for role, element in ui.walk(sheet)
            if role == "AXRow" and DOWNLOADS_LABELS.intersection(ui.texts(element))
        ]
        if len(rows) != 1 or not ui.assign(rows[0], "AXSelected", value=True):
            raise ClientBusyError(Step.FILE_NAME)
        for name in parts:
            item = until(lambda name=name: self._item(ui, sheet, name))
            if item is None:
                raise ClientBusyError(Step.FILE_NAME)
            ui.open(item)

    def _item(self, ui: Session, sheet: Ref, name: str) -> Ref | None:
        items = [
            element
            for role, element in ui.walk(sheet)
            if role == "AXImage"
            and name in (ui.string(element, "AXFilename"), ui.string(element, "AXTitle"))
        ]
        return items[0] if len(items) == 1 else None

    def _cancel(self, ui: Session, window: Ref) -> None:
        sheet = self._sheet(ui, window)
        if sheet is not None:
            buttons = [
                element
                for role, element in ui.walk(sheet)
                if role == "AXButton" and ui.string(element, "AXTitle") in CANCEL_TITLES
            ]
            if len(buttons) == 1:
                ui.press(buttons[0])
                return
        if until(lambda: None if self._sheet(ui, window) else True) is None:
            raise ClientBusyError(Step.DIALOG_CLOSE)

    def _press_send(self, ui: Session, window: Ref) -> None:
        ui.press(self._one(ui, window, "AXButton", SEND))
