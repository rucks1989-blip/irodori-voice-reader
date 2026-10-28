$ErrorActionPreference = "Stop"
# Backward-compatible stop command for the former stable listener.
& (Join-Path $PSScriptRoot "stop_kurisu_voice_api.ps1")
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host "[Irodori API] unified API stopped on port 8765."
