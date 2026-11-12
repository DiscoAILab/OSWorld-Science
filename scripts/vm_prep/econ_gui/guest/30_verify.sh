#!/usr/bin/env bash
# ubuntu_econ_gui verification, run inside the guest through `bash -lc` (as `user`, the account the guest server runs as).
# Prints PASS/FAIL lines; exit 1 if any FAIL.
fails=0
chk() { if [ "$2" = 0 ]; then echo "[PASS] $1"; else echo "[FAIL] $1"; fails=$((fails+1)); fi; }
# the four programs and their launchers (the task configs launch exactly these)
[ "$(command -v jdemetra portfolio netlogo | wc -l)" = 3 ] && [ -x /usr/bin/run_geoda.sh ]; chk "launchers jdemetra, portfolio, netlogo, /usr/bin/run_geoda.sh" $?
[ "$(dpkg-query -W -f='${Version}' geoda 2>/dev/null)" = 1.22.1-1noble1 ]; chk "GeoDa 1.22.1-1noble1" $?
! ldd /usr/local/geoda/GeoDa | grep -q "not found"; chk "GeoDa: no missing shared library" $?
ls /opt/portfolio/plugins/name.abuchen.portfolio_0.89.0*.jar >/dev/null 2>&1; chk "Portfolio Performance 0.89.0" $?
grep -q '^jdkhome="/opt/jre-jd"' /opt/nbdemetra/etc/nbdemetra.conf; chk "JDemetra+ runs on /opt/jre-jd" $?
grep -q '^/opt/jre-jd/bin/java$' /opt/portfolio/PortfolioPerformance.ini; chk "Portfolio Performance runs on /opt/jre-jd" $?
[ -x /opt/netlogo/bin/NetLogo ] && [ -d /opt/netlogo/models ]; chk "NetLogo 7.0.4 with its models library" $?
command -v libreoffice >/dev/null && [ "$(dpkg-query -W -f='${Status}' libreoffice-calc 2>/dev/null)" = "install ok installed" ]; chk "LibreOffice Calc" $?
# no compiler anywhere on the Java side the agent's programs use
! /opt/jre-jd/bin/java --list-modules | grep -qE 'jdk\.compiler|jdk\.jshell|jdk\.attach'; chk "/opt/jre-jd has no jdk.compiler, jdk.jshell or jdk.attach module" $?
[ -z "$(command -v javac jshell java 2>/dev/null)" ]; chk "no javac/jshell/java on PATH" $?
# nothing on the desktop runs a command line
[ -z "$(command -v xfce4-terminal gnome-terminal xterm uxterm x-terminal-emulator thunar xfce4-appfinder exo-open xfce4-mime-helper xfce4-settings-manager xfce4-keyboard-settings gretl 2>/dev/null)" ]; chk "no terminal, run dialog, file manager, URL chooser, settings manager, gretl" $?
grep -q 'CustomizePanel=NONE' /etc/xdg/xfce4/kiosk/kioskrc; chk "Xfce kiosk: panel locked" $?
! grep -q 'type="string"' /etc/xdg/xfce4/xfconf/xfce-perchannel-xml/xfce4-keyboard-shortcuts.xml; chk "no keyboard shortcut starts a command" $?
x=/home/user/.config/libreoffice/4/user/registrymodifications.xcu; grep -A1 'DisableMacrosExecution' $x | grep -q true && grep -A1 'MacroSecurityLevel' $x | grep -q '>3<'; chk "LibreOffice macros disabled (level 3, execution off)" $?
[ ! -e /opt/netlogo/extensions/.bundled/py ] && [ ! -e /opt/netlogo/extensions/.bundled/sr ] && [ ! -e /opt/netlogo/netlogo-headless.sh ] && [ ! -e /opt/netlogo/behaviorsearch ]; chk "NetLogo without py/sr extensions and command-line launchers" $?
out=$(timeout 20 xdg-open https://example.com 2>&1); echo "$out" | grep -q 'no web browser'; chk "xdg-open URL: no-op handler, no chooser" $?
# grader side: root-only, reachable with sudo -n (evaluator commands)
[ "$(stat -c '%U %a' /opt/grader)" = "root 700" ] && ! ls /opt/grader >/dev/null 2>&1; chk "/opt/grader is root:root 0700, unreadable for user" $?
sudo -n test -x /opt/grader/jwsacruncher-3.9.0/bin/jwsacruncher; chk "jwsacruncher 3.9.0 in /opt/grader (sudo -n)" $?
sudo -n /opt/jre-jd/bin/java -cp "/opt/grader/ppref:/opt/portfolio/plugins/*" PPRef3 2>&1 | grep -q 'ArrayIndexOutOfBounds'; chk "PPRef3 loads against PP's jars (sudo -n)" $?
sudo -n true 2>/dev/null; chk "passwordless sudo for the evaluator commands" $?
# desktop and guest-server contract (as for ubuntu_openfoam)
pgrep -x xfce4-session >/dev/null && pgrep -x xfwm4 >/dev/null && pgrep -x xfce4-panel >/dev/null; chk "Xfce session, xfwm4 and panel running on :0" $?
[ "$(xdpyinfo 2>/dev/null | awk '/dimensions/{print $2}')" = 1920x1080 ]; chk "screen is 1920x1080" $?
command -v python >/dev/null && python -c "import importlib.metadata as m; assert m.version('pyautogui') == '0.9.54'" 2>/dev/null; chk "python has pyautogui 0.9.54" $?
grep -q 'stdin=subprocess.DEVNULL' /home/user/server/main.py; chk "guest server /execute has stdin=DEVNULL" $?
systemctl is-enabled fstrim.timer 2>/dev/null | grep -q masked; chk "fstrim.timer masked" $?
! systemctl is-active --quiet snapd 2>/dev/null && ! command -v snap >/dev/null; chk "no snapd" $?
. /etc/os-release; [ "$VERSION_ID" = 24.04 ]; chk "Ubuntu 24.04 (as the stand-in)" $?
[ "$(hostname)" = osworld ]; chk "hostname osworld" $?
echo "verify: $fails failure(s)"; exit $((fails>0))
