#!/usr/bin/env bash
set -euo pipefail

# RustDesk Auto-Accept — Linux systemd user service uninstaller

SERVICE_NAME="rustdesk-autoclick"
SERVICE_FILE="${HOME}/.config/systemd/user/${SERVICE_NAME}.service"

if [ ! -f "$SERVICE_FILE" ]; then
    echo "Service file not found: ${SERVICE_FILE}"
    echo "Nothing to uninstall."
    exit 0
fi

echo "Stopping ${SERVICE_NAME}..."
systemctl --user stop "${SERVICE_NAME}.service" 2>/dev/null || true

echo "Disabling ${SERVICE_NAME}..."
systemctl --user disable "${SERVICE_NAME}.service" 2>/dev/null || true

echo "Removing service file..."
rm -f "$SERVICE_FILE"

systemctl --user daemon-reload

echo ""
echo "=== Uninstall complete ==="
echo "Service has been stopped, disabled, and removed."
echo "Config and script files are still in place — delete manually if needed."
