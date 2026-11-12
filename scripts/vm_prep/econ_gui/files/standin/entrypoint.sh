#!/bin/bash
# Start a virtual display, an XFCE desktop and a VNC server, then wait forever.
set -e
export DISPLAY=:1
SCREEN="${SCREEN:-1280x720x24}"
mkdir -p "$XDG_RUNTIME_DIR" && chmod 700 "$XDG_RUNTIME_DIR"
touch "$HOME/.Xauthority"   # python-xlib insists the file exists even with `Xvfb -ac`

# Pre-seed the panel so the "first start of the panel" wizard never appears.
PANEL_DIR="$HOME/.config/xfce4/xfconf/xfce-perchannel-xml"
mkdir -p "$PANEL_DIR"
[ -f "$PANEL_DIR/xfce4-panel.xml" ] || cp /etc/xdg/xfce4/panel/default.xml "$PANEL_DIR/xfce4-panel.xml"

Xvfb :1 -screen 0 "$SCREEN" -ac +extension RANDR -nolisten tcp &
for i in $(seq 1 50); do xdpyinfo -display :1 >/dev/null 2>&1 && break; sleep 0.2; done

eval "$(dbus-launch --sh-syntax)"
export DBUS_SESSION_BUS_ADDRESS
startxfce4 >/tmp/xfce.log 2>&1 &
sleep 5

# Lighter desktop: no compositor, no screensaver / power management.
xfconf-query -c xfwm4 -p /general/use_compositing -s false >/dev/null 2>&1 || true
xset s off -dpms >/dev/null 2>&1 || true

x11vnc -display :1 -forever -shared -nopw -quiet -rfbport 5900 >/tmp/x11vnc.log 2>&1 &
sleep 2

touch /tmp/desktop_ready
exec tail -f /dev/null
