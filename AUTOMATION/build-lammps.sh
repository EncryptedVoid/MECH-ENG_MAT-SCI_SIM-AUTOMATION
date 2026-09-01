#!/usr/bin/env bash
# =============================================================================
#  build_lammps.sh  -  build LAMMPS to match this machine, print a run command
# =============================================================================
#  Manual-only helper: gets you a working `lmp` binary and shows how to run it.
#  No venv, no Python pipeline, no pipeline deps.
#
#  Build model (simplified):
#    - A GPU-enabled LAMMPS build ALSO runs CPU-only (the GPU is opt-in at run
#      time via -sf gpu flags). So we build the GPU version whenever the machine
#      HAS a GPU toolkit to compile against, and that single binary serves both.
#    - Building the GPU version REQUIRES the toolkit at compile time (CUDA for
#      NVIDIA, OpenCL for AMD). With no toolkit (e.g. Intel-only), a GPU build is
#      impossible, so we build CPU-only.
#    - One build directory. If an existing build is CPU-only but a GPU toolkit is
#      now available, it is UPGRADED (rebuilt as GPU) and the old one replaced.
#
#  GPU DRIVERS ARE NEVER TOUCHED. On WSL the driver lives on the Windows host;
#  installing one inside WSL breaks passthrough. Updating the driver later does
#  NOT affect an existing build (binaries link the CUDA toolkit, not the driver,
#  and driver updates are backward-compatible) - update it anytime for best
#  experience without rebuilding.
#
#  Usage:
#    ./build_lammps.sh
#    ./build_lammps.sh --force-cpu     # ignore any GPU, build/keep CPU-only
# =============================================================================
set -euo pipefail

# --- config ------------------------------------------------------------------
LAMMPS_TAG="stable"
BUILD_ROOT="$HOME/.lammps-build"
BUILD_DIR="$BUILD_ROOT/build"                # single build dir (mode-agnostic)
BIN="$BUILD_DIR/lmp"
CUDA_MIN="12.8"                              # sm_120 (Blackwell) needs >= 12.8
NV_ARCH="sm_120"                             # RTX 5050. Change if a different card.
# We enable a broad package set via LAMMPS' own CMake presets (see step 5):
#   most.cmake  = most packages that need no external libraries (KSPACE,
#                 MANYBODY, MOLECULE, RIGID, MEAM, REAXFF, QEQ, MC, MISC, ...)
#   nolib.cmake = force-off the few in "most" that DO need external libs, so the
#                 build never fails on a missing third-party dependency.
# This is what makes inputs like ZnO (buck/coul/long + PPPM, i.e. the KSPACE
# package) work. Building this many packages takes longer but is done once.
# COMMON_FLAGS below are applied AFTER the presets and so win on any overlap.
COMMON_FLAGS=(-DPKG_MANYBODY=on -DPKG_KSPACE=on -DPKG_MOLECULE=on -DPKG_RIGID=on
              -DBUILD_MPI=on -DPKG_OPENMP=on -DBUILD_EXE=on
              -DCMAKE_BUILD_TYPE=Release)

FORCE_CPU=0
[[ "${1:-}" == "--force-cpu" ]] && FORCE_CPU=1

log() { echo "[$(date +%H:%M:%S)] $*"; }

# --- 1. build tools ----------------------------------------------------------
log "Installing build tools..."
sudo apt-get update -y
sudo apt-get install -y build-essential cmake git wget libopenmpi-dev openmpi-bin

# --- 2. decide the target build mode -----------------------------------------
# TARGET_MODE = cuda | opencl | cpu   (what this machine SHOULD have)
TARGET_MODE="cpu"; GPU_DESC="none (CPU build)"; RUN_FLAGS=""

nvidia_present() {
    command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi >/dev/null 2>&1
}
amd_present() {
    { command -v rocm-smi >/dev/null 2>&1 && rocm-smi >/dev/null 2>&1; } && return 0
    # lspci fallback: whole-word AMD/Radeon, case-SENSITIVE (so 'ati' inside
    # 'integrated'/'controller' can't false-match; Intel never matches).
    command -v lspci >/dev/null 2>&1 &&
        lspci 2>/dev/null | grep -E 'VGA|3D|Display' | grep -qE '\b(AMD|Radeon)\b'
}

if [[ "$FORCE_CPU" == "1" ]]; then
    log "--force-cpu given: building CPU-only."
elif nvidia_present; then
    NV_NAME="$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1)"
    DRV_CUDA="$(nvidia-smi 2>/dev/null | grep -oE 'CUDA Version: [0-9]+\.[0-9]+' | grep -oE '[0-9]+\.[0-9]+' | head -1)"
    TARGET_MODE="cuda"
    GPU_DESC="NVIDIA $NV_NAME (driver supports CUDA ${DRV_CUDA:-unknown})"
    RUN_FLAGS="-sf gpu -pk gpu 1"
    if [[ -z "$DRV_CUDA" || "$(printf '%s\n%s\n' "$CUDA_MIN" "$DRV_CUDA" | sort -V | head -1)" != "$CUDA_MIN" ]]; then
        log "NOTE: NVIDIA driver reports CUDA ${DRV_CUDA:-unknown} (< $CUDA_MIN needed"
        log "      for $NV_ARCH). RECOMMENDED: update the GPU driver in Windows for"
        log "      best experience. You can do this anytime - it won't affect a build."
    fi
    log "GPU detected: $GPU_DESC"
elif amd_present; then
    TARGET_MODE="opencl"
    GPU_DESC="AMD GPU (using OpenCL - UNTESTED on WSL; use --force-cpu if it fails)"
    RUN_FLAGS="-sf gpu -pk gpu 1"
    log "GPU detected: $GPU_DESC"
else
    log "No NVIDIA/AMD GPU detected (Intel integrated graphics are ignored - they"
    log "are slower than CPU for MD, and have no toolkit to build against)."
    log "Building CPU-only. This is the correct, fastest option on this machine."
fi

# --- 3. inspect any existing build; decide reuse vs (re)build ----------------
existing_mode() {
    [[ -x "$BIN" ]] || { echo "none"; return; }
    local h; h="$("$BIN" -h 2>/dev/null || true)"
    if   echo "$h" | grep -qi "GPU package API:.*CUDA";   then echo "cuda"
    elif echo "$h" | grep -qi "GPU package API:.*OpenCL"; then echo "opencl"
    elif [[ -n "$h" ]];                                   then echo "cpu"
    else echo "none"; fi
}
# An existing binary is only good enough to REUSE if it also has the broad
# package set (older LAVA builds were package-poor and failed on KSPACE inputs
# like ZnO). Probe for a KSPACE style as a representative marker.
has_packages() {
    [[ -x "$BIN" ]] || return 1
    "$BIN" -h 2>/dev/null | grep -qiE '\bpppm\b|\bewald\b|buck/coul/long'
}

HAVE="$(existing_mode)"
log "Existing build: $HAVE ; target: $TARGET_MODE"

NEED_BUILD=1
if [[ "$HAVE" == "$TARGET_MODE" ]] && has_packages; then
    log "Existing build matches target and has the full package set - reusing $BIN"
    NEED_BUILD=0
elif [[ "$HAVE" == "$TARGET_MODE" ]] && ! has_packages; then
    log "Existing $HAVE build is missing packages (e.g. KSPACE); rebuilding..."
    rm -rf "$BUILD_DIR"
elif [[ "$HAVE" == "cpu" && "$TARGET_MODE" != "cpu" ]]; then
    log "Upgrading CPU-only build to $TARGET_MODE (removing old build)..."
    rm -rf "$BUILD_DIR"
elif [[ "$HAVE" != "none" && "$HAVE" != "$TARGET_MODE" ]]; then
    log "Existing build ($HAVE) != target ($TARGET_MODE); rebuilding..."
    rm -rf "$BUILD_DIR"
fi

# --- 4. per-mode toolkit + cmake GPU flags -----------------------------------
GPU_FLAGS=()
if [[ "$NEED_BUILD" == "1" ]]; then
    case "$TARGET_MODE" in
      cuda)
        ensure_cuda() { for c in /usr/local/cuda/bin /usr/local/cuda-12.8/bin; do
            [[ -x "$c/nvcc" ]] && export PATH="$c:$PATH" && return 0; done; return 1; }
        if ! command -v nvcc >/dev/null 2>&1 && ! ensure_cuda; then
            log "Installing CUDA toolkit $CUDA_MIN (userspace - safe on WSL)..."
            ( cd /tmp
              wget -q https://developer.download.nvidia.com/compute/cuda/repos/wsl-ubuntu/x86_64/cuda-keyring_1.1-1_all.deb
              sudo dpkg -i cuda-keyring_1.1-1_all.deb
              sudo apt-get update -y
              sudo apt-get install -y cuda-toolkit-12-8
              rm -f cuda-keyring_1.1-1_all.deb )
            ensure_cuda || { log "ERROR: nvcc not found after install."; exit 1; }
            grep -q '/usr/local/cuda/bin' "$HOME/.bashrc" 2>/dev/null || {
                echo 'export CUDA_HOME=/usr/local/cuda' >> "$HOME/.bashrc"
                echo 'export PATH=/usr/local/cuda/bin:$PATH' >> "$HOME/.bashrc"
                echo 'export LD_LIBRARY_PATH=/usr/local/cuda/lib64:$LD_LIBRARY_PATH' >> "$HOME/.bashrc"; }
        fi
        log "CUDA: $(nvcc --version | grep -oE 'release [0-9.]+' | head -1)"
        GPU_FLAGS=(-DPKG_GPU=on -DGPU_API=cuda -DGPU_ARCH="$NV_ARCH")
        ;;
      opencl)
        log "Installing OpenCL runtime..."
        sudo apt-get install -y ocl-icd-opencl-dev
        GPU_FLAGS=(-DPKG_GPU=on -DGPU_API=opencl)
        ;;
      cpu) GPU_FLAGS=() ;;
    esac
fi

# --- 5. build (if needed) ----------------------------------------------------
is_valid() {
    [[ -x "$BIN" ]] && "$BIN" -h >/dev/null 2>&1 || return 1
    # confirm the broad package set actually compiled in (KSPACE marker)
    if ! "$BIN" -h 2>/dev/null | grep -qiE '\bpppm\b|\bewald\b|buck/coul/long'; then
        log "WARN: build succeeded but KSPACE styles are absent - inputs needing"
        log "      long-range Coulomb (e.g. ZnO) will fail. Check preset paths."
    fi
    return 0
}
if [[ "$NEED_BUILD" == "1" ]]; then
    log "Building LAMMPS ($TARGET_MODE)..."
    mkdir -p "$BUILD_ROOT"
    if [[ ! -d "$BUILD_ROOT/lammps" ]]; then
        log "Cloning LAMMPS ($LAMMPS_TAG)..."
        git -c core.filemode=false clone -b "$LAMMPS_TAG" --depth 1 \
            https://github.com/lammps/lammps.git "$BUILD_ROOT/lammps"
    fi
    mkdir -p "$BUILD_DIR"
    # Enable a broad package set via LAMMPS' presets, then our explicit flags.
    # -C <file> initializes the CMake cache from a preset; multiple are allowed
    # and are applied in order, so nolib (turn off lib-dependent pkgs) must
    # follow most (turn them on). Our COMMON_FLAGS/GPU_FLAGS come last and win.
    PRESET_DIR="$BUILD_ROOT/lammps/cmake/presets"
    PRESET_FLAGS=()
    if [[ -f "$PRESET_DIR/most.cmake" && -f "$PRESET_DIR/nolib.cmake" ]]; then
        PRESET_FLAGS=(-C "$PRESET_DIR/most.cmake" -C "$PRESET_DIR/nolib.cmake")
        log "Using LAMMPS presets: most.cmake + nolib.cmake (broad package set)."
    else
        log "WARN: LAMMPS presets not found; building with explicit flags only."
    fi
    cmake -S "$BUILD_ROOT/lammps/cmake" -B "$BUILD_DIR" \
        "${PRESET_FLAGS[@]}" "${COMMON_FLAGS[@]}" "${GPU_FLAGS[@]}"
    cmake --build "$BUILD_DIR" -j"$(nproc)"
    is_valid || { log "ERROR: build finished but $BIN is not runnable."; exit 1; }
    log "Build OK: $BIN"
fi

# --- 6. print how to run manually --------------------------------------------
cat <<EOF

=============================================================================
 LAMMPS is ready.
=============================================================================
  Mode        : $TARGET_MODE
  Hardware    : $GPU_DESC
  Binary      : $BIN

  Run a simulation manually (edit paths to your inputs):

     cd <folder with your input files>
     mpiexec -n 4 "$BIN" $RUN_FLAGS \\
         -in <configuration-file> \\
         -var infile <structure-file> \\
         -var seed 1
EOF
[[ "$TARGET_MODE" != "cpu" ]] && echo "  Watch GPU live in another terminal:  watch -n 1 nvidia-smi"
echo "============================================================================="
