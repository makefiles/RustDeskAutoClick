#!/usr/bin/env python3
"""
RustDesk Auto-Accept
Automatically accepts incoming RustDesk connection requests based on a whitelist.

Supports Linux (X11 via python-xlib + xdotool) and Windows (ctypes + user32.dll).
Requires config.json in the same directory as this script.
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import os
import platform
import re
import subprocess
import sys
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).resolve().parent
CONFIG_PATH = SCRIPT_DIR / "config.json"

# Used only to extract peer ID from title for whitelist filtering (best effort)
RUSTDESK_PEER_RE = re.compile(r"^(\d+)@")


class Config:
    """Loads and validates config.json."""

    VALID_MODES = {"whitelist", "allow_all"}

    def __init__(self, path: Path) -> None:
        if not path.exists():
            print(
                f"ERROR: config.json not found at {path}\n"
                "Create config.json before starting. Example:\n"
                '{\n  "mode": "whitelist",\n  "allowed_ids": ["123456789"],\n'
                '  "button_position": {"x_ratio": 0.25, "y_ratio": 0.95},\n'
                '  "click_delay": 0.5,\n  "log_file": "~/.rustdesk-autoclick/autoclick.log"\n}',
                file=sys.stderr,
            )
            sys.exit(1)

        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        self.mode: str = data.get("mode", "whitelist")
        if self.mode not in self.VALID_MODES:
            print(f"ERROR: mode must be one of {self.VALID_MODES}", file=sys.stderr)
            sys.exit(1)

        self.allowed_ids: set[str] = set(str(i) for i in data.get("allowed_ids", []))

        bp = data.get("button_position", {})
        self.x_ratio: float = float(bp.get("x_ratio", 0.25))
        self.y_ratio: float = float(bp.get("y_ratio", 0.95))

        self.click_delay: float = float(data.get("click_delay", 0.5))
        self.idle_threshold: float = float(data.get("idle_threshold", 1.0))
        self.idle_timeout: float = float(data.get("idle_timeout", 30.0))

        ds = data.get("dialog_size", {})
        self.dialog_width: int = int(ds.get("width", 300))
        self.dialog_height: int = int(ds.get("height", 490))
        self.dialog_tolerance: int = int(ds.get("tolerance", 50))

        log_file_raw: str = data.get("log_file", "./autoclick.log")
        log_path = Path(log_file_raw).expanduser()
        # Resolve relative paths against the script directory, not cwd
        if not log_path.is_absolute():
            log_path = SCRIPT_DIR / log_path
        self.log_file: Path = log_path


# ---------------------------------------------------------------------------
# Logger
# ---------------------------------------------------------------------------

def setup_logger(log_file: Path) -> logging.Logger:
    log_file.parent.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("rustdesk_autoclick")
    logger.setLevel(logging.DEBUG)

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

    # Rotate at 5MB, keep 3 backups
    fh = logging.handlers.RotatingFileHandler(
        log_file, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    logger.addHandler(sh)

    return logger


# ---------------------------------------------------------------------------
# Base detector
# ---------------------------------------------------------------------------

class BaseDetector(ABC):
    """Platform-agnostic interface for window detection and click."""

    def __init__(self, config: Config, logger: logging.Logger) -> None:
        self.config = config
        self.logger = logger
        # Cache of window IDs already processed to prevent duplicate clicks
        self._processed: set = set()

    @abstractmethod
    def run(self) -> None:
        """Start the event loop. Blocks until interrupted."""

    @abstractmethod
    def _get_mouse_position(self) -> tuple:
        """Return current mouse (x, y) position."""

    def _wait_for_idle(self) -> bool:
        """
        Wait until the mouse is idle (not moving) before clicking.
        Returns True if idle state reached, False if timed out.
        """
        threshold = self.config.idle_threshold
        timeout = self.config.idle_timeout
        start = time.monotonic()

        prev_x, prev_y = self._get_mouse_position()
        idle_since = time.monotonic()

        while True:
            elapsed = time.monotonic() - start
            if elapsed > timeout:
                self.logger.warning("Idle timeout (%.0fs) reached, clicking anyway", timeout)
                return True

            time.sleep(0.3)
            cur_x, cur_y = self._get_mouse_position()

            if cur_x != prev_x or cur_y != prev_y:
                # Mouse moved — reset idle timer
                idle_since = time.monotonic()
                prev_x, prev_y = cur_x, cur_y
            else:
                # Mouse idle
                idle_duration = time.monotonic() - idle_since
                if idle_duration >= threshold:
                    return True

    def _extract_peer_id(self, title: str) -> Optional[str]:
        """Try to extract peer ID from window title (best effort)."""
        if not title:
            return None
        m = RUSTDESK_PEER_RE.match(title)
        if m:
            return m.group(1).strip()
        return None

    def _is_dialog_size(self, width: int, height: int) -> bool:
        """Check if window size matches the RustDesk connection dialog."""
        tol = self.config.dialog_tolerance
        return (
            abs(width - self.config.dialog_width) <= tol
            and abs(height - self.config.dialog_height) <= tol
        )

    def _should_accept(self, peer_id: Optional[str]) -> bool:
        """Return True if this peer should be accepted."""
        if self.config.mode == "allow_all":
            return True
        if peer_id is None:
            # Can't determine peer ID — reject in whitelist mode for safety
            return False
        return peer_id in self.config.allowed_ids

    TITLE_RETRY_INTERVAL = 0.5  # seconds between title retry attempts
    TITLE_RETRY_MAX = 10  # max retries (total wait = 5 seconds)

    def _get_window_title_by_id(self, window_id) -> str:
        """Get window title by ID. Override in subclass."""
        return ""

    def _on_new_window(self, window_id, title: str = "") -> None:
        """Called when a RustDesk dialog window is detected (by process + size)."""
        if window_id in self._processed:
            return

        peer_id = self._extract_peer_id(title)

        # Title may not be set yet (Flutter sets it asynchronously).
        # Retry a few times to get the peer ID from the title.
        if peer_id is None and self.config.mode == "whitelist":
            for attempt in range(self.TITLE_RETRY_MAX):
                time.sleep(self.TITLE_RETRY_INTERVAL)
                title = self._get_window_title_by_id(window_id)
                peer_id = self._extract_peer_id(title)
                if peer_id is not None:
                    break
                self.logger.debug("Title retry %d/%d: title=%r",
                                  attempt + 1, self.TITLE_RETRY_MAX, title)

        if not self._should_accept(peer_id):
            self.logger.info("REJECTED peer=%s (not in whitelist) title=%r", peer_id, title)
            self._processed.add(window_id)
            return

        self.logger.info("ACCEPTING peer=%s title=%r — waiting for mouse idle", peer_id, title)

        self._wait_for_idle()
        time.sleep(self.config.click_delay)
        success = self._click_accept(window_id)
        if success:
            # Verify the dialog actually closed after clicking
            time.sleep(1.0)
            if self._is_window_still_open(window_id):
                self.logger.warning("Dialog still open after click — will retry on next scan")
            else:
                self._processed.add(window_id)
                self.logger.info("Dialog closed successfully for %s", window_id)
        else:
            self.logger.warning("Click failed for %s — will retry on next scan", window_id)

    def _is_window_still_open(self, window_id) -> bool:
        """Check if the window still exists. Override in subclass."""
        return False

    @abstractmethod
    def _click_accept(self, window_id) -> bool:
        """Click the accept button in the given window. Returns True on success."""


# ---------------------------------------------------------------------------
# Linux detector (X11 + xdotool)
# ---------------------------------------------------------------------------

def _detect_display_from_rustdesk() -> tuple[Optional[str], Optional[str]]:
    """
    Read DISPLAY and XAUTHORITY from the RustDesk process environment.
    Falls back to current environment variables.
    This is needed when running as a systemd service without a desktop session.
    """
    try:
        result = subprocess.run(
            ["pgrep", "-x", "rustdesk"],
            capture_output=True, text=True
        )
        pids = result.stdout.strip().splitlines()
        for pid in pids:
            environ_path = f"/proc/{pid}/environ"
            try:
                with open(environ_path, "rb") as f:
                    raw = f.read()
                env_pairs = raw.split(b"\x00")
                env = {}
                for pair in env_pairs:
                    if b"=" in pair:
                        k, _, v = pair.partition(b"=")
                        env[k.decode(errors="replace")] = v.decode(errors="replace")
                display = env.get("DISPLAY")
                xauth = env.get("XAUTHORITY")
                if display:
                    return display, xauth
            except (PermissionError, FileNotFoundError):
                continue
    except Exception:
        pass

    # Fallback: current environment
    return os.environ.get("DISPLAY"), os.environ.get("XAUTHORITY")


class LinuxDetector(BaseDetector):
    """
    Linux X11 detector using python-xlib.
    Subscribes to SubstructureNotify + PropertyChangeMask on root window.
    Uses xdotool for clicking (absolute coordinates bypass Flutter's synthetic event filter).
    """

    MAP_NOTIFY_DELAY = 0.3  # seconds to wait after MapNotify for title to be set

    def _get_window_title_by_id(self, window_id) -> str:
        try:
            result = subprocess.run(
                ["xdotool", "getwindowname", str(window_id)],
                capture_output=True, text=True
            )
            return result.stdout.strip()
        except Exception:
            return ""

    def _is_window_still_open(self, window_id) -> bool:
        try:
            result = subprocess.run(
                ["xdotool", "getwindowname", str(window_id)],
                capture_output=True, text=True
            )
            return result.returncode == 0 and result.stdout.strip() != ""
        except Exception:
            return False

    def __init__(self, config: Config, logger: logging.Logger) -> None:
        super().__init__(config, logger)
        self._display_name, self._xauthority = _detect_display_from_rustdesk()

        if self._display_name:
            os.environ["DISPLAY"] = self._display_name
            self.logger.info("Using DISPLAY=%s", self._display_name)
        if self._xauthority:
            os.environ["XAUTHORITY"] = self._xauthority
            self.logger.info("Using XAUTHORITY=%s", self._xauthority)

        try:
            from Xlib import X, display as xdisplay, error as xerror
        except ImportError:
            self.logger.error("python-xlib is not installed. Run: pip install python-xlib")
            sys.exit(1)

        self._X = X
        self._xerror = xerror

        try:
            self._dpy = xdisplay.Display(self._display_name)
        except Exception as e:
            self.logger.error("Cannot connect to X display %s: %s", self._display_name, e)
            sys.exit(1)

        self._root = self._dpy.screen().root

    def _get_window_geometry(self, window_id: int) -> Optional[tuple[int, int, int, int]]:
        """Return (x, y, width, height) using xdotool getwindowgeometry."""
        try:
            result = subprocess.run(
                ["xdotool", "getwindowgeometry", "--shell", str(window_id)],
                capture_output=True, text=True
            )
            vals = {}
            for line in result.stdout.splitlines():
                if "=" in line:
                    k, v = line.split("=", 1)
                    vals[k] = int(v)
            x = vals.get("X", 0)
            y = vals.get("Y", 0)
            w = vals.get("WIDTH", 0)
            h = vals.get("HEIGHT", 0)
            if w > 0 and h > 0:
                return (x, y, w, h)
        except Exception:
            pass
        return None

    def _get_mouse_position(self) -> tuple[int, int]:
        """Return current mouse position."""
        try:
            result = subprocess.run(
                ["xdotool", "getmouselocation", "--shell"],
                capture_output=True, text=True
            )
            x, y = 0, 0
            for line in result.stdout.splitlines():
                if line.startswith("X="):
                    x = int(line.split("=", 1)[1])
                elif line.startswith("Y="):
                    y = int(line.split("=", 1)[1])
            return x, y
        except Exception:
            return 0, 0

    def _click_accept(self, window_id) -> bool:
        """
        Click the accept button using xdotool absolute coordinates.
        Saves and restores mouse position to minimize disruption.
        Returns True on success.
        """
        try:
            geom = self._get_window_geometry(window_id)
            if geom is None:
                self.logger.warning("Could not get geometry for window 0x%x", window_id)
                return False

            abs_x, abs_y, width, height = geom
            btn_x = abs_x + int(width * self.config.x_ratio)
            btn_y = abs_y + int(height * self.config.y_ratio)

            # Save current mouse position
            saved_x, saved_y = self._get_mouse_position()

            # Activate window, move mouse, click, restore
            win_id_str = str(window_id)
            subprocess.run(["xdotool", "windowactivate", "--sync", win_id_str], check=False)
            subprocess.run(["xdotool", "mousemove", str(btn_x), str(btn_y)], check=False)
            time.sleep(0.05)
            subprocess.run(["xdotool", "click", "1"], check=False)
            time.sleep(0.05)
            subprocess.run(["xdotool", "mousemove", str(saved_x), str(saved_y)], check=False)

            self.logger.info(
                "Clicked accept button at (%d, %d) for window 0x%x", btn_x, btn_y, window_id
            )
            return True
        except Exception as e:
            self.logger.error("Click failed for window 0x%x: %s", window_id, e)
            return False

    SCAN_INTERVAL = 2  # seconds between fallback scans

    def _scan_with_xdotool(self) -> None:
        """Scan for RustDesk dialogs by process class + window size."""
        try:
            # Find all windows belonging to rustdesk process
            result = subprocess.run(
                ["xdotool", "search", "--class", "rustdesk"],
                capture_output=True, text=True
            )
            current_ids = set()
            candidates = []
            for line in result.stdout.strip().splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    win_id = int(line)
                except ValueError:
                    continue

                # Check window size to filter connection dialogs
                geom = self._get_window_geometry(win_id)
                if geom is None:
                    continue
                _, _, w, h = geom
                if self._is_dialog_size(w, h):
                    current_ids.add(win_id)
                    candidates.append(win_id)

            # Purge stale entries: if a window was destroyed, its ID can be
            # reused by X11 for a new window. Remove gone IDs from cache.
            stale = self._processed - current_ids
            if stale:
                self._processed -= stale

            for win_id in candidates:
                if win_id in self._processed:
                    continue

                # Get title for whitelist peer ID extraction (best effort)
                title = ""
                try:
                    name_result = subprocess.run(
                        ["xdotool", "getwindowname", str(win_id)],
                        capture_output=True, text=True
                    )
                    title = name_result.stdout.strip()
                except Exception:
                    pass

                self.logger.info("Detected RustDesk dialog: wid=%d size=(%d,%d) title=%r",
                                 win_id, w, h, title)
                self._on_new_window(win_id, title)
        except Exception as e:
            self.logger.debug("xdotool scan error: %s", e)

    def run(self) -> None:
        """
        Hybrid event loop:
        - Primary: X11 MapNotify events for instant detection
        - Fallback: xdotool scan every SCAN_INTERVAL seconds for missed events
        """
        self._root.change_attributes(
            event_mask=self._X.SubstructureNotifyMask
        )
        self._dpy.flush()

        self.logger.info(
            "Listening for RustDesk windows on display %s (mode=%s)",
            self._display_name or os.environ.get("DISPLAY"),
            self.config.mode,
        )
        if self.config.mode == "whitelist":
            self.logger.info("Allowed IDs: %s", sorted(self.config.allowed_ids) or "(none)")

        # Initial scan for windows already open
        self._scan_with_xdotool()

        last_scan = time.monotonic()

        while True:
            # Process all pending X events (non-blocking)
            try:
                remaining = self._dpy.pending_events()
                for _ in range(remaining):
                    ev = self._dpy.next_event()
                    self._handle_event(ev)
            except self._xerror.ConnectionClosedError:
                self.logger.error("X display connection closed. Exiting.")
                break
            except KeyboardInterrupt:
                self.logger.info("Interrupted. Exiting.")
                break
            except Exception as e:
                self.logger.debug("Event loop error: %s", e)

            # Periodic fallback scan
            now = time.monotonic()
            if now - last_scan >= self.SCAN_INTERVAL:
                self._scan_with_xdotool()
                last_scan = now

            # Sleep briefly to avoid busy-waiting (100ms)
            time.sleep(0.1)

    def _handle_event(self, ev) -> None:
        from Xlib import X

        if ev.type != X.MapNotify:
            return

        window = ev.window
        win_id = window.id

        if win_id in self._processed:
            return

        # Wait briefly for window to settle
        time.sleep(self.MAP_NOTIFY_DELAY)

        # Check if it's a rustdesk dialog by size
        geom = self._get_window_geometry(win_id)
        if geom is None:
            return
        _, _, w, h = geom
        if not self._is_dialog_size(w, h):
            return

        # Get title for peer ID extraction (best effort)
        title = ""
        try:
            result = subprocess.run(
                ["xdotool", "getwindowname", str(win_id)],
                capture_output=True, text=True
            )
            title = result.stdout.strip()
        except Exception:
            pass

        self.logger.info("Detected RustDesk dialog via event: wid=%d size=(%d,%d) title=%r",
                         win_id, w, h, title)
        self._on_new_window(win_id, title)


# ---------------------------------------------------------------------------
# Windows detector (ctypes + user32)
# ---------------------------------------------------------------------------

class WindowsDetector(BaseDetector):
    """
    Windows detector using SetWinEventHook for EVENT_OBJECT_SHOW.
    Clicks via SetForegroundWindow + mouse_event (absolute coordinates).
    """

    def __init__(self, config: Config, logger: logging.Logger) -> None:
        super().__init__(config, logger)
        import ctypes
        import ctypes.wintypes
        self._ctypes = ctypes
        self._user32 = ctypes.windll.user32

    def _get_window_title(self, hwnd) -> str:
        ctypes = self._ctypes
        length = self._user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(length + 1)
        self._user32.GetWindowTextW(hwnd, buf, length + 1)
        return buf.value

    def _get_window_title_by_id(self, window_id) -> str:
        return self._get_window_title(window_id)

    def _is_window_still_open(self, window_id) -> bool:
        return bool(self._user32.IsWindow(window_id))

    def _get_window_rect(self, hwnd) -> Optional[tuple[int, int, int, int]]:
        ctypes = self._ctypes
        rect = ctypes.wintypes.RECT()
        if self._user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top
        return None

    def _get_mouse_position(self) -> tuple[int, int]:
        ctypes = self._ctypes
        pt = ctypes.wintypes.POINT()
        self._user32.GetCursorPos(ctypes.byref(pt))
        return pt.x, pt.y

    def _click_accept(self, hwnd) -> bool:
        ctypes = self._ctypes
        user32 = self._user32

        rect = self._get_window_rect(hwnd)
        if rect is None:
            self.logger.warning("Could not get rect for hwnd %s", hwnd)
            return False

        x, y, width, height = rect
        self.logger.info(
            "[DIAG] Window rect: x=%d y=%d w=%d h=%d (hwnd=%s)",
            x, y, width, height, hwnd,
        )

        btn_x = x + int(width * self.config.x_ratio)
        btn_y = y + int(height * self.config.y_ratio)

        # Save mouse position
        saved_x, saved_y = self._get_mouse_position()

        # Bring window to foreground
        user32.SetForegroundWindow(hwnd)
        time.sleep(0.05)

        # Move mouse and click (absolute coords, MOUSEEVENTF_ABSOLUTE needs 0-65535 range)
        # Use SM_CXVIRTUALSCREEN/SM_CYVIRTUALSCREEN for multi-monitor support
        SM_XVIRTUALSCREEN = 76
        SM_YVIRTUALSCREEN = 77
        SM_CXVIRTUALSCREEN = 78
        SM_CYVIRTUALSCREEN = 79
        virt_x = user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
        virt_y = user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
        virt_w = user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)
        virt_h = user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)

        self.logger.info(
            "[DIAG] Virtual screen: offset=(%d,%d) size=%dx%d",
            virt_x, virt_y, virt_w, virt_h,
        )

        norm_x = int((btn_x - virt_x) * 65535 / virt_w)
        norm_y = int((btn_y - virt_y) * 65535 / virt_h)

        MOUSEEVENTF_MOVE = 0x0001
        MOUSEEVENTF_LEFTDOWN = 0x0002
        MOUSEEVENTF_LEFTUP = 0x0004
        MOUSEEVENTF_ABSOLUTE = 0x8000

        user32.mouse_event(MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE, norm_x, norm_y, 0, 0)
        time.sleep(0.05)
        user32.mouse_event(MOUSEEVENTF_LEFTDOWN | MOUSEEVENTF_ABSOLUTE, norm_x, norm_y, 0, 0)
        user32.mouse_event(MOUSEEVENTF_LEFTUP | MOUSEEVENTF_ABSOLUTE, norm_x, norm_y, 0, 0)
        time.sleep(0.05)

        # Restore mouse position
        saved_norm_x = int((saved_x - virt_x) * 65535 / virt_w)
        saved_norm_y = int((saved_y - virt_y) * 65535 / virt_h)
        user32.mouse_event(MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE, saved_norm_x, saved_norm_y, 0, 0)

        self.logger.info("Clicked accept button at (%d, %d) for hwnd %s", btn_x, btn_y, hwnd)
        return True

    SCAN_INTERVAL = 2  # seconds between fallback scans

    def _get_process_name(self, hwnd) -> str:
        """Get the executable name for the process owning the window."""
        ctypes = self._ctypes
        pid = ctypes.wintypes.DWORD()
        self._user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value == 0:
            return ""
        try:
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
            if not handle:
                return ""
            try:
                buf = ctypes.create_unicode_buffer(260)
                size = ctypes.wintypes.DWORD(260)
                kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size))
                return os.path.basename(buf.value).lower()
            finally:
                kernel32.CloseHandle(handle)
        except Exception:
            return ""

    def _find_rustdesk_windows(self) -> list:
        """Enumerate rustdesk windows matching dialog size."""
        ctypes = self._ctypes
        user32 = self._user32
        found = []

        WNDENUMPROC = ctypes.WINFUNCTYPE(
            ctypes.wintypes.BOOL,
            ctypes.wintypes.HWND,
            ctypes.wintypes.LPARAM,
        )

        def enum_callback(hwnd, _lparam):
            if not user32.IsWindowVisible(hwnd):
                return True
            # Check process name
            proc_name = self._get_process_name(hwnd)
            if proc_name != "rustdesk.exe":
                return True
            # Check window size
            rect = self._get_window_rect(hwnd)
            if rect is None:
                return True
            _, _, w, h = rect
            if self._is_dialog_size(w, h):
                title = self._get_window_title(hwnd)
                found.append((hwnd, title, w, h))
            return True

        user32.EnumWindows(WNDENUMPROC(enum_callback), 0)
        return found

    def _scan_windows(self) -> None:
        """Scan for RustDesk dialogs by process + size (fallback for missed events)."""
        try:
            found = self._find_rustdesk_windows()
            current_hwnds = set(hwnd for hwnd, _, _, _ in found)

            # Purge stale entries (window destroyed, hwnd may be reused)
            stale = self._processed - current_hwnds
            if stale:
                self._processed -= stale

            for hwnd, title, w, h in found:
                if hwnd in self._processed:
                    continue
                self.logger.info("[DIAG] Scan found RustDesk dialog: hwnd=%s size=(%d,%d) title=%r",
                                 hwnd, w, h, title)
                self._on_new_window(hwnd, title)
        except Exception as e:
            self.logger.debug("Scan error: %s", e)

    def run(self) -> None:
        import ctypes
        import ctypes.wintypes
        import threading

        user32 = self._user32

        EVENT_OBJECT_SHOW = 0x8002
        WINEVENT_OUTOFCONTEXT = 0x0000

        WinEventProc = ctypes.WINFUNCTYPE(
            None,
            ctypes.wintypes.HANDLE,  # hWinEventHook
            ctypes.wintypes.DWORD,   # event
            ctypes.wintypes.HWND,    # hwnd
            ctypes.wintypes.LONG,    # idObject
            ctypes.wintypes.LONG,    # idChild
            ctypes.wintypes.DWORD,   # idEventThread
            ctypes.wintypes.DWORD,   # dwmsEventTime
        )

        def win_event_callback(hWinEventHook, event, hwnd, idObject, idChild, idEventThread, dwmsEventTime):
            if not hwnd:
                return
            if idObject != 0:
                return
            if hwnd in self._processed:
                return
            try:
                # Check process name
                proc_name = self._get_process_name(hwnd)
                if proc_name != "rustdesk.exe":
                    return
                # Check window size
                rect = self._get_window_rect(hwnd)
                if rect is None:
                    return
                _, _, w, h = rect
                if not self._is_dialog_size(w, h):
                    return
                title = self._get_window_title(hwnd)
                self.logger.info("[DIAG] Event detected: hwnd=%s size=(%d,%d) title=%r",
                                 hwnd, w, h, title)
                time.sleep(self.config.click_delay)
                self._on_new_window(hwnd, title)
            except Exception as e:
                self.logger.debug("win_event_callback error: %s", e)

        proc = WinEventProc(win_event_callback)

        hook = user32.SetWinEventHook(
            EVENT_OBJECT_SHOW,
            EVENT_OBJECT_SHOW,
            None,
            proc,
            0,
            0,
            WINEVENT_OUTOFCONTEXT,
        )

        if not hook:
            self.logger.error("SetWinEventHook failed.")
            sys.exit(1)

        self.logger.info(
            "Listening for RustDesk windows (mode=%s)", self.config.mode
        )
        if self.config.mode == "whitelist":
            self.logger.info("Allowed IDs: %s", sorted(self.config.allowed_ids) or "(none)")

        # Initial scan
        self._scan_windows()

        # Background fallback scan thread
        def fallback_scanner():
            while True:
                time.sleep(self.SCAN_INTERVAL)
                self._scan_windows()

        scanner = threading.Thread(target=fallback_scanner, daemon=True)
        scanner.start()

        # Windows message pump with Ctrl+C support
        # PeekMessage allows periodic checking for KeyboardInterrupt
        PM_REMOVE = 0x0001
        msg = ctypes.wintypes.MSG()
        try:
            while True:
                # Non-blocking peek instead of blocking GetMessage
                while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_REMOVE):
                    if msg.message == 0x0012:  # WM_QUIT
                        raise SystemExit(0)
                    user32.TranslateMessage(ctypes.byref(msg))
                    user32.DispatchMessageW(ctypes.byref(msg))
                time.sleep(0.1)
        except (KeyboardInterrupt, SystemExit):
            self.logger.info("Interrupted. Exiting.")
        finally:
            user32.UnhookWinEvent(hook)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    config = Config(CONFIG_PATH)
    logger = setup_logger(config.log_file)

    logger.info("RustDesk Auto-Accept starting (platform=%s, mode=%s)", platform.system(), config.mode)

    system = platform.system()
    if system == "Linux":
        detector = LinuxDetector(config, logger)
    elif system == "Windows":
        detector = WindowsDetector(config, logger)
    else:
        logger.error("Unsupported platform: %s", system)
        sys.exit(1)

    try:
        detector.run()
    except KeyboardInterrupt:
        logger.info("Stopped by user.")
    except Exception as e:
        logger.exception("Fatal error: %s", e)
        sys.exit(1)


if __name__ == "__main__":
    main()
