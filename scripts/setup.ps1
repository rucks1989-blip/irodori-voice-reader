[CmdletBinding()]
param([string]$InstallRoot=(Join-Path $env:LOCALAPPDATA 'IrodoriVoiceReader\App'),[switch]$CheckOnly,[ValidateSet('auto','cpu','cuda')][string]$Backend='auto',[switch]$NewEnvironment,[ValidateSet('prompt','chrome','firefox','both')][string]$Browsers='prompt',[switch]$Unattended,[string]$ModelPath='',[string]$ReferenceWav='',[switch]$NoOpenGuide)
. (Join-Path $PSScriptRoot 'common.ps1')
. (Join-Path $PSScriptRoot 'install-files.ps1')
$SourceRoot=Split-Path -Parent $PSScriptRoot
$manifest=Get-Content (Join-Path $SourceRoot 'distribution.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$InstallRoot=Assert-LocalDirectory $InstallRoot
$StateRoot=Join-Path $env:LOCALAPPDATA 'IrodoriVoiceReader\Installer'
$statePath=Join-Path $StateRoot 'state.json'
$ApiPort=8765;$BackendPort=8091
$step='開始前';$ownedPids=@();$lock=$null
function Confirm-Install([string]$Message){
    if($Unattended){Write-Host ('承諾済みの通常セットアップ処理: '+$Message);return $true}
    return Confirm $Message
}
function Set-Step([string]$Name){
    Assert-SetupActive
    $script:step=$Name
    Write-Host "`n=== $Name ===" -ForegroundColor Cyan
    Save-Json $statePath @{version=$manifest.version;step=$Name;error='';completed=$false}
}
function Find-BackendAsset([string]$Kind,[string]$Arch){
    return @($manifest.assets | Where-Object {$_.kind -eq 'backend' -and $_.backend -eq $Kind -and ($Kind -eq 'cpu' -or $_.arch -eq $Arch)}) | Select-Object -First 1
}
function Get-Asset($Asset){
    $local=Join-Path $SourceRoot ('assets\'+$Asset.name)
    if(Test-Path $local){
        if((Get-FileHash $local -Algorithm SHA256).Hash -ne $Asset.sha256){throw '配布パッケージ内のファイル検査に失敗しました。GitHubから取得し直してください。'}
        return $local
    }
    $uri='https://github.com/'+$manifest.repository+'/releases/tag/'+$manifest.release
    if($Unattended){throw ('配布ファイルがありません: '+$Asset.name)}
    [void](Show-SetupDialog 'confirm' "配布ファイル $($Asset.name) がありません。非公開リポジトリのReleaseからWindows-test一式を取得してください。`r`n$uri`r`n個人トークンを入力する必要はありません。")
    throw '配布ファイルがありません。個人トークンの入力は不要です。READMEの推奨「Windowsお試し版一式」を取得すると必要ファイルが揃います。'
}
function Write-Guide([bool]$VoiceReady){
    $folder=Join-Path $InstallRoot 'docs'
    New-Item -ItemType Directory -Force -Path $folder | Out-Null
    $chrome=[Security.SecurityElement]::Escape((Join-Path $InstallRoot 'chrome-extension'))
    $voice=if($VoiceReady){'音声生成テスト：通常版・声質安定版とも成功'}else{'音声生成テスト：未実施。使う声の登録が必要です。準備完了とは別の確認項目です。'}
    $firefoxGuide=if($Browsers -in @('firefox','both')){'<section><h2>Firefox</h2><p>独立環境のソースと firefox-isolated-unsigned.xpi を作成しました。未署名のため通常版Firefoxへの恒久導入はまだできません。既存の署名済みXPIはこの独立環境へ流用しません。署名工程は未解決です。</p></section>'}else{''}
    $chromeGuide=if($Browsers -in @('chrome','both')){@"
<section><h2>Chrome</h2><ol><li>アドレス欄に <code>chrome://extensions/</code> と入力して開きます。</li><li>右上の「デベロッパー モード」をONにします。</li><li>「パッケージ化されていない拡張機能を読み込む」を押します。</li><li>下の場所をコピーし、フォルダー選択画面でこのフォルダーを選びます。</li></ol>
<input id="path" readonly value="$chrome"><button onclick="const e=document.getElementById('path');e.select();try{document.execCommand('copy')}catch(x){}">選ぶフォルダーをコピー</button><p>このフォルダーは削除・移動しないでください。ダウンロードした導入用フォルダーとは別の場所です。</p></section>
"@
    }else{''}
    $html=@"
<!doctype html><html lang="ja"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Irodori Voice Reader 導入の最後の手順</title>
<style>body{font:17px/1.8 system-ui;background:#f4f3fa;color:#252036;max-width:900px;margin:30px auto;padding:20px}section{background:white;padding:24px;border-radius:12px;margin:20px 0}button,a{padding:8px}input{width:95%;padding:10px}strong{color:#6032a5}</style>
<h1>あと少し：拡張機能を自分で追加します</h1><p>ローカル環境の準備は終了しました。<strong>拡張機能の追加はまだです。</strong></p><p>$voice</p>
$chromeGuide

$firefoxGuide
<section><h2>最後の確認</h2><ol><li>Irodori Voice Readerのサイドバーを開きます。</li><li>APIが接続済みになり、使うVoiceが選べることを確認します。</li><li>使いたいサイト（ChatGPT / Museなど）と「音声を自動再生」をONにします。</li><li>対象サイトを再読み込みし、新しい短い質問を送ります。</li></ol><p>API Only版と同じサイトを両方ONにすると、二重に読み上げる場合があります。</p><p>Voiceをまだ登録していない場合は、拡張のVoice管理で利用権限のあるWAVを追加してください。</p></section>
<section><h2>うまくいかない場合</h2><p>まずREADMEの「困ったとき」を確認し、setup.batを再実行してください。解決しない場合だけ、配布フォルダーの <code>AI-support.bat</code> で相談文を作成できます。自動送信はしません。</p></section></html>
"@
    [IO.File]::WriteAllText((Join-Path $folder 'finish.html'),$html,$Utf8)
    if(-not $NoOpenGuide -and -not $Unattended -and $Browsers -in @('chrome','both')){Start-Process 'chrome.exe' -ArgumentList @(('"'+(Join-Path $folder 'finish.html')+'"'))}else{Write-Host (Join-Path $folder 'finish.html')}
}
try{
    if(-not[Environment]::Is64BitOperatingSystem -or $env:PROCESSOR_ARCHITECTURE -eq 'ARM64'){throw 'この試験版はWindows x64用です。ARM64版ではありません。'}
    $gpu=Get-Gpu
    Write-Host "Irodori Voice Reader セットアップ $($manifest.version)`nGPU: $($gpu.name)`n保存先: $InstallRoot"
    if($CheckOnly){
        Write-Host "Python 3.12 x64: $([bool](Get-Python))"
        foreach($port in @(8765,8091)){Write-Host "ポート$port 使用中: $([bool](Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue))"}
        Write-Host '診断のみ完了。ダウンロード・インストール・登録・サーバー起動は行っていません。';exit 0
    }
    if($Unattended -and $Browsers -eq 'prompt'){throw '自動実行では-Browsers chrome/firefox/bothを明示してください。'}
    if($Browsers -eq 'prompt'){$Browsers=Select-Browsers}
    $drive=New-Object IO.DriveInfo([IO.Path]::GetPathRoot($InstallRoot))
    if($drive.AvailableFreeSpace -lt 5GB){throw '保存先に最低5GBの空き容量が必要です。任意ビルドではさらに数十GB必要です。'}
    if(-not(Confirm-Install "Irodori Voice Readerの導入を開始しますか？`r`n`r`n保存先は次の確認画面で表示します。`r`n・モデル約1.09GB、専用Python環境、音声生成プログラムを準備します。`r`n・完成品を優先します。不足時は取得・任意ビルド・中止から選べます。`r`n・後半で確認後に現在のWindowsユーザーへブラウザー接続ヘルパーを登録します。`r`n・拡張機能の追加は最後にご自身で行います。`r`n・音声や診断情報を自動送信しません。`r`n`r`nLICENSEとTHIRD_PARTY_NOTICES.mdの利用条件、および利用権限のある音声のみを使うことを確認してください。")){Write-Host '中止しました。';exit 0}
    New-Item -ItemType Directory -Force -Path $StateRoot | Out-Null
    try{$lock=[IO.File]::Open((Join-Path $StateRoot 'setup.lock'),'OpenOrCreate','ReadWrite','None')}catch{throw '別のセットアップが実行中です。終了後に再試行してください。'}
    if(-not $Unattended){Start-SetupCancellation}
    $env:IVR_SETUP_LOG_ROOT=$StateRoot
    # Every new installation is a separate Chrome environment. Never adopt an old root.
    $sessionFile=Join-Path $StateRoot 'chrome-session.json'
    $session=$null
    if(Test-Path $sessionFile){$session=Get-Content $sessionFile -Raw -Encoding UTF8 | ConvertFrom-Json}
    if($session -and -not $NewEnvironment -and $session.browsers -eq $Browsers){
        if($session.PSObject.Properties['version'] -and $session.version -ne $manifest.version){throw '前回のセットアップと版が異なります。新規作成はsetup.bat -NewEnvironmentで開始してください。'}
        $InstallRoot=Assert-LocalDirectory $session.installRoot
        $ApiPort=[int]$session.apiPort;$BackendPort=[int]$session.backendPort
        if(-not(Confirm-Install "前回の専用環境へセットアップを再開しますか？`r`n保存先: $InstallRoot`r`nAPI: $ApiPort / audio.cpp: $BackendPort`r`nVoiceとユーザー設定を保持します。")){Write-Host '中止しました。';exit 0}
    }else{
        $detected=@(Get-ExistingEnvironment)
        $detail=if($detected.Count){$detected -join "`r`n"}else{'既存環境は検出されませんでした。'}
        $InstallRoot=Join-Path (Split-Path $InstallRoot -Parent) (($Browsers+'-')+[guid]::NewGuid().ToString('N').Substring(0,12))
        $ApiPort=Find-FreePort 18765
        $BackendPort=Find-FreePort 18091
        if(-not(Confirm-Install "$detail`r`n`r`n既存環境を変更せず、選択したブラウザー ($Browsers) の新しい環境を作成しますか？`r`n保存先: $InstallRoot`r`nAPI: $ApiPort / audio.cpp: $BackendPort`r`n各ブラウザーに専用の拡張IDとNative Hostを作成します。")){Write-Host '中止しました。';exit 0}
        $session=@{installRoot=$InstallRoot;apiPort=$ApiPort;backendPort=$BackendPort;browsers=$Browsers;version=$manifest.version}
        Save-Json $sessionFile $session
    }
    Assert-FreePorts @($ApiPort,$BackendPort)
    Set-Step '1/7 配布ファイルを検査して配置'
    Copy-Application -SourceRoot $SourceRoot -InstallRoot $InstallRoot -Version $manifest.version
    $StateRoot=Join-Path $InstallRoot 'installer-state'
    $statePath=Join-Path $StateRoot 'state.json'
    $env:IVR_SETUP_LOG_ROOT=$StateRoot
    $api=Join-Path $InstallRoot 'irodori-api'
    & (Join-Path $PSScriptRoot 'configure-chrome.ps1') -InstallRoot $InstallRoot -ApiPort $ApiPort -BackendPort $BackendPort
    Set-Step '2/7 専用Pythonの準備'
    $python=Get-Python
    if(-not $python){
        if($Unattended){throw 'Python 3.12 x64が必要です。自動実行ではシステムへ追加しません。'}
        if(-not(Confirm-Install 'Python 3.12 x64がありません。Python公式パッケージをwinget経由で現在のWindowsユーザーにインストールしますか？')){throw 'Pythonの導入が許可されませんでした。'}
        Install-Winget 'Python.Python.3.12' @('--scope','user')
        $python=Get-Python
        if(-not $python){throw 'Pythonを確認できません。setup.batを再実行してください。'}
    }
    $venv=Join-Path $api '.venv\Scripts\python.exe'
    if(-not(Test-Path $venv)){Run $python @('-m','venv',(Join-Path $api '.venv'))}
    Run $venv @('-m','pip','--isolated','install','--disable-pip-version-check','--only-binary=:all:','--index-url','https://pypi.org/simple','-r',(Join-Path $api 'requirements.txt'))
    Set-Step '3/7 モデルの取得（再開できます）'
    $model=Join-Path $api ('models\'+$manifest.model.name)
    if(-not(Test-Path $model)){
        $selected=if($ModelPath){[IO.Path]::GetFullPath($ModelPath)}elseif($Unattended){'cancel'}else{Show-SetupDialog 'file' '既存の同一モデルを再利用できます。なければキャンセルでダウンロードへ' 'Irodori model|irodori-tts-500m-v3-q8_0.gguf'}
        if($selected -ne 'cancel'){
            if((Get-FileHash $selected -Algorithm SHA256).Hash -ne $manifest.model.sha256){throw '選択したモデルのハッシュが一致しません。元ファイルは変更していません。'}
            New-Item -ItemType Directory -Force -Path (Split-Path $model) | Out-Null
            Copy-Item -LiteralPath $selected -Destination $model
        }
    }
    Get-Verified $manifest.model.url $model $manifest.model.sha256 $manifest.model.bytes
    Set-Step '4/7 通常版・声質安定版の共通エンジンを準備'
    $runtimeMissing=@(@('vcruntime140.dll','vcruntime140_1.dll','msvcp140.dll','vcomp140.dll') | Where-Object {-not(Test-Path (Join-Path $env:WINDIR "System32\$_"))})
    if($runtimeMissing.Count){
        if($Unattended){throw 'Visual C++ x64実行環境が必要です。自動実行ではシステムへ追加しません。'}
        if(-not(Confirm-Install 'Microsoft Visual C++実行環境が不足しています。Microsoft公式のVisual C++ x64再頒布可能パッケージをwingetでインストールしますか？管理者確認が必要な場合があります。')){throw 'Visual C++実行環境が未導入です。CPU方式にも必要です。'}
        Install-Winget 'Microsoft.VCRedist.2015+.x64'
    }
    if($Backend -eq 'auto'){
        $Backend=Get-BackendChoice $Backend $gpu.arch
        Write-Host "選択した実行方式: $Backend / GPU: $($gpu.name)"
    }
    $asset=Find-BackendAsset $Backend $gpu.arch
    $backendDir=Join-Path $api 'audio-cpp\bin'
    if($asset){
        $archive=Get-Asset $asset
        New-Item -ItemType Directory -Force -Path $backendDir | Out-Null
        Expand-Archive -LiteralPath $archive -DestinationPath $backendDir -Force
    }else{
        if($Unattended){throw '対応する完成品がありません。対応完成品を準備して再実行してください。自動ビルドは行いません。'}
        $choice=Show-SetupDialog 'backend' '対応完成品がありません。取得して再開するか、任意ビルドを選んでください。'
        if($choice -ne 'build'){throw '完成品を準備して再実行できます。ビルドは開始していません。'}
        if(-not(Confirm-Install '開発者向けビルドを開始します。CUDA Toolkit、C++ツール、数十GBの空き容量と数十分以上が必要な場合があります。続けますか？')){throw '任意ビルドを中止しました。'}
        $built=& (Join-Path $PSScriptRoot 'build-backend.ps1') -Python $venv -WorkRoot $StateRoot -Backend $Backend -CudaArch $(if($Backend -eq 'cuda'){$gpu.arch}else{''}) -AllowToolInstall
        $built=[string](@($built)[-1])
        New-Item -ItemType Directory -Force -Path $backendDir | Out-Null
        Get-ChildItem $built -File | Copy-Item -Destination $backendDir -Force
    }
    foreach($name in @('config','config.voice_stable')){
        $path=Join-Path $api "config\$name.json"
        if(-not(Test-Path $path)){Copy-Item (Join-Path $api "config\$name.example.json") $path}
    }
    $voicesPath=Join-Path $api 'config\voices.json'
    if(-not(Test-Path $voicesPath)){Save-Json $voicesPath @{voices=@()}}
    Save-Json (Join-Path $api 'config\runtime.json') @{backend_start_script='start_audio_cpp_shared.ps1';backend_stop_script='stop_audio_cpp_shared.ps1';audio_cpp_shared_executable='audio-cpp\bin\audiocpp_server.exe';audio_cpp_shared_config='config\audio_cpp_server.shared.json';audio_cpp_shared_cuda_bin='audio-cpp\bin';audio_cpp_shared_port=$BackendPort;stop_backend_with_api=$false}
    $models=@('irodori-500m-v3-q8','irodori-500m-v3-q8-voice-stable-speaker') | ForEach-Object { @{id=$_;family='irodori_tts';path=$model;task='tts';mode='offline';session_options=@{'irodori_tts.mem_saver'='true';'irodori_tts.reference_cache_slots'='1'}} }
    Save-Json (Join-Path $api 'config\audio_cpp_server.shared.json') @{host='127.0.0.1';port=$BackendPort;backend=$Backend;device=0;threads=4;lazy_load=$false;models=@($models)}
    Set-Step '5/7 利用する声の登録と音声生成テスト'
    $voices=Get-Content $voicesPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $voiceReady=$false
    if(@($voices.voices).Count -eq 0 -and (-not $Unattended -or $ReferenceWav) -and (Confirm-Install '個人の声は配布物に含みません。利用権限のある短いWAVを選び、Voice「my-voice」としてこのPCだけに登録しますか？通常版・声質安定版のテストで使います。「いいえ」はVoice未設定のまま先へ進みます。')){
        $selected=if($ReferenceWav){[IO.Path]::GetFullPath($ReferenceWav)}else{Show-SetupDialog 'file' '本人の音声、または利用許可のあるWAVを選択' 'Reference WAV|*.wav'}
        if($selected -ne 'cancel'){
            Run $venv @('-c','import soundfile as s,sys; a,r=s.read(sys.argv[1]); assert 0.1 <= len(a)/r <= 30',$selected)
            $voiceFile=Join-Path $api 'voice_refs\my-voice.wav';New-Item -ItemType Directory -Force -Path (Split-Path $voiceFile) | Out-Null
            Copy-Item -LiteralPath $selected -Destination $voiceFile
            Save-Json $voicesPath @{voices=@(@{id='my-voice';name='自分のVoice';reference_wav='voice_refs/my-voice.wav';enabled=$true})}
        }
    }
    Assert-FreePorts @($ApiPort,$BackendPort)
    try{
        # Track only the processes started for this test. Do not touch foreign PIDs.
        $env:PATH=$backendDir+';'+$env:PATH
        $config=Join-Path $api 'config\audio_cpp_server.shared.json'
        $p=Start-Process (Join-Path $backendDir 'audiocpp_server.exe') -ArgumentList @('--config',('"'+$config+'"')) -WorkingDirectory $backendDir -PassThru -WindowStyle Hidden -RedirectStandardOutput (Join-Path $StateRoot 'backend.stdout.log') -RedirectStandardError (Join-Path $StateRoot 'backend.stderr.log')
        [void](Add-SetupProcess $p)
        $ownedPids+=,$p
        $deadline=[DateTime]::UtcNow.AddMinutes(8);$health=$null
        while([DateTime]::UtcNow -lt $deadline){
            Assert-SetupActive
            $p.Refresh();if($p.HasExited){$tail=(Get-Content (Join-Path $StateRoot 'backend.stderr.log') -Tail 16 -ErrorAction SilentlyContinue) -join "`r`n";throw "音声エンジンが終了しました (exit=$($p.ExitCode))。`r`n$tail"}
            try{$health=Invoke-RestMethod "http://127.0.0.1:$BackendPort/health" -TimeoutSec 2;if($health.status -eq 'ok'){break}}catch{}
            Start-Sleep -Milliseconds 500
        }
        if(-not $health -or $health.status -ne 'ok'){throw 'モデルの起動確認がタイムアウトしました。GPUメモリとドライバー、またはCPU方式を確認してください。'}
        $modelsReply=Invoke-RestMethod "http://127.0.0.1:$BackendPort/v1/models" -TimeoutSec 5
        foreach($id in @('irodori-500m-v3-q8','irodori-500m-v3-q8-voice-stable-speaker')){if(@($modelsReply.data.id) -notcontains $id){throw '共有エンジンに必要なモデルIDがありません。'}}
        $apiProcess=Start-Process $venv -ArgumentList @('"'+(Join-Path $api 'kurisu_voice_api.py')+'"') -WorkingDirectory $api -PassThru -WindowStyle Hidden -RedirectStandardOutput (Join-Path $StateRoot 'api.stdout.log') -RedirectStandardError (Join-Path $StateRoot 'api.stderr.log')
        [void](Add-SetupProcess $apiProcess)
        $ownedPids+=,$apiProcess
        $deadline=[DateTime]::UtcNow.AddSeconds(30);$ready=$false
        while([DateTime]::UtcNow -lt $deadline){Assert-SetupActive;$apiProcess.Refresh();if($apiProcess.HasExited){$tail=(Get-Content (Join-Path $StateRoot 'api.stderr.log') -Tail 16 -ErrorAction SilentlyContinue) -join "`r`n";throw "APIが起動中に終了しました。`r`n$tail"};try{$h=Invoke-RestMethod "http://127.0.0.1:$ApiPort/health" -TimeoutSec 2;if($h.status -eq 'ok'){$ready=$true;break}}catch{};Start-Sleep -Milliseconds 500}
        if(-not $ready){throw 'APIの接続を確認できませんでした。'}
        $list=Invoke-RestMethod "http://127.0.0.1:$ApiPort/voices" -TimeoutSec 5
        if(@($list.voices).Count -gt 0){
            $id=$list.voices[0].id
            foreach($engine in @('standard','voice_stable')){
                $wav=Join-Path $StateRoot "test-$engine.wav"
                $body=@{text='こんにちは。音声の動作確認です。';voice=$id;engine=$engine;speaker_tag_policy='preserve'} | ConvertTo-Json
                $origin='chrome-extension://'+(Get-Content (Join-Path $InstallRoot 'chrome-identity.json') -Raw | ConvertFrom-Json).extensionId
                Run $venv @((Join-Path $PSScriptRoot 'verify-tts.py'),"http://127.0.0.1:$ApiPort",$origin,$id,$engine,$wav)
                $bytes=[IO.File]::ReadAllBytes($wav)
                if($bytes.Length -le 44 -or [Text.Encoding]::ASCII.GetString($bytes,0,4) -ne 'RIFF' -or [Text.Encoding]::ASCII.GetString($bytes,8,4) -ne 'WAVE'){throw '正常なWAVを取得できませんでした。'}
            }
            $voiceReady=$true
            if(-not $Unattended -and (Confirm-Install '通常版と声質安定版の音声ファイルを生成できました。テスト音声を再生しますか？')){(New-Object Media.SoundPlayer((Join-Path $StateRoot 'test-voice_stable.wav'))).PlaySync()}
        }
    }finally{
        foreach($process in @($ownedPids | Select-Object -Last 2)){$process.Refresh();if(-not $process.HasExited){$process.Kill();$process.WaitForExit(10000) | Out-Null}}
        $ownedPids=@()
    }
    Set-Step '6/7 ブラウザー接続ヘルパーの登録'
    if($Browsers -in @('firefox','both')){
        $fxZip=Join-Path $StateRoot 'firefox-isolated.zip'
        Compress-Archive -Path (Join-Path $InstallRoot 'firefox-extension\*') -DestinationPath $fxZip -Force
        Copy-Item $fxZip (Join-Path $InstallRoot 'firefox-isolated-unsigned.xpi') -Force
    }
    if(-not(Confirm-Install "選択したブラウザー ($Browsers) の専用接続ヘルパーを現在のWindowsユーザーに登録しますか？`r`n専用フォルダーを使用します。既存Native Host登録は上書きしません。")){throw 'ヘルパーは未登録です。setup.batから再開できます。'}
    & (Join-Path $PSScriptRoot 'register-hosts.ps1') -InstallRoot $InstallRoot -Browsers $Browsers
    Set-Step '7/7 拡張機能を追加する案内'
    Save-Json (Join-Path $InstallRoot '.ivr-install.json') @{version=$manifest.version;state='runtime-ready';voiceTest=$voiceReady}
    Save-Json $statePath @{version=$manifest.version;step='ローカル環境の準備完了・拡張機能の手動追加待ち';error='';completed=$true;voiceTest=$voiceReady}
    Write-Guide $voiceReady
    Write-Host 'ローカル環境の準備が完了しました。ブラウザーで最後の追加手順を確認してください。' -ForegroundColor Green
}catch{
    $message=$_.Exception.Message
    Write-Host "`n停止した工程: $step`n$message" -ForegroundColor Red
    if(-not $CheckOnly){
        Save-Json $statePath @{version=$manifest.version;step=$step;error=$message;completed=$false}
        Write-Host 'まず上の対処を確認し、setup.batを再実行してください。解決しない場合のみAI-support.batで相談文を作成できます。'
        if(-not $Unattended -and (Confirm-Install 'エラー内容を含むAI相談文の確認画面を開きますか？ 自動送信はしません。')) {& (Join-Path $PSScriptRoot 'support.ps1') -StateRoot $StateRoot}
    }
    exit 1
}finally{
    foreach($process in $ownedPids){try{$process.Refresh();if(-not $process.HasExited){$process.Kill()}}catch{}}
    Stop-SetupCancellation
    if($lock){$lock.Dispose()}
}
