# Everything the stm snapshot and its grader image pin, in one place (sourced by the scripts of this directory).
# Change a pin here, rebuild both, and grade an export made in the guest inside the image (README.md).

# The delivered STM-Bench commit: the instrument in the guest and the grader image are built from the same one,
# because a replay is only the recorded episode when code and numerics are identical.
# Set to the delivery commit (1740614e53db...); override with the environment.
STM_COMMIT=${STM_COMMIT:-1740614e53db615b025013f974bd36a37ed1f823}
STM_REPO_URL=${STM_REPO_URL:-https://github.com/HamsterPark/STM-Bench.git}

# python-build-standalone (astral-sh), CPython 3.13 "install_only" build for glibc x86-64; the sha256 is the release's
# SHA256SUMS line (also GitHub's asset digest). Not the Ubuntu 22.04 python3 (3.10): STM-Bench needs >= 3.13.
PY_RELEASE=${PY_RELEASE:-20261110}
PY_VERSION=${PY_VERSION:-3.13.18}
PY_SHA256=${PY_SHA256:-50c774f6674740739f791c05a9177ed03e2fe02abebda9bca3512eb7309b37cc}
PY_URL=${PY_URL:-https://github.com/astral-sh/python-build-standalone/releases/download/${PY_RELEASE}/cpython-${PY_VERSION}%2B${PY_RELEASE}-x86_64-unknown-linux-gnu-install_only.tar.gz}

# numpy, scipy and PyYAML come from the repository's deploy/osworld/grader/requirements.txt (the grader image installs
# the same file). The console's Qt: the version the console was developed and its layout tuned with
# (PySide6_Essentials 6.12.0); Ubuntu 22.04's glibc 2.35 satisfies its manylinux_2_34 wheels.
PYSIDE_PIN=${PYSIDE_PIN:-PySide6_Essentials==6.12.0 shiboken6==6.12.0}

# The account the agent works as in the OSWorld base image, and the console's service account.
AGENT_USER=${AGENT_USER:-user}
STM_USER=${STM_USER:-stm}
