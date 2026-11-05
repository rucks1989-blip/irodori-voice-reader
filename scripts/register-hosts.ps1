param([Parameter(Mandatory=$true)][string]$InstallRoot,[ValidateSet('chrome','firefox','both')][string]$Browsers='chrome',[switch]$PlanOnly)
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
    if(Test-Path $machineKey){throw 'PC全体の既存Native Host登録を検出しました。上書きしません。'}
    $expected=$spec.manifestPath
    if(Test-Path $spec.registry){
        if((Get-Item $spec.registry).GetValue('') -ne $expected){throw '既存Native Host登録は変更しません。対象の独立環境の登録先を確認してください。'}
    }
}
foreach($spec in $specs){
    $folder=$spec.folderPath
    New-Item -ItemType Directory -Force -Path $folder | Out-Null
    foreach($name in @('irodori_native_host.py','chrome_native_host.py')){Copy-Item (Join-Path $InstallRoot "native-host\$name") -Destination $folder -Force}
    $launch=Join-Path $folder 'launch.bat'
    [IO.File]::WriteAllText($launch,"@echo off`r`nchcp 65001 >nul`r`n`"$python`" -u `"$(Join-Path $folder $spec.entry)`" %*`r`n",$Utf8)
    Save-Json (Join-Path $folder 'config.json') $spec.config
    $path=$spec.manifestPath
    Save-Json $path $spec.manifest
    New-Item -Path $spec.registry -Force | Out-Null
    Set-Item -Path $spec.registry -Value $path
}
