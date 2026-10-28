$ErrorActionPreference = "Stop"

# Compatibility entry point. The stable-speaker API uses the same shared
# audio.cpp server as the normal API and irodori-UI on port 8091.
$root = $PSScriptRoot
$sharedStart = Join-Path $root "start_audio_cpp_shared.ps1"
if (-not (Test-Path -LiteralPath $sharedStart -PathType Leaf)) {
    throw "Shared audio.cpp start script not found: $sharedStart"
}

Write-Host "[audio.cpp] voice-stable-speaker uses the shared server on port 8091; no dedicated server is started."
& $sharedStart
