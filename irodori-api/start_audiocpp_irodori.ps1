$ErrorActionPreference = "Stop"

$root = $PSScriptRoot
$runtimeConfigPath = Join-Path $root "config\runtime.json"
$runtimeConfig = @{}
if (Test-Path -LiteralPath $runtimeConfigPath) {
    $runtimeConfig = Get-Content -LiteralPath $runtimeConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json
}
function Resolve-LocalPath([string]$value, [string]$fallback) {
    $selected = if ([string]::IsNullOrWhiteSpace($value)) { $fallback } else { $value }
    if ([System.IO.Path]::IsPathRooted($selected)) { return [System.IO.Path]::GetFullPath($selected) }
    return [System.IO.Path]::GetFullPath((Join-Path $root $selected))
}
$exe = Resolve-LocalPath $runtimeConfig.audio_cpp_executable "audio-cpp\bin\audiocpp_server.exe"
$binDir = Split-Path -Parent $exe
$config = Resolve-LocalPath $runtimeConfig.audio_cpp_config "config\audio_cpp_server.json"
$runtimeDir = Join-Path $root "logs\audio_cpp_irodori"
$pidPath = Join-Path $runtimeDir "server.pid"
$healthUrl = "http://127.0.0.1:7863/health"
$modelsUrl = "http://127.0.0.1:7863/v1/models"
$modelId = "irodori-500m-v3-q8"

New-Item -ItemType Directory -Force -Path $runtimeDir | Out-Null

function Test-IrodoriServer {
    try {
        $health = Invoke-RestMethod -Uri $healthUrl -TimeoutSec 3
        $models = Invoke-RestMethod -Uri $modelsUrl -TimeoutSec 3
        $ids = @($models.data | ForEach-Object { $_.id })
        return ($health -and $ids -contains $modelId)
    } catch {
        return $false
    }
}

if (Test-IrodoriServer) {
    Write-Host "[audio.cpp] Irodori server is already ready on port 7863."
    return
}

if (Get-NetTCPConnection -LocalPort 7863 -State Listen -ErrorAction SilentlyContinue) {
    throw "Port 7863 is in use by a different or unhealthy service."
}
if (-not (Test-Path -LiteralPath $exe)) {
    throw "audio.cpp server not found: $exe"
}
if (-not (Test-Path -LiteralPath $config)) {
    throw "audio.cpp config not found: $config"
}

$oldPath = $env:PATH
$env:PATH = "$binDir;$oldPath"
try {
    $process = Start-Process `
        -FilePath $exe `
        -ArgumentList @("--config", $config, "--log", "--log-file", (Join-Path $runtimeDir "trace.log")) `
        -WorkingDirectory $root `
        -RedirectStandardOutput (Join-Path $runtimeDir "stdout.log") `
        -RedirectStandardError (Join-Path $runtimeDir "stderr.log") `
        -WindowStyle Hidden `
        -PassThru
    Set-Content -LiteralPath $pidPath -Value $process.Id -Encoding ASCII

    $deadline = [DateTime]::UtcNow.AddSeconds(360)
    while ([DateTime]::UtcNow -lt $deadline) {
        if ($process.HasExited) {
            throw "audio.cpp server exited during startup (exit=$($process.ExitCode))."
        }
        if (Test-IrodoriServer) {
            Write-Host "[audio.cpp] Irodori server is ready (pid=$($process.Id), port=7863)."
            return
        }
        Start-Sleep -Milliseconds 500
    }
    throw "audio.cpp Irodori startup timed out."
} catch {
    if ($null -ne $process -and -not $process.HasExited) {
        Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
    }
    Remove-Item -LiteralPath $pidPath -Force -ErrorAction SilentlyContinue
    throw
} finally {
    $env:PATH = $oldPath
}
