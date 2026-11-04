$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

# 共有 audio.cpp サーバー（既定 8091）の起動確認・起動スクリプト。
# 通常版と声質安定版が共有する単一API（8765）から呼ばれます。
# 8091 が両モデルIDで正常応答していれば再利用し、二重起動しません。

$root = $PSScriptRoot
$bundleTemp = Join-Path $root "cache\temp"
New-Item -ItemType Directory -Force -Path $bundleTemp | Out-Null
$env:TEMP = $bundleTemp
$env:TMP = $bundleTemp
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
$config = Resolve-Configured $runtimeConfig.audio_cpp_shared_config "config\audio_cpp_server.shared.json"
$cudaBin = Resolve-Configured $runtimeConfig.audio_cpp_shared_cuda_bin (Split-Path $exe)
$port = if ($null -ne $runtimeConfig.audio_cpp_shared_port) { [int]$runtimeConfig.audio_cpp_shared_port } else { 8091 }
$healthUrl = "http://127.0.0.1:$port/health"
$modelsUrl = "http://127.0.0.1:$port/v1/models"
$requiredModelIds = @((Get-Content $config -Raw -Encoding UTF8 | ConvertFrom-Json).models | ForEach-Object {$_.id})
$runtimeDir = Join-Path $root "logs\audio_cpp_shared"
$pidPath = Join-Path $runtimeDir "server.pid"

New-Item -ItemType Directory -Force -Path $runtimeDir | Out-Null

function Test-SharedServer {
    try {
        $health = Invoke-RestMethod -Uri $healthUrl -TimeoutSec 3
        if ($health.status -ne "ok") { return $false }
        $models = Invoke-RestMethod -Uri $modelsUrl -TimeoutSec 3
        $ids = @($models.data | ForEach-Object { $_.id })
        foreach ($id in $requiredModelIds) {
            if ($ids -notcontains $id) { return $false }
        }
        return $true
    } catch {
        return $false
    }
}

function Get-PortOwner {
    # Some Windows TCP snapshots report PID 0 while the loopback socket changes
    # state. Use our process identity record, never the idle process, as fallback.
    $connections=@(Get-NetTCPConnection -LocalPort $port -ErrorAction SilentlyContinue | Where-Object {$_.OwningProcess -gt 0})
    foreach($connection in $connections){
        $candidate=Get-CimInstance Win32_Process -Filter "ProcessId = $($connection.OwningProcess)" -ErrorAction SilentlyContinue
        if($candidate){return $candidate}
    }
    $ownerPath=Join-Path $runtimeDir 'server.owner.json'
    if(Test-Path $ownerPath){
        $record=Get-Content $ownerPath -Raw -Encoding UTF8 | ConvertFrom-Json
        $candidate=Get-CimInstance Win32_Process -Filter "ProcessId = $($record.pid)" -ErrorAction SilentlyContinue
        if($candidate -and $record.executable -eq $exe -and $record.config -eq $config -and
           [math]::Abs(($candidate.CreationDate.ToUniversalTime()-[DateTime]::Parse($record.createdUtc).ToUniversalTime()).TotalMilliseconds) -lt 2 -and
           (Test-SameExe $candidate) -and (Get-ProcessConfigArg $candidate) -eq $config){return $candidate}
    }
    return $null
}

function Test-SameExe([object]$process) {
    if ($null -eq $process -or [string]::IsNullOrWhiteSpace($process.ExecutablePath)) { return $false }
    try {
        return ([IO.Path]::GetFullPath($process.ExecutablePath) -eq $exe)
    } catch {
        return $false
    }
}

function Get-ProcessConfigArg([object]$process) {
    $match = [regex]::Match([string]$process.CommandLine, '--config\s+(?:"([^"]+)"|(\S+))')
    if (-not $match.Success) { return "" }
    try {
        $value = if ($match.Groups[1].Success) { $match.Groups[1].Value } else { $match.Groups[2].Value }
        return [IO.Path]::GetFullPath($value)
    } catch {
        return $match.Groups[1].Value
    }
}

$initialOwner = Get-PortOwner
if ($null -ne $initialOwner -and (-not(Test-SameExe $initialOwner) -or (Get-ProcessConfigArg $initialOwner) -ne $config)) {
    throw "Port $port belongs to a different installation. It will not be reused or stopped."
}
if (Test-SharedServer) {
    $owner = Get-PortOwner
    if($null -eq $owner){throw "Port $port responds but its installation ownership cannot be verified."}
    Write-Host "[audio.cpp] shared server is already ready on port $port (pid=$($owner.ProcessId))."
    return
}

$owner = Get-PortOwner
$waitingForOther = $false
if ($null -ne $owner) {
    if (Test-SameExe $owner) {
        $ownerConfig = Get-ProcessConfigArg $owner
        if ($ownerConfig -ne "" -and $ownerConfig -eq $config) {
            Write-Host "[audio.cpp] shared server is starting with the same config (port $port); waiting for models."
            $waitingForOther = $true
        } else {
            throw "Port $port は別の server.json で起動中の共有サーバーです（現在: $ownerConfig / 要求: $config）。server.json を両モデルID入りに更新して再起動してください。"
        }
    } else {
        throw "Port $port is in use by a different process (pid=$($owner.ProcessId), $($owner.ExecutablePath))."
    }
}

if (-not (Test-Path -LiteralPath $exe -PathType Leaf)) {
    throw "共有audio.cppサーバーの実行ファイルが見つかりません: $exe`nconfig\runtime.json の audio_cpp_shared_executable を確認してください。"
}
if (-not (Test-Path -LiteralPath $config -PathType Leaf)) {
    throw "共有audio.cppサーバーの設定が見つかりません: $config`nconfig\runtime.json の audio_cpp_shared_config を確認してください。"
}

$process = $null
if (-not $waitingForOther) {
    $oldPath = $env:PATH
    if (Test-Path -LiteralPath $cudaBin) { $env:PATH = "$cudaBin;$oldPath" }
    try {
        $process = Start-Process `
            -FilePath $exe `
            -ArgumentList @("--config", ('"' + $config + '"'), "--log", "--log-file", ('"' + (Join-Path $runtimeDir "trace.log") + '"')) `
            -WorkingDirectory (Split-Path $exe) `
            -RedirectStandardOutput (Join-Path $runtimeDir "stdout.log") `
            -RedirectStandardError (Join-Path $runtimeDir "stderr.log") `
            -WindowStyle Hidden `
            -PassThru
        [void]$process.Handle
        Set-Content -LiteralPath $pidPath -Value $process.Id -Encoding ASCII
        $identity=Get-CimInstance Win32_Process -Filter "ProcessId = $($process.Id)" -ErrorAction Stop
        $record=@{pid=$process.Id;createdUtc=$identity.CreationDate.ToUniversalTime().ToString('o');executable=$exe;config=$config}
        [IO.File]::WriteAllText((Join-Path $runtimeDir 'server.owner.json'),($record | ConvertTo-Json),(New-Object Text.UTF8Encoding($false)))
    } finally {
        $env:PATH = $oldPath
    }
}

$deadline = [DateTime]::UtcNow.AddSeconds(360)
while ([DateTime]::UtcNow -lt $deadline) {
    if (Test-SharedServer) {
        $activePid = (Get-PortOwner).ProcessId
        Write-Host "[audio.cpp] shared server is ready (port=$port, models=$($requiredModelIds -join ', '), pid=$activePid)."
        return
    }
    if ($null -ne $process -and $process.HasExited) {
        throw "共有audio.cppサーバーが起動前に終了しました（exit=$($process.ExitCode)）。ログ: $(Join-Path $runtimeDir 'stderr.log')"
    }
    Start-Sleep -Milliseconds 500
}
throw "共有audio.cppサーバーの起動確認がタイムアウトしました（360秒）。ログ: $(Join-Path $runtimeDir 'stderr.log')"
