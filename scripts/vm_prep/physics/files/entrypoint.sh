#!/bin/bash
# PID-1 helper: runtime dirs that must exist before the desktop starts, then hand over to supervisord.
mkdir -p /var/log/guest /tmp/runtime-user /tmp/.X11-unix
chown user:user /tmp/runtime-user && chmod 700 /tmp/runtime-user
chmod 1777 /tmp/.X11-unix
# Real MIT-MAGIC-COOKIE for :0 in ~/.Xauthority so python-xlib (pyautogui, guest server cursor capture)
# finds auth details and stops warning "no xauthority details available". Xvfb still runs with -ac, so
# the cookie is never actually checked; the file just has to contain an entry for this display/host.
rm -f /home/user/.Xauthority && touch /home/user/.Xauthority && chown user:user /home/user/.Xauthority
runuser -u user -- env HOME=/home/user xauth -q add :0 . "$(mcookie)" 2>/dev/null || true
# the harness may bind-mount task files here; keep ownership sane
chown -R user:user /home/user/work 2>/dev/null || true
exec "$@"
