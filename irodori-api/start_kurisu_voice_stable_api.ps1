$ErrorActionPreference = "Stop"
# Backward-compatible launcher. Both logical modes use the single API at
# http://127.0.0.1:8765; the request body selects voice_stable internally.
& (Join-Path $PSScriptRoot "start_kurisu_voice_api.ps1")
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
