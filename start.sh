#!/bin/bash
set -e

echo "==> Cleaning up stale PID files..."
rm -f /run/dbus/pid
rm -f /run/avahi-daemon/pid

echo "==> Starting D-Bus system daemon..."
mkdir -p /run/dbus
dbus-daemon --system --fork
sleep 1

echo "==> Starting Samsung Scan-to-PC server..."
exec python3 /usr/local/bin/samsungScannerServer.py
