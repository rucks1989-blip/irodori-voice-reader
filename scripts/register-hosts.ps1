param([Parameter(Mandatory=$true)][string]$InstallRoot)
. (Join-Path $PSScriptRoot 'common.ps1')
$apiRoot=Join-Path $InstallRoot 'irodori-api'
$python=Join-Path $apiRoot '.venv\Scripts\python.exe'
if(-not(Test-Path $python)){throw '専用Python環境がありません。setup.batから準備してください。'}
$base=Join-Path $env:LOCALAPPDATA 'IrodoriVoiceReader'
$specs=@(
    @{folder='NativeHost';host='io.github.irodori.voice_reader';registry='HKCU:\Software\Mozilla\NativeMessagingHosts\io.github.irodori.voice_reader';entry='irodori_native_host.py'},
    @{folder='ChromeNativeHost';host='io.github.irodori.voice_reader.chrome';registry='HKCU:\Software\Google\Chrome\NativeMessagingHosts\io.github.irodori.voice_reader.chrome';entry='chrome_native_host.py'}
)
# Validate both targets before writing either. Existing installs are never adopted
# or overwritten just because they use the same fixed extension ID.
foreach($spec in $specs){
    $config=Join-Path (Join-Path $base $spec.folder) 'config.json'
    if(Test-Path $config){
        $old=Get-Content $config -Raw -Encoding UTF8 | ConvertFrom-Json
        if([IO.Path]::GetFullPath($old.irodori_root) -ne [IO.Path]::GetFullPath($apiRoot)){throw '別のIrodori Voice Readerヘルパーが登録済みです。既存環境は変更しません。別PCまたは別Windowsユーザーで試してください。'}
    }
    if(Test-Path $spec.registry){
        $oldManifest=(Get-Item $spec.registry).GetValue('')
        $expected=Join-Path (Join-Path $base $spec.folder) ($spec.host+'.json')
        if($oldManifest -ne $expected){throw '別のNative Host登録を検出しました。自動上書きはしません。'}
    }
}
foreach($spec in $specs){
    $folder=Join-Path $base $spec.folder
    New-Item -ItemType Directory -Force -Path $folder | Out-Null
    foreach($name in @('irodori_native_host.py','chrome_native_host.py')){Copy-Item (Join-Path $InstallRoot "native-host\$name") -Destination $folder -Force}
    $launch=Join-Path $folder 'launch.bat'
    [IO.File]::WriteAllText($launch,"@echo off`r`nchcp 65001 >nul`r`n`"$python`" -u `"$(Join-Path $folder $spec.entry)`" %*`r`n",$Utf8)
    Save-Json (Join-Path $folder 'config.json') @{irodori_root=$apiRoot;start_task='';chrome_extension_id='aeaichhflmacmifblidbpojgfemjijlb';startup_timeout_seconds=420;startup_poll_interval_seconds=2}
    $manifest=@{name=$spec.host;description='Irodori Voice Reader local controller';path=$launch;type='stdio'}
    if($spec.folder -eq 'NativeHost'){$manifest.allowed_extensions=@('irodori-voice-reader@local')}else{$manifest.allowed_origins=@('chrome-extension://aeaichhflmacmifblidbpojgfemjijlb/')}
    $path=Join-Path $folder ($spec.host+'.json')
    Save-Json $path $manifest
    New-Item -Path $spec.registry -Force | Out-Null
    Set-Item -Path $spec.registry -Value $path
}
# Native Host already supports direct script launching. No scheduled task,
# startup registration, firewall exception or machine-wide PATH is needed.
