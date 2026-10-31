# Install Trillion on Windows (Windows PowerShell 5.1 or later):
#
#   irm https://raw.githubusercontent.com/HelloTrillion/trillion/main/install.ps1 | iex
#
# It installs uv (Astral's Python tool manager) if you don't have it, installs
# Trillion with uv, puts it on your PATH, then runs `trillion setup`, which
# prepares the data folder, checks this machine and opens the app.
# Running it again upgrades Trillion. It needs no administrator rights.
#
# Options. Piped into iex, use the environment variables; run as a file
# (.\install.ps1 -NoSetup), the parameters work too:
#   -Version X     install Trillion X instead of the latest   $env:TRILLION_VERSION = "X"
#   -Wheel PATH    install this wheel file or URL instead      $env:TRILLION_WHEEL = "PATH"
#   -NoSetup       install only; don't run `trillion setup`    $env:TRILLION_NO_SETUP = "1"
#   -Help          show this help
#
# macOS and Linux: use install.sh instead (see the README).

param(
    [string]$Version = $env:TRILLION_VERSION,
    [string]$Wheel = $env:TRILLION_WHEEL,
    [switch]$NoSetup,
    [switch]$Help
)

function Write-TrillionStep([string]$Message) {
    Write-Host "trillion-install: $Message"
}

# Runs a program, shows everything it prints, and returns nothing. Windows
# PowerShell 5.1 turns a program's stderr into errors, which 'Stop' would throw
# on, so errors are only 'Continue' here and the exit code is what counts.
function Invoke-TrillionNative([string]$Exe, [string[]]$Arguments) {
    $saved = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & $Exe @Arguments 2>&1 | ForEach-Object { "$_" } | Out-Host
        return
    } finally {
        $ErrorActionPreference = $saved
    }
}

# uv on PATH, else where Astral's installer puts it.
function Find-TrillionUv {
    $found = Get-Command uv -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($found) { return $found.Source }
    $candidates = @()
    if ($env:UV_INSTALL_DIR) { $candidates += Join-Path $env:UV_INSTALL_DIR 'uv.exe' }
    if ($env:XDG_BIN_HOME) { $candidates += Join-Path $env:XDG_BIN_HOME 'uv.exe' }
    $candidates += Join-Path $HOME '.local\bin\uv.exe'
    $candidates += Join-Path $HOME '.cargo\bin\uv.exe'
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) { return $candidate }
    }
    return $null
}

# Installs everything and returns the path to trillion.exe. Throws on failure.
function Install-Trillion([string]$Version, [string]$Wheel) {
    $ErrorActionPreference = 'Stop'

    if ($Version -and $Wheel) { throw 'use -Version or -Wheel, not both' }
    if ([Environment]::OSVersion.Platform -ne 'Win32NT') {
        throw 'this installer is for Windows. On macOS or Linux, use install.sh: curl -fsSL https://raw.githubusercontent.com/HelloTrillion/trillion/main/install.sh | sh'
    }

    # 1. uv, with Astral's official installer: https://docs.astral.sh/uv/getting-started/installation/
    $uv = Find-TrillionUv
    if (-not $uv) {
        Write-TrillionStep 'installing uv, the tool that installs and runs Trillion'
        Invoke-TrillionNative 'powershell' @('-ExecutionPolicy', 'ByPass', '-c', 'irm https://astral.sh/uv/install.ps1 | iex')
        if ($LASTEXITCODE -ne 0) { throw 'the uv installer failed (see above)' }
        $uv = Find-TrillionUv
        if (-not $uv) { throw "uv was installed but can't be found. Open a new PowerShell window and run this again." }
    }
    Write-TrillionStep "using uv at $uv"

    # 2. Trillion. uv downloads Python 3.13 itself if this machine doesn't have it.
    if ($Wheel) {
        # --reinstall, so a rebuilt wheel with the same version still replaces the old one.
        Write-TrillionStep "installing Trillion from $Wheel"
        Invoke-TrillionNative $uv @('tool', 'install', '--reinstall', '--python', '3.13', $Wheel)
        if ($LASTEXITCODE -ne 0) { throw "uv could not install $Wheel (see above)" }
    } else {
        $package = 'trillion'
        if ($Version) { $package = "trillion==$Version" }
        Write-TrillionStep "installing $package (this upgrades it if it is already installed)"
        Invoke-TrillionNative $uv @('tool', 'install', '--upgrade', '--python', '3.13', $package)
        if ($LASTEXITCODE -ne 0) { throw "uv could not install $package (see above)" }
    }

    # 3. PATH: for new windows (uv saves it for your user), and for this one.
    $ErrorActionPreference = 'Continue'
    $bindir = (& $uv tool dir --bin 2>$null | Select-Object -First 1)
    $said = (& $uv tool update-shell 2>&1 | ForEach-Object { "$_" }) -join ' '
    $updated = $LASTEXITCODE
    $ErrorActionPreference = 'Stop'
    if (-not $bindir) { throw "uv didn't say where it put Trillion" }
    if ($updated -eq 0 -or $said -match 'already') {
        Write-TrillionStep "$bindir is on your PATH in new windows"
    } else {
        Write-TrillionStep "couldn't add $bindir to your PATH; add it yourself to run trillion from any window"
    }
    if (($env:Path -split ';') -notcontains $bindir) { $env:Path = "$bindir;$env:Path" }

    $trillion = Join-Path $bindir 'trillion.exe'
    if (-not (Test-Path -LiteralPath $trillion -PathType Leaf)) {
        throw "Trillion was installed but $trillion is missing"
    }
    $installed = (& $trillion --version | Select-Object -First 1)
    Write-TrillionStep "installed $installed in $bindir"
    return $trillion
}

if ($Help) {
    Write-Host @'
Install Trillion on Windows, or upgrade it, then run `trillion setup`.

  irm https://raw.githubusercontent.com/HelloTrillion/trillion/main/install.ps1 | iex

Options, as parameters when run as a file, or the environment variable beside each:
  -Version X     install Trillion X instead of the latest   $env:TRILLION_VERSION = "X"
  -Wheel PATH    install this wheel file or URL instead      $env:TRILLION_WHEEL = "PATH"
  -NoSetup       install only; don't run `trillion setup`    $env:TRILLION_NO_SETUP = "1"
'@
    return
}

$TrillionStatus = 0
try {
    $TrillionExe = Install-Trillion -Version $Version -Wheel $Wheel
    if ($NoSetup -or @('1', 'true', 'yes') -contains "$env:TRILLION_NO_SETUP") {
        Write-TrillionStep "done. Run 'trillion setup' to start."
    } else {
        # Setup: prepares, checks, starts the app and opens the browser.
        Write-TrillionStep "running trillion setup (Ctrl+C stops Trillion; start it again with 'trillion run')"
        & $TrillionExe setup
        $TrillionStatus = $LASTEXITCODE
    }
} catch {
    [Console]::Error.WriteLine("trillion-install: error: $($_.Exception.Message)")
    $TrillionStatus = 1
}

# Run as a file, exit with the status. Piped into iex, `exit` would close the
# PowerShell window, so leave the status in $LASTEXITCODE instead.
if ($PSCommandPath) {
    exit $TrillionStatus
} else {
    $global:LASTEXITCODE = $TrillionStatus
}
