#!/bin/bash
set -e

echo "=== Initializing Zoom Tab Recorder Cloud Environment ==="

# 1. Start DBus system bus if not running
if [ -d /var/run/dbus ]; then
    rm -f /var/run/dbus/pid
fi
service dbus start 2>/dev/null || true

# 2. Start PulseAudio in daemon mode with virtual null-sink for audio loopback
echo "Configuring virtual PulseAudio sink for browser audio capture..."
pulseaudio --kill 2>/dev/null || true
pulseaudio -D --exit-idle-time=-1 --disallow-exit --log-target=syslog 2>/dev/null || true
sleep 1

# Load null sink module
pactl load-module module-null-sink sink_name=virtual_sink sink_properties=device.description="Virtual_Zoom_Sink" 2>/dev/null || true
pactl set-default-sink virtual_sink 2>/dev/null || true
pactl set-default-source virtual_sink.monitor 2>/dev/null || true

# 3. Start Xvfb virtual display on :99
export DISPLAY="${DISPLAY:-:99}"
RESOLUTION="${RECORDING_RESOLUTION:-1280x720}"
echo "Launching Xvfb on display ${DISPLAY} (${RESOLUTION}x24)..."

# Remove any old X locks
rm -f /tmp/.X99-lock /tmp/.X11-unix/X99 2>/dev/null || true

Xvfb "${DISPLAY}" -screen 0 "${RESOLUTION}x24" -ac +extension GLX +render -noreset &
XVFB_PID=$!
sleep 2

# Verify Xvfb started
if ! kill -0 $XVFB_PID 2>/dev/null; then
    echo "ERROR: Xvfb failed to start."
    exit 1
fi
echo "Xvfb running on PID ${XVFB_PID}."

# 4. Trap shutdown signals to terminate cleanly
trap "echo 'Stopping container services...'; kill -TERM $XVFB_PID 2>/dev/null; exit 0" SIGTERM SIGINT

echo "=== Starting Zoom Recorder Bot Application ==="
exec python -m app.main "$@"
