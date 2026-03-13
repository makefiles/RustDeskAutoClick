# AGENTS.md — RustDesk Auto-Accept

## Project Overview

Cross-platform (Linux X11 + Windows) Python program that automatically clicks the "Accept" button on RustDesk incoming connection request dialogs. Replicates the paid auto-accept feature for free.

## Architecture

Single-file program (`rustdesk_autoclick.py`) with class hierarchy:

```
BaseDetector (ABC)
├── LinuxDetector   — X11 events + xdotool clicks
└── WindowsDetector — SetWinEventHook + ctypes mouse_event
```

Config is loaded from `config.json` in the same directory as the script.

## Detection Strategy

**How dialogs are found:** Process name (`rustdesk` / `rustdesk.exe`) + window size (~300×490px). NOT title-based.

**Dual detection (hybrid):**
- **Primary:** OS event (X11 MapNotify / Windows EVENT_OBJECT_SHOW) for instant detection
- **Fallback:** Periodic scan every 2 seconds to catch missed events

**Peer ID extraction:** Title regex `^(\d+)@` is used only for whitelist filtering (best effort). Title may load asynchronously (Flutter), so there's a retry loop (up to 5s) in whitelist mode.

## Click Mechanism

1. Wait for mouse idle (configurable threshold)
2. Save current mouse position
3. Activate window → move mouse to button coordinates → click → restore mouse
4. Verify dialog closed; if still open, retry on next scan cycle

**Linux:** xdotool absolute coordinates (Flutter ignores synthetic X events)
**Windows:** mouse_event with MOUSEEVENTF_ABSOLUTE (normalized 0-65535 range for multi-monitor)

## Key Files

| File | Purpose |
|------|---------|
| `rustdesk_autoclick.py` | Main program (all logic in one file) |
| `config.json` | User configuration (mode, allowed IDs, button position, delays) |
| `requirements.txt` | Python dependencies (python-xlib for Linux only) |
| `install_linux.sh` | systemd user service installer |
| `install_windows.bat` | Windows Task Scheduler installer (UAC auto-elevation) |
| `uninstall_linux.sh` | systemd service remover |
| `uninstall_windows.bat` | Task Scheduler task remover |

## Config Modes

- `"whitelist"` — Only accept connections from IDs listed in `allowed_ids`
- `"allow_all"` — Accept all incoming connections regardless of ID

## Known Constraints

- Linux: X11 only (no Wayland support)
- Flutter (RustDesk UI) sets window title asynchronously — title may be just "rustdesk" initially
- Button position (`x_ratio`, `y_ratio`) assumes default RustDesk dialog layout
- `dialog_size` tolerance handles minor size variations across OS/DPI settings

## Dependencies

- **Linux:** `python-xlib` (pip), `xdotool` (system package)
- **Windows:** None (uses ctypes + user32.dll + kernel32.dll)

## Service Details

- **Linux:** systemd user service (`~/.config/systemd/user/rustdesk-autoclick.service`), auto-detects DISPLAY/XAUTHORITY from RustDesk process `/proc/{pid}/environ`
- **Windows:** Task Scheduler task `RustDeskAutoAccept`, runs `pythonw` at logon with highest privileges
