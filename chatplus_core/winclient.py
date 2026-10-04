# Copyright (c) 2026 chatplus-core contributors
"""Drive the running ChattingPlus window to send one picture, as a person would.

The client is a Flutter app: one window, no native controls and no accessibility
tree. It does take clicks and typed characters sent as window messages, and its
open-file dialog is the standard Windows one. So the driver clicks at measured
places in a window of the measured size, reads the screen with the Windows OCR
engine to confirm each step, and fills the dialog's file-name box. Every refusal
before the final click on send has sent nothing.
"""

import ctypes
import os
import subprocess
import tempfile
import time
import zlib
from collections.abc import Callable, Generator
from contextlib import contextmanager
from ctypes import wintypes
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from struct import pack
from typing import TYPE_CHECKING, Final, Protocol, TypeAlias, cast, final

import anyio

from chatplus_core.envelope import ErrorCode
from chatplus_core.sender import ClientUnavailableError, RoomUnverifiedError, SendError

if TYPE_CHECKING:
    from chatplus_core.models import Room

WINDOW_CLASS: Final = "FLUTTER_RUNNER_WIN32_WINDOW"
WINDOW_TITLE: Final = "채팅+"
VIEW_CLASS: Final = "FLUTTERVIEW"
DIALOG_CLASS: Final = "#32770"
IMAGE_NAME: Final = "\\chattingplus.exe"
# Measured on client 1.5.4.6 at 96 DPI; every place below is in that window.
CLIENT_SIZE: Final = (768, 679)
NEW_CHAT: Final = (344, 627)
DIALOG_CLOSE: Final = (513, 68)
ATTACH: Final = (411, 655)
SEND: Final = (716, 656)
FILE_NAME_ID: Final = 1148
OPEN_ID: Final = 1
CANCEL_ID: Final = 2
# An empty composer measured 0 pixels darker than 110 and ~270 placeholder
# pixels between 176 and 240; one typed letter gave 18 dark pixels.
DRAFT_SHADE: Final = 100
DRAFT_PIXELS: Final = 3
PLACEHOLDER_SHADES: Final = (176, 240)
PLACEHOLDER_PIXELS: Final = 50
# The send button rested at 0 coloured pixels and filled at ~1648 of 1824.
SEND_SATURATION: Final = 40
SEND_PIXELS: Final = 400
STEP_SECONDS: Final = 10.0
POLL_SECONDS: Final = 0.2
OCR_SECONDS: Final = 20.0
MESSAGE_TIMEOUT_MS: Final = 2000

WM_NULL: Final = 0x0000
WM_SETTEXT: Final = 0x000C
WM_CHAR: Final = 0x0102
WM_MOUSEMOVE: Final = 0x0200
WM_LBUTTONDOWN: Final = 0x0201
WM_LBUTTONUP: Final = 0x0202
BM_CLICK: Final = 0x00F5
MK_LBUTTON: Final = 0x0001
SMTO_ABORTIFHUNG: Final = 0x0002
SW_SHOWNOACTIVATE: Final = 4
SWP_NOSIZE: Final = 0x0001
SWP_NOMOVE: Final = 0x0002
SWP_NOZORDER: Final = 0x0004
SWP_NOACTIVATE: Final = 0x0010
SWP_SHOWWINDOW: Final = 0x0040
HWND_TOPMOST: Final = -1
HWND_NOTOPMOST: Final = -2
SPI_GETWORKAREA: Final = 0x0030
SPI_GETSCREENSAVERRUNNING: Final = 0x0072
DESKTOP_SWITCHDESKTOP: Final = 0x0100
PROCESS_QUERY_LIMITED_INFORMATION: Final = 0x1000
SRCCOPY: Final = 0x00CC0020
PER_MONITOR_AWARE_V2: Final = -4
BASE_DPI: Final = 96
POWERSHELL: Final = str(
    Path(os.environ.get("SYSTEMROOT", "C:\\Windows"))
    / "System32/WindowsPowerShell/v1.0/powershell.exe"
)

OCR_SCRIPT: Final = r"""param([string]$Path, [string]$Out)
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Storage.StorageFile,Windows.Storage,ContentType=WindowsRuntime]
$null = [Windows.Media.Ocr.OcrEngine,Windows.Foundation,ContentType=WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder,Windows.Graphics,ContentType=WindowsRuntime]
$null = [Windows.Globalization.Language,Windows.Globalization,ContentType=WindowsRuntime]
$asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
  $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
  $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
function Await($op, [Type]$t) {
  $task = $asTask.MakeGenericMethod($t).Invoke($null, @($op))
  $null = $task.Wait()
  $task.Result
}
$storage = [Windows.Storage.StorageFile]
$file = Await ($storage::GetFileFromPathAsync($Path)) ($storage)
$read = [Windows.Storage.FileAccessMode]::Read
$stream = Await ($file.OpenAsync($read)) ([Windows.Storage.Streams.IRandomAccessStream])
$imaging = [Windows.Graphics.Imaging.BitmapDecoder]
$decoder = Await ($imaging::CreateAsync($stream)) ($imaging)
$bitmap = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
$korean = [Windows.Globalization.Language]::new('ko')
$engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage($korean)
$result = Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
$stream.Dispose()
$lines = foreach ($line in $result.Lines) {
  $box = ($line.Words | Select-Object -First 1).BoundingRect
  '{0}|{1}' -f [int]$box.Y, $line.Text
}
[IO.File]::WriteAllLines($Out, [string[]]@($lines), [Text.UTF8Encoding]::new($false))
"""


@dataclass(frozen=True, slots=True)
class Box:
    """A region of the measured window, in its 96-DPI pixels."""

    x: int
    y: int
    width: int
    height: int


@dataclass(frozen=True, slots=True)
class Line:
    """One line the OCR engine read, with its top in the measured window's pixels."""

    top: int
    text: str


DIALOG_TITLE: Final = Box(230, 50, 220, 40)
RESULTS: Final = Box(240, 280, 300, 380)
HEADER: Final = Box(440, 40, 300, 55)
COMPOSER: Final = Box(392, 560, 368, 40)
SEND_BUTTON: Final = Box(688, 640, 57, 32)


class ClientLockedError(SendError):
    """Windows is locked or its session has no screen; nothing was sent."""

    def __init__(self) -> None:
        """Refuse with the reauth-required code: a person unlocks the PC."""
        super().__init__(
            ErrorCode.REAUTH_REQUIRED,
            "Windows is locked or disconnected where the client runs; nothing was sent",
        )


class DraftPresentError(SendError):
    """The conversation already holds unsent text or a file; nothing was sent."""

    def __init__(self) -> None:
        """Refuse rather than send someone's draft along with the picture."""
        super().__init__(
            ErrorCode.FORBIDDEN,
            "The conversation has something unsent in the client; nothing was sent",
        )


class Step(StrEnum):
    """The steps the client normally takes, named in a refusal when one does not happen."""

    RESTORE = "restore its window"
    SIZE = "take the size it is driven at"
    MESSAGES = "answer window messages"
    TYPING = "take typed characters"
    DIALOG = "open its file dialog"
    FILE_NAME = "take the picture's name"
    DIALOG_CLOSE = "close its file dialog"
    STAGED = "show the picture in the conversation"
    MESSAGE = "take the message into the box"


class ClientBusyError(SendError):
    """The client did not take a step it normally takes; nothing was sent."""

    def __init__(self, step: Step) -> None:
        """Name the step so a person can look at the client."""
        super().__init__(ErrorCode.BUSY, f"The client did not {step}; nothing was sent")


def digits(text: str) -> str:
    """Only the digits of a line, so a number matches however the client spaces it."""
    return "".join(ch for ch in text if ch.isdigit())


def has_number(lines: list[Line], number: str) -> Line | None:
    """Return the first line that reads exactly as the number, ignoring separators."""
    return next((line for line in lines if digits(line.text) == number), None)


def luminance(bgra: bytes, index: int) -> int:
    """Return the perceived brightness, 0 to 255, of one BGRA pixel."""
    blue, green, red = bgra[index * 4], bgra[index * 4 + 1], bgra[index * 4 + 2]
    return (299 * red + 587 * green + 114 * blue) // 1000


def saturated(bgra: bytes, index: int) -> int:
    """Return how colourful one BGRA pixel is: the spread of its channels."""
    blue, green, red = bgra[index * 4], bgra[index * 4 + 1], bgra[index * 4 + 2]
    return max(red, green, blue) - min(red, green, blue)


def ready_to_send(bgra: bytes) -> bool:
    """Tell the client's filled send button, coloured when it will send, from its grey rest."""
    coloured = sum(1 for index in range(len(bgra) // 4) if saturated(bgra, index) > SEND_SATURATION)
    return coloured >= SEND_PIXELS


def composer_empty(bgra: bytes) -> bool:
    """Tell an empty composer, showing only its grey placeholder, from one holding a draft.

    Typed text is near black and the placeholder light grey; the caret sits
    between them. A draft, or a picture waiting above a moved placeholder,
    leaves dark pixels or no placeholder in the box.
    """
    shades = [luminance(bgra, index) for index in range(len(bgra) // 4)]
    dark = sum(1 for shade in shades if shade < DRAFT_SHADE)
    placeholder = sum(
        1 for shade in shades if PLACEHOLDER_SHADES[0] <= shade < PLACEHOLDER_SHADES[1]
    )
    return dark < DRAFT_PIXELS and placeholder >= PLACEHOLDER_PIXELS


def png(width: int, height: int, bgra: bytes) -> bytes:
    """Encode top-down 32-bit BGRA pixels as an RGB PNG."""
    rows = bytearray()
    for y in range(height):
        rows.append(0)
        row = bgra[y * width * 4 : (y + 1) * width * 4]
        for x in range(width):
            rows += bytes((row[x * 4 + 2], row[x * 4 + 1], row[x * 4]))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return pack(">I", len(data)) + kind + data + pack(">I", zlib.crc32(kind + data))

    header = pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(bytes(rows), 1))
        + chunk(b"IEND", b"")
    )


@final
class BitmapInfo(ctypes.Structure):
    """BITMAPINFOHEADER for a top-down 32-bit read."""

    _fields_ = [
        ("size", wintypes.DWORD),
        ("width", wintypes.LONG),
        ("height", wintypes.LONG),
        ("planes", wintypes.WORD),
        ("bits", wintypes.WORD),
        ("compression", wintypes.DWORD),
        ("image_size", wintypes.DWORD),
        ("x_ppm", wintypes.LONG),
        ("y_ppm", wintypes.LONG),
        ("used", wintypes.DWORD),
        ("important", wintypes.DWORD),
    ]


Call: TypeAlias = Callable[..., int]
HandleCall: TypeAlias = Callable[..., int | None]


def _bind(
    library: ctypes.WinDLL, name: str, argtypes: list[type], restype: type = ctypes.c_int
) -> Call:
    function = library.__getattr__(name)
    function.argtypes = argtypes
    function.restype = restype
    return cast("Call", function)


def _handle(library: ctypes.WinDLL, name: str, argtypes: list[type]) -> HandleCall:
    return cast("HandleCall", _bind(library, name, argtypes, ctypes.c_void_p))


@final
class Win32:
    """The user32, gdi32 and kernel32 functions this driver calls, with their prototypes."""

    def __init__(self) -> None:
        """Load the libraries and declare every prototype before any call."""
        user = ctypes.WinDLL("user32", use_last_error=True)
        gdi = ctypes.WinDLL("gdi32", use_last_error=True)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        hwnd, uint, pointer = wintypes.HWND, wintypes.UINT, ctypes.c_void_p
        number = ctypes.c_int
        self.find_child: HandleCall = _handle(
            user, "FindWindowExW", [hwnd, hwnd, wintypes.LPCWSTR, wintypes.LPCWSTR]
        )
        self.send_timeout: Call = _bind(
            user,
            "SendMessageTimeoutW",
            [hwnd, uint, wintypes.WPARAM, wintypes.LPARAM, uint, uint, pointer],
            ctypes.c_size_t,
        )
        self.send_text: Call = _bind(
            user, "SendMessageW", [hwnd, uint, wintypes.WPARAM, wintypes.LPCWSTR], ctypes.c_ssize_t
        )
        self.post: Call = _bind(
            user, "PostMessageW", [hwnd, uint, wintypes.WPARAM, wintypes.LPARAM]
        )
        self.dialog_item: HandleCall = _handle(user, "GetDlgItem", [hwnd, number])
        self.set_pos: Call = _bind(
            user, "SetWindowPos", [hwnd, hwnd, number, number, number, number, uint]
        )
        self.get_dc: HandleCall = _handle(user, "GetDC", [hwnd])
        self.release_dc: Call = _bind(user, "ReleaseDC", [hwnd, pointer])
        self.dpi: Call = _bind(user, "GetDpiForWindow", [hwnd], uint)
        self.thread_dpi: HandleCall = _handle(user, "SetThreadDpiAwarenessContext", [pointer])
        self.input_desktop: HandleCall = _handle(
            user, "OpenInputDesktop", [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        )
        self.switch_desktop: Call = _bind(user, "SwitchDesktop", [pointer])
        self.close_desktop: Call = _bind(user, "CloseDesktop", [pointer])
        self.thread_process: Call = _bind(user, "GetWindowThreadProcessId", [hwnd, pointer], uint)
        self.class_name: Call = _bind(user, "GetClassNameW", [hwnd, wintypes.LPWSTR, number])
        self.window_text: Call = _bind(user, "GetWindowTextW", [hwnd, wintypes.LPWSTR, number])
        self.visible: Call = _bind(user, "IsWindowVisible", [hwnd])
        self.is_window: Call = _bind(user, "IsWindow", [hwnd])
        self.iconic: Call = _bind(user, "IsIconic", [hwnd])
        self.show: Call = _bind(user, "ShowWindow", [hwnd, number])
        self.enum_windows: Call = _bind(user, "EnumWindows", [pointer, wintypes.LPARAM])
        self.window_rect: Call = _bind(user, "GetWindowRect", [hwnd, pointer])
        self.client_rect: Call = _bind(user, "GetClientRect", [hwnd, pointer])
        self.client_to_screen: Call = _bind(user, "ClientToScreen", [hwnd, pointer])
        self.parameters: Call = _bind(user, "SystemParametersInfoW", [uint, uint, pointer, uint])
        self.memory_dc: HandleCall = _handle(gdi, "CreateCompatibleDC", [pointer])
        self.bitmap: HandleCall = _handle(gdi, "CreateCompatibleBitmap", [pointer, number, number])
        self.select: HandleCall = _handle(gdi, "SelectObject", [pointer, pointer])
        self.blit: Call = _bind(
            gdi,
            "BitBlt",
            [pointer, number, number, number, number, pointer, number, number, wintypes.DWORD],
        )
        self.bits: Call = _bind(
            gdi, "GetDIBits", [pointer, pointer, uint, uint, pointer, pointer, uint]
        )
        self.delete_object: Call = _bind(gdi, "DeleteObject", [pointer])
        self.delete_dc: Call = _bind(gdi, "DeleteDC", [pointer])
        self.open_process: HandleCall = _handle(
            kernel, "OpenProcess", [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        )
        self.image_name: Call = _bind(
            kernel,
            "QueryFullProcessImageNameW",
            [pointer, wintypes.DWORD, wintypes.LPWSTR, pointer],
        )
        self.close_handle: Call = _bind(kernel, "CloseHandle", [pointer])


@final
class ClientWindow:
    """The running client's main window, driven by window messages and read by OCR."""

    def __init__(self) -> None:
        """Bind the Windows functions; nothing touches the client until an upload."""
        self._api: Win32 = Win32()
        self._script: Path = Path(tempfile.gettempdir()) / "chatplus-core-ocr.ps1"

    async def upload(self, number: str, picture: Path) -> None:
        """Send the picture to the number's conversation, or refuse having sent nothing."""
        await anyio.to_thread.run_sync(self._upload, number, picture)

    async def send_text(self, number: str, text: str) -> None:
        """Type the text into the number's conversation and send it, or refuse sending nothing."""
        await anyio.to_thread.run_sync(self._send_text, number, text)

    def _send_text(self, number: str, text: str) -> None:
        _ = self._api.thread_dpi(PER_MONITOR_AWARE_V2)
        if self._locked():
            raise ClientLockedError
        top, _pid = self._window()
        view = self._api.find_child(top, None, VIEW_CLASS, None)
        if not view:
            raise ClientUnavailableError
        with self._placed(top, view) as scale:
            self._open_room(view, scale, number)
            if not self._until(
                lambda: composer_empty(self._pixels(view, scale, COMPOSER)[2]), STEP_SECONDS
            ):
                raise DraftPresentError
            self._type(view, text)
            if not self._until(
                lambda: ready_to_send(self._pixels(view, scale, SEND_BUTTON)[2]), STEP_SECONDS
            ):
                raise ClientBusyError(Step.MESSAGE)
            self._click(view, scale, SEND)

    def _upload(self, number: str, picture: Path) -> None:
        _ = self._api.thread_dpi(PER_MONITOR_AWARE_V2)
        if self._locked():
            raise ClientLockedError
        top, pid = self._window()
        view = self._api.find_child(top, None, VIEW_CLASS, None)
        if not view:
            raise ClientUnavailableError
        with self._placed(top, view) as scale:
            self._open_room(view, scale, number)
            if not self._until(
                lambda: composer_empty(self._pixels(view, scale, COMPOSER)[2]), STEP_SECONDS
            ):
                raise DraftPresentError
            self._attach(view, scale, pid, picture)
            self._click(view, scale, SEND)

    def _locked(self) -> bool:
        saver = wintypes.BOOL()
        _ = self._api.parameters(SPI_GETSCREENSAVERRUNNING, 0, ctypes.byref(saver), 0)
        if saver.value:
            return True
        desktop = self._api.input_desktop(0, 0, DESKTOP_SWITCHDESKTOP)
        if not desktop:
            return True
        try:
            return not self._api.switch_desktop(desktop)
        finally:
            _ = self._api.close_desktop(desktop)

    def _image(self, window: int) -> tuple[str, int]:
        pid = wintypes.DWORD()
        _ = self._api.thread_process(window, ctypes.byref(pid))
        process = self._api.open_process(PROCESS_QUERY_LIMITED_INFORMATION, 0, pid.value)
        if not process:
            return "", pid.value
        try:
            size = wintypes.DWORD(1024)
            name = ctypes.create_unicode_buffer(size.value)
            if not self._api.image_name(process, 0, name, ctypes.byref(size)):
                return "", pid.value
            return ctypes.wstring_at(name).lower(), pid.value
        finally:
            _ = self._api.close_handle(process)

    def _windows(self, pid: int | None, class_name: str) -> list[int]:
        found: list[int] = []
        callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        def visit(window: int, _param: int) -> bool:
            name = ctypes.create_unicode_buffer(64)
            _ = self._api.class_name(window, name, 64)
            if ctypes.wstring_at(name) != class_name or not self._api.visible(window):
                return True
            owner = wintypes.DWORD()
            _ = self._api.thread_process(window, ctypes.byref(owner))
            if pid is None or owner.value == pid:
                found.append(window)
            return True

        _ = self._api.enum_windows(callback_type(visit), 0)
        return found

    def _window(self) -> tuple[int, int]:
        candidates: list[tuple[int, int]] = []
        for window in self._windows(None, WINDOW_CLASS):
            title = ctypes.create_unicode_buffer(64)
            _ = self._api.window_text(window, title, 64)
            image, pid = self._image(window)
            if ctypes.wstring_at(title) == WINDOW_TITLE and image.endswith(IMAGE_NAME):
                candidates.append((window, pid))
        if len(candidates) != 1:
            raise ClientUnavailableError
        return candidates[0]

    @contextmanager
    def _placed(self, top: int, view: int) -> Generator[float]:
        if self._api.iconic(top):
            _ = self._api.show(top, SW_SHOWNOACTIVATE)
            if not self._until(lambda: not self._api.iconic(top), STEP_SECONDS):
                raise ClientBusyError(Step.RESTORE)
        scale = self._api.dpi(view) / BASE_DPI
        original = wintypes.RECT()
        _ = self._api.window_rect(top, ctypes.byref(original))
        width, height = (round(side * scale) for side in CLIENT_SIZE)
        client = wintypes.RECT()
        _ = self._api.client_rect(view, ctypes.byref(client))
        outer_width = original.right - original.left + width - client.right
        outer_height = original.bottom - original.top + height - client.bottom
        area = wintypes.RECT()
        _ = self._api.parameters(SPI_GETWORKAREA, 0, ctypes.byref(area), 0)
        flags = SWP_NOACTIVATE | SWP_SHOWWINDOW
        _ = self._api.set_pos(
            top, HWND_TOPMOST, area.left, area.top, outer_width, outer_height, flags
        )
        try:
            _ = self._api.client_rect(view, ctypes.byref(client))
            if (client.right, client.bottom) != (width, height):
                raise ClientBusyError(Step.SIZE)
            yield scale
        finally:
            _ = self._api.set_pos(
                top,
                HWND_NOTOPMOST,
                original.left,
                original.top,
                original.right - original.left,
                original.bottom - original.top,
                SWP_NOACTIVATE,
            )

    def _until(self, done: Callable[[], bool], seconds: float) -> bool:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if done():
                return True
            time.sleep(POLL_SECONDS)
        return done()

    def _send(self, window: int, message: int, wparam: int, lparam: int) -> None:
        result = ctypes.c_size_t()
        if not self._api.send_timeout(
            window,
            message,
            wparam,
            lparam,
            SMTO_ABORTIFHUNG,
            MESSAGE_TIMEOUT_MS,
            ctypes.byref(result),
        ):
            raise ClientBusyError(Step.MESSAGES)

    def _click(self, view: int, scale: float, at: tuple[int, int]) -> None:
        x, y = round(at[0] * scale), round(at[1] * scale)
        where = (y << 16) | (x & 0xFFFF)
        self._send(view, WM_NULL, 0, 0)
        self._send(view, WM_MOUSEMOVE, 0, where)
        self._send(view, WM_LBUTTONDOWN, MK_LBUTTON, where)
        self._send(view, WM_LBUTTONUP, 0, where)

    def _press(self, dialog: int, control: int) -> None:
        button = self._api.dialog_item(dialog, control)
        if not button:
            raise ClientBusyError(Step.MESSAGES)
        self._send(button, BM_CLICK, 0, 0)

    def _type(self, view: int, text: str) -> None:
        for ch in text:
            if not self._api.post(view, WM_CHAR, ord(ch), 1):
                raise ClientBusyError(Step.TYPING)

    def _capture(self, view: int, scale: float, box: Box) -> bytes:
        return png(*self._pixels(view, scale, box))

    def _pixels(self, view: int, scale: float, box: Box) -> tuple[int, int, bytes]:
        origin = wintypes.POINT(0, 0)
        _ = self._api.client_to_screen(view, ctypes.byref(origin))
        x, y = origin.x + round(box.x * scale), origin.y + round(box.y * scale)
        width, height = round(box.width * scale), round(box.height * scale)
        screen = self._api.get_dc(None)
        memory = self._api.memory_dc(screen)
        bitmap = self._api.bitmap(screen, width, height)
        try:
            _ = self._api.select(memory, bitmap)
            _ = self._api.blit(memory, 0, 0, width, height, screen, x, y, SRCCOPY)
            info = BitmapInfo(ctypes.sizeof(BitmapInfo), width, -height, 1, 32, 0, 0, 0, 0, 0, 0)
            pixels = ctypes.create_string_buffer(width * height * 4)
            _ = self._api.bits(memory, bitmap, 0, height, pixels, ctypes.byref(info), 0)
            return width, height, pixels.raw
        finally:
            _ = self._api.delete_object(bitmap)
            _ = self._api.delete_dc(memory)
            _ = self._api.release_dc(None, screen)

    def _read(self, view: int, scale: float, box: Box) -> list[Line]:
        if not self._script.exists():
            _ = self._script.write_text(OCR_SCRIPT, encoding="utf-8-sig")
        with tempfile.TemporaryDirectory(prefix="chatplus-core-") as folder:
            image = Path(folder) / "screen.png"
            text = Path(folder) / "lines.txt"
            _ = image.write_bytes(self._capture(view, scale, box))
            _ = subprocess.run(  # noqa: S603 - fixed system PowerShell, no shell
                [
                    POWERSHELL,
                    "-NoProfile",
                    "-NonInteractive",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(self._script),
                    "-Path",
                    str(image),
                    "-Out",
                    str(text),
                ],
                capture_output=True,
                timeout=OCR_SECONDS,
                check=False,
            )
            read = text.read_text(encoding="utf-8") if text.exists() else ""
        lines: list[Line] = []
        for raw in read.splitlines():
            top, _, content = raw.partition("|")
            if top.strip().lstrip("-").isdigit():
                lines.append(Line(box.y + round(int(top) / scale), content.strip()))
        return lines

    def _wait_lines(
        self, view: int, scale: float, box: Box, done: Callable[[list[Line]], bool]
    ) -> list[Line] | None:
        deadline = time.monotonic() + STEP_SECONDS
        while True:
            lines = self._read(view, scale, box)
            if done(lines):
                return lines
            if time.monotonic() >= deadline:
                return None
            time.sleep(POLL_SECONDS)

    def _open_room(self, view: int, scale: float, number: str) -> None:
        self._click(view, scale, NEW_CHAT)
        if not self._wait_lines(
            view, scale, DIALOG_TITLE, lambda ls: any("받는" in line.text for line in ls)
        ):
            raise RoomUnverifiedError
        self._type(view, number)
        found = self._wait_lines(
            view, scale, RESULTS, lambda ls: has_number(ls, number) is not None
        )
        row = has_number(found, number) if found else None
        if row is None:
            self._click(view, scale, DIALOG_CLOSE)
            raise RoomUnverifiedError
        self._click(view, scale, (RESULTS.x + 60, row.top + 8))
        if not self._wait_lines(view, scale, HEADER, lambda ls: has_number(ls, number) is not None):
            raise RoomUnverifiedError

    def _attach(self, view: int, scale: float, pid: int, picture: Path) -> None:
        self._click(view, scale, ATTACH)
        dialogs: list[int] = []

        def opened() -> bool:
            dialogs[:] = self._windows(pid, DIALOG_CLASS)
            return len(dialogs) == 1

        if not self._until(opened, STEP_SECONDS):
            raise ClientBusyError(Step.DIALOG)
        dialog = dialogs[0]
        combo = self._api.dialog_item(dialog, FILE_NAME_ID)
        edit = self._api.find_child(
            self._api.find_child(combo, None, "ComboBox", None), None, "Edit", None
        )
        if not edit or not self._api.send_text(edit, WM_SETTEXT, 0, str(picture)):
            self._press(dialog, CANCEL_ID)
            raise ClientBusyError(Step.FILE_NAME)
        self._press(dialog, OPEN_ID)
        if not self._until(lambda: not self._api.is_window(dialog), STEP_SECONDS):
            self._press(dialog, CANCEL_ID)
            raise ClientBusyError(Step.DIALOG_CLOSE)
        if not self._until(
            lambda: ready_to_send(self._pixels(view, scale, SEND_BUTTON)[2]), STEP_SECONDS
        ):
            raise ClientBusyError(Step.STAGED)


@final
class NumberSource(Protocol):
    """Something that maps each room id to its lone mobile number, or None."""

    def recipient_numbers(self) -> dict[str, str | None]:
        """Return every room's lone mobile recipient in national form, None where not derivable."""
        ...


class TextWindow(Protocol):
    """Something that types one message into a mobile number's conversation."""

    async def send_text(self, number: str, text: str) -> None:
        """Type the text and send it, or raise a SendError having sent nothing."""
        ...


@final
class WindowSender:
    """Send text by driving the window, resolving a room's lone mobile number from the cache."""

    def __init__(self, reader: NumberSource, window: TextWindow) -> None:
        """Hold the cache to resolve the number and the window that does the typing."""
        self._reader: NumberSource = reader
        self._window: TextWindow = window

    async def submit(self, room: "Room", text: str) -> None:
        """Type the text into the room's mobile conversation, or refuse having sent nothing."""
        numbers = await anyio.to_thread.run_sync(self._reader.recipient_numbers)
        number = numbers.get(room.id)
        if number is None:
            raise RoomUnverifiedError
        await self._window.send_text(number, text)
