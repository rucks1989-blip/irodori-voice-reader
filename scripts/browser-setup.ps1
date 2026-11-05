# Read-only discovery. Browser selection must not imply a new engine environment.
function Get-BrowserEnvironments([string]$SessionRoot,[string]$ExplicitRoot='') {
    $records=@()
    if(Test-Path -LiteralPath $SessionRoot){
        foreach($file in @(Get-ChildItem -LiteralPath $SessionRoot -Filter '*session.json' -File)+@(Get-ChildItem -LiteralPath $SessionRoot -Filter 'installation.json' -File)){
            try{$records+=Get-Content -LiteralPath $file.FullName -Raw -Encoding UTF8 | ConvertFrom-Json}catch{}
        }
    }
    $roots=@($ExplicitRoot)
    foreach($key in @('HKCU:\Software\Google\Chrome\NativeMessagingHosts','HKCU:\Software\Mozilla\NativeMessagingHosts','HKLM:\Software\Google\Chrome\NativeMessagingHosts','HKLM:\Software\Mozilla\NativeMessagingHosts')){
        foreach($entry in @(Get-ChildItem $key -ErrorAction SilentlyContinue | Where-Object PSChildName -match 'irodori')){
            try{
                $hostPath=$entry.GetValue('')
                $config=Get-Content -LiteralPath (Join-Path (Split-Path -Parent $hostPath) 'config.json') -Raw -Encoding UTF8 | ConvertFrom-Json
                $roots+=Split-Path -Parent $config.irodori_root
            }catch{}
        }
    }
    foreach($root in $roots){
        if(-not $root){continue}
        $local=Join-Path $root 'installer-session\installation.json'
        if(Test-Path -LiteralPath $local){try{$records+=Get-Content -LiteralPath $local -Raw -Encoding UTF8 | ConvertFrom-Json}catch{}}
    }
    $seen=@{}
    foreach($record in $records){
        try{
            $root=Assert-LocalDirectory $record.installRoot
            if($seen.ContainsKey($root) -or [int]$record.apiPort -lt 1 -or [int]$record.backendPort -lt 1){continue}
            # Return the same canonical spelling used for deduplication and
            # -InstallRoot comparison (including Windows 8.3 path aliases).
            # Only the in-memory record changes; no installed data is written.
            $record.installRoot=$root
            $seen[$root]=$true
            $record
        }catch{}
    }
}
function Assert-BrowserEnvironmentPorts([string]$InstallRoot,[int]$ApiPort,[int]$BackendPort){
    $identity=Get-Content -LiteralPath (Join-Path $InstallRoot 'chrome-identity.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    if([int]$identity.apiPort -ne $ApiPort -or [int]$identity.backendPort -ne $BackendPort){throw "既存のブラウザー識別情報と導入記録のポートが異なります。既存設定は変更せず停止しました。保存先: $InstallRoot。AI-support.batで相談できます。"}
    foreach($spec in @(@{name='API';file='api.json';port=$ApiPort},@{name='audio.cpp';file='audio_cpp_server.shared.json';port=$BackendPort})){
        $path=Join-Path $InstallRoot ('irodori-api\config\'+$spec.file)
        if(Test-Path -LiteralPath $path){
            $config=Get-Content -LiteralPath $path -Raw -Encoding UTF8 | ConvertFrom-Json
            if([int]$config.port -ne $spec.port){throw "既存$($spec.name)のポート ($($config.port)) と導入記録 ($($spec.port)) が異なります。ブラウザー接続先を自動変更せず停止しました。保存先: $InstallRoot。AI-support.batで相談できます。"}
        }
    }
}
