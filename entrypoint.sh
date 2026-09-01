#!/usr/bin/env bash
#
# LAVA — Linux / WSL entrypoint (entrypoint.sh)
# ---------------------------------------------
# Run this every time on Linux (or let entrypoint.ps1 call it on Windows/WSL).
# It keeps packages current, makes sure LAVA's dependencies are present, then
# starts the app. Safe to re-run — everything here is idempotent.
#
# On plain Linux you can bootstrap the whole thing in one line:
#   git clone https://github.com/EncryptedVoid/LAVA_LAMMPS-Automation-Validation-Aid.git \
#     && cd LAVA_LAMMPS-Automation-Validation-Aid && chmod +x entrypoint.sh && ./entrypoint.sh

set -euo pipefail

say()  { printf '\n==> %s\n' "$*"; }
ok()   { printf '    %s\n' "$*"; }
warn() { printf '    [!] %s\n' "$*" >&2; }

# Move into the LAVA folder — the one that actually contains run.py.
# Preference order: the standard install location (~/LAVA, where entrypoint.ps1
# puts it), then the directory this script lives in. This keeps the launch
# correct even if the script is run from a copy or via a symlink.
# SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
# if   [ -f "$HOME/LAVA/run.py" ]; then cd "$HOME/LAVA"
# elif [ -f "$SCRIPT_DIR/run.py" ]; then cd "$SCRIPT_DIR"
# else
#    warn "Could not find run.py in ~/LAVA or $SCRIPT_DIR."
#    warn "Make sure this script is in the LAVA folder (next to run.py)."
#    exit 1
# fi
# ok "Working in: $(pwd)"

# --- 1. Detect the package manager ------------------------------------------
if   command -v apt-get >/dev/null 2>&1; then PM=apt
elif command -v dnf     >/dev/null 2>&1; then PM=dnf
elif command -v pacman  >/dev/null 2>&1; then PM=pacman
else
    warn "No supported package manager (apt/dnf/pacman) found."
    warn "Install python3, python3-pip, python3-tk and git manually, then re-run."
    PM=none
fi

# sudo only if we're not already root.
SUDO=''
if [ "$(id -u)" -ne 0 ]; then SUDO='sudo'; fi

# --- 2. Update + upgrade, then ensure system packages -----------------------
SYS_PKGS_APT=(python3 python3-pip python3-tk git)
SYS_PKGS_DNF=(python3 python3-pip python3-tkinter git)
SYS_PKGS_PAC=(python python-pip tk git)

case "$PM" in
  apt)
    say "Updating and upgrading packages (apt) ..."
    $SUDO apt-get update -y
    $SUDO DEBIAN_FRONTEND=noninteractive apt-get upgrade -y
    say "Ensuring system packages: ${SYS_PKGS_APT[*]}"
    $SUDO DEBIAN_FRONTEND=noninteractive apt-get install -y "${SYS_PKGS_APT[@]}"
    ;;
  dnf)
    say "Updating and upgrading packages (dnf) ..."
    $SUDO dnf upgrade -y || true
    say "Ensuring system packages: ${SYS_PKGS_DNF[*]}"
    $SUDO dnf install -y "${SYS_PKGS_DNF[@]}"
    ;;
  pacman)
    say "Updating and upgrading packages (pacman) ..."
    $SUDO pacman -Syu --noconfirm
    say "Ensuring system packages: ${SYS_PKGS_PAC[*]}"
    $SUDO pacman -S --needed --noconfirm "${SYS_PKGS_PAC[@]}"
    ;;
  none)
    warn "Skipping system package step."
    ;;
esac

# --- 3. Python helpers LAVA needs -------------------------------------------
# psutil (CPU/RAM, required) · plotly (interactive report) · matplotlib+numpy (PNG backups)
PY_PKGS=(psutil plotly matplotlib numpy)
say "Ensuring Python packages: ${PY_PKGS[*]}"

# --break-system-packages is needed on modern Debian/Ubuntu (PEP 668). Fall back
# gracefully if the flag isn't supported on this pip.
if pip3 install --help 2>/dev/null | grep -q -- '--break-system-packages'; then
    pip3 install --break-system-packages --upgrade "${PY_PKGS[@]}"
else
    pip3 install --upgrade "${PY_PKGS[@]}"
fi

# --- 4. Launch LAVA ----------------------------------------------------------
# We're already in the folder containing run.py (guaranteed above).
say "Starting LAVA Program!"
cd AUTOMATION/
exec python3 run.py &
