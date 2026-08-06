<#
.SYNOPSIS
    Install the Descent 3 POF/OOF add-on into your Blender user add-ons directory.

.DESCRIPTION
    Finds every Blender install on this machine -- Steam (via libraryfolders.vdf),
    standalone installers, portable/unzipped builds, winget, and anything already on
    PATH -- works out each one's major.minor version, and copies the descent3_plugin
    package folder into that version's user add-ons directory:

        %APPDATA%\Blender Foundation\Blender\<version>\scripts\addons\descent3_plugin\

    The add-on MUST be installed as a package folder (it uses `from . import poformat`),
    so this script always copies the whole folder, never loose files.

    Note: Steam and standalone Blender share the same user config directory, so a given
    version only needs to be installed once regardless of where the .exe lives.

.PARAMETER List
    Show every Blender install found and its target add-ons directory, then exit.

.PARAMETER All
    Install into every detected Blender version. Default is the newest one only.

.PARAMETER Version
    Install into a specific version, e.g. -Version 4.2 or -Version 5.2.

.PARAMETER Target
    Install into an explicit path, skipping detection. Accepts a version root
    (...\Blender\5.2), an add-ons directory (...\scripts\addons), or an existing
    ...\scripts\addons\descent3_plugin folder.

.PARAMETER DryRun
    Print what would be copied without touching the filesystem.

.PARAMETER Clean
    Delete the destination descent3_plugin folder before copying, and remove any
    loose add-on .py files sitting directly in scripts\addons (a known bad install
    state that stops the add-on from loading).

.EXAMPLE
    .\install.ps1 -List

.EXAMPLE
    .\install.ps1 -All -Clean

.EXAMPLE
    .\install.ps1 -Target "D:\BlenderPortable\5.2"
#>
#Requires -Version 5.1
[CmdletBinding()]
param(
    [switch]$List,
    [switch]$All,
    [Alias('BlenderVersion')]
    [string]$Version,
    [string]$Target,
    [switch]$DryRun,
    [switch]$Clean
)

$ErrorActionPreference = 'Stop'

$AddonId       = 'descent3_plugin'
$RequiredFiles = @(
    '__init__.py',
    'config.py',
    'constants.py',
    'export_pof.py',
    'import_pof.py',
    'mathutil.py',
    'naming.py',
    'poformat.py',
    'preferences.py',
    'texexport.py',
    'texutil.py'
)

# Non-.py files that must also be installed. blender_manifest.toml is the
# canonical version and is what Blender 4.2+ reads to treat the folder as an
# extension; the previous manifest sat in the repo unshipped because the copy
# step filtered on '*.py' only.
$DataFiles = @('blender_manifest.toml')

# Package folder names this add-on used to ship under. They declare the same
# operator bl_idnames, so leaving one in place next to the new folder gives the
# user two File > Import entries and lets Blender pick either one.
$LegacyAddonIds = @('descent3_importer')

# ---------------------------------------------------------------- output helpers

function Write-Step { param([string]$Message) Write-Host "==> $Message" -ForegroundColor Cyan }
function Write-Ok   { param([string]$Message) Write-Host "    $Message" -ForegroundColor Green }
function Write-Note { param([string]$Message) Write-Host "    $Message" -ForegroundColor DarkGray }
function Write-Warn { param([string]$Message) Write-Host "  ! $Message" -ForegroundColor Yellow }
function Write-Fail { param([string]$Message) Write-Host "  x $Message" -ForegroundColor Red }

# ---------------------------------------------------------------- source package

function Get-SourceDir {
    $root = $PSScriptRoot
    if (-not $root) { $root = (Get-Location).Path }

    $src = Join-Path $root $AddonId
    if (-not (Test-Path -LiteralPath $src -PathType Container)) {
        throw "Add-on source folder not found: $src (run this script from the repo root)"
    }
    foreach ($file in $RequiredFiles) {
        if (-not (Test-Path -LiteralPath (Join-Path $src $file) -PathType Leaf)) {
            throw "Add-on source is incomplete -- missing $file in $src"
        }
    }
    return (Resolve-Path -LiteralPath $src).Path
}

# blender_manifest.toml is the single source of truth for the version;
# bl_info carries a literal copy only because Blender ast-parses it, and a
# test fails the build if the two drift apart.
function Get-AddonVersion {
    param([string]$SourceDir)

    $manifestPath = Join-Path $SourceDir 'blender_manifest.toml'
    if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) { return 'unknown' }
    $match = [regex]::Match((Get-Content -LiteralPath $manifestPath -Raw), '(?m)^\s*version\s*=\s*"([^"]+)"')
    if ($match.Success) { return $match.Groups[1].Value }
    return 'unknown'
}

# ---------------------------------------------------------------- steam discovery

function Get-SteamRoots {
    $roots = New-Object System.Collections.Generic.List[string]

    $registryKeys = @(
        @{ Path = 'HKCU:\Software\Valve\Steam';               Name = 'SteamPath' },
        @{ Path = 'HKLM:\SOFTWARE\WOW6432Node\Valve\Steam';   Name = 'InstallPath' },
        @{ Path = 'HKLM:\SOFTWARE\Valve\Steam';               Name = 'InstallPath' }
    )
    foreach ($key in $registryKeys) {
        try {
            $value = (Get-ItemProperty -Path $key.Path -Name $key.Name -ErrorAction Stop).$($key.Name)
            if ($value -and (Test-Path -LiteralPath $value)) { $roots.Add((Convert-Path $value)) }
        } catch { }
    }

    foreach ($guess in @("${env:ProgramFiles(x86)}\Steam", "$env:ProgramFiles\Steam", 'C:\Steam')) {
        if ($guess -and (Test-Path -LiteralPath $guess -PathType Container)) { $roots.Add($guess) }
    }

    return $roots | Select-Object -Unique
}

function Get-SteamLibraries {
    $libraries = New-Object System.Collections.Generic.List[string]

    foreach ($root in Get-SteamRoots) {
        $libraries.Add($root)

        # Extra library folders live in libraryfolders.vdf; its location moved between
        # Steam client versions, so check both.
        foreach ($vdf in @("$root\steamapps\libraryfolders.vdf", "$root\config\libraryfolders.vdf")) {
            if (-not (Test-Path -LiteralPath $vdf -PathType Leaf)) { continue }
            $text = Get-Content -LiteralPath $vdf -Raw
            foreach ($match in [regex]::Matches($text, '"path"\s*"([^"]+)"')) {
                $path = $match.Groups[1].Value -replace '\\\\', '\'
                if (Test-Path -LiteralPath $path -PathType Container) { $libraries.Add($path) }
            }
        }
    }

    return $libraries | Select-Object -Unique
}

# ------------------------------------------------------------- blender discovery

function Get-InstallVersion {
    param([string]$InstallDir, [string]$ExePath)

    # Blender ships its runtime data in a <major>.<minor> folder next to the
    # executable. Reading that is instant and works for Steam builds, which carry no
    # version anywhere in their path.
    if ($InstallDir -and (Test-Path -LiteralPath $InstallDir -PathType Container)) {
        $versionDirs = Get-ChildItem -LiteralPath $InstallDir -Directory -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -match '^\d+\.\d+$' } |
            Sort-Object { [version]$_.Name } -Descending
        if ($versionDirs) { return $versionDirs[0].Name }
    }

    # Fall back to asking Blender itself (slower, ~1s).
    if ($ExePath -and (Test-Path -LiteralPath $ExePath -PathType Leaf)) {
        try {
            $output = & $ExePath --version
            $match = [regex]::Match(($output -join "`n"), 'Blender\s+(\d+)\.(\d+)')
            if ($match.Success) { return "$($match.Groups[1].Value).$($match.Groups[2].Value)" }
        } catch { }
    }

    return $null
}

function New-InstallRecord {
    param([string]$Source, [string]$InstallDir, [string]$ExePath)

    $version = Get-InstallVersion -InstallDir $InstallDir -ExePath $ExePath
    if (-not $version) { return $null }

    return [pscustomobject]@{
        Version    = $version
        Source     = $Source
        InstallDir = $InstallDir
        ExePath    = $ExePath
    }
}

function Get-BlenderInstalls {
    $found = New-Object System.Collections.Generic.List[object]

    function Add-Candidate {
        param([string]$Source, [string]$Dir)
        if (-not $Dir -or -not (Test-Path -LiteralPath $Dir -PathType Container)) { return }
        $exe = Join-Path $Dir 'blender.exe'
        if (-not (Test-Path -LiteralPath $exe -PathType Leaf)) { $exe = '' }
        $record = New-InstallRecord -Source $Source -InstallDir (Convert-Path $Dir) -ExePath $exe
        if ($record) { $found.Add($record) }
    }

    # Steam
    foreach ($library in Get-SteamLibraries) {
        Add-Candidate -Source 'Steam' -Dir (Join-Path $library 'steamapps\common\Blender')
    }

    # Standalone installers (MSI/EXE) and winget
    $standaloneRoots = @(
        "$env:ProgramFiles\Blender Foundation",
        "${env:ProgramFiles(x86)}\Blender Foundation",
        "$env:LOCALAPPDATA\Programs\Blender Foundation",
        "$env:LOCALAPPDATA\Microsoft\WinGet\Packages"
    )
    foreach ($root in $standaloneRoots) {
        if (-not $root -or -not (Test-Path -LiteralPath $root -PathType Container)) { continue }
        Get-ChildItem -LiteralPath $root -Directory -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -like 'Blender*' } |
            ForEach-Object { Add-Candidate -Source 'Standalone' -Dir $_.FullName }
    }

    # Uninstall registry entries catch installs in unusual locations
    $uninstallKeys = @(
        'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*',
        'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*',
        'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*'
    )
    foreach ($key in $uninstallKeys) {
        try {
            Get-ItemProperty -Path $key -ErrorAction SilentlyContinue |
                Where-Object { $_.DisplayName -like 'Blender*' -and $_.InstallLocation } |
                ForEach-Object { Add-Candidate -Source 'Standalone' -Dir $_.InstallLocation }
        } catch { }
    }

    # Explicit override, then PATH
    if ($env:BLENDER_EXE -and (Test-Path -LiteralPath $env:BLENDER_EXE -PathType Leaf)) {
        Add-Candidate -Source 'BLENDER_EXE' -Dir (Split-Path -Parent $env:BLENDER_EXE)
    }
    $onPath = Get-Command 'blender.exe' -ErrorAction SilentlyContinue
    if ($onPath) { Add-Candidate -Source 'PATH' -Dir (Split-Path -Parent $onPath.Source) }

    return $found
}

function Get-ConfigRoot {
    return (Join-Path $env:APPDATA 'Blender Foundation\Blender')
}

function Get-Targets {
    $configRoot = Get-ConfigRoot
    $byVersion  = @{}

    # Detected executables
    foreach ($install in Get-BlenderInstalls) {
        $version = $install.Version
        if (-not $byVersion.ContainsKey($version)) {
            $byVersion[$version] = [pscustomobject]@{
                Version   = $version
                Sources   = New-Object System.Collections.Generic.List[string]
                Installs  = New-Object System.Collections.Generic.List[string]
                AddonsDir = Join-Path (Join-Path $configRoot $version) 'scripts\addons'
            }
        }
        $entry = $byVersion[$version]
        if (-not $entry.Sources.Contains($install.Source))    { $entry.Sources.Add($install.Source) }
        if (-not $entry.Installs.Contains($install.InstallDir)) { $entry.Installs.Add($install.InstallDir) }
    }

    # Versions that already have a config directory but whose .exe we did not find
    # (uninstalled, on a drive that is offline, or launched from somewhere unusual).
    if (Test-Path -LiteralPath $configRoot -PathType Container) {
        Get-ChildItem -LiteralPath $configRoot -Directory -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -match '^\d+\.\d+$' } |
            ForEach-Object {
                if (-not $byVersion.ContainsKey($_.Name)) {
                    $entry = [pscustomobject]@{
                        Version   = $_.Name
                        Sources   = New-Object System.Collections.Generic.List[string]
                        Installs  = New-Object System.Collections.Generic.List[string]
                        AddonsDir = Join-Path $_.FullName 'scripts\addons'
                    }
                    $entry.Sources.Add('existing config')
                    $byVersion[$_.Name] = $entry
                }
            }
    }

    return $byVersion.Values | Sort-Object { [version]$_.Version } -Descending
}

function Resolve-TargetPath {
    param([string]$Path)

    $leaf = Split-Path -Leaf $Path
    if ($leaf -eq $AddonId)          { return (Split-Path -Parent $Path) }   # ...\addons\descent3_plugin
    if ($leaf -match '^\d+\.\d+$')   { return (Join-Path $Path 'scripts\addons') }  # ...\Blender\5.2
    return $Path                                                             # assume it is the addons dir
}

# ------------------------------------------------------------------- the install

function Install-Addon {
    param([string]$SourceDir, [string]$AddonsDir)

    $destination = Join-Path $AddonsDir $AddonId
    Write-Step "Installing to $destination"

    # Loose .py files directly in scripts\addons break the package import and make the
    # operators silently fail to register. Flag them; -Clean removes them.
    foreach ($file in $RequiredFiles) {
        $loose = Join-Path $AddonsDir $file
        if (Test-Path -LiteralPath $loose -PathType Leaf) {
            if ($Clean -and -not $DryRun) {
                Remove-Item -LiteralPath $loose -Force
                Write-Warn "removed stray loose file $file from scripts\addons"
            } elseif ($Clean) {
                Write-Warn "would remove stray loose file $file from scripts\addons"
            } else {
                Write-Warn "stray loose file in scripts\addons: $file -- re-run with -Clean to remove it (it can stop the add-on loading)"
            }
        }
    }

    # A folder from a previous package name is not an upgrade target -- it is a
    # duplicate add-on. Always remove it, -Clean or not.
    foreach ($legacy in $LegacyAddonIds) {
        $legacyDir = Join-Path $AddonsDir $legacy
        if (Test-Path -LiteralPath $legacyDir -PathType Container) {
            if ($DryRun) {
                Write-Warn "would remove previous add-on folder $legacyDir (renamed to $AddonId)"
            } else {
                Remove-Item -LiteralPath $legacyDir -Recurse -Force
                Write-Warn "removed previous add-on folder $legacyDir (renamed to $AddonId)"
            }
        }
    }

    if ($Clean -and (Test-Path -LiteralPath $destination -PathType Container)) {
        if ($DryRun) {
            Write-Note "would delete existing $destination"
        } else {
            Remove-Item -LiteralPath $destination -Recurse -Force
            Write-Note "deleted existing $destination"
        }
    }

    $sourceFiles = @(
        Get-ChildItem -LiteralPath $SourceDir -Filter '*.py' -File
        foreach ($data in $DataFiles) {
            $dataPath = Join-Path $SourceDir $data
            if (Test-Path -LiteralPath $dataPath -PathType Leaf) { Get-Item -LiteralPath $dataPath }
        }
    ) | Sort-Object Name

    if ($DryRun) {
        Write-Note "would create $destination"
        foreach ($file in $sourceFiles) { Write-Note "would copy $($file.Name)" }
        return $true
    }

    New-Item -ItemType Directory -Force -Path $destination | Out-Null
    foreach ($file in $sourceFiles) {
        Copy-Item -LiteralPath $file.FullName -Destination $destination -Force
    }
    Write-Ok "copied $($sourceFiles.Count) file(s): $(($sourceFiles | ForEach-Object { $_.Name }) -join ', ')"

    # Stale bytecode from a previous version shadows the new code.
    $pycache = Join-Path $destination '__pycache__'
    if (Test-Path -LiteralPath $pycache) {
        Remove-Item -LiteralPath $pycache -Recurse -Force
        Write-Note 'cleared __pycache__'
    }

    # Modules removed from the repo but still sitting in the destination
    $sourceNames = $sourceFiles | ForEach-Object { $_.Name }
    Get-ChildItem -LiteralPath $destination -Filter '*.py' -File |
        Where-Object { $sourceNames -notcontains $_.Name } |
        ForEach-Object { Write-Warn "stale file left behind: $($_.Name) (use -Clean to remove)" }

    # Sanity check
    foreach ($file in $RequiredFiles) {
        if (-not (Test-Path -LiteralPath (Join-Path $destination $file) -PathType Leaf)) {
            Write-Fail "verification failed -- $file is missing from $destination"
            return $false
        }
    }
    return $true
}

# ------------------------------------------------------------------------- main

$sourceDir = Get-SourceDir
Write-Step "Add-on source: $sourceDir (v$(Get-AddonVersion -SourceDir $sourceDir))"

if ($Target) {
    $addonsDir = Resolve-TargetPath -Path $Target
    if (Install-Addon -SourceDir $sourceDir -AddonsDir $addonsDir) {
        Write-Host ''
        Write-Host 'Done. Restart Blender (or toggle the add-on off/on in Preferences) so the new code loads.' -ForegroundColor Green
        exit 0
    }
    exit 1
}

$targets = @(Get-Targets)

if ($targets.Count -eq 0) {
    Write-Fail 'No Blender installation found.'
    Write-Host ''
    Write-Host 'Searched:'
    Write-Host '  - Steam libraries (libraryfolders.vdf)'
    Write-Host '  - %ProgramFiles%\Blender Foundation, %LOCALAPPDATA%\Programs\Blender Foundation'
    Write-Host '  - Uninstall registry entries, PATH, $env:BLENDER_EXE'
    Write-Host "  - $(Get-ConfigRoot)"
    Write-Host ''
    Write-Host 'If Blender lives somewhere else, point at it directly:'
    Write-Host '  .\install.ps1 -Target "D:\Path\To\Blender\5.2"'
    exit 1
}

if ($List) {
    Write-Host ''
    # NB: not $target -- that would collide with the [string]$Target parameter and
    # PowerShell would coerce each record to a string.
    foreach ($blender in $targets) {
        $sources = $blender.Sources -join ', '
        Write-Host ("  Blender {0,-6} [{1}]" -f $blender.Version, $sources) -ForegroundColor White
        foreach ($install in $blender.Installs) { Write-Note "exe:    $install" }
        $installed = Test-Path -LiteralPath (Join-Path $blender.AddonsDir $AddonId) -PathType Container
        if ($installed) {
            Write-Ok   "addons: $($blender.AddonsDir)  [add-on installed]"
        } else {
            Write-Note "addons: $($blender.AddonsDir)"
        }
    }
    Write-Host ''
    Write-Note 'Steam and standalone Blender share one user config dir per version, so each'
    Write-Note 'version above only needs installing once.'
    exit 0
}

$selected = $targets
if ($Version) {
    $selected = @($targets | Where-Object { $_.Version -eq $Version })
    if ($selected.Count -eq 0) {
        Write-Fail "No Blender $Version found. Available: $(($targets | ForEach-Object { $_.Version }) -join ', ')"
        exit 1
    }
} elseif (-not $All) {
    $selected = @($targets[0])
    if ($targets.Count -gt 1) {
        Write-Note "$($targets.Count) versions found; installing to newest ($($targets[0].Version)). Use -All or -Version to change that."
    }
}

if ($DryRun) { Write-Note 'DRY RUN -- nothing will be written' }

$failures = 0
foreach ($blender in $selected) {
    Write-Host ''
    Write-Host "Blender $($blender.Version)  [$($blender.Sources -join ', ')]" -ForegroundColor White
    if (-not (Install-Addon -SourceDir $sourceDir -AddonsDir $blender.AddonsDir)) { $failures++ }
}

Write-Host ''
if ($failures -gt 0) {
    Write-Fail "$failures install(s) failed."
    exit 1
}
if (-not $DryRun) {
    Write-Host 'Done. In Blender: Edit > Preferences > Add-ons, enable "Descent 3 POF/OOF Importer/Exporter".' -ForegroundColor Green
    Write-Host 'If it was already enabled, restart Blender (or toggle it off/on) so the new code loads.' -ForegroundColor Green
}
exit 0
