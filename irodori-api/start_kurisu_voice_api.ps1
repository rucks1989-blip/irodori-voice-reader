$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$bundleTemp = Join-Path $root "cache\temp"
New-Item -ItemType Directory -Force -Path $bundleTemp | Out-Null
$env:TEMP = $bundleTemp
$env:TMP = $bundleTemp
$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    $python = "python"
}
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

$runtimeConfigPath = Join-Path $root "config\runtime.json"
$backendStartScript = "start_audiocpp_irodori.ps1"
if (Test-Path -LiteralPath $runtimeConfigPath) {
    $runtimeConfig = Get-Content -LiteralPath $runtimeConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($runtimeConfig.backend_start_script) {
        $backendStartScript = [string]$runtimeConfig.backend_start_script
    }
}
$backendStartPath = if ([IO.Path]::IsPathRooted($backendStartScript)) {
    $backendStartScript
} else {
    Join-Path $root $backendStartScript
}
if (-not (Test-Path -LiteralPath $backendStartPath)) {
    throw "TTS backend start script not found: $backendStartPath"
}
& $backendStartPath
$health = "http://127.0.0.1:8765/health"
try {
    $ready = Invoke-RestMethod -Uri $health -TimeoutSec 2
    if ($ready.status -eq "ok") {
        Write-Host "[Kurisu Voice API] mode=unified port=8765 model=$($ready.model) reference=wav,speaker_embedding device=$($ready.device) (already ready)"
        return
    }
} catch {}

$logDir = Join-Path $root "logs\kurisu_voice_api"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$process = Start-Process `
    -FilePath $python `
    -ArgumentList @('"' + (Join-Path $root "kurisu_voice_api.py") + '"') `
    -WorkingDirectory $root `
    -RedirectStandardOutput (Join-Path $logDir "stdout.log") `
    -RedirectStandardError (Join-Path $logDir "stderr.log") `
    -WindowStyle Hidden `
    -PassThru
$pidPath = Join-Path $logDir "server.pid"
Set-Content -LiteralPath $pidPath -Value $process.Id -Encoding ASCII
$deadline = [DateTime]::UtcNow.AddSeconds(20)
while ([DateTime]::UtcNow -lt $deadline) {
    if ($process.HasExited) {
        throw "Kurisu Voice API exited during startup (exit=$($process.ExitCode))."
    }
    try {
        $ready = Invoke-RestMethod -Uri $health -TimeoutSec 3
        if ($ready.status -eq "ok") {
            Write-Host "[Kurisu Voice API] mode=unified port=8765 model=$($ready.model) reference=wav,speaker_embedding device=$($ready.device) pid=$($process.Id)"
            return
        }
    } catch {}
    Start-Sleep -Milliseconds 300
}
Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $pidPath -Force -ErrorAction SilentlyContinue
throw "Kurisu Voice API startup timed out."
