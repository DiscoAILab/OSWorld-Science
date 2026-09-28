#!/usr/bin/env bash
# Open the Chandra image in DS9 and put the display into the initial state the
# astro tasks assume (the task authors' set-up checklist, § 2.1):
#   frame 1, block 1, Scale -> Log, Scale -> Min Max, cmap grey, zoom 4,
#   pan to 138.704375 8.8891944 (fk5 degrees), no regions.
#
# The pre-task hook (scripts/vm_prep/astro/prepare_episode.py) installs this
# file over the baked /home/user/start_ds9.sh at the start of every episode and
# runs it; the desktop launcher "SAOImageDS9 (CIAO)" runs the same file.
#
# block must stay 1: dax exports the *displayed* image to a temporary FITS
# before running dmextract, so a changed display block changes every value.
#
# ds9.auto: DS9 keeps an automatic backup (ds9.auto, ds9.auto.dir/ with every
# region) in $HOME. When the previous DS9 was killed, the next start asks
# "Found Auto Backup, restore?" and Yes brings the previous session's regions
# back, a state-pollution channel. A clean XPA `exit` removes the backup and
# SIGTERM leaves it, so: exit cleanly first, then delete whatever is left.
#
# Differences from the script baked into the image (the solver-side patch
# applied during the CIAO benchmark runs, folded in):
#   * if DS9 is already showing a file, that file and its display state are
#     restored instead of the task-1 baseline, so a mis-click on the desktop
#     icon during another task neither swaps the image nor leaks task 1's file
#     name; if DS9 was closed, the file recorded by the task's last staging
#     step (~/.ds9_current_file) is used; only then the baseline
#   * /tmp/ds9_dax.user is emptied, never removed: dax creates its temporary
#     files inside it and never recreates the directory, so removing it
#     disables dax for the whole session
export DISPLAY=${DISPLAY:-:0}
[ -n "$XAUTHORITY" ] || export XAUTHORITY=/run/user/1000/gdm/Xauthority
source /home/user/miniconda3/etc/profile.d/conda.sh
conda activate ciao-4.18
cd /home/user/CIAO_001 || exit 1
IMG=13858/acisf13858_broad_thresh.img

# 0) remember what DS9 is showing, and how
_XP0=$(xpaget xpans 2>/dev/null | awk '{print $4}' | head -1)
CURFILE=""; CUR_SCALE=""; CUR_MODE=""; CUR_CMAP=""; CUR_ZOOM=""; CUR_PAN=""
if [ -n "$_XP0" ]; then
  CURFILE=$(xpaget "$_XP0" file 2>/dev/null | sed 's/\[.*$//')
  CUR_SCALE=$(xpaget "$_XP0" scale 2>/dev/null); CUR_MODE=$(xpaget "$_XP0" scale mode 2>/dev/null)
  CUR_CMAP=$(xpaget "$_XP0" cmap 2>/dev/null);  CUR_ZOOM=$(xpaget "$_XP0" zoom 2>/dev/null)
  CUR_PAN=$(xpaget "$_XP0" pan physical 2>/dev/null)
fi

# 1) close a running DS9 cleanly
for xp in $(xpaget xpans 2>/dev/null | awk '{print $4}'); do
  xpaset -p "$xp" exit 2>/dev/null
done
sleep 2
pkill -x ds9 2>/dev/null   # -x: exact executable name; never -f, which would match this script
sleep 1
# 2) leftovers: the auto-backup, and the contents (only) of the dax directory
rm -rf /home/user/ds9.auto /home/user/ds9.auto.dir
mkdir -p /tmp/ds9_dax.user && find /tmp/ds9_dax.user -mindepth 1 -delete 2>/dev/null

# 3) which file: a live DS9 -> the marker written after staging -> the baseline
RESTORED=0
if [ -z "$CURFILE" ] && [ -f /home/user/.ds9_current_file ]; then
  CURFILE=$(cat /home/user/.ds9_current_file 2>/dev/null)
fi
if [ -n "$CURFILE" ] && [ -f "$CURFILE" ]; then IMG="$CURFILE"; RESTORED=1; fi

setsid nohup ds9 "$IMG" > /tmp/ds9.log 2>&1 < /dev/null &
XPA=""
for i in $(seq 1 60); do
  sleep 2
  XPA=$(xpaget xpans 2>/dev/null | awk '{print $4}' | head -1)
  [ -n "$XPA" ] && break
done
[ -n "$XPA" ] || { echo "DS9 did not come up"; exit 1; }
xpaset -p "$XPA" frame 1
xpaset -p "$XPA" block 1
xpaset -p "$XPA" scale log
xpaset -p "$XPA" scale mode minmax
xpaset -p "$XPA" cmap grey
if [ "$RESTORED" = "1" ]; then
  # restore the display as it was; do not apply the task-1 zoom/pan
  [ -n "$CUR_SCALE" ] && xpaset -p "$XPA" scale "$CUR_SCALE"
  [ -n "$CUR_MODE" ]  && xpaset -p "$XPA" scale mode "$CUR_MODE"
  [ -n "$CUR_CMAP" ]  && xpaset -p "$XPA" cmap "$CUR_CMAP"
  [ -n "$CUR_ZOOM" ]  && xpaset -p "$XPA" zoom to "$CUR_ZOOM"
  [ -n "$CUR_PAN" ]   && xpaset -p "$XPA" pan to $CUR_PAN physical
else
  xpaset -p "$XPA" zoom to 4
  xpaset -p "$XPA" pan to 138.704375 8.8891944 wcs fk5
fi
xpaset -p "$XPA" regions delete all
NREG=$(xpaget "$XPA" regions 2>/dev/null | grep -cE '^(circle|annulus|ellipse|box|polygon)')
GEO=$(wmctrl -lG 2>/dev/null | grep "SAOImage ds9" | awk '{print $3","$4" "$5"x"$6}')
echo "DS9 ready: file=$(xpaget "$XPA" file | sed 's/\[.*$//') restored=$RESTORED frame=$(xpaget "$XPA" frame) block=$(xpaget "$XPA" block) scale=$(xpaget "$XPA" scale) mode=$(xpaget "$XPA" scale mode) cmap=$(xpaget "$XPA" cmap) zoom=$(xpaget "$XPA" zoom) regions=$NREG geometry=$GEO autobackup=$(ls /home/user/ds9.auto 2>/dev/null | wc -l)"
