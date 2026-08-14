#!/bin/bash
set -e

echo "==> Cleaning up stale PID files..."
rm -f /run/dbus/pid
rm -f /run/avahi-daemon/pid

echo "==> Starting D-Bus system daemon..."
mkdir -p /run/dbus
dbus-daemon --system --fork
sleep 1

# ---------------------------------------------------------------------------
# Dynamic user setup — driven by the same env vars the Python script reads.
# ---------------------------------------------------------------------------
OWNER_UID="${OWNER_UID:-1000}"
OWNER="${OWNER:-scanuser}"
SCAN_OUTPUT_DIR="${SCAN_OUTPUT_DIR:-/scans}"

echo "==> Setting up scan user '${OWNER}' (uid=${OWNER_UID})..."

# The scanner group is created by sane-utils; create it defensively anyway.
getent group scanner >/dev/null 2>&1 || groupadd --system scanner

# Only create the user when no account with that UID exists yet.
if id -u "${OWNER_UID}" >/dev/null 2>&1; then
    echo "    A user with uid=${OWNER_UID} already exists — skipping creation."
elif id -u "${OWNER}" >/dev/null 2>&1; then
    echo "    WARNING: username '${OWNER}' already exists with a different UID." \
         "Skipping creation."
else
    useradd -u "${OWNER_UID}" -m -s /bin/bash -G scanner "${OWNER}"
    echo "    Created user '${OWNER}' with uid=${OWNER_UID}."
fi

# Ensure the output directory exists and is writable by the scan user.
mkdir -p "${SCAN_OUTPUT_DIR}"
chown "${OWNER_UID}" "${SCAN_OUTPUT_DIR}"

echo "==> Starting Samsung Scan-to-PC server..."
exec python3 /usr/local/bin/samsungScannerServer.py
