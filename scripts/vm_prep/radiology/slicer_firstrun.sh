#!/usr/bin/env bash
# 3D Slicer first-run configuration — runs INSIDE the guest (delivered by guest_run.py), once, before baking.
# It removes four first-launch obstacles that would otherwise cost the agent steps (measured 2026-09-03):
#   1 the start-up application update check: the Slicer.ini written on first launch has
#     [ApplicationUpdate] AutoUpdateCheck=true, which goes online and may pop an update prompt
#     ([Extensions] AutoUpdateCheck already defaults to false),
#   2 the "create DICOM database?" dialog the agent would meet on first opening the DICOM module
#     (pre-create ~/Documents/SlicerDICOMDatabase),
#   3 the "Thank you for using 3D Slicer / not intended for clinical use" disclaimer
#     (key MainWindow/DontShowDisclaimerMessage=1024, i.e. QMessageBox::Ok),
#   4 the 1040x600 default window: maximise once, RestoreGeometry=true remembers it; the Welcome
#     module stays the home page (default), so the agent sees the official getting-started layout.
#
#   scripts/vm_prep/common/guest_run.py slicer_firstrun scripts/vm_prep/radiology/slicer_firstrun.sh --port 5050
set -e
INI=/home/user/.config/slicer.org/Slicer.ini
pkill -x SlicerApp-real 2>/dev/null || true; sleep 2

# 1. write through Slicer's own QSettings (no hand-editing of the ini and its escaping rules);
#    2. create the DICOM database on the way out
cat > /tmp/slicer_firstrun.py <<'PY'
import qt, slicer, os
s = qt.QSettings()
s.setValue("ApplicationUpdate/AutoUpdateCheck", False)
s.setValue("Extensions/AutoUpdateCheck", False)
# The first-launch "Thank you for using 3D Slicer / not intended for clinical use" dialog:
# ctkMessageBox's dontShowAgain key (found with `strings` on libqSlicerBaseQTGUI.so) stores
# the standard button that was clicked, QMessageBox::Ok = 1024
s.setValue("MainWindow/DontShowDisclaimerMessage", 1024)
# The exit confirmation (also a ctkMessageBox, key MainWindow/DontConfirmExit): disable it,
# because a normal close() below must let Slicer write the window geometry back to the ini,
# and because it is one dialog fewer for the agent when it quits
s.setValue("MainWindow/DontConfirmExit", 1024)
s.sync()
# The DICOM module creates its database on first open; do that here instead of the agent
slicer.util.selectModule("DICOM")
import DICOMLib.DICOMUtils as du
db_dir = os.path.expanduser("~/Documents/SlicerDICOMDatabase")
os.makedirs(db_dir, exist_ok=True)
du.openDatabase(db_dir)
print("FIRSTRUN: db", os.path.exists(os.path.join(db_dir, "ctkDICOM.sql")))
slicer.util.selectModule("Welcome")
# The window defaults to 1040x600; [MainWindow] RestoreGeometry=true writes the geometry back
# only on a *normal* close: slicer.util.exit() bypasses closeEvent, which is why the first
# version of this script did not remember the maximised state. So: maximise, give the window
# manager a few seconds to apply it, then go through mainWindow().close().
slicer.util.mainWindow().showMaximized()
qt.QTimer.singleShot(5000, lambda: slicer.util.mainWindow().close())
PY
DISPLAY=:0 /opt/Slicer/Slicer --no-splash --python-script /tmp/slicer_firstrun.py > /tmp/slicer_firstrun.log 2>&1 || true
grep -E "FIRSTRUN|rror" /tmp/slicer_firstrun.log | head -5
rm -f /tmp/slicer_firstrun.py

echo "--- resulting settings ---"
grep -A2 "^\[ApplicationUpdate\]" "$INI"
grep -A3 "^\[Extensions\]" "$INI" | grep AutoUpdateCheck
ls /home/user/Documents/SlicerDICOMDatabase/ 2>/dev/null | head -3
grep -q "AutoUpdateCheck=false" <(grep -A2 "^\[ApplicationUpdate\]" "$INI") && echo FIRSTRUN_OK
