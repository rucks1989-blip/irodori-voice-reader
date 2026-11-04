[CmdletBinding()]
param([string]$InstallRoot='',[switch]$CheckOnly,[ValidateSet('auto','cpu','cuda')][string]$Backend='auto',[switch]$NewEnvironment,[ValidateSet('prompt','chrome','firefox','both')][string]$Browsers='prompt',[switch]$Unattended,[string]$ModelPath='',[string]$ReferenceWav='',[switch]$NoOpenGuide,[string]$BackendDirectory='', [ValidateSet('prompt','skip','prebuilt','build')][string]$StableMode='prompt',[string]$StableBackendDirectory='', [string]$SessionDirectory='', [switch]$Repair)
. (Join-Path $PSScriptRoot 'common.ps1')
. (Join-Path $PSScriptRoot 'install-files.ps1')
$SourceRoot=Split-Path -Parent $PSScriptRoot
$manifest=Get-Content (Join-Path $SourceRoot 'distribution.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$InstallRootExplicit=$PSBoundParameters.ContainsKey('InstallRoot')
if(-not $InstallRoot){$InstallRoot=Join-Path $env:LOCALAPPDATA 'IrodoriVoiceReader\App'}
$InstallRoot=Assert-LocalDirectory $InstallRoot
$SessionRoot=Join-Path $env:LOCALAPPDATA 'IrodoriVoiceReader\Installer'
if($SessionDirectory){$SessionRoot=Assert-LocalDirectory $SessionDirectory}
$StateRoot=$SessionRoot
$statePath=Join-Path $StateRoot 'state.json'
$ApiPort=8765;$BackendPort=8091
$step='開始前';$ownedPids=@();$lock=$null;$stableInstalled=$false;$stableNoticeShown=$false
function Get-InstallBaseDefault {
    $locationPath=Join-Path $SessionRoot 'location.json'
    if(Test-Path -LiteralPath $locationPath){
        try{
            $saved=Get-Content $locationPath -Raw -Encoding UTF8 | ConvertFrom-Json
            if($saved.base -and (Test-Path -LiteralPath $saved.base -PathType Container)){return (Assert-LocalDirectory $saved.base)}
        }catch{}
    }
    return (Assert-LocalDirectory (Split-Path -Parent $InstallRoot))
}
function Get-FreeSpace([string]$Path){
    $drive=New-Object IO.DriveInfo([IO.Path]::GetPathRoot([IO.Path]::GetFullPath($Path)))
    return [long]$drive.AvailableFreeSpace
}
function Select-InstallBase([string]$DefaultBase,[switch]$Prompt,[switch]$KeepRoot){
    $candidate=Assert-LocalDirectory $DefaultBase
    if($Prompt){
        $choice=Show-SetupDialog 'directory' 'Irodori Voice Reader の保存先を選択してください。空き容量を確認し、この場所に専用フォルダーを作成します。' '' $candidate
        if($choice -eq 'cancel'){throw [OperationCanceledException]::new('保存先の選択を中止しました。')}
        $candidate=Assert-LocalDirectory $choice
    }
    while($true){
        if(-not $KeepRoot -and $candidate -match '^[A-Za-z]:\\$' -and (Split-Path -Leaf $candidate) -notmatch 'IrodoriVoiceReader'){
            $candidate=Assert-LocalDirectory (Join-Path $candidate 'IrodoriVoiceReader')
        }
        if((Get-FreeSpace $candidate) -ge 5GB){return $candidate}
        if($Unattended){throw '保存先に最低5GBの空き容量が必要です。空き容量のあるフォルダーを指定して再実行してください。'}
        [void](Show-SetupDialog 'notice' "選択した保存先の空き容量が5GB未満です。`r`n別のドライブまたはフォルダーを選択してください。`r`n`r`n現在の保存先: $candidate")
        $choice=Show-SetupDialog 'directory' 'Irodori Voice Reader の保存先を選択してください。空き容量を確認し、この場所に専用フォルダーを作成します。' '' $candidate
        if($choice -eq 'cancel'){throw [OperationCanceledException]::new('保存先の選択を中止しました。')}
        $candidate=Assert-LocalDirectory $choice
    }
}
function Show-StableAbsentNotice([switch]$Dialog){
    if($stableInstalled){return}
    $message='声質安定版はインストールされていません。通常版のみ利用できます。'
    if(-not $stableNoticeShown){Write-Host $message -ForegroundColor Yellow;$script:stableNoticeShown=$true}
    if($Dialog -and -not $Unattended){
        [void](Show-SetupDialog 'notice' ($message+"`r`n`r`n声質安定版を追加する場合は、setup.bat -Repair を実行し、声質安定版の完成品を使用するか任意ビルドを選んでください。`r`n既存のVoice設定は保持します。"))
    }
}
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
    return @($manifest.assets | Where-Object {$_.kind -eq 'backend' -and (-not $_.PSObject.Properties['role'] -or $_.role -ne 'stable') -and $_.backend -eq $Kind -and ($Kind -eq 'cpu' -or $_.arch -eq '*' -or $_.arch -eq $Arch)}) | Select-Object -First 1
}
function Get-Asset($Asset){
    $local=Join-Path $SourceRoot ('assets\'+$Asset.name)
    if(Test-Path $local){
        if(Test-DownloadFile $local $Asset.sha256 ([long](Get-DownloadProperty $Asset 'bytes' '0'))){return $local}
        Write-Host '同梱アセットが必要な容量/SHA-256と異なります。元ファイルを保持し、自動取得または復旧画面から正しいファイルを指定できます。' -ForegroundColor Yellow
    }
    if($Asset.PSObject.Properties['url'] -and $Asset.url){
        $cache=Join-Path $StateRoot ('downloads\'+$Asset.name)
        Write-Host ('完成品を自動取得します: '+$Asset.name) -ForegroundColor Cyan
        $size=if($Asset.PSObject.Properties['bytes']){[long]$Asset.bytes}else{0}
        return (Get-RequiredDownload $Asset $cache -NonInteractive:$Unattended -Python (Get-Python))
    }
    return (Get-RequiredDownload $Asset (Join-Path $StateRoot ('downloads\'+$Asset.name)) -NonInteractive:$Unattended -Python (Get-Python))
}
function Write-Guide([bool]$VoiceReady){
    $folder=Join-Path $InstallRoot 'docs'
    New-Item -ItemType Directory -Force -Path $folder | Out-Null
    $chrome=[Security.SecurityElement]::Escape((Join-Path $InstallRoot 'chrome-extension'))
    $voice=if($VoiceReady){'音声生成テスト：導入済みモードのWAV生成成功'}else{'音声生成テスト：未実施。使う声の登録が必要です。準備完了とは別の確認項目です。'}
    $stableGuide=if($stableInstalled){'<p>声質安定版もインストールされています。</p>'}else{'<p><strong>声質安定版はインストールされていません。</strong>通常版のみ利用できます。声質安定版を追加する場合は、setup.bat -Repair を実行し、声質安定版の完成品を使用するか任意ビルドを選んでください。既存のVoice設定は保持します。</p>'}
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
$stableGuide
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
    Write-Host "Irodori Voice Reader セットアップ $($manifest.version)`nGPU: $($gpu.name)"
    if($CheckOnly){
        Write-Host "Python 3.12 x64: $([bool](Get-Python))"
        foreach($port in @(8765,8091)){Write-Host "ポート$port 使用中: $([bool](Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue))"}
        Write-Host '診断のみ完了。ダウンロード・インストール・登録・サーバー起動は行っていません。';exit 0
    }
    if($Unattended -and $Browsers -eq 'prompt'){throw '自動実行では-Browsers chrome/firefox/bothを明示してください。'}
    if($Browsers -eq 'prompt'){$Browsers=Select-Browsers}
    $sessionFile=Join-Path $SessionRoot ($Browsers+'-session.json')
    $session=$null
    if(Test-Path $sessionFile){$session=Get-Content $sessionFile -Raw -Encoding UTF8 | ConvertFrom-Json}
    $resume=$false
    if($session -and -not $NewEnvironment -and $session.browsers -eq $Browsers){
        $action=if($Unattended){'resume'}else{Show-SetupDialog 'installation' ("前回の環境: "+$session.installRoot+"`r`n続きから再開するか、新しい別環境を作るか選んでください。")}
        if($action -eq 'resume'){$resume=$true}
        elseif($action -eq 'new'){$NewEnvironment=$true}
        else{Write-Host '中止しました。';exit 0}
    }
    if($resume){
        if($session.PSObject.Properties['version'] -and $session.version -ne $manifest.version){throw '前回のセットアップと版が異なります。新規作成はsetup.bat -NewEnvironmentで開始してください。'}
        $InstallRoot=Assert-LocalDirectory $session.installRoot
        $ApiPort=[int]$session.apiPort;$BackendPort=[int]$session.backendPort
        $InstallBase=Assert-LocalDirectory (Split-Path -Parent $InstallRoot)
        if((Get-FreeSpace $InstallRoot) -lt 5GB){
            $message="前回の保存先に5GB以上の空き容量がありません。`r`n既存環境は変更していません。`r`n空き容量を確保するか、setup.bat -NewEnvironment で別の保存先を選んでください。`r`n`r`n保存先: $InstallRoot"
            if($Unattended){throw $message}
            [void](Show-SetupDialog 'notice' $message)
            throw '前回の保存先の空き容量が不足しています。'
        }
    }else{
        $InstallBase=if($InstallRootExplicit){Assert-LocalDirectory (Split-Path -Parent $InstallRoot)}elseif($session){Assert-LocalDirectory (Split-Path -Parent $session.installRoot)}else{Get-InstallBaseDefault}
        if(-not $Unattended -and -not $InstallRootExplicit){
            $InstallBase=Select-InstallBase $InstallBase -Prompt
        }else{
            $InstallBase=Select-InstallBase $InstallBase -KeepRoot:$InstallRootExplicit
        }
        try{Save-Json (Join-Path $SessionRoot 'location.json') @{base=$InstallBase}}catch{}
        $detected=@(Get-ExistingEnvironment)
        $detail=if($detected.Count){$detected -join "`r`n"}else{'既存環境は検出されませんでした。'}
        $InstallRoot=Join-Path $InstallBase (($Browsers+'-')+[guid]::NewGuid().ToString('N').Substring(0,12))
        $ApiPort=Find-FreePort 18765
        $BackendPort=Find-FreePort 18091
        if(-not(Confirm-Install "$detail`r`n`r`n既存環境を変更せず、選択したブラウザー ($Browsers) の新しい環境を作成しますか？`r`n保存先: $InstallRoot`r`nAPI: $ApiPort / audio.cpp: $BackendPort`r`n各ブラウザーに専用の拡張IDとNative Hostを作成します。`r`n`r`n・モデル約1.09GB、専用Python環境、音声生成プログラムもこの保存先に準備します。`r`n・完成品を優先します。不足時は取得・任意ビルド・中止から選べます。`r`n・後半で確認後に現在のWindowsユーザーへブラウザー接続ヘルパーを登録します。`r`n・拡張機能の追加は最後にご自身で行います。`r`n・音声や診断情報を自動送信しません。`r`n`r`nLICENSEとTHIRD_PARTY_NOTICES.mdの利用条件、および利用権限のある音声のみを使うことを確認してください。")){Write-Host '中止しました。';exit 0}
        $session=@{installRoot=$InstallRoot;apiPort=$ApiPort;backendPort=$BackendPort;browsers=$Browsers;version=$manifest.version}
        Save-Json $sessionFile $session
    }
    New-Item -ItemType Directory -Force -Path $SessionRoot | Out-Null
    try{$lock=[IO.File]::Open((Join-Path $SessionRoot 'setup.lock'),'OpenOrCreate','ReadWrite','None')}catch{throw '別のセットアップが実行中です。終了後に再試行してください。'}
    $env:IVR_SETUP_LOG_ROOT=$SessionRoot
    Write-Host ("保存先: "+$InstallRoot)
    # A completed environment is not an invitation to initialize it again.
    # This read-only reuse path also works while its existing API is running.
    $completedMarker=Join-Path $InstallRoot '.ivr-install.json'
    $runtimePath=Join-Path $InstallRoot 'irodori-api\config\runtime.json'
    if($resume -and -not $Repair -and (Test-Path $completedMarker) -and (Test-Path $runtimePath)){
        $completed=Get-Content $completedMarker -Raw -Encoding UTF8 | ConvertFrom-Json
        $savedRuntime=Get-Content $runtimePath -Raw -Encoding UTF8 | ConvertFrom-Json
        $savedModel=Join-Path $InstallRoot ('irodori-api\models\'+$manifest.model.name)
        if($completed.state -eq 'runtime-ready' -and (Test-DownloadFile $savedModel $manifest.model.sha256 $manifest.model.bytes) -and (Test-Path -LiteralPath $savedRuntime.audio_cpp_shared_executable -PathType Leaf)){
            [void](Get-PackageFiles $SourceRoot)
            $stableInstalled=[bool]$completed.voiceStable
            Write-Host '完了済み環境と検証済みモデルを再利用します。Voice・設定・Native Host・実行中APIは変更しません。再取得・再ビルドも行いません。修復／アプリ更新が必要な場合は setup.bat -Repair を使用してください。' -ForegroundColor Green
            Write-Guide ([bool]$completed.voiceTest)
            return
        }
    }
    if(-not $Unattended){Start-SetupCancellation}else{
        if(-not ('IvrSetupJob' -as [type])){Add-Type -Path (Join-Path $PSScriptRoot 'setup-job.cs')}
        $script:SetupJob=New-Object IvrSetupJob
    }
    Assert-FreePorts @($ApiPort,$BackendPort)
    # Copy-Application must see a new/empty target. The installer state folder
    # is created only after its marker has been written.
    $step='1/7 配布ファイルを検査して配置'
    Write-Host "`n=== $step ===" -ForegroundColor Cyan
    Copy-Application -SourceRoot $SourceRoot -InstallRoot $InstallRoot -Version $manifest.version
    $StateRoot=Join-Path $InstallRoot 'installer-state'
    $statePath=Join-Path $StateRoot 'state.json'
    New-Item -ItemType Directory -Force -Path $StateRoot | Out-Null
    New-Item -ItemType Directory -Force -Path (Join-Path $StateRoot 'temp') | Out-Null
    New-Item -ItemType Directory -Force -Path (Join-Path $StateRoot 'pip-cache') | Out-Null
    $env:IVR_SETUP_LOG_ROOT=$StateRoot
    $env:TEMP=Join-Path $StateRoot 'temp'
    $env:TMP=$env:TEMP
    $env:PIP_CACHE_DIR=Join-Path $StateRoot 'pip-cache'
    $env:PIP_TMPDIR=$env:TEMP
    $env:UV_CACHE_DIR=Join-Path $StateRoot 'pip-cache'
    Save-Json $statePath @{version=$manifest.version;step=$step;error='';completed=$false}
    $api=Join-Path $InstallRoot 'irodori-api'
    & (Join-Path $PSScriptRoot 'configure-chrome.ps1') -InstallRoot $InstallRoot -ApiPort $ApiPort -BackendPort $BackendPort -ChromeOnly:($Browsers -eq 'chrome')
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
    $installedJson=& $venv -c 'import importlib.metadata as m,json;print(json.dumps({d.name.lower().replace(chr(95),chr(45)):d.version for d in m.distributions()}))'
    if($LASTEXITCODE -ne 0){throw '専用Python環境のパッケージ検査に失敗しました。'}
    $installed=$installedJson | ConvertFrom-Json
    $dependenciesReady=$true
    foreach($wheel in $manifest.pythonWheels){
        if(-not $installed.PSObject.Properties[$wheel.package] -or $installed.($wheel.package) -ne $wheel.version){$dependenciesReady=$false}
    }
    if(-not $dependenciesReady){
        $wheelFolder=Join-Path $StateRoot 'downloads\python-wheels'
        foreach($wheel in $manifest.pythonWheels){
            $wheel | Add-Member NoteProperty title ($wheel.package+' '+$wheel.version+' Windows Python 3.12依存パッケージ') -Force
            $wheel | Add-Member NoteProperty pageUrl ('https://pypi.org/project/'+$wheel.package+'/'+$wheel.version+'/#files') -Force
            $wheel | Add-Member NoteProperty searchWords ('PyPI '+$wheel.package+' '+$wheel.version+' '+$wheel.name) -Force
            $wheel | Add-Member NoteProperty instructions ('PyPIの上記バージョンのページを開き、Download filesで '+$wheel.name+' を選びます。tar.gzや他のPython/GPU/OS向けファイルは選びません。保存先は自由です。.whlを展開せず、復旧画面の「ファイルを指定する」→「参照」で指定してください。セットアップが検証し、専用Python環境にのみ導入します。') -Force
            [void](Get-RequiredDownload $wheel (Join-Path $wheelFolder $wheel.name) -NonInteractive:$Unattended -Python $venv)
        }
        $requirementsLock=Join-Path $StateRoot 'verified-requirements.txt'
        $lockLines=@($manifest.pythonWheels | ForEach-Object {$_.package+'=='+$_.version+' --hash=sha256:'+$_.sha256})
        [IO.File]::WriteAllText($requirementsLock,($lockLines -join "`n"),$Utf8)
        # All network transfers above have pinned hashes. pip installs only
        # these verified wheels, including transitive dependencies, offline.
        Run $venv @('-m','pip','--isolated','--cache-dir',(Join-Path $StateRoot 'pip-cache'),'install','--disable-pip-version-check','--only-binary=:all:','--no-index','--require-hashes','--find-links',$wheelFolder,'-r',$requirementsLock)
    }else{Write-Host '専用Pythonの依存パッケージは検証済みバージョンです。再取得・再導入しません。'}
    Set-Step '3/7 モデルの取得（再開できます）'
    $model=Join-Path $api ('models\'+$manifest.model.name)
    $selected=''
    if($ModelPath){$selected=[IO.Path]::GetFullPath($ModelPath)}
    elseif(-not(Test-Path $model) -and -not $Unattended){
        $selected=Show-SetupDialog 'file' '既存の同一モデルを再利用できます。なければ「指定せず進む」で自動取得します。失敗時も参照から指定できます。' 'Irodori model|irodori-tts-500m-v3-q8_0.gguf'
        if($selected -eq 'cancel'){$selected=''}
    }
    [void](Get-RequiredDownload $manifest.model $model -NonInteractive:$Unattended -Python $venv -Selected $selected)
    Set-Step '4/7 通常版を準備（声質安定版は任意）'
    $runtimeMissing=@(@('vcruntime140.dll','vcruntime140_1.dll','msvcp140.dll','vcomp140.dll') | Where-Object {-not(Test-Path (Join-Path $env:WINDIR "System32\$_"))})
    if($runtimeMissing.Count){
        if($Unattended){throw 'Visual C++ x64実行環境が必要です。自動実行ではシステムへ追加しません。'}
        if(-not(Confirm-Install 'Microsoft Visual C++実行環境が不足しています。Microsoft公式のVisual C++ x64再頒布可能パッケージをwingetでインストールしますか？管理者確認が必要な場合があります。')){throw 'Visual C++実行環境が未導入です。CPU方式にも必要です。'}
        Install-Winget 'Microsoft.VCRedist.2015+.x64'
    }
    if($Backend -eq 'auto'){
        $savedServerPath=Join-Path $api 'config\audio_cpp_server.shared.json'
        if($resume -and (Test-Path $savedServerPath)){
            $savedServer=Get-Content $savedServerPath -Raw -Encoding UTF8 | ConvertFrom-Json
            if($savedServer.backend -in @('cpu','cuda')){$Backend=[string]$savedServer.backend}
        }
        $Backend=Get-BackendChoice $Backend $gpu.arch
        Write-Host "選択した実行方式: $Backend / GPU: $($gpu.name)"
    }
    $asset=Find-BackendAsset $Backend $gpu.arch
    $backendDir=Join-Path $api 'audio-cpp\bin'
    if(-not $BackendDirectory -and -not $asset -and -not $Unattended){
        $exeChoice=Show-SetupDialog 'file' '通常版のGPU完成品を指定できます。既存環境のaudiocpp_server.exeを選ぶと、必要DLLとともに独立環境へコピーします。未指定なら取得・任意ビルド・中止へ進みます。' 'audio.cpp|audiocpp_server.exe'
        if($exeChoice -ne 'cancel'){$BackendDirectory=Split-Path $exeChoice -Parent}
    }
    if($BackendDirectory){
        if([IO.Path]::GetFullPath($BackendDirectory) -ne [IO.Path]::GetFullPath($backendDir)){Copy-BackendRuntime $BackendDirectory $backendDir}
    }elseif($asset){
        $archive=Get-Asset $asset
        New-Item -ItemType Directory -Force -Path $backendDir | Out-Null
        Expand-Download $archive $asset $backendDir $venv
        if($asset.PSObject.Properties['dependencies']){
            foreach($dependency in $asset.dependencies){
                $dependencyArchive=Get-Asset $dependency
                Expand-Download $dependencyArchive $dependency $backendDir $venv
            }
        }
    }else{
        if($Unattended){throw '対応する完成品がありません。対応完成品を準備して再実行してください。自動ビルドは行いません。'}
        $choice=Show-SetupDialog 'backend' '対応完成品がありません。取得して再開するか、任意ビルドを選んでください。'
        if($choice -ne 'build'){throw '完成品を準備して再実行できます。ビルドは開始していません。'}
        if(-not(Confirm-Install '開発者向けビルドを開始します。CUDA Toolkit、C++ツール、数十GBの空き容量と数十分以上が必要な場合があります。続けますか？')){throw '任意ビルドを中止しました。'}
        $buildArgs=@('-NoProfile','-ExecutionPolicy','Bypass','-File',(Join-Path $PSScriptRoot 'build-backend.ps1'),'-Python',$venv,'-WorkRoot',$StateRoot,'-CacheRoot',(Join-Path $InstallBase '.ivr-build-cache'),'-Backend',$Backend,'-AllowToolInstall','-StandardOnly')
        if($Backend -eq 'cuda'){$buildArgs+=@('-CudaArch',$gpu.arch)}
        Run 'powershell.exe' $buildArgs
        $built=Join-Path $StateRoot ('build-output\'+$Backend+$(if($Backend -eq 'cuda'){'-sm'+$gpu.arch}else{''}))
        if(-not(Test-Path (Join-Path $built 'audiocpp_server.exe'))){throw '任意ビルドの完成品が見つかりません。'}
        New-Item -ItemType Directory -Force -Path $backendDir | Out-Null
        Get-ChildItem $built -File | Copy-Item -Destination $backendDir -Force
    }
    $stableInstalled=$false
    if($resume -and (Test-Path (Join-Path $api 'config\capabilities.json')) -and (Test-Path $runtimePath) -and -not $StableBackendDirectory -and $StableMode -ne 'build'){
        $savedCapabilities=Get-Content (Join-Path $api 'config\capabilities.json') -Raw -Encoding UTF8 | ConvertFrom-Json
        $savedRuntime=Get-Content $runtimePath -Raw -Encoding UTF8 | ConvertFrom-Json
        if($savedCapabilities.voice_stable -and (Test-Path $savedRuntime.audio_cpp_shared_executable)){
            $StableMode='prebuilt';$StableBackendDirectory=Split-Path -Parent $savedRuntime.audio_cpp_shared_executable
            Write-Host '導入済みの声質安定版を保持・再利用します。通常版のみに初期化しません。'
        }
    }
    if($StableMode -eq 'prompt'){
        $StableMode=if($Unattended){'skip'}else{Show-SetupDialog 'stable' '声質安定版を追加しますか？未導入でも通常版は利用できます。'}
    }
    if($StableMode -in @('prebuilt','build')){
        $stableCandidate=Join-Path $StateRoot 'stable-candidate'
        try{
            if($StableMode -eq 'prebuilt'){
                $stableAsset=@($manifest.assets | Where-Object {$_.kind -eq 'backend' -and $_.PSObject.Properties['role'] -and $_.role -eq 'stable' -and $_.backend -eq $Backend -and ($Backend -eq 'cpu' -or $_.arch -eq '*' -or $_.arch -eq $gpu.arch)}) | Select-Object -First 1
                if(-not $StableBackendDirectory -and $stableAsset){
                    Write-Host '対応する検証済み声質安定版の完成品を優先します。ソースビルドは不要です。'
                    $stableArchive=Get-Asset $stableAsset
                    $stableExtract=Join-Path $StateRoot ('stable-prebuilt-'+$stableAsset.sha256.Substring(0,12))
                    Expand-Download $stableArchive $stableAsset $stableExtract $venv
                    $StableBackendDirectory=$stableExtract
                }
                if(-not $StableBackendDirectory){
                    if($Unattended){throw '声質安定版完成品の指定がありません。'}
                    $selectedStable=Show-SetupDialog 'file' '通常・声質安定の両プロファイルに対応する完成品を選択してください。' 'audio.cpp|audiocpp_server.exe'
                    if($selectedStable -eq 'cancel'){throw '声質安定版の指定を中止しました。'}
                    $StableBackendDirectory=Split-Path $selectedStable -Parent
                }
                if([IO.Path]::GetFullPath($StableBackendDirectory) -ne [IO.Path]::GetFullPath($stableCandidate)){Copy-BackendRuntime $StableBackendDirectory $stableCandidate}
            }else{
                $stableArgs=@('-NoProfile','-ExecutionPolicy','Bypass','-File',(Join-Path $PSScriptRoot 'build-backend.ps1'),'-Python',$venv,'-WorkRoot',(Join-Path $StateRoot 'stable-build'),'-CacheRoot',(Join-Path $InstallBase '.ivr-build-cache'),'-Backend',$Backend,'-AllowToolInstall')
                if($Backend -eq 'cuda'){$stableArgs+=@('-CudaArch',$gpu.arch)}
                Run 'powershell.exe' $stableArgs
                $stableBuilt=Join-Path $StateRoot ('stable-build\build-output\'+$Backend+$(if($Backend -eq 'cuda'){'-sm'+$gpu.arch}else{''}))
                Copy-BackendRuntime $stableBuilt $stableCandidate
            }
            Test-StableCandidate $stableCandidate $model $Backend $BackendPort
            $stableInstalled=$true
        }catch{
            Assert-SetupActive
            if($_.Exception -is [OperationCanceledException]){throw}
            Write-Host ('声質安定版の追加を見送り、通常版で継続します: '+$_.Exception.Message) -ForegroundColor Yellow
        }
    }
    if($stableInstalled){
        $standardRuntime=Join-Path $StateRoot 'standard-runtime'
        Copy-BackendRuntime $backendDir $standardRuntime
        $backendDir=$stableCandidate
    }
    Save-Json (Join-Path $api 'config\capabilities.json') @{standard=$true;voice_stable=$stableInstalled}
    $enabledModes=if($stableInstalled){@('standard','voice_stable')}else{@('standard')}
    $modelIds=if($stableInstalled){@('irodori-500m-v3-q8','irodori-500m-v3-q8-voice-stable-speaker')}else{@('irodori-500m-v3-q8')}
    foreach($name in @('config','config.voice_stable')){
        $path=Join-Path $api "config\$name.json"
        if(-not(Test-Path $path)){Copy-Item (Join-Path $api "config\$name.example.json") $path}
    }
    $voicesPath=Join-Path $api 'config\voices.json'
    if(-not(Test-Path $voicesPath)){Save-Json $voicesPath @{voices=@()}}
    if(-not(Test-Path $runtimePath)){
        Save-Json $runtimePath @{backend_start_script='start_audio_cpp_shared.ps1';backend_stop_script='stop_audio_cpp_shared.ps1';audio_cpp_shared_executable=(Join-Path $backendDir 'audiocpp_server.exe');audio_cpp_shared_config='config\audio_cpp_server.shared.json';audio_cpp_shared_cuda_bin=$backendDir;audio_cpp_shared_port=$BackendPort;stop_backend_with_api=$false}
    }elseif($stableInstalled){
        $existingRuntime=Get-Content $runtimePath -Raw -Encoding UTF8 | ConvertFrom-Json
        $existingRuntime.audio_cpp_shared_executable=Join-Path $backendDir 'audiocpp_server.exe'
        $existingRuntime.audio_cpp_shared_cuda_bin=$backendDir
        Save-Json $runtimePath $existingRuntime
    }
    $models=@($modelIds) | ForEach-Object { @{id=$_;family='irodori_tts';path=$model;task='tts';mode='offline';session_options=@{'irodori_tts.mem_saver'='true';'irodori_tts.reference_cache_slots'='1'}} }
    $sharedConfig=Join-Path $api 'config\audio_cpp_server.shared.json'
    if(-not(Test-Path $sharedConfig)){
        Save-Json $sharedConfig @{host='127.0.0.1';port=$BackendPort;backend=$Backend;device=0;threads=4;lazy_load=$false;models=@($models)}
    }else{
        $existingServer=Get-Content $sharedConfig -Raw -Encoding UTF8 | ConvertFrom-Json
        if([int]$existingServer.port -ne $BackendPort){throw '既存の音声エンジン設定のポートが異なります。設定は変更しません。'}
        $missingModels=@($models | Where-Object {$_.id -notin @($existingServer.models.id)})
        if($missingModels.Count){$existingServer.models=@($existingServer.models)+$missingModels;Save-Json $sharedConfig $existingServer}
    }
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
    $retryNormal=$false
    do{
    $retryNormal=$false
    try{
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
        foreach($id in $modelIds){if(@($modelsReply.data.id) -notcontains $id){throw '共有エンジンに必要なモデルIDがありません。'}}
        $apiProcess=Start-Process $venv -ArgumentList @('"'+(Join-Path $api 'kurisu_voice_api.py')+'"') -WorkingDirectory $api -PassThru -WindowStyle Hidden -RedirectStandardOutput (Join-Path $StateRoot 'api.stdout.log') -RedirectStandardError (Join-Path $StateRoot 'api.stderr.log')
        [void](Add-SetupProcess $apiProcess)
        $ownedPids+=,$apiProcess
        $deadline=[DateTime]::UtcNow.AddSeconds(30);$ready=$false
        while([DateTime]::UtcNow -lt $deadline){Assert-SetupActive;$apiProcess.Refresh();if($apiProcess.HasExited){$tail=(Get-Content (Join-Path $StateRoot 'api.stderr.log') -Tail 16 -ErrorAction SilentlyContinue) -join "`r`n";throw "APIが起動中に終了しました。`r`n$tail"};try{$h=Invoke-RestMethod "http://127.0.0.1:$ApiPort/health" -TimeoutSec 2;if($h.status -eq 'ok'){$ready=$true;break}}catch{};Start-Sleep -Milliseconds 500}
        if(-not $ready){throw 'APIの接続を確認できませんでした。'}
        $list=Invoke-RestMethod "http://127.0.0.1:$ApiPort/voices" -TimeoutSec 5
        if(@($list.voices).Count -gt 0){
            $id=$list.voices[0].id
            foreach($engine in $enabledModes){
                $wav=Join-Path $StateRoot "test-$engine.wav"
                $body=@{text='こんにちは。音声の動作確認です。';voice=$id;engine=$engine;speaker_tag_policy='preserve'} | ConvertTo-Json
                $origin='chrome-extension://'+(Get-Content (Join-Path $InstallRoot 'chrome-identity.json') -Raw | ConvertFrom-Json).extensionId
                Run $venv @((Join-Path $PSScriptRoot 'verify-tts.py'),"http://127.0.0.1:$ApiPort",$origin,$id,$engine,$wav)
                $bytes=[IO.File]::ReadAllBytes($wav)
                if($bytes.Length -le 44 -or [Text.Encoding]::ASCII.GetString($bytes,0,4) -ne 'RIFF' -or [Text.Encoding]::ASCII.GetString($bytes,8,4) -ne 'WAVE'){throw '正常なWAVを取得できませんでした。'}
            }
            $voiceReady=$true
            if(-not $Unattended -and (Confirm-Install '選択したモードの音声ファイルを生成できました。テスト音声を再生しますか？')){(New-Object Media.SoundPlayer((Join-Path $StateRoot 'test-standard.wav'))).PlaySync()}
        }
    }finally{
        foreach($process in @($ownedPids | Select-Object -Last 2)){$process.Refresh();if(-not $process.HasExited){$process.Kill();$process.WaitForExit(10000) | Out-Null}}
        $ownedPids=@()
    }
    }catch{
        Assert-SetupActive
        if(-not $stableInstalled){throw}
        Write-Host ('声質安定版の生成確認に失敗しました。保存済みの通常版で導入を続行します: '+$_.Exception.Message) -ForegroundColor Yellow
        $stableInstalled=$false
        $enabledModes=@('standard');$modelIds=@('irodori-500m-v3-q8')
        $backendDir=$standardRuntime
        Save-Json (Join-Path $api 'config\capabilities.json') @{standard=$true;voice_stable=$false}
        $runtime=Get-Content (Join-Path $api 'config\runtime.json') -Raw -Encoding UTF8 | ConvertFrom-Json
        $runtime.audio_cpp_shared_executable=Join-Path $backendDir 'audiocpp_server.exe'
        $runtime.audio_cpp_shared_cuda_bin=$backendDir
        Save-Json (Join-Path $api 'config\runtime.json') $runtime
        $server=Get-Content (Join-Path $api 'config\audio_cpp_server.shared.json') -Raw -Encoding UTF8 | ConvertFrom-Json
        $server.models=@($server.models | Where-Object {$_.id -eq 'irodori-500m-v3-q8'})
        Save-Json (Join-Path $api 'config\audio_cpp_server.shared.json') $server
        $retryNormal=$true
    }
    }while($retryNormal)
    Set-Step '6/7 ブラウザー接続ヘルパーの登録'
    if($Browsers -in @('firefox','both')){
        $fxZip=Join-Path $StateRoot 'firefox-isolated.zip'
        Compress-Archive -Path (Join-Path $InstallRoot 'firefox-extension\*') -DestinationPath $fxZip -Force
        Copy-Item $fxZip (Join-Path $InstallRoot 'firefox-isolated-unsigned.xpi') -Force
    }
    if(-not(Confirm-Install "選択したブラウザー ($Browsers) の専用接続ヘルパーを現在のWindowsユーザーに登録しますか？`r`n専用フォルダーを使用します。既存Native Host登録は上書きしません。")){throw 'ヘルパーは未登録です。setup.batから再開できます。'}
    & (Join-Path $PSScriptRoot 'register-hosts.ps1') -InstallRoot $InstallRoot -Browsers $Browsers
    Set-Step '7/7 拡張機能を追加する案内'
    Save-Json (Join-Path $InstallRoot '.ivr-install.json') @{version=$manifest.version;state='runtime-ready';voiceTest=$voiceReady;voiceStable=$stableInstalled}
    Save-Json $statePath @{version=$manifest.version;step='ローカル環境の準備完了・拡張機能の手動追加待ち';error='';completed=$true;voiceTest=$voiceReady;voiceStable=$stableInstalled}
    Show-StableAbsentNotice -Dialog
    Write-Guide $voiceReady
    Write-Host 'ローカル環境の準備が完了しました。ブラウザーで最後の追加手順を確認してください。' -ForegroundColor Green
}catch{
    $message=$_.Exception.Message
    if($_.Exception -is [OperationCanceledException]){
        Save-Json $statePath @{version=$manifest.version;step=$step;error='';completed=$false;canceled=$true}
        Write-Host '中止しました。再実行時に「再開」または「新しい別環境を作成」を選べます。' -ForegroundColor Yellow
        exit 0
    }
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
