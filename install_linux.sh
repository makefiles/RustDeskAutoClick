#!/usr/bin/env bash
set -euo pipefail

# RustDesk Auto-Accept — Linux systemd user service installer

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SCRIPT_PATH="${SCRIPT_DIR}/rustdesk_autoclick.py"
SERVICE_NAME="rustdesk-autoclick"
SERVICE_DIR="${HOME}/.config/systemd/user"
SERVICE_FILE="${SERVICE_DIR}/${SERVICE_NAME}.service"

if [ ! -f "$SCRIPT_PATH" ]; then
    echo "ERROR: rustdesk_autoclick.py not found at ${SCRIPT_PATH}"
    exit 1
fi

if [ ! -f "${SCRIPT_DIR}/config.json" ]; then
    echo "ERROR: config.json not found at ${SCRIPT_DIR}/config.json"
    exit 1
fi

# Check python-xlib
if ! python3 -c "import Xlib" 2>/dev/null; then
    echo "Installing python-xlib..."
    python3 -m pip install --user python-xlib
fi

# Check xdotool
if ! command -v xdotool &>/dev/null; then
    echo "ERROR: xdotool is not installed. Install it with:"
    echo "  sudo apt install xdotool   # Debian/Ubuntu"
    echo "  sudo dnf install xdotool   # Fedora"
    exit 1
fi

# Create systemd user service directory
mkdir -p "$SERVICE_DIR"

# Write service file
# DISPLAY/XAUTHORITY are detected dynamically by the script from RustDesk process
cat > "$SERVICE_FILE" << EOF
[Unit]
Description=RustDesk Auto-Accept
After=graphical-session.target

[Service]
Type=simple
WorkingDirectory=${SCRIPT_DIR}
ExecStart=/usr/bin/python3 ${SCRIPT_PATH}
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
EOF

echo "Service file created: ${SERVICE_FILE}"

# Reload and enable
systemctl --user daemon-reload
systemctl --user enable --now "${SERVICE_NAME}.service"

echo ""
echo "=== Installation complete ==="
echo "Service status: systemctl --user status ${SERVICE_NAME}"
echo "View logs:      journalctl --user -u ${SERVICE_NAME} -f"
echo "Uninstall:      bash ${SCRIPT_DIR}/uninstall_linux.sh"
