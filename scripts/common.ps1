Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
$Utf8 = New-Object Text.UTF8Encoding($false)
[Console]::OutputEncoding=$Utf8
$OutputEncoding=$Utf8
function Save-Json([string]$Path, $Value) {
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Path) | Out-Null
    $temporary = "$Path.new"
    [IO.File]::WriteAllText($temporary, ($Value | ConvertTo-Json -Depth 30), $Utf8)
    Move-Item -LiteralPath $temporary -Destination $Path -Force
}
function Confirm([string]$Message) {
    Add-Type -AssemblyName System.Windows.Forms
    return [Windows.Forms.MessageBox]::Show($Message, 'Irodori Voice Reader', 'YesNo', 'Question', 'Button2') -eq 'Yes'
}
function Run([string]$Exe, [string[]]$Arguments) {
    $saved=$ErrorActionPreference
    try{
        $ErrorActionPreference='Continue'
        $lines=@(& $Exe @Arguments 2>&1 | ForEach-Object {Write-Host $_;[string]$_})
        $code=$LASTEXITCODE
    }finally{$ErrorActionPreference=$saved}
    if($env:IVR_SETUP_LOG_ROOT -and (Test-Path $env:IVR_SETUP_LOG_ROOT)){
        [IO.File]::WriteAllText((Join-Path $env:IVR_SETUP_LOG_ROOT 'last-command.log'),($lines -join "`r`n"),$Utf8)
    }
    if ($code -notin @(0,3010)) {
        $tail=($lines | Select-Object -Last 16) -join "`r`n"
        throw "コマンドの終了コード: $code ($([IO.Path]::GetFileName($Exe)))`r`n$tail"
    }
}
function Get-Verified([string]$Url, [string]$Destination, [string]$Sha256, [long]$Bytes = 0) {
    if ($Url -notmatch '^https://') { throw 'HTTPS以外の取得元は利用しません。' }
    if ($Sha256 -notmatch '^[0-9a-fA-F]{64}$') { throw '取得ファイルの検証用ハッシュがありません。' }
    if (Test-Path -LiteralPath $Destination) {
        if ((Get-FileHash -LiteralPath $Destination -Algorithm SHA256).Hash -eq $Sha256) { return }
        throw "既存ファイルの検査に失敗しました。上書きせず停止します: $([IO.Path]::GetFileName($Destination))"
    }
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Destination) | Out-Null
    $partial = "$Destination.partial"
    $curl = Get-Command curl.exe -ErrorAction SilentlyContinue
    if (-not $curl) { throw 'Windows標準のcurl.exeが見つかりません。Windows Updateを確認してください。' }
    & $curl.Source --location --fail --retry 3 --retry-delay 2 --proto '=https' --proto-redir '=https' --continue-at - --output $partial $Url
    if ($LASTEXITCODE -ne 0) { throw "ダウンロード失敗。再実行すると再開します (curl=$LASTEXITCODE)。" }
    if ($Bytes -gt 0 -and (Get-Item -LiteralPath $partial).Length -ne $Bytes) { throw 'ダウンロードの容量が想定と一致しません。' }
    if ((Get-FileHash -LiteralPath $partial -Algorithm SHA256).Hash -ne $Sha256) {
        Move-Item -LiteralPath $partial -Destination ($partial + '.rejected-' + [guid]::NewGuid().ToString('N'))
        throw 'ファイル検査に失敗しました。破損ファイルを隔離しました。setup.batを再実行してください。'
    }
    Move-Item -LiteralPath $partial -Destination $Destination
}
function Get-Gpu {
    $smi = Get-Command nvidia-smi.exe -ErrorAction SilentlyContinue
    if ($smi) {
        $saved=$ErrorActionPreference
        try{$ErrorActionPreference='Continue';$rows = @(& $smi.Source '--query-gpu=name,compute_cap,driver_version' '--format=csv,noheader' 2>$null);$row=$rows | Select-Object -First 1}finally{$ErrorActionPreference=$saved}
        if ($LASTEXITCODE -eq 0 -and $row -match '^(.+?),\s*(\d+)\.(\d+),\s*(.+)$') {
            return [pscustomobject]@{ name=$Matches[1].Trim(); arch=($Matches[2]+$Matches[3]); driver=$Matches[4].Trim() }
        }
    }
    return [pscustomobject]@{ name='NVIDIA CUDA GPUを確認できません'; arch=''; driver='' }
}
function Get-Python {
    $candidates = @((Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe'))
    $command = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($command -and $command.Source -notmatch 'WindowsApps') { $candidates += $command.Source }
    $py = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($py) {
        $saved=$ErrorActionPreference
        try{$ErrorActionPreference='Continue';$candidate = & $py.Source -3.12 -c 'import sys;print(sys.executable)' 2>$null}finally{$ErrorActionPreference=$saved}
        if ($LASTEXITCODE -eq 0 -and $candidate) { $candidates += [string]$candidate }
    }
    foreach ($candidate in ($candidates | Select-Object -Unique)) {
        if (Test-Path -LiteralPath $candidate) {
            $saved=$ErrorActionPreference
            try{$ErrorActionPreference='Continue';$v = @(& $candidate -c 'import sys;print(sys.version_info.major*100+sys.version_info.minor);print(sys.maxsize)' 2>$null)}finally{$ErrorActionPreference=$saved}
            if ($LASTEXITCODE -eq 0 -and $v.Count -eq 2 -and $v[0] -eq '312' -and [long]$v[1] -gt 4294967296) { return $candidate }
        }
    }
    return ''
}
function Install-Winget([string]$Id, [string[]]$Extra = @()) {
    $wg = Get-Command winget.exe -ErrorAction SilentlyContinue
    if (-not $wg) { throw 'wingetがありません。Microsoft Storeで「アプリ インストーラー」を更新してください。' }
    Run $wg.Source (@('install','--id',$Id,'--exact','--source','winget','--accept-package-agreements','--accept-source-agreements','--disable-interactivity') + $Extra)
}
function Assert-FreePorts {
    foreach ($port in @(8765,8091)) {
        if (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue) {
            throw "ポート$port は使用中です。既存Irodoriまたは他のアプリを本人の操作で終了してから再試行してください。自動停止は行いません。"
        }
    }
}
function Assert-LocalDirectory([string]$Path) {
    $full = [IO.Path]::GetFullPath($Path).TrimEnd('\')
    if ($full -match '^[\\]{2}' -or $full -match '[%!]') { throw 'ネットワーク共有または % ! を含む保存先は、この試験版では利用できません。' }
    return $full
}
