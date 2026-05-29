param(
    [switch]$SkipPyArmor,
    [switch]$Console,
    [switch]$CleanNodeModules
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root = (Resolve-Path (Join-Path $ScriptDir "..\..")).Path
$BuildDir = Join-Path $Root "build"
$DistDir = Join-Path $Root "dist"
$AgentVenvPython = Join-Path $Root "agent\.venv\Scripts\python.exe"

function Assert-InRepoPath {
    param([string]$Path)
    $resolved = [System.IO.Path]::GetFullPath($Path)
    $rootFull = [System.IO.Path]::GetFullPath($Root)
    if (-not $resolved.StartsWith($rootFull, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to touch path outside repo: $resolved"
    }
    return $resolved
}

function Remove-InRepoDir {
    param([string]$Path)
    $target = Assert-InRepoPath $Path
    if (Test-Path -LiteralPath $target) {
        Remove-Item -LiteralPath $target -Recurse -Force
    }
}

function Remove-InRepoFile {
    param([string]$Path)
    $target = Assert-InRepoPath $Path
    if (Test-Path -LiteralPath $target) {
        Remove-Item -LiteralPath $target -Force
    }
}

function Assert-LastExitCode {
    param([string]$Label)
    if ($LASTEXITCODE -ne 0) {
        throw "$Label failed with exit code $LASTEXITCODE"
    }
}

function Copy-Dir {
    param(
        [string]$Source,
        [string]$Destination
    )
    $dest = Assert-InRepoPath $Destination
    if (Test-Path -LiteralPath $dest) {
        Remove-Item -LiteralPath $dest -Recurse -Force
    }
    Copy-Item -LiteralPath $Source -Destination $dest -Recurse
}

if (-not (Test-Path -LiteralPath $AgentVenvPython)) {
    py -3.11 -m venv (Join-Path $Root "agent\.venv")
}

Write-Host "Installing Python build dependencies..."
& $AgentVenvPython -m pip install -U pip
Assert-LastExitCode "pip upgrade"
$AgentEditable = (Join-Path $Root "agent") + "[build]"
& $AgentVenvPython -m pip install -e $AgentEditable
Assert-LastExitCode "Python package install"

Write-Host "Building frontend..."
Push-Location (Join-Path $Root "frontend")
try {
    if ($CleanNodeModules) {
        Remove-InRepoDir (Join-Path $Root "frontend\node_modules")
        npm ci
        Assert-LastExitCode "npm ci"
    } else {
        npm install
        Assert-LastExitCode "npm install"
    }
    npm run build
    Assert-LastExitCode "frontend build"
}
finally {
    Pop-Location
}

Remove-InRepoDir (Join-Path $BuildDir "pyarmor")
Remove-InRepoDir (Join-Path $BuildDir "flowboard")
Remove-InRepoDir (Join-Path $BuildDir "update")
Remove-InRepoDir (Join-Path $BuildDir "demucs_soundfile")
Remove-InRepoFile (Join-Path $DistDir "Flowboard.exe")
Remove-InRepoFile (Join-Path $DistDir "update.exe")
Remove-InRepoFile (Join-Path $DistDir "demucs_soundfile.exe")
Remove-InRepoFile (Join-Path $DistDir "update.json")
Remove-InRepoFile (Join-Path $DistDir "build-info.json")
Remove-InRepoFile (Join-Path $DistDir "flowboard-windows.zip")
Remove-InRepoFile (Join-Path $DistDir "flowboard-tools-windows.zip")
Remove-InRepoFile (Join-Path $DistDir "flowboard-full-windows.zip")

if ($SkipPyArmor) {
    $ObfRoot = Join-Path $Root "agent"
    Write-Host "Skipping PyArmor; PyInstaller will use source package."
} else {
    $ObfRoot = Join-Path $BuildDir "pyarmor"
    Write-Host "Obfuscating Python package with PyArmor..."
    $PyArmor = Join-Path $Root "agent\.venv\Scripts\pyarmor.exe"
    & $PyArmor gen -O $ObfRoot -r (Join-Path $Root "agent\flowboard")
    Assert-LastExitCode "PyArmor obfuscation"
}

Write-Host "Building Flowboard.exe with PyInstaller..."
$env:FLOWBOARD_ROOT = $Root
$env:FLOWBOARD_OBF_ROOT = $ObfRoot
$env:FLOWBOARD_CONSOLE = if ($Console) { "1" } else { "0" }
& $AgentVenvPython -m PyInstaller --noconfirm (Join-Path $ScriptDir "flowboard.spec")
Assert-LastExitCode "PyInstaller build"

Write-Host "Building update.exe..."
& $AgentVenvPython -m PyInstaller --noconfirm (Join-Path $ScriptDir "update.spec")
Assert-LastExitCode "Updater build"

Write-Host "Building demucs_soundfile.exe..."
& $AgentVenvPython -m PyInstaller --noconfirm (Join-Path $ScriptDir "demucs.spec")
Assert-LastExitCode "Demucs tool build"

$ExtensionOut = Join-Path $DistDir "extension"
Copy-Dir -Source (Join-Path $Root "extension") -Destination $ExtensionOut

$UpdateRepo = if ($env:FLOWBOARD_UPDATE_REPO) { $env:FLOWBOARD_UPDATE_REPO } else { "Namzee10112002/flowboard-exe" }
$UpdateConfig = @{
    repo = $UpdateRepo
    asset = "flowboard-windows.zip"
    tools_asset = "flowboard-tools-windows.zip"
} | ConvertTo-Json
$UpdateConfigPath = Join-Path $DistDir "update.json"
Set-Content -LiteralPath $UpdateConfigPath -Value $UpdateConfig -Encoding UTF8

$FrontendPackage = Get-Content -Raw -LiteralPath (Join-Path $Root "frontend\package.json") | ConvertFrom-Json
$BuildInfo = @{
    version = [string]$FrontendPackage.version
    built_at = (Get-Date).ToUniversalTime().ToString("o")
    release_repo = $UpdateRepo
    codex_git_repo_check_skipped = $true
} | ConvertTo-Json
$BuildInfoPath = Join-Path $DistDir "build-info.json"
Set-Content -LiteralPath $BuildInfoPath -Value $BuildInfo -Encoding UTF8

$ReleaseDir = Join-Path $DistDir "release"
$ToolsReleaseDir = Join-Path $DistDir "release-tools"
$FullReleaseDir = Join-Path $DistDir "release-full"
Remove-InRepoDir $ReleaseDir
Remove-InRepoDir $ToolsReleaseDir
Remove-InRepoDir $FullReleaseDir

New-Item -ItemType Directory -Path $ReleaseDir | Out-Null
Copy-Item -LiteralPath (Join-Path $DistDir "Flowboard.exe") -Destination $ReleaseDir
Copy-Item -LiteralPath (Join-Path $DistDir "update.exe") -Destination $ReleaseDir
Copy-Item -LiteralPath $UpdateConfigPath -Destination $ReleaseDir
Copy-Item -LiteralPath $BuildInfoPath -Destination $ReleaseDir
Copy-Dir -Source $ExtensionOut -Destination (Join-Path $ReleaseDir "extension")
Compress-Archive -Path (Join-Path $ReleaseDir "*") -DestinationPath (Join-Path $DistDir "flowboard-windows.zip") -Force

New-Item -ItemType Directory -Path (Join-Path $ToolsReleaseDir "tools") | Out-Null
Copy-Item -LiteralPath (Join-Path $DistDir "demucs_soundfile.exe") -Destination (Join-Path $ToolsReleaseDir "tools")
Compress-Archive -Path (Join-Path $ToolsReleaseDir "*") -DestinationPath (Join-Path $DistDir "flowboard-tools-windows.zip") -Force

Copy-Dir -Source $ReleaseDir -Destination $FullReleaseDir
Copy-Dir -Source (Join-Path $ToolsReleaseDir "tools") -Destination (Join-Path $FullReleaseDir "tools")
Compress-Archive -Path (Join-Path $FullReleaseDir "*") -DestinationPath (Join-Path $DistDir "flowboard-full-windows.zip") -Force

Write-Host ""
Write-Host "Done."
Write-Host "Executable: $(Join-Path $DistDir 'Flowboard.exe')"
Write-Host "Updater: $(Join-Path $DistDir 'update.exe')"
Write-Host "Demucs tool: $(Join-Path $DistDir 'demucs_soundfile.exe')"
Write-Host "Chrome extension folder: $ExtensionOut"
Write-Host "Core update zip: $(Join-Path $DistDir 'flowboard-windows.zip')"
Write-Host "Tools zip: $(Join-Path $DistDir 'flowboard-tools-windows.zip')"
Write-Host "Full install zip: $(Join-Path $DistDir 'flowboard-full-windows.zip')"
