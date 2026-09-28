# osworld-openfoam guest: pin the XDG base dirs to the real home before /etc/profile.d/openfoam-99run.sh
# runs (it only sets them when empty and would otherwise redirect to /tmp/.home.$USER whenever DISPLAY is
# set). Result: ParaView/Thunar/... read the same ~/.config regardless of how they were started.
if [ -n "$HOME" ]; then
    [ -n "$XDG_CONFIG_HOME" ] || export XDG_CONFIG_HOME="$HOME/.config"
    [ -n "$XDG_CACHE_HOME" ]  || export XDG_CACHE_HOME="$HOME/.cache"
    [ -n "$XDG_DATA_HOME" ]   || export XDG_DATA_HOME="$HOME/.local/share"
    [ -n "$XDG_STATE_HOME" ]  || export XDG_STATE_HOME="$HOME/.local/state"
fi
if [ -z "$XDG_RUNTIME_DIR" ]; then
    export XDG_RUNTIME_DIR="/tmp/runtime-${USER:-$(id -un)}"
    mkdir -p -m 0700 "$XDG_RUNTIME_DIR" 2>/dev/null
fi
