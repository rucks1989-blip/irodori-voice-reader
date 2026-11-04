param([Parameter(Mandatory=$true)][string]$InstallRoot,[int]$ApiPort,[int]$BackendPort)
. (Join-Path $PSScriptRoot 'common.ps1')
$identityPath=Join-Path $InstallRoot 'chrome-identity.json'
if(Test-Path $identityPath){$identity=Get-Content $identityPath -Raw -Encoding UTF8 | ConvertFrom-Json}else{
    # Only the public key is retained. DER SubjectPublicKeyInfo is accepted by Chrome.
    $rsa=New-Object Security.Cryptography.RSACryptoServiceProvider(2048)
    try{
        $parameters=$rsa.ExportParameters($false)
        function Der([byte]$Tag,[byte[]]$Bytes){
            [byte[]]$length=if($Bytes.Length -lt 128){@([byte]$Bytes.Length)}elseif($Bytes.Length -lt 256){@(129,[byte]$Bytes.Length)}else{@(130,[byte]($Bytes.Length -shr 8),[byte]($Bytes.Length -band 255))}
            return [byte[]](@($Tag)+@($length)+@($Bytes))
        }
        [byte[]]$modulus=@(0)+@($parameters.Modulus)
        [byte[]]$key=Der 48 ([byte[]](@(Der 2 $modulus)+@(Der 2 $parameters.Exponent)))
        [byte[]]$algorithm=@(48,13,6,9,42,134,72,134,247,13,1,1,1,5,0)
        [byte[]]$spki=Der 48 ([byte[]](@($algorithm)+@(Der 3 ([byte[]](@(0)+@($key))))))
        $sha=[Security.Cryptography.SHA256]::Create()
        try{$hash=$sha.ComputeHash($spki)}finally{$sha.Dispose()}
        $id=-join ($hash[0..15] | ForEach-Object {[char](97+($_ -shr 4));[char](97+($_ -band 15))})
        $identity=@{extensionId=$id;key=[Convert]::ToBase64String($spki);hostName=('io.github.irodori.voice_reader.chrome.i'+$id);apiPort=$ApiPort;backendPort=$BackendPort}
        Save-Json $identityPath $identity
    }finally{$rsa.PersistKeyInCsp=$false;$rsa.Dispose()}
}
if([int]$identity.apiPort -ne $ApiPort -or [int]$identity.backendPort -ne $BackendPort){throw '再開時のポートが導入済み識別情報と一致しません。既存設定は変更しません。'}
$extension=Join-Path $InstallRoot 'chrome-extension'
$manifestPath=Join-Path $extension 'manifest.json'
$extensionManifest=Get-Content $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
$extensionManifest.key=$identity.key
$extensionManifest.name='Irodori Voice Reader'
Save-Json $manifestPath $extensionManifest
$configJson=@{apiBase="http://127.0.0.1:$ApiPort";nativeHost=$identity.hostName} | ConvertTo-Json -Compress
[IO.File]::WriteAllText((Join-Path $extension 'install-config.js'),("globalThis.IrodoriInstallConfig = Object.freeze("+$configJson+");`n"),$Utf8)
$api=Join-Path $InstallRoot 'irodori-api'
Save-Json (Join-Path $api 'config\api.json') @{host='127.0.0.1';port=$ApiPort;max_input_chars=20000}
foreach($name in @('config','config.voice_stable')){
    $path=Join-Path $api "config\$name.json"
    if(-not(Test-Path $path)){
        $value=Get-Content (Join-Path $api "config\$name.example.json") -Raw -Encoding UTF8 | ConvertFrom-Json
        $value.irodori_audiocpp_api_url="http://127.0.0.1:$BackendPort/v1/audio/speech"
        Save-Json $path $value
    }else{
        $value=Get-Content $path -Raw -Encoding UTF8 | ConvertFrom-Json
        if($value.irodori_audiocpp_api_url -ne "http://127.0.0.1:$BackendPort/v1/audio/speech"){throw '音声生成先が今回の専用ポートと異なります。設定は変更しません。'}
    }
}

$firefox=Join-Path $InstallRoot 'firefox-extension'
if(Test-Path $firefox){
    $firefoxId=('irodori-'+$identity.extensionId+'@isolated.local')
    $firefoxHost=('io.github.irodori.voice_reader.firefox.i'+$identity.extensionId)
    $fm=Get-Content (Join-Path $firefox 'manifest.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    $fm.browser_specific_settings.gecko.id=$firefoxId
    $fm.name='Irodori Voice Reader (独立環境)'
    Save-Json (Join-Path $firefox 'manifest.json') $fm
    $fc=@{apiBase="http://127.0.0.1:$ApiPort";nativeHost=$firefoxHost} | ConvertTo-Json -Compress
    [IO.File]::WriteAllText((Join-Path $firefox 'install-config.js'),("globalThis.IrodoriInstallConfig = Object.freeze("+$fc+");`n"),$Utf8)
}
