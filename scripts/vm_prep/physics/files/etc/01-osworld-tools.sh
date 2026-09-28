# osworld-openfoam guest: defaults that keep common plotting/CLI tools from blocking a
# non-interactive caller (the guest server's /execute has a 120 s cap; a blocked command means
# HTTP 500 and an agent that concludes the tool is broken).
#
# MPLBACKEND=Agg  - /execute exports DISPLAY=:0, so matplotlib would pick TkAgg and the very common
#                   "savefig(...); plt.show()" script would block until the cap and return 500 even
#                   though the PNG was already written. Also set as an image-level ENV and in
#                   supervisord.conf. Override per command if you really want a GUI figure:
#                   MPLBACKEND=TkAgg python3 myplot.py
# PAGER / GNUPLOT_PAGER - gnuplot 6 pages long `set term` / `help` output itself and stops on
#                   "Press return for more:"; the same applies to git/man reaching for `less`.
#                   gnuplot 6.0.0 honours PAGER (its binary has no GNUPLOT_PAGER string), so set
#                   both, but ONLY for non-interactive shells, so that a human/agent typing `man`
#                   or `git log` in xfce4-terminal still gets a real pager.
export MPLBACKEND="${MPLBACKEND:-Agg}"
export GNUPLOT_PAGER="${GNUPLOT_PAGER:-cat}"
case $- in
    *i*) ;;
      *) export PAGER="${PAGER:-cat}" ;;
esac
