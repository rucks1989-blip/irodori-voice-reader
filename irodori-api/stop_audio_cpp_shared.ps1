$ErrorActionPreference = "Stop"

# 共有 audio.cpp サーバー（既定 8091）の停止スクリプト。
# irodori-UI と共有しているため、通常は停止不要です（API停止でも自動停止しません）。

$root = $PSScriptRoot
$runtimeConfigPath = Join-Path $root "config\runtime.json"
$runtimeConfig = @{}
if (Test-Path -LiteralPath $runtimeConfigPath) {
    $runtimeConfig = Get-Content -LiteralPath $runtimeConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json
}

function Resolve-Configured([string]$value, [string]$fallback) {
    $selected = if ([string]::IsNullOrWhiteSpace($value)) { $fallback } else { $value }
    if ([IO.Path]::IsPathRooted($selected)) { return [IO.Path]::GetFullPath($selected) }
    return [IO.Path]::GetFullPath((Join-Path $root $selected))
}

$exe = Resolve-Configured $runtimeConfig.audio_cpp_shared_executable "audio-cpp\bin\audiocpp_server.exe"
$pidPath = Join-Path $root "logs\audio_cpp_shared\server.pid"

if (-not (Test-Path -LiteralPath $pidPath)) {
    Write-Host "[audio.cpp] 共有サーバーはこのチェックアウトから起動されていません。停止しません。"
    return
}

$serverPid = 0
if (-not [int]::TryParse((Get-Content -LiteralPath $pidPath -Raw).Trim(), [ref]$serverPid)) {
    Remove-Item -LiteralPath $pidPath -Force -ErrorAction SilentlyContinue
    return
}

$process = Get-CimInstance Win32_Process -Filter "ProcessId = $serverPid" -ErrorAction SilentlyContinue
if ($null -ne $process -and $process.ExecutablePath) {
    try {
        $actual = [IO.Path]::GetFullPath($process.ExecutablePath)
        if ($actual -eq $exe -and ([string]$process.CommandLine).IndexOf((Resolve-Configured $runtimeConfig.audio_cpp_shared_config "config\audio_cpp_server.shared.json"),[StringComparison]::OrdinalIgnoreCase) -ge 0) {
            Stop-Process -Id $serverPid -Force -ErrorAction SilentlyContinue
            Write-Host "[audio.cpp] 共有サーバーを停止しました（pid=$serverPid）。irodori-UI の音声も使えなくなります。"
        } else {
            Write-Host "[audio.cpp] PIDファイルのプロセスは設定された共有サーバーではありません。停止しません。"
        }
    } catch {
        Write-Host "[audio.cpp] プロセス確認に失敗しました。停止しません。"
    }
}
Remove-Item -LiteralPath $pidPath -Force -ErrorAction SilentlyContinue
