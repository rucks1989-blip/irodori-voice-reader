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
function Show-SetupDialog([string]$Kind,[string]$Message,[string]$Filter='') {
    $dialogRoot=Join-Path $env:LOCALAPPDATA 'IrodoriVoiceReader\Installer\Dialogs'
    New-Item -ItemType Directory -Force -Path $dialogRoot | Out-Null
    $source=Join-Path $PSScriptRoot 'setup-dialog.cs'
    $hash=(Get-FileHash $source -Algorithm SHA256).Hash.Substring(0,16)
    $exe=Join-Path $dialogRoot ("SetupDialog-$hash.exe")
    if(-not(Test-Path $exe)){
        Add-Type -Path $source -ReferencedAssemblies @('System.Windows.Forms','System.Drawing','System.Web.Extensions') -OutputAssembly $exe -OutputType WindowsApplication
    }
    $request=Join-Path $dialogRoot ([guid]::NewGuid().ToString('N')+'.json')
    Save-Json $request @{kind=$Kind;message=$Message;filter=$Filter}
    Write-Host ("確認画面を待っています: "+$Message) -ForegroundColor Yellow
    $process=Add-SetupProcess (Start-Process $exe -ArgumentList @(('"'+$request+'"')) -PassThru -WindowStyle Normal)
    while(-not $process.WaitForExit(200)){Assert-SetupActive}
    if($process.ExitCode -ne 0 -or -not(Test-Path ($request+'.result'))){throw 'セットアップ画面を確認できませんでした。'}
    $answer=Get-Content ($request+'.result') -Raw -Encoding UTF8 | ConvertFrom-Json
    return [string]$answer.value
}
function Confirm([string]$Message) { return (Show-SetupDialog 'confirm' $Message) -eq 'yes' }
function Assert-SetupActive {
    if($script:CancelRequest -and (Test-Path ($script:CancelRequest+'.cancel'))){throw [OperationCanceledException]::new('セットアップを安全に中止しました。再実行できます。')}
}
function Add-SetupProcess($Process){
    [void]$Process.Handle
    if($script:SetupJob){try{$script:SetupJob.Add($Process.Handle)}catch{if(-not $Process.HasExited){$Process.Kill();$Process.WaitForExit()};throw}}
    return $Process
}
function Quote-WindowsArgument([string]$Value){
    return '"'+([regex]::Replace([regex]::Replace($Value,'(\\*)"','$1$1\"'),'(\\+)$','$1$1'))+'"'
}
function Run([string]$Exe,[string[]]$Arguments){
    Assert-SetupActive
    $logRoot=if($env:IVR_SETUP_LOG_ROOT){$env:IVR_SETUP_LOG_ROOT}else{$env:TEMP}
    $out=Join-Path $logRoot ('command-'+[guid]::NewGuid().ToString('N')+'.stdout.log')
    $err=$out+'.stderr.log'
    $launchArguments=if([IO.Path]::GetFileName($Exe) -ieq 'cmd.exe'){($Arguments -join ' ')}else{($Arguments | ForEach-Object {Quote-WindowsArgument $_}) -join ' '}
    $p=Add-SetupProcess (Start-Process $Exe -ArgumentList $launchArguments -PassThru -WindowStyle Hidden -RedirectStandardOutput $out -RedirectStandardError $err)
    Write-Host ("実行中: "+[IO.Path]::GetFileName($Exe)) -ForegroundColor Cyan
    Write-Host ("ログ: "+$out)
    $readers=@()
    foreach($file in @($out,$err)){
        $stream=[IO.File]::Open($file,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::ReadWrite)
        $readers+=New-Object IO.StreamReader($stream,[Text.Encoding]::UTF8,$true)
    }
    $lastNotice=[DateTime]::UtcNow
    try{
        while(-not $p.WaitForExit(200)){
            Assert-SetupActive
            foreach($reader in $readers){$chunk=$reader.ReadToEnd();if($chunk){Write-Host -NoNewline $chunk}}
            if(([DateTime]::UtcNow-$lastNotice).TotalSeconds -ge 5){Write-Host ("処理継続中: "+[IO.Path]::GetFileName($Exe)+" / PID "+$p.Id);$lastNotice=[DateTime]::UtcNow}
        }
        $code=$p.ExitCode
        foreach($reader in $readers){$chunk=$reader.ReadToEnd();if($chunk){Write-Host -NoNewline $chunk}}
    }
    finally{foreach($reader in $readers){$reader.Dispose()};if(-not $p.HasExited){$p.Kill();$p.WaitForExit()}}
    $lines=@(Get-Content $out -ErrorAction SilentlyContinue)+@(Get-Content $err -ErrorAction SilentlyContinue)
    if($env:IVR_SETUP_LOG_ROOT){[IO.File]::WriteAllText((Join-Path $logRoot 'last-command.log'),($lines -join "`r`n"),$Utf8)}
    if($code -notin @(0,3010)){throw ("コマンド終了コード: $code ($Exe)`r`n"+(($lines | Select-Object -Last 16) -join "`r`n"))}
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
    Run $curl.Source @('--location','--fail','--retry','3','--retry-delay','2','--proto','=https','--proto-redir','=https','--continue-at','-','--output',$partial,$Url)
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
function Assert-FreePorts([int[]]$Ports=@(8765,8091)) {
    foreach ($port in $Ports) {
        if (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue) {
            throw "ポート$port は使用中です。今回の専用ポートが他のアプリに使われています。既存プロセスは停止しません。"
        }
    }
}
function Assert-LocalDirectory([string]$Path) {
    $full = [IO.Path]::GetFullPath($Path).TrimEnd('\')
    if ($full -match '^[\\]{2}' -or $full -match '[%!]') { throw 'ネットワーク共有または % ! を含む保存先は、この試験版では利用できません。' }
    return $full
}

function Find-FreePort([int]$Start) {
    for($candidate=$Start;$candidate -lt $Start+1000;$candidate++){
        $listener=New-Object Net.Sockets.TcpListener([Net.IPAddress]::Loopback,$candidate)
        try{$listener.Start();return $candidate}catch{}finally{$listener.Stop()}
    }
    throw '空きポートを確保できませんでした。'
}
function Get-ExistingEnvironment {
    foreach($folder in @('NativeHost','ChromeNativeHost')){
        $config=Join-Path $env:LOCALAPPDATA "IrodoriVoiceReader\$folder\config.json"
        if(Test-Path $config){"既存Native Host: $folder"}
    }
    foreach($key in @('HKCU:\Software\Google\Chrome\NativeMessagingHosts','HKCU:\Software\Mozilla\NativeMessagingHosts','HKLM:\Software\Google\Chrome\NativeMessagingHosts','HKLM:\Software\Mozilla\NativeMessagingHosts')){
        Get-ChildItem $key -ErrorAction SilentlyContinue | Where-Object PSChildName -match 'irodori' | ForEach-Object {"既存登録: $($_.PSChildName)"}
    }
    foreach($port in @(8765,8091)){
        if(Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue){"既存ポート使用中: $port"}
    }
    Get-CimInstance Win32_Process | Where-Object { $_.Name -match 'audiocpp|python' -and $_.CommandLine -match 'irodori|kurisu_voice_api|audio.cpp|audiocpp' } | ForEach-Object {"既存エンジン/API: PID $($_.ProcessId) ($($_.Name))"}
}

function Select-Browsers {
    $choice=Show-SetupDialog 'browsers' 'Chromeのみ / Firefoxのみ / 両方から選んでください。'
    if($choice -notin @('chrome','firefox','both')){throw 'ブラウザー選択を中止しました。'}
    return $choice
}

function Get-BackendChoice([ValidateSet('auto','cpu','cuda')][string]$Requested,[string]$GpuArch){
    if($Requested -ne 'auto'){return $Requested}
    if($GpuArch){return 'cuda'}
    return 'cpu'
}

$script:SetupJob=$null
$script:CancelRequest=$null
function Start-SetupCancellation {
    if(-not ('IvrSetupJob' -as [type])){Add-Type -Path (Join-Path $PSScriptRoot 'setup-job.cs')}
    $script:SetupJob=New-Object IvrSetupJob
    $dialogRoot=Join-Path $env:LOCALAPPDATA 'IrodoriVoiceReader\Installer\Dialogs'
    New-Item -ItemType Directory -Force $dialogRoot | Out-Null
    $source=Join-Path $PSScriptRoot 'setup-dialog.cs'
    $exe=Join-Path $dialogRoot ('SetupDialog-'+(Get-FileHash $source).Hash.Substring(0,16)+'.exe')
    if(-not(Test-Path $exe)){Add-Type -Path $source -ReferencedAssemblies @('System.Windows.Forms','System.Drawing','System.Web.Extensions') -OutputAssembly $exe -OutputType WindowsApplication}
    $script:CancelRequest=Join-Path $dialogRoot ([guid]::NewGuid().ToString('N')+'.json')
    Save-Json $script:CancelRequest @{kind='progress';message=''}
    [void](Start-Process $exe -ArgumentList @(('"'+$script:CancelRequest+'"')) -WindowStyle Normal -PassThru)
}
function Stop-SetupCancellation {
    if($script:CancelRequest){[IO.File]::WriteAllText(($script:CancelRequest+'.done'),'done')}
    if($script:SetupJob){$script:SetupJob.Dispose();$script:SetupJob=$null}
}

function Copy-BackendRuntime([string]$Source,[string]$Destination){
    $src=[IO.Path]::GetFullPath($Source)
    $dst=[IO.Path]::GetFullPath($Destination)
    if($src -eq $dst){throw 'コピー元と専用保存先が同じです。'}
    if(-not(Test-Path (Join-Path $src 'audiocpp_server.exe'))){throw '選択した完成品にaudiocpp_server.exeが見つかりません。'}
    New-Item -ItemType Directory -Force -Path $dst | Out-Null
    $runtimeFiles=@(Get-Item (Join-Path $src 'audiocpp_server.exe'))+@(Get-ChildItem -LiteralPath $src -Filter '*.dll' -File)
    foreach($file in $runtimeFiles){
        Assert-SetupActive
        Write-Host ('実行ファイル・依存DLLを検査/配置中: '+$file.Name)
        $target=Join-Path $dst $file.Name
        if((Test-Path $target) -and (Get-FileHash $target).Hash -eq (Get-FileHash $file.FullName).Hash){continue}
        Copy-Item -LiteralPath $file.FullName -Destination $target -Force
    }
}

function Test-StableCandidate([string]$Directory,[string]$Model,[string]$Backend,[int]$Port){
    $candidateConfig=Join-Path $Directory 'candidate.json'
    $candidateModels=@('irodori-500m-v3-q8','irodori-500m-v3-q8-voice-stable-speaker') | ForEach-Object {@{id=$_;family='irodori_tts';path=$Model;task='tts';mode='offline';session_options=@{'irodori_tts.mem_saver'='true'}}}
    Save-Json $candidateConfig @{host='127.0.0.1';port=$Port;backend=$Backend;models=@($candidateModels)}
    Assert-FreePorts @($Port)
    $p=Add-SetupProcess (Start-Process (Join-Path $Directory 'audiocpp_server.exe') -ArgumentList @('--config',('"'+$candidateConfig+'"')) -WorkingDirectory $Directory -PassThru -WindowStyle Hidden -RedirectStandardOutput (Join-Path $Directory 'candidate.stdout.log') -RedirectStandardError (Join-Path $Directory 'candidate.stderr.log'))
    try{
        $deadline=[DateTime]::UtcNow.AddMinutes(3);$ready=$false
        while([DateTime]::UtcNow -lt $deadline){
            Assert-SetupActive
            if($p.HasExited){throw '声質安定版の完成品が起動できませんでした。通常版を保持します。'}
            try{$h=Invoke-RestMethod "http://127.0.0.1:$Port/health" -TimeoutSec 2;if($h.status -eq 'ok'){$ready=$true;break}}catch{}
            Start-Sleep -Milliseconds 300
        }
        if(-not $ready){throw '声質安定版の起動確認がタイムアウトしました。'}
        Run (Get-Python) @((Join-Path $PSScriptRoot 'verify-backend.py'),"http://127.0.0.1:$Port")
    }finally{if(-not $p.HasExited){$p.Kill();$p.WaitForExit()}}
}
