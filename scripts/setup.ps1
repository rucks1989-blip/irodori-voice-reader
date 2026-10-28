[CmdletBinding()]
param([string]$InstallRoot=(Join-Path $env:LOCALAPPDATA 'IrodoriVoiceReader\App'),[switch]$CheckOnly,[ValidateSet('auto','cpu','cuda')][string]$Backend='auto')
. (Join-Path $PSScriptRoot 'common.ps1')
$SourceRoot=Split-Path -Parent $PSScriptRoot
$manifest=Get-Content (Join-Path $SourceRoot 'distribution.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$InstallRoot=Assert-LocalDirectory $InstallRoot
$StateRoot=Join-Path $env:LOCALAPPDATA 'IrodoriVoiceReader\Installer'
$statePath=Join-Path $StateRoot 'state.json'
$step='開始前';$ownedPids=@();$lock=$null
function Set-Step([string]$Name){
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
    $uri='https://github.com/'+$manifest.repository+'/releases/download/'+$manifest.release+'/'+$Asset.name
    Add-Type -AssemblyName System.Windows.Forms
    [void][Windows.Forms.MessageBox]::Show("非公開リポジトリのため自動取得しません。GitHubにログインして、次のReleaseから $($Asset.name) をダウンロードし、配布一式の assets フォルダーに保存してください。`r`n`r`n$uri`r`n`r`n保存後にsetup.batを再実行してください。",'ファイルの取得が必要です')
    Start-Process ('https://github.com/'+$manifest.repository+'/releases/tag/'+$manifest.release)
    throw '配布ファイルがありません。個人トークンの入力は不要です。READMEの推奨「Windowsお試し版一式」を取得すると必要ファイルが揃います。'
}
function Copy-Application {
    $inventory=Get-Content (Join-Path $SourceRoot 'package-files.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    foreach($item in $inventory.files){
        $src=Join-Path $SourceRoot $item.path
        if($item.path -match '(^|/)\.\.(/|$)' -or [IO.Path]::IsPathRooted($item.path)){throw '配布ファイル一覧の相対パスが不正です。'}
        if(-not(Test-Path $src) -or (Get-FileHash $src -Algorithm SHA256).Hash -ne $item.sha256){throw "配布ソースの検査失敗: $($item.path)"}
    }
    # After validating all sources, mark ownership before the first copy so an
    # interrupted install can resume without adopting an unrelated directory.
    Save-Json (Join-Path $InstallRoot '.ivr-install.json') @{version=$manifest.version;state='preparing'}
    foreach($item in $inventory.files){
        $src=Join-Path $SourceRoot $item.path
        if($item.path -notmatch '^(chrome-extension|firefox-extension|native-host|irodori-api|docs)/' -and $item.path -notin @('LICENSE','THIRD_PARTY_NOTICES.md')){continue}
        $dest=Join-Path $InstallRoot $item.path
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $dest) | Out-Null
        Copy-Item -LiteralPath $src -Destination $dest -Force
    }
}
function Write-Guide([bool]$VoiceReady){
    $folder=Join-Path $InstallRoot 'docs'
    New-Item -ItemType Directory -Force -Path $folder | Out-Null
    $chrome=[Security.SecurityElement]::Escape((Join-Path $InstallRoot 'chrome-extension'))
    $voice=if($VoiceReady){'音声生成テスト：通常版・声質安定版とも成功'}else{'音声生成テスト：未実施。使う声の登録が必要です。準備完了とは別の確認項目です。'}
    $xpi=([Uri]::new((Join-Path $InstallRoot 'firefox-signed.xpi'))).AbsoluteUri
    $html=@"
<!doctype html><html lang="ja"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Irodori Voice Reader 導入の最後の手順</title>
<style>body{font:17px/1.8 system-ui;background:#f4f3fa;color:#252036;max-width:900px;margin:30px auto;padding:20px}section{background:white;padding:24px;border-radius:12px;margin:20px 0}button,a{padding:8px}input{width:95%;padding:10px}strong{color:#6032a5}</style>
<h1>あと少し：拡張機能を自分で追加します</h1><p>ローカル環境の準備は終了しました。<strong>拡張機能の追加はまだです。</strong></p><p>$voice</p>
<section><h2>Chrome</h2><ol><li>アドレス欄に <code>chrome://extensions/</code> と入力して開きます。</li><li>右上の「デベロッパー モード」をONにします。</li><li>「パッケージ化されていない拡張機能を読み込む」を押します。</li><li>下の場所をコピーし、フォルダー選択画面でこのフォルダーを選びます。</li></ol>
<input id="path" readonly value="$chrome"><button onclick="const e=document.getElementById('path');e.select();try{document.execCommand('copy')}catch(x){}">選ぶフォルダーをコピー</button><p>このフォルダーは削除・移動しないでください。ダウンロードした導入用フォルダーとは別の場所です。</p></section>
<section><h2>Firefox</h2><ol><li>Firefoxのアドレス欄で <code>about:addons</code> を開きます。</li><li>歯車メニュー →「ファイルからアドオンをインストール」を選びます。</li><li><code>$( [Security.SecurityElement]::Escape((Join-Path $InstallRoot 'firefox-signed.xpi')) )</code> を選びます。</li><li>製品名と権限を確認し、「追加」を押します。</li></ol><p>署名を無効化する設定や、一時的なデバッグ導入は不要です。</p></section>
<section><h2>最後の確認</h2><ol><li>Irodori Voice Readerのサイドバーを開きます。</li><li>APIが接続済みになり、使うVoiceが選べることを確認します。</li><li>使いたいサイト（ChatGPT / Museなど）と「音声を自動再生」をONにします。</li><li>対象サイトを再読み込みし、新しい短い質問を送ります。</li></ol><p>API Only版と同じサイトを両方ONにすると、二重に読み上げる場合があります。</p><p>Voiceをまだ登録していない場合は、拡張のVoice管理で利用権限のあるWAVを追加してください。</p></section>
<section><h2>うまくいかない場合</h2><p>まずREADMEの「困ったとき」を確認し、setup.batを再実行してください。解決しない場合だけ、配布フォルダーの <code>AI-support.bat</code> で相談文を作成できます。自動送信はしません。</p></section></html>
"@
    [IO.File]::WriteAllText((Join-Path $folder 'finish.html'),$html,$Utf8)
    Start-Process (Join-Path $folder 'finish.html')
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
    $drive=New-Object IO.DriveInfo([IO.Path]::GetPathRoot($InstallRoot))
    if($drive.AvailableFreeSpace -lt 5GB){throw '保存先に最低5GBの空き容量が必要です。ビルドする場合はさらに数十GBを確保してください。'}
    if(-not(Confirm "Irodori Voice Readerの導入を開始しますか？`r`n`r`n保存先: $InstallRoot`r`n・モデル約1.09GB、専用Python環境、音声生成プログラムを準備します。`r`n・必要なら別の確認後にビルドツールを導入します。`r`n・後半で確認後に現在のWindowsユーザーへブラウザー接続ヘルパーを登録します。`r`n・拡張機能の追加は最後にご自身で行います。`r`n・音声や診断情報を自動送信しません。`r`n`r`nLICENSEとTHIRD_PARTY_NOTICES.mdの利用条件、および利用権限のある音声のみを使うことを確認してください。")){Write-Host '中止しました。';exit 0}
    New-Item -ItemType Directory -Force -Path $StateRoot | Out-Null
    try{$lock=[IO.File]::Open((Join-Path $StateRoot 'setup.lock'),'OpenOrCreate','ReadWrite','None')}catch{throw '別のセットアップが実行中です。終了後に再試行してください。'}
    $env:IVR_SETUP_LOG_ROOT=$StateRoot
    Assert-FreePorts
    if(Test-Path $InstallRoot){
        if(-not(Test-Path (Join-Path $InstallRoot '.ivr-install.json'))){throw '保存先には既存ファイルがあります。上書きせず停止します。別の空フォルダーを指定してください。'}
        $previous=Get-Content (Join-Path $InstallRoot '.ivr-install.json') -Raw -Encoding UTF8 | ConvertFrom-Json
        if($previous.version -ne $manifest.version){throw '異なる試験版が導入済みです。この版では上書き更新せず停止します。Voiceを保持した更新は正式版で扱います。'}
    }
    # Do not get as far as downloading when the fixed IDs belong to another app.
    foreach($hostFolder in @('NativeHost','ChromeNativeHost')){
        $config=Join-Path (Join-Path $env:LOCALAPPDATA "IrodoriVoiceReader\$hostFolder") 'config.json'
        if(Test-Path $config){
            $old=Get-Content $config -Raw -Encoding UTF8 | ConvertFrom-Json
            if([IO.Path]::GetFullPath($old.irodori_root) -ne [IO.Path]::GetFullPath((Join-Path $InstallRoot 'irodori-api'))){throw '別の通常版Irodori Voice Readerが登録済みです。既存環境は変更しません。別PCで試してください。'}
        }
    }
    Set-Step '1/7 配布ファイルを検査して配置'
    Copy-Application
    Save-Json (Join-Path $InstallRoot '.ivr-install.json') @{version=$manifest.version;state='preparing'}
    $api=Join-Path $InstallRoot 'irodori-api'
    Set-Step '2/7 専用Pythonの準備'
    $python=Get-Python
    if(-not $python){
        if(-not(Confirm 'Python 3.12 x64がありません。Python公式パッケージをwinget経由で現在のWindowsユーザーにインストールしますか？')){throw 'Pythonの導入が許可されませんでした。'}
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
        Add-Type -AssemblyName System.Windows.Forms
        $dialog=New-Object Windows.Forms.OpenFileDialog
        $dialog.Title='既存の同一モデルを再利用できます。持っていなければキャンセルでダウンロードへ';$dialog.Filter='Irodori model|irodori-tts-500m-v3-q8_0.gguf'
        if($dialog.ShowDialog() -eq 'OK'){
            if((Get-FileHash $dialog.FileName -Algorithm SHA256).Hash -ne $manifest.model.sha256){throw '選択したモデルはこの版の検証済みモデルと一致しません。ファイルは変更していません。'}
            New-Item -ItemType Directory -Force -Path (Split-Path $model) | Out-Null
            Copy-Item -LiteralPath $dialog.FileName -Destination $model
        }
    }
    Get-Verified $manifest.model.url $model $manifest.model.sha256 $manifest.model.bytes
    Set-Step '4/7 通常版・声質安定版の共通エンジンを準備'
    $runtimeMissing=@(@('vcruntime140.dll','vcruntime140_1.dll','msvcp140.dll','vcomp140.dll') | Where-Object {-not(Test-Path (Join-Path $env:WINDIR "System32\$_"))})
    if($runtimeMissing.Count){
        if(-not(Confirm 'Microsoft Visual C++実行環境が不足しています。Microsoft公式のVisual C++ x64再頒布可能パッケージをwingetでインストールしますか？管理者確認が必要な場合があります。')){throw 'Visual C++実行環境が未導入です。CPU方式にも必要です。'}
        Install-Winget 'Microsoft.VCRedist.2015+.x64'
    }
    if($Backend -eq 'auto'){
        $Backend=if($gpu.arch -and (Confirm "NVIDIA GPU $($gpu.name) を検出しました。GPU方式を使いますか？`r`nGPU用完成品がない場合は、許可確認後にビルドします。数GB以上の追加取得が必要です。`r`n「いいえ」は低速ですが追加ツール不要のCPU完成品を優先します。")){'cuda'}else{'cpu'}
    }
    $asset=Find-BackendAsset $Backend $gpu.arch
    $backendDir=Join-Path $api 'audio-cpp\bin'
    if($asset){
        $archive=Get-Asset $asset
        New-Item -ItemType Directory -Force -Path $backendDir | Out-Null
        Expand-Archive -LiteralPath $archive -DestinationPath $backendDir -Force
    }else{
        if(-not(Confirm 'このPC向けの完成品がありません。固定したaudio.cppソースを取得し、声質安定化パッチを適用して自動ビルドしますか？必要なツールはそれぞれ説明後に確認します。ビルドには数十分以上・数十GBの空き容量が必要な場合があります。')){throw 'ビルドを中止しました。CPU方式でも再試行できます。'}
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
    Save-Json (Join-Path $api 'config\runtime.json') @{backend_start_script='start_audio_cpp_shared.ps1';backend_stop_script='stop_audio_cpp_shared.ps1';audio_cpp_shared_executable='audio-cpp\bin\audiocpp_server.exe';audio_cpp_shared_config='config\audio_cpp_server.shared.json';audio_cpp_shared_cuda_bin='audio-cpp\bin';audio_cpp_shared_port=8091;stop_backend_with_api=$false}
    $models=@('irodori-500m-v3-q8','irodori-500m-v3-q8-voice-stable-speaker') | ForEach-Object { @{id=$_;family='irodori_tts';path=$model;task='tts';mode='offline';session_options=@{'irodori_tts.mem_saver'='true';'irodori_tts.reference_cache_slots'='1'}} }
    Save-Json (Join-Path $api 'config\audio_cpp_server.shared.json') @{host='127.0.0.1';port=8091;backend=$Backend;device=0;threads=4;lazy_load=$false;models=@($models)}
    Set-Step '5/7 利用する声の登録と音声生成テスト'
    $voices=Get-Content $voicesPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $voiceReady=$false
    if(@($voices.voices).Count -eq 0 -and (Confirm '個人の声は配布物に含みません。利用権限のある短いWAVを選び、Voice「my-voice」としてこのPCだけに登録しますか？通常版・声質安定版のテストで使います。「いいえ」はVoice未設定のまま先へ進みます。')){
        $dialog=New-Object Windows.Forms.OpenFileDialog;$dialog.Filter='Reference WAV|*.wav';$dialog.Title='本人の音声、または利用許可のあるWAVを選択'
        if($dialog.ShowDialog() -eq 'OK'){
            Run $venv @('-c','import soundfile as s,sys; a,r=s.read(sys.argv[1]); assert 0.1 <= len(a)/r <= 30',$dialog.FileName)
            $voiceFile=Join-Path $api 'voice_refs\my-voice.wav';New-Item -ItemType Directory -Force -Path (Split-Path $voiceFile) | Out-Null
            Copy-Item -LiteralPath $dialog.FileName -Destination $voiceFile
            Save-Json $voicesPath @{voices=@(@{id='my-voice';name='自分のVoice';reference_wav='voice_refs/my-voice.wav';enabled=$true})}
        }
    }
    Assert-FreePorts
    try{
        # Track only the processes started for this test. Do not touch foreign PIDs.
        $env:PATH=$backendDir+';'+$env:PATH
        $config=Join-Path $api 'config\audio_cpp_server.shared.json'
        $p=Start-Process (Join-Path $backendDir 'audiocpp_server.exe') -ArgumentList @('--config',('"'+$config+'"')) -WorkingDirectory $backendDir -PassThru -WindowStyle Hidden -RedirectStandardOutput (Join-Path $StateRoot 'backend.stdout.log') -RedirectStandardError (Join-Path $StateRoot 'backend.stderr.log')
        $ownedPids+=,$p
        $deadline=[DateTime]::UtcNow.AddMinutes(8);$health=$null
        while([DateTime]::UtcNow -lt $deadline){
            $p.Refresh();if($p.HasExited){$tail=(Get-Content (Join-Path $StateRoot 'backend.stderr.log') -Tail 16 -ErrorAction SilentlyContinue) -join "`r`n";throw "音声エンジンが終了しました (exit=$($p.ExitCode))。`r`n$tail"}
            try{$health=Invoke-RestMethod 'http://127.0.0.1:8091/health' -TimeoutSec 2;if($health.status -eq 'ok'){break}}catch{}
            Start-Sleep -Milliseconds 500
        }
        if(-not $health -or $health.status -ne 'ok'){throw 'モデルの起動確認がタイムアウトしました。GPUメモリとドライバー、またはCPU方式を確認してください。'}
        $modelsReply=Invoke-RestMethod 'http://127.0.0.1:8091/v1/models' -TimeoutSec 5
        foreach($id in @('irodori-500m-v3-q8','irodori-500m-v3-q8-voice-stable-speaker')){if(@($modelsReply.data.id) -notcontains $id){throw '共有エンジンに必要なモデルIDがありません。'}}
        $apiProcess=Start-Process $venv -ArgumentList @('"'+(Join-Path $api 'kurisu_voice_api.py')+'"') -WorkingDirectory $api -PassThru -WindowStyle Hidden -RedirectStandardOutput (Join-Path $StateRoot 'api.stdout.log') -RedirectStandardError (Join-Path $StateRoot 'api.stderr.log')
        $ownedPids+=,$apiProcess
        $deadline=[DateTime]::UtcNow.AddSeconds(30);$ready=$false
        while([DateTime]::UtcNow -lt $deadline){$apiProcess.Refresh();if($apiProcess.HasExited){$tail=(Get-Content (Join-Path $StateRoot 'api.stderr.log') -Tail 16 -ErrorAction SilentlyContinue) -join "`r`n";throw "APIが起動中に終了しました。`r`n$tail"};try{$h=Invoke-RestMethod 'http://127.0.0.1:8765/health' -TimeoutSec 2;if($h.status -eq 'ok'){$ready=$true;break}}catch{};Start-Sleep -Milliseconds 500}
        if(-not $ready){throw 'APIの接続を確認できませんでした。'}
        $list=Invoke-RestMethod 'http://127.0.0.1:8765/voices' -TimeoutSec 5
        if(@($list.voices).Count -gt 0){
            $id=$list.voices[0].id
            foreach($engine in @('standard','voice_stable')){
                $wav=Join-Path $StateRoot "test-$engine.wav"
                $body=@{text='こんにちは。音声の動作確認です。';voice=$id;engine=$engine;speaker_tag_policy='preserve'} | ConvertTo-Json
                Invoke-WebRequest -Uri 'http://127.0.0.1:8765/tts' -Method Post -ContentType 'application/json; charset=utf-8' -Body ([Text.Encoding]::UTF8.GetBytes($body)) -OutFile $wav -UseBasicParsing -TimeoutSec 1200
                $bytes=[IO.File]::ReadAllBytes($wav)
                if($bytes.Length -le 44 -or [Text.Encoding]::ASCII.GetString($bytes,0,4) -ne 'RIFF' -or [Text.Encoding]::ASCII.GetString($bytes,8,4) -ne 'WAVE'){throw '正常なWAVを取得できませんでした。'}
            }
            $voiceReady=$true
            if(Confirm '通常版と声質安定版の音声ファイルを生成できました。テスト音声を再生しますか？'){(New-Object Media.SoundPlayer((Join-Path $StateRoot 'test-voice_stable.wav'))).PlaySync()}
        }
    }finally{
        foreach($process in @($ownedPids | Select-Object -Last 2)){$process.Refresh();if(-not $process.HasExited){$process.Kill();$process.WaitForExit(10000) | Out-Null}}
        $ownedPids=@()
    }
    Set-Step '6/7 ブラウザー接続ヘルパーの登録'
    $xpiAsset=@($manifest.assets | Where-Object {$_.kind -eq 'firefox'}) | Select-Object -First 1
    if(-not $xpiAsset){throw '署名済みFirefox拡張の情報がありません。完成したReleaseを取得してください。'}
    Copy-Item (Get-Asset $xpiAsset) -Destination (Join-Path $InstallRoot 'firefox-signed.xpi') -Force
    if(-not(Confirm 'ChromeとFirefoxがローカルAPIを起動・操作するためのヘルパーを、現在のWindowsユーザーに登録しますか？ HKCUレジストリと専用フォルダーを使用します。PC起動時の自動実行・ファイアウォール変更・拡張機能の自動追加は行いません。')){throw 'ヘルパーは未登録です。拡張の自動接続には登録が必要です。setup.batから再開できます。'}
    & (Join-Path $PSScriptRoot 'register-hosts.ps1') -InstallRoot $InstallRoot
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
        if(Confirm 'エラー内容を含むAI相談文の確認画面を開きますか？ 自動送信はしません。') {& (Join-Path $PSScriptRoot 'support.ps1') -StateRoot $StateRoot}
    }
    exit 1
}finally{
    foreach($process in $ownedPids){try{$process.Refresh();if(-not $process.HasExited){$process.Kill()}}catch{}}
    if($lock){$lock.Dispose()}
}
