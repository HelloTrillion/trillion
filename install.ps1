# Install Trillion on Windows (Windows PowerShell 5.1 or later):
#
#   irm https://raw.githubusercontent.com/HelloTrillion/trillion/main/install.ps1 | iex
#
# It installs uv (Astral's Python tool manager) if you don't have it, installs
# Trillion with uv, puts it on your PATH, then runs `trillion setup`, which
# prepares the data folder, checks this machine and opens the app.
# Running it again upgrades Trillion. It needs no administrator rights.
#
# Options. Piped into iex, use the environment variables. Run as a file, or as a
# script block, the parameters work too:
#   powershell -ExecutionPolicy Bypass -File .\install.ps1 -NoSetup
#   & ([scriptblock]::Create((irm https://raw.githubusercontent.com/HelloTrillion/trillion/main/install.ps1))) -NoSetup
#
#   -Channel C     stable (the default), beta or dev           $env:TRILLION_CHANNEL = "C"
#   -Version X     install Trillion X instead of the latest   $env:TRILLION_VERSION = "X"
#   -Wheel PATH    install this wheel file or URL instead      $env:TRILLION_WHEEL = "PATH"
#   -NoSetup       install only; don't run `trillion setup`    $env:TRILLION_NO_SETUP = "1"
#   -Help          show this help
#
# The channel is remembered (in ~\.trillion\channel), so running it again, or
# `trillion update`, stays on it. stable and beta come from PyPI; dev is the newest
# build of main, from the `dev` release on GitHub.
#
# macOS and Linux: use install.sh instead (see the README).
#
# Piped into iex, this runs in your session, so everything is inside one script
# block: nothing it defines is left behind, and it never calls `exit`, which would
# close your window or stop the script that ran it. Run as a file, it exits with
# a nonzero status on failure; otherwise the status is left in $LASTEXITCODE.

& {
    param([string[]]$Arguments, $Outer)

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

    # The wheel on the rolling `dev` release: the newest build of main, or $null.
    function Find-DevWheel {
        $api = 'https://api.github.com/repos/HelloTrillion/trillion/releases/tags/dev'
        if ($env:TRILLION_DEV_RELEASE) { $api = $env:TRILLION_DEV_RELEASE }
        try {
            $answer = Invoke-RestMethod -Uri $api -Headers @{ Accept = 'application/vnd.github+json' } -UseBasicParsing
        } catch {
            throw "couldn't ask GitHub for the dev build ($api): $($_.Exception.Message)"
        }
        $found = @($answer.assets) | Where-Object { "$($_.browser_download_url)" -match '/trillion-[^/]*\.whl$' } | Select-Object -First 1
        if ($found) { return "$($found.browser_download_url)" }
        return $null
    }

    # Installs everything and returns the path to trillion.exe. Throws on failure.
    function Install-Trillion([string]$Version, [string]$Wheel, [string]$Channel) {
        if ($Version -and $Wheel) { throw 'use -Version or -Wheel, not both' }
        # A tag works as a version too: v2026.11.0-beta.1 is 2026.11.0-beta.1 to uv.
        if ($Version) { $Version = $Version -replace '^v', '' }

        # The channel: the one asked for, else the one remembered, else stable.
        $dataHome = Join-Path $HOME '.trillion'
        if ($env:TRILLION_HOME) { $dataHome = $env:TRILLION_HOME }
        $channelFile = Join-Path $dataHome 'channel'
        $was = ''
        if (Test-Path -LiteralPath $channelFile -PathType Leaf) {
            $was = "$(Get-Content -LiteralPath $channelFile -TotalCount 1)".Trim().ToLowerInvariant()
        }
        if (@('stable', 'beta', 'dev') -notcontains $was) { $was = 'stable' }
        $chosen = $was
        if ($Channel) { $chosen = $Channel.ToLowerInvariant() }
        if (@('stable', 'beta', 'dev') -notcontains $chosen) { throw "unknown channel: $chosen (use stable, beta or dev)" }
        if ($chosen -eq 'dev' -and $Version) {
            throw 'the dev channel has no versions: it is always the newest build of main. Use -Wheel to install a particular build.'
        }
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
        # First, what is installed already, and with which extras (such as Whisper):
        # a plain `uv tool install trillion` would replace them with none. An older uv
        # can't show extras; then a plain list says whether Trillion is there at all.
        $ErrorActionPreference = 'Continue'
        $detailed = $true
        $listing = & $uv tool list --show-version-specifiers --show-extras 2>&1
        if ($LASTEXITCODE -ne 0) {
            $detailed = $false
            $listing = & $uv tool list 2>&1
            if ($LASTEXITCODE -ne 0) {
                $why = (@($listing) | ForEach-Object { "$_" }) -join ' '
                throw "uv couldn't list what it has installed: $why Update uv ('uv self update', or the way you installed it) and try again."
            }
        }
        $ErrorActionPreference = 'Stop'
        $installed = "$(@($listing) | ForEach-Object { "$_" } | Where-Object { $_ -like 'trillion v*' } | Select-Object -First 1)"
        $extras = ''
        if ($installed -match '\[extras: ([^\]]*)\]') { $extras = $Matches[1] -replace '\s', '' }
        $package = 'trillion'
        if ($extras) { $package = "trillion[$extras]" }
        # `uv tool upgrade` keeps the extras and the Python, but also keeps a pinned
        # version or a wheel file, which this installer exists to move past.
        $pinned = $installed.Contains('[required:')
        $keeping = ''
        if ($extras) { $keeping = " (keeping $extras)" }

        if ($Wheel) {
            # The extras go on the wheel: path[extras], or name[extras] @ URL.
            $target = $Wheel
            if ($extras) {
                if ($Wheel -match '://') { $target = "trillion[$extras] @ $Wheel" } else { $target = "$($Wheel)[$extras]" }
            }
            # --reinstall, so a rebuilt wheel with the same version still replaces the old one.
            Write-Step "installing Trillion from $Wheel$keeping"
            Invoke-Native $uv @('tool', 'install', '--reinstall', '--python', '3.13', $target)
            if ($LASTEXITCODE -ne 0) { throw "uv could not install $target (see above). $oldUv" }
        } elseif ($chosen -eq 'dev') {
            $url = Find-DevWheel
            if (-not $url) { throw 'there is no dev build to install yet (no wheel on https://github.com/HelloTrillion/trillion/releases/tag/dev)' }
            $target = "trillion @ $url"
            if ($extras) { $target = "trillion[$extras] @ $url" }
            # --reinstall: each dev build replaces the one before.
            Write-Step "installing the newest dev build of Trillion$keeping"
            Invoke-Native $uv @('tool', 'install', '--reinstall', '--python', '3.13', $target)
            if ($LASTEXITCODE -ne 0) { throw "uv could not install $url (see above). $oldUv" }
        } elseif ($installed -and -not $Version -and -not $pinned -and $chosen -eq 'stable' -and $was -eq 'stable') {
            Write-Step "upgrading Trillion$keeping"
            if ($detailed) {
                $reinstall = "uv tool install --reinstall --python 3.13 '$package'"
            } else {
                $reinstall = "uv tool install --reinstall --python 3.13 trillion (as 'trillion[whisper]' if you use Whisper)"
            }
            Invoke-Native $uv @('tool', 'upgrade', 'trillion')
            if ($LASTEXITCODE -ne 0) { throw "uv could not upgrade Trillion (see above). To reinstall it: $reinstall. $oldUv" }
        } else {
            if ($Version) {
                $package = "$package==$Version"
            } elseif ($chosen -eq 'beta') {
                # A requirement that names a pre-release lets uv pick one for Trillion,
                # and for nothing else it depends on.
                $package = "$package>=0.0.0b0"
            }
            # Leaving a channel needs a fresh start: --upgrade alone would keep a beta
            # that is installed, and moving to stable means the newest stable.
            $fresh = '--upgrade'
            if ($chosen -ne $was) { $fresh = '--reinstall' }
            Write-Step "installing $package from the $chosen channel (this upgrades it if it is already installed)"
            Invoke-Native $uv @('tool', 'install', $fresh, '--python', '3.13', $package)
            if ($LASTEXITCODE -ne 0) { throw "uv could not install $package (see above). $oldUv" }
        }

        # 3. PATH: for new windows (uv saves it for your user), and for this one.
        $ErrorActionPreference = 'Continue'
        # Output captured whole, then read: a pipeline here would make
        # $LASTEXITCODE unreliable in Windows PowerShell 5.1.
        $dirOutput = & $uv tool dir --bin 2>$null
        $found = $LASTEXITCODE
        $bindir = "$(@($dirOutput) | Select-Object -First 1)".Trim()
        $shellOutput = & $uv tool update-shell 2>&1
        $updated = $LASTEXITCODE
        $said = (@($shellOutput) | ForEach-Object { "$_" }) -join ' '
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
        # Continue, not Stop: a host that turns a program's stderr into errors
        # would otherwise throw on its first log line.
        $ErrorActionPreference = 'Continue'
        $versionOutput = & $trillion --version 2>$null
        $ErrorActionPreference = 'Stop'
        Write-Step "installed $(@($versionOutput) | Select-Object -First 1) in $bindir"

        # Remember a channel that was asked for, so a re-run and `trillion update` stay on it.
        if ($Channel) {
            try {
                New-Item -ItemType Directory -Force -Path $dataHome | Out-Null
                Set-Content -LiteralPath $channelFile -Value $chosen -Encoding Ascii
                Write-Step "on the $chosen channel from now on"
            } catch {
                Write-Step "couldn't note the $chosen channel in $channelFile; a re-run will need -Channel $chosen again"
            }
        }
        return $trillion
    }

    # Run from a file, or piped into iex? A script block read from a file knows
    # its file; one parsed by Invoke-Expression has none. Asked here, inside this
    # block, it is about this script and not whoever ran it.
    $AsFile = -not ([string]::IsNullOrEmpty($MyInvocation.MyCommand.ScriptBlock.File) -and
        [string]::IsNullOrEmpty(({ }).File))

    # Are $args ours? Yes when run as a file, or as a script block of its own:
    #   & ([scriptblock]::Create((irm .../install.ps1))) -NoSetup
    # Then the top level ($Outer) is this script's own text. Piped into iex, the
    # top level is whoever ran iex, and so are its $args: not ours to read.
    $ownArgs = $AsFile
    if (-not $ownArgs) {
        try {
            $root = ({ }).Ast
            while ($root.Parent) { $root = $root.Parent }
            $outerBlock = $Outer.MyCommand.ScriptBlock
            $ownArgs = ($null -ne $outerBlock) -and ($outerBlock.Ast.Extent.Text -eq $root.Extent.Text)
        } catch {
            $ownArgs = $false
        }
    }

    # Options: the environment first, then the parameters.
    if (-not $ownArgs) {
        if ($Arguments.Count -gt 0) {
            Write-Step 'piped into iex, so parameters are ignored; set TRILLION_CHANNEL, TRILLION_VERSION, TRILLION_WHEEL or TRILLION_NO_SETUP instead'
        }
        $Arguments = @()
    }
    $version = $env:TRILLION_VERSION
    $channel = $env:TRILLION_CHANNEL
    $wheel = $env:TRILLION_WHEEL
    $noSetup = @('1', 'true', 'yes') -contains "$env:TRILLION_NO_SETUP"
    $status = 0
    try {
        for ($i = 0; $i -lt $Arguments.Count; $i++) {
            $name = $Arguments[$i]
            switch ($name) {
                { $_ -in '-Version', '-Wheel', '-Channel' } {
                    if ($i + 1 -ge $Arguments.Count) { throw "$name needs a value" }
                    $i++
                    if ($name -eq '-Version') { $version = $Arguments[$i] }
                    elseif ($name -eq '-Wheel') { $wheel = $Arguments[$i] }
                    else { $channel = $Arguments[$i] }
                }
                '-NoSetup' { $noSetup = $true }
                { $_ -in '-Help', '-?' } {
                    Write-Host @'
Install Trillion on Windows, or upgrade it, then run `trillion setup`.

  irm https://raw.githubusercontent.com/HelloTrillion/trillion/main/install.ps1 | iex
  powershell -ExecutionPolicy Bypass -File .\install.ps1 -NoSetup
  & ([scriptblock]::Create((irm https://raw.githubusercontent.com/HelloTrillion/trillion/main/install.ps1))) -NoSetup

Options, as parameters (not through iex), or the environment variable beside each:
  -Channel C     stable (the default), beta or dev           $env:TRILLION_CHANNEL = "C"
  -Version X     install Trillion X instead of the latest   $env:TRILLION_VERSION = "X"
  -Wheel PATH    install this wheel file or URL instead      $env:TRILLION_WHEEL = "PATH"
  -NoSetup       install only; don't run `trillion setup`    $env:TRILLION_NO_SETUP = "1"

The channel is remembered, so running this again stays on it. beta has the
pre-releases as well as the releases; dev is the newest build of main.
'@
                    return
                }
                default { throw "unknown option: $name (try -Help)" }
            }
        }

        # This process too, for the downloads uv's installer and irm make.
        [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor 3072

        $trillion = Install-Trillion -Version $version -Wheel $wheel -Channel $channel
        if ($noSetup) {
            Write-Step "done. Open a new window and run 'trillion setup' to start."
        } else {
            # Setup: prepares, checks, starts the app and opens the browser.
            # Continue, not Stop: in a host that turns a program's stderr into errors
            # (the ISE, VS Code), Stop would throw on setup's first log line.
            Write-Step "running trillion setup (Ctrl+C stops Trillion; to start it again later, open a new window and run 'trillion run')"
            $ErrorActionPreference = 'Continue'
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
} -Arguments $args -Outer $MyInvocation
