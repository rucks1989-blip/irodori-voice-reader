$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$pidPath = Join-Path $root "logs\kurisu_voice_api\server.pid"
if (Test-Path -LiteralPath $pidPath) {
    $apiPid = 0
    if ([int]::TryParse((Get-Content -LiteralPath $pidPath -Raw).Trim(), [ref]$apiPid)) {
        $process = Get-CimInstance Win32_Process -Filter "ProcessId = $apiPid" -ErrorAction SilentlyContinue
        if ($null -ne $process -and $process.CommandLine -like "*kurisu_voice_api.py*") {
            Stop-Process -Id $apiPid -Force -ErrorAction SilentlyContinue
        }
    }
    Remove-Item -LiteralPath $pidPath -Force -ErrorAction SilentlyContinue
}
$runtimeConfigPath = Join-Path $root "config\runtime.json"
$backendStopScript = "stop_audiocpp_irodori.ps1"
$stopBackendWithApi = $false
if (Test-Path -LiteralPath $runtimeConfigPath) {
    $runtimeConfig = Get-Content -LiteralPath $runtimeConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($runtimeConfig.backend_stop_script) {
        $backendStopScript = [string]$runtimeConfig.backend_stop_script
    }
    if ($null -ne $runtimeConfig.stop_backend_with_api) {
        $stopBackendWithApi = [bool]$runtimeConfig.stop_backend_with_api
    }
}
$backendStopPath = if ([IO.Path]::IsPathRooted($backendStopScript)) {
    $backendStopScript
} else {
    Join-Path $root $backendStopScript
}
if ($stopBackendWithApi -and (Test-Path -LiteralPath $backendStopPath)) {
    & $backendStopPath
}

