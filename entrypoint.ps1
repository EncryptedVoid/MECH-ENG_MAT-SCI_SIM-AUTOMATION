<#
    LAVA — Windows entrypoint (entrypoint.ps1)
    ------------------------------------------
    Run this SAME script every time. It figures out what still needs doing:

      * Fresh machine   -> installs Git (if missing), installs WSL + Ubuntu-22.04,
                           drops a copy of itself on the Desktop, then asks you to
                           REBOOT and run it again.
      * After reboot    -> finishes Ubuntu's first-time user setup if needed,
                           moves the cloned repo into WSL (~/LAVA), then launches.
      * Every run after -> skips all setup and just opens WSL and runs the app.

    You never need a different script. Just re-run this one.

    NOTE: This script relaunches itself elevated and with an execution-policy
    bypass automatically, so you can start it however is convenient.
#>

# -----------------------------------------------------------------------------
# 0. Self-bootstrap: execution policy + administrator elevation
#    (Addresses: unsigned-script refusal, and "run as admin" never being checked)
# -----------------------------------------------------------------------------
# We do this BEFORE setting ErrorActionPreference so a relaunch is clean.

function Test-IsAdmin {
    try {
        $id = [Security.Principal.WindowsIdentity]::GetCurrent()
        $pr = New-Object Security.Principal.WindowsPrincipal($id)
        return $pr.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    } catch { return $false }
}

# Figure out where THIS script lives (needed to relaunch it).
$SelfPath = $PSCommandPath
if (-not $SelfPath) { $SelfPath = $MyInvocation.MyCommand.Path }

if (-not (Test-IsAdmin)) {
    if (-not $SelfPath) {
        Write-Host "Please re-run this script as Administrator." -ForegroundColor Yellow
        Read-Host "Press Enter to exit"
        exit 1
    }
    Write-Host "Requesting administrator rights..." -ForegroundColor Cyan
    # Relaunch elevated, bypassing execution policy for this process only.
    $psExe = (Get-Process -Id $PID).Path
    if (-not $psExe) { $psExe = 'powershell.exe' }
    try {
        Start-Process -FilePath $psExe `
            -ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-File', "`"$SelfPath`"") `
            -Verb RunAs
    } catch {
        Write-Host "Could not elevate automatically. Right-click PowerShell and 'Run as administrator', then run this script again." -ForegroundColor Yellow
        Read-Host "Press Enter to exit"
        exit 1
    }
    exit 0
}

# -----------------------------------------------------------------------------
# From here on we are elevated. Do NOT hard-Stop on native exe stderr; we drive
# control flow off $LASTEXITCODE explicitly. Keep Continue so a stray stderr
# line from wsl.exe/git doesn't abort the whole run.
# (Addresses: ErrorActionPreference='Stop' aborting on native-call noise)
# -----------------------------------------------------------------------------
$ErrorActionPreference = 'Continue'

# ---- Settings ---------------------------------------------------------------
$Distro       = 'Ubuntu-22.04'
$RepoFolder   = 'LAVA_LAMMPS-Automation-Validation-Aid'   # name git clone creates
$RepoUrl      = "https://github.com/EncryptedVoid/$RepoFolder.git"
$WslTarget    = 'LAVA'                                     # -> ~/LAVA inside WSL
$DesktopCopy  = Join-Path ([Environment]::GetFolderPath('Desktop')) 'entrypoint.ps1'
# -----------------------------------------------------------------------------

function Write-Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Write-Ok($msg)   { Write-Host "    $msg"   -ForegroundColor Green }
function Write-Warn($msg) { Write-Host "    $msg"   -ForegroundColor Yellow }
function Write-Err($msg)  { Write-Host "    $msg"   -ForegroundColor Red }

function Pause-Exit($code) {
    Write-Host ""
    Read-Host "Press Enter to close this window"
    exit $code
}

# Always keep a copy on the Desktop so a non-technical user can get back in later.
# (Addresses: OneDrive-redirected Desktop / missing folder)
function Ensure-DesktopCopy {
    try {
        $self = $SelfPath
        if (-not $self -or -not (Test-Path $self)) { return }

        $desktopDir = Split-Path -Parent $DesktopCopy
        if (-not (Test-Path $desktopDir)) {
            # OneDrive redirection or unusual profile — try the OneDrive Desktop.
            $od = $env:OneDrive
            if ($od -and (Test-Path (Join-Path $od 'Desktop'))) {
                $script:DesktopCopy = Join-Path (Join-Path $od 'Desktop') 'entrypoint.ps1'
                $desktopDir = Split-Path -Parent $script:DesktopCopy
            }
        }
        if (-not (Test-Path $desktopDir)) { return }  # give up quietly; not critical

        $selfResolved = (Resolve-Path $self).Path
        $destResolved = $null
        if (Test-Path $script:DesktopCopy) {
            $destResolved = (Resolve-Path $script:DesktopCopy).Path
        }
        if ($selfResolved -ne $destResolved) {
            Copy-Item -Path $self -Destination $script:DesktopCopy -Force
            Write-Ok "Saved a copy to your Desktop: entrypoint.ps1"
        }
    } catch {
        Write-Warn "Could not copy to Desktop (not critical): $($_.Exception.Message)"
    }
}

# ---- Git bootstrap ----------------------------------------------------------
# (Addresses: fresh Windows has no git, so README's `git clone` one-liner fails
#  and the repo may never be present next to the Desktop copy.)
function Test-GitPresent {
    $g = Get-Command git.exe -ErrorAction SilentlyContinue
    return [bool]$g
}

function Install-Git {
    Write-Warn "Git is not installed. Installing it now..."
    # Prefer winget (present on Win 11). Fall back to a clear manual message.
    $winget = Get-Command winget.exe -ErrorAction SilentlyContinue
    if ($winget) {
        winget install --id Git.Git -e --source winget `
            --accept-source-agreements --accept-package-agreements
        # winget doesn't refresh PATH for the current process; add the usual path.
        $gitCmd = Join-Path $env:ProgramFiles 'Git\cmd'
        if ((Test-Path $gitCmd) -and ($env:Path -notlike "*$gitCmd*")) {
            $env:Path = "$env:Path;$gitCmd"
        }
    }
    if (-not (Test-GitPresent)) {
        Write-Err "Git could not be installed automatically."
        Write-Err "Please install Git from https://git-scm.com/download/win , then re-run this script."
        Pause-Exit 1
    }
    Write-Ok "Git is installed."
}

# ---- WSL detection ----------------------------------------------------------
# (Addresses: `wsl --status` returning 0 on a machine with only the stub.)
# We treat WSL as "really present" only if a version query succeeds AND at
# least one distro is registered, OR the kernel/version output looks real.
function Test-WslInstalled {
    # `wsl --version` exists on a properly installed modern WSL and prints
    # kernel/build info. The legacy stub does NOT support it and errors out.
    $null = & wsl.exe --version 2>$null
    if ($LASTEXITCODE -eq 0) { return $true }

    # Fallback: `wsl -l -q` listing an actual distro means WSL is functional.
    $listed = & wsl.exe -l -q 2>$null
    if ($LASTEXITCODE -eq 0 -and $listed) {
        $names = ($listed -split "`r?`n") | ForEach-Object { $_.Trim() } | Where-Object { $_ }
        if ($names.Count -gt 0) { return $true }
    }
    return $false
}

function Get-InstalledDistros {
    $out = & wsl.exe -l -q 2>$null
    if ($LASTEXITCODE -ne 0 -or -not $out) { return @() }
    # wsl -l -q output on some builds is UTF-16 with stray NULs; strip them.
    return ($out -split "`r?`n") |
        ForEach-Object { ($_ -replace "`0", '').Trim() } |
        Where-Object { $_ }
}

# Is the target distro installed AND initialised (has a non-root default user)?
function Test-DistroReady {
    if ((Get-InstalledDistros) -notcontains $Distro) { return $false }
    $whoami = & wsl.exe -d $Distro -- bash -lc 'whoami' 2>$null
    if ($LASTEXITCODE -eq 0 -and $whoami) {
        $whoami = ($whoami -replace "`0", '').Trim()
        if ($whoami -and $whoami -ne 'root') { return $true }
    }
    return $false
}

# =============================================================================
# 1. housekeeping
# =============================================================================
Ensure-DesktopCopy

# =============================================================================
# 2. Git present?  (needed so the repo actually exists on disk)
# =============================================================================
Write-Step "Checking for Git ..."
if (-not (Test-GitPresent)) { Install-Git } else { Write-Ok "Git is available." }

# =============================================================================
# 3. WSL + distro
# =============================================================================
Write-Step "Checking for WSL and $Distro ..."

$wslPresent = Test-WslInstalled

if (-not (Test-DistroReady)) {

    if (-not $wslPresent) {
        Write-Warn "WSL is not installed yet. Installing WSL + $Distro ..."
        Write-Warn "This needs administrator rights (you have them) and a REBOOT afterwards."
        & wsl.exe --install -d $Distro
        if ($LASTEXITCODE -ne 0) {
            Write-Warn "Named install did not succeed; installing base WSL, then the distro separately..."
            & wsl.exe --install
            & wsl.exe --install -d $Distro
        }
        Write-Host ""
        Write-Warn "============================================================"
        Write-Warn " WSL was installed. Please REBOOT your computer now."
        Write-Warn " After rebooting, run this SAME entrypoint.ps1 again."
        Write-Warn " (There's now a copy on your Desktop.)"
        Write-Warn "============================================================"
        Pause-Exit 0
    }

    # WSL exists but our distro is missing or not yet initialised.
    if ((Get-InstalledDistros) -notcontains $Distro) {
        Write-Warn "Installing $Distro ..."
        & wsl.exe --install -d $Distro
        if ($LASTEXITCODE -ne 0) {
            Write-Err "Could not install $Distro automatically."
            Write-Err "Open the Microsoft Store, install 'Ubuntu 22.04.x LTS', launch it once"
            Write-Err "to create your username/password, then re-run this script."
            Pause-Exit 1
        }
    }

    Write-Host ""
    Write-Warn "============================================================"
    Write-Warn " $Distro needs its first-time setup."
    Write-Warn " A console will open asking you to create a Linux USERNAME"
    Write-Warn " and PASSWORD. Write the password down — you'll need it."
    Write-Warn " Once you see a green Linux prompt (e.g. name@host:~$),"
    Write-Warn " type  exit  and press Enter, then run this entrypoint.ps1 again."
    Write-Warn "============================================================"
    Write-Host ""
    Read-Host "Press Enter to open the Ubuntu setup window"
    # Launch interactively so the OOBE username/password prompt appears.
    & wsl.exe -d $Distro
    Pause-Exit 0
}

Write-Ok "$Distro is installed and ready."

# =============================================================================
# 4. Move the repo into WSL (first run only)
# =============================================================================
Write-Step "Making sure LAVA is on the Linux filesystem (~/$WslTarget) ..."

$scriptDir = Split-Path -Parent $SelfPath

# Does ~/LAVA already exist inside WSL with run.py?
$null = & wsl.exe -d $Distro -- bash -lc "test -f `$HOME/$WslTarget/run.py" 2>$null
$haveRepo = ($LASTEXITCODE -eq 0)

if (-not $haveRepo) {
    # Find (or create) the Windows-side clone.
    $winRepo = $null
    if (Test-Path (Join-Path $scriptDir 'run.py')) {
        $winRepo = $scriptDir
    } else {
        $candidate = Join-Path $scriptDir $RepoFolder
        if (Test-Path (Join-Path $candidate 'run.py')) { $winRepo = $candidate }
    }

    # If we still don't have it (e.g. user double-clicked the Desktop copy),
    # clone it fresh next to the script. We have Git guaranteed by step 2.
    if (-not $winRepo) {
        Write-Warn "No local copy of the project found. Cloning it now..."
        $cloneParent = $scriptDir
        $cloneTarget = Join-Path $cloneParent $RepoFolder
        if (-not (Test-Path (Join-Path $cloneTarget 'run.py'))) {
            & git.exe clone $RepoUrl $cloneTarget
        }
        if (Test-Path (Join-Path $cloneTarget 'run.py')) {
            $winRepo = $cloneTarget
        }
    }

    if (-not $winRepo) {
        Write-Err "Couldn't obtain the project on Windows (no run.py found and clone failed)."
        Write-Err "Check your internet connection and that GitHub is reachable, then re-run."
        Pause-Exit 1
    }

    Write-Ok "Found repo on Windows: $winRepo"

    # Translate the Windows path to a path WSL can read. Guard against paths
    # WSL can't mount (spaces/non-ASCII are fine via wslpath; unmounted drives
    # are not). (Addresses: fragile wslpath/cp with no real error handling.)
    $wslSrcRaw = & wsl.exe -d $Distro -- wslpath -a "$winRepo" 2>$null
    $wslSrc = $null
    if ($LASTEXITCODE -eq 0 -and $wslSrcRaw) {
        $wslSrc = ($wslSrcRaw -replace "`0", '').Trim()
    }
    if (-not $wslSrc) {
        Write-Err "WSL could not translate this Windows path: $winRepo"
        Write-Err "Clone the project somewhere simple like C:\LAVA and run it from there."
        Pause-Exit 1
    }

    # Confirm WSL can actually SEE the source before copying.
    $null = & wsl.exe -d $Distro -- bash -lc "test -f '$wslSrc/run.py'" 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Err "WSL cannot read the project files at: $wslSrc"
        Write-Err "This usually means the drive isn't auto-mounted in WSL."
        Write-Err "Move the clone to your C: drive (e.g. C:\LAVA) and re-run this script."
        Pause-Exit 1
    }

    Write-Ok "Copying into WSL as ~/$WslTarget (this can take a moment) ..."
    & wsl.exe -d $Distro -- bash -lc "mkdir -p `$HOME && rm -rf `$HOME/$WslTarget && cp -r '$wslSrc' `$HOME/$WslTarget && chmod +x `$HOME/$WslTarget/entrypoint.sh 2>/dev/null; test -f `$HOME/$WslTarget/run.py"
    if ($LASTEXITCODE -ne 0) {
        Write-Err "Copying the project into WSL failed."
        Write-Err "Check you have free disk space, then re-run this script."
        Pause-Exit 1
    }
    Write-Ok "Repo is now at ~/$WslTarget inside $Distro."
} else {
    Write-Ok "Repo already present at ~/$WslTarget — skipping copy."
}

# =============================================================================
# 5. Launch
# =============================================================================
Write-Step "Launching LAVA Linux Entrypoint..."
# Normalise line endings on the shell script (in case it was checked out as CRLF),
# make sure it's executable, then run it. WSLg shows the Tkinter window on Win 11.
& wsl.exe -d $Distro -- bash -lc "cd `$HOME/$WslTarget && sed -i 's/\r$//' entrypoint.sh 2>/dev/null; chmod +x entrypoint.sh 2>/dev/null; ./entrypoint.sh"

$code = $LASTEXITCODE
Write-Host ""
if ($code -eq 0) { Write-Ok "LAVA exited normally." }
else { Write-Warn "LAVA exited with code $code. Check the Log box / SESSION log for details." }

Pause-Exit $code
