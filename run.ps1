#requires -Version 5.1
$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot

$candidates = New-Object System.Collections.Generic.List[string]
if ($env:GPT_SOVITS_HOME) {
    $candidates.Add((Join-Path $env:GPT_SOVITS_HOME "runtime\python.exe"))
}
$sibling = Join-Path (Split-Path -Parent $PSScriptRoot) "GPT-SoVITS-v2pro-20250604-nvidia50\runtime\python.exe"
$candidates.Add($sibling)
$generic = Join-Path (Split-Path -Parent $PSScriptRoot) "GPT-SoVITS\runtime\python.exe"
$candidates.Add($generic)

$python = $null
foreach ($candidate in $candidates) {
    if (Test-Path -LiteralPath $candidate) {
        $python = $candidate
        break
    }
}
if (-not $python) {
    $command = Get-Command python -ErrorAction SilentlyContinue
    if ($command) {
        $python = $command.Source
    }
}
if (-not $python) {
    Write-Error "Python not found. Set GPT_SOVITS_HOME or install Python 3.9+."
    exit 2
}

& $python -B -u -X "pycache_prefix=$PSScriptRoot\cache\pycache" `
    (Join-Path $PSScriptRoot "gptsovits_auto.py") @args
exit $LASTEXITCODE
