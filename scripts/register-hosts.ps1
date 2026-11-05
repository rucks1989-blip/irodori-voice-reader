param([Parameter(Mandatory=$true)][string]$InstallRoot,[ValidateSet('chrome','firefox','both')][string]$Browsers='chrome',[switch]$PlanOnly,[switch]$ConfirmSwitch,[switch]$RefreshHostFiles)
. (Join-Path $PSScriptRoot 'common.ps1')
$apiRoot=Join-Path $InstallRoot 'irodori-api'
$python=Join-Path $apiRoot '.venv\Scripts\python.exe'
if(-not(Test-Path $python)){throw '専用Python環境がありません。setup.batから準備してください。'}
$identity=Get-Content (Join-Path $InstallRoot 'chrome-identity.json') -Raw -Encoding UTF8 | ConvertFrom-Json
if($identity.extensionId -notmatch '^[a-p]{32}$' -or $identity.hostName -cne ('io.github.irodori.voice_reader.chrome.i'+$identity.extensionId)){throw '専用Native Host識別情報が不正です。既存登録は変更しません。'}
$specs=@()
if($Browsers -in @('chrome','both')){$specs+=@{folder='chrome-host';host=$identity.hostName;registry=('HKCU:\Software\Google\Chrome\NativeMessagingHosts\'+$identity.hostName);entry='chrome_native_host.py'}}
if($Browsers -in @('firefox','both')){$specs+=@{folder='firefox-host';host='io.github.irodori.voice_reader.firefox';registry='HKCU:\Software\Mozilla\NativeMessagingHosts\io.github.irodori.voice_reader.firefox';entry='irodori_native_host.py'}}
foreach($spec in $specs){
    $spec.folderPath=Join-Path $InstallRoot $spec.folder
    $spec.manifestPath=Join-Path $spec.folderPath ($spec.host+'.json')
    $spec.launchPath=Join-Path $spec.folderPath 'launch.bat'
    $spec.config=@{firefox_extension_id='irodori-voice-reader@local';isolated_environment=$true;irodori_root=$apiRoot;health_url="http://127.0.0.1:$($identity.apiPort)/health";backend_health_url="http://127.0.0.1:$($identity.backendPort)/health";start_task='';chrome_extension_id=$identity.extensionId;startup_timeout_seconds=420;startup_poll_interval_seconds=2}
    $spec.manifest=@{name=$spec.host;description='Irodori Voice Reader isolated local controller';path=$spec.launchPath;type='stdio'}
    if($spec.folder -eq 'firefox-host'){$spec.manifest.allowed_extensions=@('irodori-voice-reader@local')}else{$spec.manifest.allowed_origins=@("chrome-extension://$($identity.extensionId)/")}
}
if($PlanOnly){ConvertTo-Json -InputObject @($specs) -Depth 15;return}
foreach($spec in $specs){
    $machineKey=$spec.registry.Replace('HKCU:','HKLM:')
    $spec.beforeUserExists=[bool](Test-Path $spec.registry)
    $spec.beforeMachineExists=[bool](Test-Path $machineKey)
    $spec.beforeUser=if(Test-Path $spec.registry){[string](Get-Item $spec.registry).GetValue('')}else{''}
    $spec.beforeMachine=if(Test-Path $machineKey){[string](Get-Item $machineKey).GetValue('')}else{''}
    foreach($registration in @(@{exists=$spec.beforeUserExists;value=$spec.beforeUser},@{exists=$spec.beforeMachineExists;value=$spec.beforeMachine})){
        if($registration.exists -and $registration.value -ne $spec.manifestPath){
            $existing=if($registration.value){$registration.value}else{'（登録キーはありますが、登録先が未設定です）'}
            $browser=if($spec.folder -eq 'firefox-host'){'Firefox'}else{'Chrome'}
            $detail="Native Host登録の衝突: $browser`r`n既存の登録先: $existing`r`n新しく登録する先: $($spec.manifestPath)`r`n接続する環境: $InstallRoot`r`n`r`nこのブラウザーを上記の環境へ切り替えますか？ 既存環境のモデル・Voice・設定は削除しません。PC全体の登録は変更せず、現在のユーザーの登録先だけを変更します。「いいえ」なら既存登録を維持します。困った場合はAI-support.batでChatGPTへの相談文を作成できます。"
            if($browser -eq 'Firefox'){$detail+="`r`n`r`nFirefoxの署名済み拡張IDは共通です。現在のWindowsユーザーで、この拡張を使うFirefoxの全プロファイルの接続先が切り替わります。別の独立環境を使い続ける場合は「いいえ」を選んでください。"}
            if(-not $ConfirmSwitch -or -not(Confirm $detail)){throw ($detail+"`r`n既存登録は変更していません。")}
        }
    }
}
# Recheck every registration after confirmation, before changing any of them.
function Assert-HostRegistrationUnchanged($spec){
    $current=if(Test-Path $spec.registry){[string](Get-Item $spec.registry).GetValue('')}else{''}
    $machineKey=$spec.registry.Replace('HKCU:','HKLM:')
    $machine=if(Test-Path $machineKey){[string](Get-Item $machineKey).GetValue('')}else{''}
    if($current -ne $spec.beforeUser -or $machine -ne $spec.beforeMachine -or [bool](Test-Path $spec.registry) -ne $spec.beforeUserExists -or [bool](Test-Path $machineKey) -ne $spec.beforeMachineExists){
        $browser=if($spec.folder -eq 'firefox-host'){'Firefox'}else{'Chrome'}
        throw "確認中にNative Host登録が変更されました。再選択してください。ブラウザー: $browser / 既存の登録先: $current / PC全体の登録先: $machine / 新しく登録する先: $($spec.manifestPath)"
    }
}
foreach($spec in $specs){Assert-HostRegistrationUnchanged $spec}
foreach($spec in $specs){
    if($spec.beforeUser -eq $spec.manifestPath -and -not $RefreshHostFiles){continue} # No rewrite of a live host. Explicit repair may refresh helpers.
    $folder=$spec.folderPath
    New-Item -ItemType Directory -Force -Path $folder | Out-Null
    foreach($name in @('irodori_native_host.py','chrome_native_host.py')){Copy-Item (Join-Path $InstallRoot "native-host\$name") -Destination $folder -Force}
    $launch=Join-Path $folder 'launch.bat'
    [IO.File]::WriteAllText($launch,"@echo off`r`nchcp 65001 >nul`r`n`"$python`" -u `"$(Join-Path $folder $spec.entry)`" %*`r`n",$Utf8)
    Save-Json (Join-Path $folder 'config.json') $spec.config
    $path=$spec.manifestPath
    Save-Json $path $spec.manifest
    Assert-HostRegistrationUnchanged $spec
    New-Item -Path $spec.registry -Force | Out-Null
    Set-Item -Path $spec.registry -Value $path
}
