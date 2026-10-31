# Install Trillion on Windows (Windows PowerShell 5.1 or later):
#
#   irm https://raw.githubusercontent.com/HelloTrillion/trillion/main/install.ps1 | iex
#
# It installs uv (Astral's Python tool manager) if you don't have it, installs
# Trillion with uv, puts it on your PATH, then runs `trillion setup`, which
# prepares the data folder, checks this machine and opens the app.
# Running it again upgrades Trillion. It needs no administrator rights.
#
# Options. Piped into iex, use the environment variables. Run as a file, the
# parameters work too:
#   powershell -ExecutionPolicy Bypass -File .\install.ps1 -NoSetup
#
#   -Version X     install Trillion X instead of the latest   $env:TRILLION_VERSION = "X"
#   -Wheel PATH    install this wheel file or URL instead      $env:TRILLION_WHEEL = "PATH"
#   -NoSetup       install only; don't run `trillion setup`    $env:TRILLION_NO_SETUP = "1"
#   -Help          show this help
#
# macOS and Linux: use install.sh instead (see the README).
#
# Piped into iex, this runs in your session, so everything is inside one script
# block: nothing it defines is left behind, and it never calls `exit`, which would
# close your window or stop the script that ran it. Run as a file, it exits with
# a nonzero status on failure.

& {
    param([string[]]$Arguments, [bool]$AsFile)

    $ErrorActionPreference = 'Stop'

    function Write-Step([string]$Message) {
        Write-Host "trillion-install: $Message"
    }

    # Runs a program, shows everything it prints, and returns nothing. Windows
    # PowerShell 5.1 turns a program's stderr into errors, which 'Stop' would throw
    # on, so errors are only 'Continue' here and the exit code is what counts.
    function Invoke-Native([string]$Exe, [string[]]$ArgumentList) {
        $ErrorActionPreference = 'Continue'
        & $Exe @ArgumentList 2>&1 | ForEach-Object { "$_" } | Out-Host
    }

    # uv on PATH, else where Astral's installer puts it.
    function Find-Uv {
        $found = Get-Command uv -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($found) { return $found.Source }
        $candidates = @()
        if ($env:UV_INSTALL_DIR) {
            $candidates += Join-Path $env:UV_INSTALL_DIR 'uv.exe'
            $candidates += Join-Path $env:UV_INSTALL_DIR 'bin\uv.exe'
        }
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
        if ($Version -and $Wheel) { throw 'use -Version or -Wheel, not both' }
        if ([Environment]::OSVersion.Platform -ne 'Win32NT') {
            throw 'this installer is for Windows. On macOS or Linux, use install.sh: curl -fsSL https://raw.githubusercontent.com/HelloTrillion/trillion/main/install.sh | sh'
        }
        $oldUv = "If your uv is old, run 'uv self update' and try again."

        # 1. uv, with Astral's official installer: https://docs.astral.sh/uv/getting-started/installation/
        $uv = Find-Uv
        if (-not $uv) {
            Write-Step 'installing uv, the tool that installs and runs Trillion'
            # TLS 1.2 (3072): older Windows PowerShell may not offer it by default.
            $tls = '[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor 3072; '
            Invoke-Native 'powershell' @('-ExecutionPolicy', 'ByPass', '-c', ($tls + 'irm https://astral.sh/uv/install.ps1 | iex'))
            if ($LASTEXITCODE -ne 0) { throw 'the uv installer failed (see above)' }
            $uv = Find-Uv
            if (-not $uv) { throw "uv was installed but can't be found. Open a new PowerShell window and run this again." }
        }
        Write-Step "using uv at $uv"

        # 2. Trillion. uv downloads Python 3.13 itself if this machine doesn't have it.
        if ($Wheel) {
            # --reinstall, so a rebuilt wheel with the same version still replaces the old one.
            Write-Step "installing Trillion from $Wheel"
            Invoke-Native $uv @('tool', 'install', '--reinstall', '--python', '3.13', $Wheel)
            if ($LASTEXITCODE -ne 0) { throw "uv could not install $Wheel (see above). $oldUv" }
        } else {
            $package = 'trillion'
            if ($Version) { $package = "trillion==$Version" }
            Write-Step "installing $package (this upgrades it if it is already installed)"
            Invoke-Native $uv @('tool', 'install', '--upgrade', '--python', '3.13', $package)
            if ($LASTEXITCODE -ne 0) { throw "uv could not install $package (see above). $oldUv" }
        }

        # 3. PATH: for new windows (uv saves it for your user), and for this one.
        $ErrorActionPreference = 'Continue'
        $bindir = (& $uv tool dir --bin 2>$null | Select-Object -First 1)
        $found = $LASTEXITCODE
        $said = (& $uv tool update-shell 2>&1 | ForEach-Object { "$_" }) -join ' '
        $updated = $LASTEXITCODE
        $ErrorActionPreference = 'Stop'
        if ($found -ne 0 -or -not $bindir) {
            throw "uv didn't say where it put Trillion; your uv is probably too old. Run 'uv self update' and try again."
        }
        if ($updated -eq 0 -or $said -match 'already') {
            Write-Step "$bindir is on your PATH in new windows"
        } else {
            Write-Step "couldn't add $bindir to your PATH; add it yourself to run trillion from any window"
        }
        if (($env:Path -split ';') -notcontains $bindir) { $env:Path = "$bindir;$env:Path" }

        $trillion = Join-Path $bindir 'trillion.exe'
        if (-not (Test-Path -LiteralPath $trillion -PathType Leaf)) {
            throw "Trillion was installed but $trillion is missing"
        }
        $installed = (& $trillion --version | Select-Object -First 1)
        Write-Step "installed $installed in $bindir"
        return $trillion
    }

    # Options: the environment first, then the parameters of a file run. Under
    # iex, $args belongs to whoever ran it, so it is not ours to read.
    if (-not $AsFile) { $Arguments = @() }
    $version = $env:TRILLION_VERSION
    $wheel = $env:TRILLION_WHEEL
    $noSetup = @('1', 'true', 'yes') -contains "$env:TRILLION_NO_SETUP"
    $status = 0
    try {
        for ($i = 0; $i -lt $Arguments.Count; $i++) {
            $name = $Arguments[$i]
            switch ($name) {
                { $_ -in '-Version', '-Wheel' } {
                    if ($i + 1 -ge $Arguments.Count) { throw "$name needs a value" }
                    $i++
                    if ($name -eq '-Version') { $version = $Arguments[$i] } else { $wheel = $Arguments[$i] }
                }
                '-NoSetup' { $noSetup = $true }
                { $_ -in '-Help', '-?' } {
                    Write-Host @'
Install Trillion on Windows, or upgrade it, then run `trillion setup`.

  irm https://raw.githubusercontent.com/HelloTrillion/trillion/main/install.ps1 | iex
  powershell -ExecutionPolicy Bypass -File .\install.ps1 -NoSetup

Options, as parameters when run as a file, or the environment variable beside each:
  -Version X     install Trillion X instead of the latest   $env:TRILLION_VERSION = "X"
  -Wheel PATH    install this wheel file or URL instead      $env:TRILLION_WHEEL = "PATH"
  -NoSetup       install only; don't run `trillion setup`    $env:TRILLION_NO_SETUP = "1"
'@
                    return
                }
                default { throw "unknown option: $name (try -Help)" }
            }
        }

        # This process too, for the downloads uv's installer and irm make.
        [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor 3072

        $trillion = Install-Trillion -Version $version -Wheel $wheel
        if ($noSetup) {
            Write-Step "done. Open a new window and run 'trillion setup' to start."
        } else {
            # Setup: prepares, checks, starts the app and opens the browser.
            Write-Step "running trillion setup (Ctrl+C stops Trillion; to start it again later, open a new window and run 'trillion run')"
            & $trillion setup
            $status = $LASTEXITCODE
        }
    } catch {
        [Console]::Error.WriteLine("trillion-install: error: $($_.Exception.Message)")
        $status = 1
    }

    # Never `exit` when piped into iex: leave the status in $LASTEXITCODE.
    $global:LASTEXITCODE = $status
    if ($AsFile -and $status -ne 0) { exit $status }
} -Arguments $args -AsFile ([IO.Path]::GetFileName("$($MyInvocation.MyCommand.Path)") -eq 'install.ps1')
