<#
.SYNOPSIS
    Run the test suite using Blender's own Python interpreter.

.DESCRIPTION
    The suite is bpy-free, so it does not need Blender running -- only Blender's
    interpreter. Using that instead of your system Python is what proves the
    code works on the Python version your users actually have: a stdlib module
    or a syntax feature present in one and not the other would pass locally and
    fail on their machine.

    This talks to Blender's python.exe directly rather than launching Blender,
    so it costs no startup time. Tests that genuinely need bpy live in
    tests/blender/ and are run through Blender itself -- see docs/development.md.

    pytest is installed on first use into .pytest-blender\ in this repo, not
    into the Blender install, so a Blender update cannot remove it. The path to
    Blender's interpreter is cached there too, so only the first run pays for
    asking Blender where it lives.

.PARAMETER Blender
    Path to a specific Blender executable. Defaults to $env:BLENDER, then a
    short search: PATH, the locations Blender's own installer writes to, every
    Steam library Steam itself has a record of, and whichever Blender the last
    run used. A portable build unpacked somewhere of your own has to be named
    here once. Run '.\install.ps1 -List' to see every Blender on the machine.

.PARAMETER System
    Use the system Python instead of Blender's.

.PARAMETER PytestArgs
    Everything else is passed straight to pytest, including bare paths.

    One argument cannot get through: PowerShell binds -v to its own -Verbose
    before this script sees it, so pytest never receives it. Use --verbose or
    -vv, both of which pass through untouched.

.EXAMPLE
    .\run_tests.ps1

.EXAMPLE
    .\run_tests.ps1 tests\test_poformat.py

.EXAMPLE
    .\run_tests.ps1 -k naming --verbose

.EXAMPLE
    .\run_tests.ps1 -Blender 'D:\Blender\blender.exe'
#>
[CmdletBinding()]
param(
    # Declared first, and the only parameter given an explicit Position, so a
    # bare argument goes to pytest. -Blender used to sit here and take the first
    # positional slot with it: `.\run_tests.ps1 tests\test_foo.py` reported
    # "Blender: tests\test_foo.py" and then failed to find an interpreter,
    # naming a path the user never offered as one.
    [Parameter(Position = 0, ValueFromRemainingArguments = $true)]
    [string[]]$PytestArgs,
    [string]$Blender = $env:BLENDER,
    [switch]$System
)

# 'Continue', not 'Stop': every native call below is checked via $LASTEXITCODE,
# and PowerShell raises NativeCommandError for anything an exe writes to stderr
# -- pip's version-upgrade notice alone would abort the script under 'Stop'.
$ErrorActionPreference = 'Continue'

$RepoDir   = $PSScriptRoot
if (-not $RepoDir) { $RepoDir = (Get-Location).Path }
$VendorDir = Join-Path $RepoDir '.pytest-blender'
$CacheFile = Join-Path $VendorDir '.interpreter'

function Write-Step { param([string]$Message) Write-Host "==> $Message" -ForegroundColor Green }
function Write-Note { param([string]$Message) Write-Host "    $Message" -ForegroundColor DarkGray }
function Write-Warn { param([string]$Message) Write-Host "  ! $Message" -ForegroundColor Yellow }
function Write-Fail { param([string]$Message) Write-Host "  x $Message" -ForegroundColor Red }

# The Blender a previous run resolved an interpreter for -- line 1 of the same
# cache file Get-BlenderPython writes. Tried last, so it only ever rescues the
# case the searches cannot reach: a portable build unpacked somewhere of the
# user's own choosing. Naming that one with -Blender once is then enough,
# instead of every run.
function Get-CachedBlender {
    if (-not (Test-Path -LiteralPath $CacheFile -PathType Leaf)) { return $null }
    $lines = @(Get-Content -LiteralPath $CacheFile)
    if ($lines.Count -lt 1) { return $null }
    $exe = $lines[0].Trim()
    if ($exe -and (Test-Path -LiteralPath $exe -PathType Leaf)) { return $exe }
    return $null
}

# Every Steam library on this machine, read out of Steam's own record of them.
#
# This is not the drive-letter sweep that used to live in Find-Blender. That
# sweep tried C through F and called it a search: it happened to find Blender for
# whoever wrote it, missed a library on any other drive, and reported "no
# Blender" as though it had looked everywhere. Steam writes down where its
# libraries are -- its install path in the registry, its extra libraries in
# libraryfolders.vdf -- so asking is exhaustive and costs one file read.
# install.ps1 reads the same two sources for the same reason.
function Get-SteamLibraries {
    $roots = @()
    foreach ($key in @(
        @{ Path = 'HKCU:\Software\Valve\Steam';             Name = 'SteamPath' },
        @{ Path = 'HKLM:\SOFTWARE\WOW6432Node\Valve\Steam'; Name = 'InstallPath' }
    )) {
        try {
            $value = (Get-ItemProperty -Path $key.Path -Name $key.Name -ErrorAction Stop).$($key.Name)
            if ($value) { $roots += $value }
        } catch { }
    }
    $roots += "${env:ProgramFiles(x86)}\Steam"
    $roots += "$env:ProgramFiles\Steam"

    $libraries = @()
    foreach ($root in $roots) {
        if (-not $root -or -not (Test-Path -LiteralPath $root -PathType Container)) { continue }
        $libraries += $root
        # The vdf moved between Steam client versions, so both places are read.
        foreach ($vdf in @("$root\steamapps\libraryfolders.vdf", "$root\config\libraryfolders.vdf")) {
            if (-not (Test-Path -LiteralPath $vdf -PathType Leaf)) { continue }
            $text = Get-Content -LiteralPath $vdf -Raw
            foreach ($match in [regex]::Matches($text, '"path"\s*"([^"]+)"')) {
                # Paths are escaped for the vdf, so '\\' means one separator.
                $libraries += ($match.Groups[1].Value -replace '\\\\', '\')
            }
        }
    }
    return $libraries | Select-Object -Unique
}

function Find-Blender {
    # An explicitly named Blender is a requirement, not a hint: falling back to
    # a different one would test an interpreter the caller did not ask for.
    if ($Blender) {
        if (Test-Path -LiteralPath $Blender -PathType Leaf) { return $Blender }
        Write-Fail "not an executable Blender: $Blender"
        Write-Note 'it was given explicitly, so no fallback is applied'
        exit 1
    }

    $onPath = Get-Command blender -ErrorAction SilentlyContinue
    if ($onPath) { return $onPath.Source }

    # The roots Blender's own installer writes to, read from the environment
    # rather than spelled out with a drive letter. Steam follows below, and
    # between them they cover how Blender is actually installed on Windows.
    # Anything else -- a portable build in a folder of the user's own -- is what
    # -Blender and the cache are for; '.\install.ps1 -List' prints every Blender
    # on the machine, which is what the failure below points at.
    $roots = @($env:ProgramFiles, ${env:ProgramFiles(x86)})
    if ($env:LOCALAPPDATA) { $roots += (Join-Path $env:LOCALAPPDATA 'Programs') }
    foreach ($root in $roots) {
        if (-not $root) { continue }
        $foundation = Join-Path $root 'Blender Foundation'
        if (-not (Test-Path -LiteralPath $foundation -PathType Container)) { continue }
        # Newest version directory first: 'Blender 5.2' sorts above 'Blender 4.2'.
        $exe = Get-ChildItem -LiteralPath $foundation -Filter 'Blender*' -Directory -ErrorAction SilentlyContinue |
            Sort-Object Name -Descending |
            ForEach-Object { Join-Path $_.FullName 'blender.exe' } |
            Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } |
            Select-Object -First 1
        if ($exe) { return $exe }
    }

    foreach ($library in Get-SteamLibraries) {
        $exe = Join-Path $library 'steamapps\common\Blender\blender.exe'
        if (Test-Path -LiteralPath $exe -PathType Leaf) { return $exe }
    }

    return (Get-CachedBlender)
}

function Get-BlenderPython {
    param([string]$Exe)

    # Asking Blender costs a launch, which is most of this script's runtime --
    # and avoiding Blender startup is the entire point. So cache the answer,
    # keyed by the executable, and re-derive when that changes.
    if (Test-Path -LiteralPath $CacheFile -PathType Leaf) {
        $lines = Get-Content -LiteralPath $CacheFile
        if ($lines.Count -ge 2 -and $lines[0] -eq $Exe -and (Test-Path -LiteralPath $lines[1] -PathType Leaf)) {
            return $lines[1]
        }
    }

    $output = & $Exe --background --factory-startup --python-expr "import sys; print('PYEXE:' + sys.executable)"
    $match = $output | Select-String '^PYEXE:(.+)$' | Select-Object -First 1
    if (-not $match) { return $null }
    $resolved = $match.Matches[0].Groups[1].Value.Trim()
    if (-not (Test-Path -LiteralPath $resolved -PathType Leaf)) { return $null }

    New-Item -ItemType Directory -Force -Path $VendorDir | Out-Null
    Set-Content -LiteralPath $CacheFile -Value @($Exe, $resolved) -Encoding utf8
    return $resolved
}

# ------------------------------------------------------------ pick a python

if ($System) {
    $py = Get-Command python -ErrorAction SilentlyContinue
    if (-not $py) { Write-Fail 'no system python found'; exit 1 }
    $Python = $py.Source
    Write-Step 'Using system Python'
} else {
    $exe = Find-Blender
    if (-not $exe) {
        Write-Fail 'could not find a Blender executable'
        Write-Note 'not on PATH, not in a standard install location, not in any Steam'
        Write-Note 'library, and no previous run to remember. Point this script at'
        Write-Note 'yours -- any one of:'
        Write-Note '  .\run_tests.ps1 -Blender C:\path\to\blender.exe'
        Write-Note "  `$env:BLENDER = 'C:\path\to\blender.exe'; .\run_tests.ps1"
        Write-Note "  .\install.ps1 -List      # prints every Blender on this machine"
        Write-Note "  .\run_tests.ps1 -System  # use the system Python instead"
        exit 1
    }
    Write-Step "Blender: $exe"
    $Python = Get-BlenderPython -Exe $exe
    if (-not $Python) {
        Write-Fail 'Blender did not report a usable interpreter'
        Write-Note 'rerun with -System to use the system Python instead'
        exit 1
    }
}

# No double quotes inside: PowerShell mangles them when handing an argument to
# a native executable, and python then sees a broken expression.
$version = & $Python -c 'import sys; print(sys.version.split()[0])'
Write-Note "Python $version  ($Python)"

# ------------------------------------------------------------------- pytest

# find_spec rather than a bare import, and no stderr redirect: redirecting a
# native command's stderr in PowerShell wraps each line in an ErrorRecord and
# trips ErrorActionPreference='Stop' even when the exe exited cleanly. Exiting
# with a status instead means there is no stderr to suppress in the first place.
$probe = "import importlib.util,sys; sys.exit(0 if importlib.util.find_spec('pytest') else 1)"
& $Python -c $probe
if ($LASTEXITCODE -ne 0) {
    $env:PYTHONPATH = $VendorDir
    & $Python -c $probe
    if ($LASTEXITCODE -ne 0) {
        Write-Step 'Installing pytest into .pytest-blender\ (first run only)'
        Write-Note 'goes in the repo, not the Blender install, so a Blender update cannot remove it'
        # --disable-pip-version-check silences the upgrade notice pip otherwise
        # writes to stderr on every invocation.
        & $Python -m pip install --quiet --disable-pip-version-check --target $VendorDir pytest
        if ($LASTEXITCODE -ne 0) {
            Write-Fail 'could not install pytest'
            Write-Note "install it yourself with:"
            Write-Note "  & `"$Python`" -m pip install --target `"$VendorDir`" pytest"
            exit 1
        }
    }
}

# ---------------------------------------------------------------------- run

$arguments = @()
if ($PytestArgs) { $arguments = $PytestArgs } else { $arguments = @('-q') }

Write-Step 'Running tests'
Push-Location $RepoDir
try {
    & $Python -m pytest @arguments
    $status = $LASTEXITCODE
} finally {
    Pop-Location
}

if ($status -eq 0) {
    Write-Host '==> all tests passed' -ForegroundColor Green
} else {
    Write-Warn "pytest exited with $status"
}
exit $status
