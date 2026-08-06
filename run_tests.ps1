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
    tests/blender/ and are run through Blender itself -- see the README.

    pytest is installed on first use into .pytest-blender\ in this repo, not
    into the Blender install, so a Blender update cannot remove it. The path to
    Blender's interpreter is cached there too, so only the first run pays for
    asking Blender where it lives.

.PARAMETER Blender
    Path to a specific Blender executable. Defaults to $env:BLENDER, then a
    short search. Run '.\install.ps1 -List' to see every Blender found.

.PARAMETER System
    Use the system Python instead of Blender's.

.PARAMETER PytestArgs
    Everything else is passed straight to pytest.

.EXAMPLE
    .\run_tests.ps1

.EXAMPLE
    .\run_tests.ps1 -k naming -v

.EXAMPLE
    .\run_tests.ps1 -Blender 'D:\Blender\blender.exe'
#>
[CmdletBinding()]
param(
    [string]$Blender = $env:BLENDER,
    [switch]$System,
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$PytestArgs
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

function Find-Blender {
    # Deliberately a short list: install.ps1 does exhaustive discovery, and
    # '.\install.ps1 -List' prints everything it finds if these guesses miss.
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

    $guesses = @()
    $guesses += Get-ChildItem 'C:\Program Files\Blender Foundation' -Filter 'Blender*' -Directory -ErrorAction SilentlyContinue |
        ForEach-Object { Join-Path $_.FullName 'blender.exe' }
    foreach ($drive in @('C', 'D', 'E', 'F')) {
        $guesses += "${drive}:\Steam\steamapps\common\Blender\blender.exe"
        $guesses += "${drive}:\Program Files (x86)\Steam\steamapps\common\Blender\blender.exe"
    }
    foreach ($guess in $guesses) {
        if ($guess -and (Test-Path -LiteralPath $guess -PathType Leaf)) { return $guess }
    }
    return $null
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
        Write-Note "pass one with -Blender <path>, set `$env:BLENDER,"
        Write-Note "run '.\install.ps1 -List' to see every Blender found,"
        Write-Note "or run '.\run_tests.ps1 -System' to use the system Python"
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
