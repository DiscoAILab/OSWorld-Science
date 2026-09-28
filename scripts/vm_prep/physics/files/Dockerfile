# OSWorld-Science · OpenFOAM desktop guest  (image v2512-pv5.11.2, revision r3 — see env/README.md)
# Base: official OpenCFD image (Ubuntu 24.04.3, OpenFOAM v2512 at /usr/lib/openfoam/openfoam2512).
# Adds: Xfce desktop on Xvfb (1920x1080), x11vnc + noVNC for humans, ParaView 5.11.2 (binary tarball),
#       gnuplot (qt/x11/wxt terminals), python3 scipy/matplotlib/pandas, common CLI tools, image/PDF viewers,
#       the OSWorld guest HTTP server (:5000, pyautogui) running as user `user`, supervisord as PID 1.
FROM opencfd/openfoam-default:2512
ARG DEBIAN_FRONTEND=noninteractive
ENV LANG=C.UTF-8 LC_ALL=C.UTF-8 TZ=UTC

# archive.ubuntu.com is slow from us-east-2: point apt at the EC2 regional mirror for the build only and
# restore the stock source in the same layer (the guest has no network at run time anyway).
ARG APT_MIRROR=http://us-east-2.ec2.archive.ubuntu.com/ubuntu
RUN sed -i "s|http://archive.ubuntu.com/ubuntu|${APT_MIRROR}|g" /etc/apt/sources.list.d/ubuntu.sources \
    && apt-get update && apt-get install -y --no-install-recommends \
      xvfb x11vnc novnc websockify supervisor dbus-x11 at-spi2-core xauth \
      xfce4-session xfwm4 xfce4-panel xfdesktop4 xfce4-settings xfconf xfce4-terminal thunar mousepad \
      xfce4-appfinder xfce4-taskmanager ristretto atril xdg-utils shared-mime-info desktop-file-utils libglib2.0-bin \
      elementary-xfce-icon-theme greybird-gtk-theme tango-icon-theme adwaita-icon-theme fonts-dejavu-core \
      python3 python3-venv python3-pip python3-dev python3-tk python3-pyatspi python3-xlib python3-gi gir1.2-atspi-2.0 \
      python3-numpy python3-scipy python3-matplotlib python3-pandas \
      scrot xdotool wmctrl xclip x11-xserver-utils x11-utils \
      libgl1 libglu1-mesa libgl1-mesa-dri libegl1 libopengl0 libxrender1 libxt6 libxcursor1 libxkbcommon-x11-0 \
      libxcb-xinerama0 libxcb-cursor0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 libxcb-randr0 libxcb-render-util0 \
      libxcb-shape0 libxcb-xkb1 libxcb-xinput0 libxcb-util1 libxcb-shm0 libxcb-sync1 libxcb-xfixes0 libxcb-glx0 \
      libxi6 libsm6 libice6 libxrandr2 libxss1 libdbus-1-3 libfontconfig1 libfreetype6 libglib2.0-0 libxext6 libx11-xcb1 \
      sudo curl ca-certificates less nano vim bc gnuplot-qt procps psmisc \
      git unzip zip bzip2 xz-utils file jq tree htop iproute2 iputils-ping time \
    && rm -rf /var/lib/apt/lists/* \
    && sed -i "s|${APT_MIRROR}|http://archive.ubuntu.com/ubuntu|g" /etc/apt/sources.list.d/ubuntu.sources

# ParaView 5.11.2 (official binary). The tarball comes from the build context (see build.sh) and is
# bind-mounted for this step only, so the image carries exactly one ParaView layer (no ADD+mv double copy).
RUN --mount=type=bind,source=ParaView-5.11.2-MPI-Linux-Python3.9-x86_64.tar.gz,target=/tmp/paraview.tar.gz \
    mkdir -p /opt/paraview \
    && tar --strip-components=1 -C /opt/paraview -xzf /tmp/paraview.tar.gz \
    && ln -s /opt/paraview/bin/paraview /usr/local/bin/paraview \
    && ln -s /opt/paraview/bin/pvpython /usr/local/bin/pvpython \
    && ln -s /opt/paraview/bin/pvbatch  /usr/local/bin/pvbatch \
    && cp -r /opt/paraview/share/icons/hicolor/. /usr/share/icons/hicolor/ \
    && gtk-update-icon-cache -f -q /usr/share/icons/hicolor || true

# The OSWorld convention: user `user`, password `password`, passwordless sudo.
# .bashrc: OpenFOAM env, software GL, ParaView on PATH, and the shell-history instrumentation that
# harness/runner.py also appends at setup (baked here so history works even without the harness).
RUN useradd -m -s /bin/bash -G sudo user && echo 'user:password' | chpasswd \
    && echo 'user ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/user && chmod 0440 /etc/sudoers.d/user \
    && mkdir -p /home/user/work /home/user/Desktop /home/user/.config/ParaView \
    && printf '%s\n' 'source /usr/lib/openfoam/openfoam2512/etc/bashrc' \
                     'export LIBGL_ALWAYS_SOFTWARE=1' \
                     'export PATH=$PATH:/opt/paraview/bin' \
                     '' \
                     '# --- shell history instrumentation (same block harness/runner.py appends at setup; idempotent) ---' \
                     'export HISTFILE="$HOME/.bash_history"' \
                     'shopt -s histappend' \
                     'export HISTTIMEFORMAT="%F %T "' \
                     'export HISTSIZE=100000 HISTFILESIZE=200000' \
                     'export PROMPT_COMMAND="history -a${PROMPT_COMMAND:+;$PROMPT_COMMAND}"' \
                     '' \
                     '# xfce4-terminal starts a NON-login shell, so /etc/profile.d is not read there' \
                     'export MPLBACKEND="${MPLBACKEND:-Agg}"' \
                     'export GNUPLOT_PAGER="${GNUPLOT_PAGER:-cat}"' >> /home/user/.bashrc

# Keep XDG_*_HOME at the real home for every login shell: the base image's /etc/profile.d/openfoam-99run.sh
# would otherwise redirect them to /tmp/.home.$USER whenever DISPLAY is set, so programs started through the
# guest server (bash -lc) and from the Xfce session would read different config dirs (ParaView settings!).
COPY etc/00-osworld-xdg.sh /etc/profile.d/00-osworld-xdg.sh
# MPLBACKEND=Agg plus a non-interactive PAGER for every login shell (r3): otherwise plt.show() and
# gnuplot's pager block until the guest server's 120 s cap and /execute returns HTTP 500.
COPY etc/01-osworld-tools.sh /etc/profile.d/01-osworld-tools.sh

# Guest HTTP server (vendored from xlang-ai/OSWorld, Apache-2.0). Its python deps go into the SYSTEM
# python: the agent's controller runs `python -c "import pyautogui; ..."` on the guest, so `python` must
# resolve to an interpreter that has pyautogui (OSWorld guests are set up the same way).
COPY guest_server/ /home/user/server/
RUN pip install --no-cache-dir --break-system-packages \
        "pyautogui==0.9.54" "pillow>=10" flask lxml requests pynput "python-xlib==0.33" \
    && ln -sf /usr/bin/python3 /usr/local/bin/python \
    && rm -rf /root/.cache

# r3 additions kept in their own apt layer on purpose: touching the big apt layer above would also
# invalidate the 1.85 GB ParaView layer on every rebuild, and the build host's root disk is small.
#   tumbler(-plugins-extra) — without a thumbnailing daemon Ristretto/Thunar pop a modal
#   "The thumbnailer-service can not be reached ... Install Tumbler" every time an image is opened,
#   which a screenshot-driven agent reads as "the image viewer is broken" (r2 review §4.1).
RUN sed -i "s|http://archive.ubuntu.com/ubuntu|${APT_MIRROR}|g" /etc/apt/sources.list.d/ubuntu.sources \
    && apt-get update && apt-get install -y --no-install-recommends tumbler tumbler-plugins-extra \
    && rm -rf /var/lib/apt/lists/* \
    && sed -i "s|${APT_MIRROR}|http://archive.ubuntu.com/ubuntu|g" /etc/apt/sources.list.d/ubuntu.sources

# No browser / no mailer in this image: a no-op wrapper registered as the http/https/mailto scheme
# handler and (via a thin exit-0 wrapper, see bin/) as the Xfce WebBrowser/MailReader helper, so
# xdg-open <URL>, exo-open, gio open and in-app "Read Online" links print one line on stderr and open
# no window, instead of raising the untitled "Failed to execute default Web Browser. Input/output
# error." modal and blocking until it is dismissed (r2 review §4.2).
COPY bin/osworld-no-browser /usr/local/bin/osworld-no-browser
COPY bin/osworld-no-browser-xfce-helper /usr/local/bin/osworld-no-browser-xfce-helper
COPY desktop/osworld-no-browser.desktop /usr/share/applications/osworld-no-browser.desktop
COPY desktop/xfce4-helper-no-browser.desktop /usr/share/xfce4/helpers/osworld-no-browser.desktop
COPY desktop/xfce4-helper-no-mailer.desktop /usr/share/xfce4/helpers/osworld-no-mailer.desktop
COPY desktop/xfce4-helpers.rc /etc/xdg/xfce4/helpers.rc
COPY desktop/ristretto.xml /etc/xdg/xfce4/xfconf/xfce-perchannel-xml/ristretto.xml
RUN chmod 0755 /usr/local/bin/osworld-no-browser /usr/local/bin/osworld-no-browser-xfce-helper

# Desktop: shortcuts, ParaView menu entry with .foam/VTK MIME associations, Xfce panel without the dead
# Web Browser launcher, hidden Web Browser / Mail Reader menu entries (no browser/mailer installed),
# ParaView Welcome dialog pre-dismissed for `user`.
COPY desktop/paraview.desktop /usr/share/applications/paraview.desktop
COPY desktop/openfoam-mime.xml /usr/share/mime/packages/openfoam.xml
COPY desktop/mimeapps.list /etc/xdg/mimeapps.list
COPY desktop/thunar-uca.xml /etc/xdg/Thunar/uca.xml
COPY desktop/xfce4-panel-default.xml /etc/xdg/xfce4/panel/default.xml
COPY desktop/xfce-terminal.desktop /home/user/Desktop/Terminal.desktop
COPY desktop/ParaView5.11.2.ini /home/user/.config/ParaView/ParaView5.11.2.ini
RUN update-mime-database /usr/share/mime \
    && update-desktop-database /usr/share/applications \
    && sed -i '/^\[Desktop Entry\]/a NoDisplay=true' /usr/share/applications/xfce4-web-browser.desktop \
                                                     /usr/share/applications/xfce4-mail-reader.desktop \
                                                     /usr/share/applications/xfce4-session-logout.desktop \
                                                     /usr/share/applications/x11vnc.desktop \
    && cp /usr/share/applications/paraview.desktop /home/user/Desktop/ParaView.desktop \
    && chmod +x /home/user/Desktop/*.desktop && chown -R user:user /home/user

COPY supervisord.conf /etc/supervisor/conf.d/guest.conf
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

# Belt-and-braces for the two blocking defaults (also in /etc/profile.d/01-osworld-tools.sh, ~/.bashrc and
# supervisord.conf): image-level ENV reaches every process, including non-interactive non-login shells
# such as `docker exec bash -c` and anything /execute spawns without a login shell. Kept last so it does
# not invalidate the layer cache. Users can still override per command (MPLBACKEND=TkAgg python3 ...).
ENV MPLBACKEND=Agg GNUPLOT_PAGER=cat

EXPOSE 5000 5900 8006
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["/usr/bin/supervisord", "-n", "-c", "/etc/supervisor/supervisord.conf"]
