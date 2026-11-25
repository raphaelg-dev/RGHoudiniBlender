# Builds the Blender extension (dist\rg_houdini_bridge-<version>.zip) and installs it in the user_default repository.
#   .\build.ps1                    build and install
#   .\build.ps1 -NoInstall         build only
#   .\build.ps1 -Blender <path>    use another blender.exe

param(
    [switch]$NoInstall,
    [string]$Blender = "C:\Program Files\Blender Foundation\Blender 5.2\blender.exe"
)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$dist = Join-Path $root "dist"

if (-not (Test-Path $Blender)) {
    throw "blender.exe not found at '$Blender'. Pass its location with -Blender <path>."
}

# Runs Blender and returns its exit code. Blender's stderr may hold the traceback of another
# add-on even when the command succeeds, and Windows PowerShell 5.1 turns each such line into
# an error that "Stop" makes fatal: the lines are printed as text and the exit code decides.
function Invoke-Blender {
    $ErrorActionPreference = "Continue"
    & $Blender @args 2>&1 | ForEach-Object { "$_" } | Out-Host
    return $LASTEXITCODE
}

Get-ChildItem -Path (Join-Path $root "addon") -Recurse -Directory -Filter "__pycache__" | Remove-Item -Recurse -Force
New-Item -ItemType Directory -Force $dist | Out-Null

# The GPL asks for the licence text to travel with the zip; the source folder keeps one copy, at the root.
$license = Join-Path $root "addon\LICENSE"
Copy-Item (Join-Path $root "LICENSE") $license
try {
    $code = Invoke-Blender --factory-startup --command extension build --source-dir (Join-Path $root "addon") --output-dir $dist
    if ($code -ne 0) { throw "build failed (Blender exit code $code)" }
} finally {
    Remove-Item $license
}

$version = (Select-String -Path (Join-Path $root "addon\blender_manifest.toml") -Pattern '^version = "(.+)"').Matches[0].Groups[1].Value
$package = Join-Path $dist "rg_houdini_bridge-$version.zip"
Write-Host "built: $package"

if (-not $NoInstall) {
    $code = Invoke-Blender --command extension install-file -r user_default -e $package
    if ($code -ne 0) { throw "install failed (Blender exit code $code)" }
    Write-Host "installed and enabled in Blender (user_default repository)"
}
