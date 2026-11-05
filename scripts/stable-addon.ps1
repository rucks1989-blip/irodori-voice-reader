# Shared stable-only acquisition. Callers load common.ps1; no build/tool install.
function Get-StableRuntimeFingerprint([string]$Directory,[string]$Backend,[string]$Arch,[string]$Model){
    $files=@(Get-Item -LiteralPath (Join-Path $Directory 'audiocpp_server.exe'))+@(Get-ChildItem -LiteralPath $Directory -Filter '*.dll' -File | Sort-Object Name)
    $hashes=@($files | ForEach-Object {$_.Name+':'+(Get-FileHash -LiteralPath $_.FullName).Hash})
    return (@($Backend,$Arch,[IO.Path]::GetFullPath($Model),(Get-FileHash (Join-Path $PSScriptRoot 'verify-backend.py')).Hash)+$hashes) -join '|'
}
function Test-StableValidation([string]$Directory,[string]$Backend,[string]$Arch,[string]$Model=''){
    try{
        $receipt=Get-Content (Join-Path $Directory 'stable-validation.json') -Raw -Encoding UTF8 | ConvertFrom-Json
        if(-not $Model){$Model=$receipt.model}
        return $receipt.fingerprint -eq (Get-StableRuntimeFingerprint $Directory $Backend $Arch $Model)
    }catch{return $false}
}
function Test-StableRuntimeMetadata([string]$Directory,[string]$Backend,[string]$Arch){
    $metadata=Join-Path $Directory 'build.json'
    if(-not(Test-Path -LiteralPath $metadata)){return $false}
    try{
        $build=Get-Content -LiteralPath $metadata -Raw -Encoding UTF8 | ConvertFrom-Json
        $declared=$build.PSObject.Properties['stableSupported'] -and $build.stableSupported
        # Earlier CPU artifacts predate stableSupported; their shared-session
        # patch identity is only a discovery hint. Native probing still follows.
        $legacy=$build.PSObject.Properties['sharedSessionPatchSha256'] -and $build.sharedSessionPatchSha256 -eq (Get-FileHash (Join-Path (Split-Path $PSScriptRoot) 'audio-cpp-patch\apply_shared_session_patch.py')).Hash
        if((-not $declared -and -not $legacy) -or ($build.PSObject.Properties['standardOnly'] -and $build.standardOnly) -or $build.backend -ne $Backend){return $false}
        if($Backend -eq 'cuda' -and $build.arch -ne $Arch -and
           -not($build.PSObject.Properties['supportedArchitectures'] -and $Arch -in @($build.supportedArchitectures))){return $false}
        return (Get-FileHash -LiteralPath (Join-Path $Directory 'audiocpp_server.exe')).Hash -eq $build.exeSha256
    }catch{return $false}
}

function Get-StableCompletedRuntime {
    [CmdletBinding()]
    param([string]$SourceRoot,[string]$StateRoot,[string]$Python,[string]$Backend,[string]$Arch,
          [string]$Directory='',[string[]]$ExistingDirectories=@(),[string]$CacheRoot='',
          [switch]$Unattended)
    if($Directory){return [IO.Path]::GetFullPath($Directory)} # Explicit legacy artifacts are native-probed by the caller.
    foreach($existing in $ExistingDirectories){
        if($existing -and ((Test-StableRuntimeMetadata $existing $Backend $Arch) -or (Test-StableValidation $existing $Backend $Arch))){return $existing}
    }
    if($CacheRoot -and (Test-Path -LiteralPath $CacheRoot)){
        foreach($entry in Get-ChildItem -LiteralPath $CacheRoot -Directory){
            $directory=Join-Path $entry.FullName 'runtime'
            $completion=Join-Path $entry.FullName 'complete.json'
            if(-not(Test-Path -LiteralPath $completion) -or -not(Test-StableRuntimeMetadata $directory $Backend $Arch)){continue}
            try{
                $saved=Get-Content -LiteralPath $completion -Raw -Encoding UTF8 | ConvertFrom-Json
                $valid=$true
                foreach($file in $saved.files){
                    if([IO.Path]::GetFileName($file.name) -ne $file.name -or
                       -not(Test-DownloadFile (Join-Path $directory $file.name) $file.sha256 ([long]$file.bytes))){$valid=$false;break}
                }
                if($valid -and @($saved.files | Where-Object {$_.name -eq 'audiocpp_server.exe'}).Count){return $directory}
            }catch{continue}
        }
    }
    $manifest=Get-Content (Join-Path $SourceRoot 'distribution.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    $asset=@($manifest.assets | Where-Object {
        $_.kind -eq 'backend' -and (Get-DownloadProperty $_ 'role') -eq 'stable' -and $_.backend -eq $Backend -and
        ($Backend -eq 'cpu' -or $_.arch -eq $Arch -or
         ($_.PSObject.Properties['supportedArchitectures'] -and $Arch -in @($_.supportedArchitectures)))
    }) | Select-Object -First 1
    if($asset){
        $archive=Join-Path $SourceRoot ('assets\'+$asset.name)
        if(-not(Test-DownloadFile $archive $asset.sha256 ([long]$asset.bytes))){
            $archive=Get-RequiredDownload $asset (Join-Path $StateRoot ('downloads\'+$asset.name)) -Python $Python -NonInteractive:$Unattended
        }
        $directory=Join-Path $StateRoot ('stable-prebuilt-'+$asset.sha256.Substring(0,12))
        Expand-Download $archive $asset $directory $Python
        return $directory
    }
    if(-not $Unattended){
        $selected=Show-SetupDialog 'file' '対応するパッチ済み完成品がありません。以前の声質安定版の完成品を指定できます。ビルドや開発ツールの追加は行いません。' 'audio.cpp|*.exe'
        if($selected -ne 'cancel'){
            if([IO.Path]::GetFileName($selected) -ne 'audiocpp_server.exe' -and [IO.Path]::GetFileName($selected) -notlike 'audiocpp_server_voice_stable*_speaker_direct.exe'){throw 'audio.cppのサーバー完成品を選択してください。'}
            return (Split-Path -Parent $selected)
        }
    }
    throw "対応する声質安定版完成品がありません (backend=$Backend / GPU=$Arch)。通常版を保持し、ビルドせず停止しました。-StableBackendDirectoryでパッチ済み完成品フォルダーを指定できます。"
}

function Prepare-StableBackend {
    [CmdletBinding()]
    param([string]$SourceRoot,[string]$StateRoot,[string]$Python,[string]$Backend,[string]$Arch,
          [string]$Directory='',[string[]]$ExistingDirectories=@(),[string]$CacheRoot='',
          [string]$Model,[int]$Port,[switch]$Unattended)
    $source=Get-StableCompletedRuntime -SourceRoot $SourceRoot -StateRoot $StateRoot -Python $Python -Backend $Backend -Arch $Arch -Directory $Directory -ExistingDirectories $ExistingDirectories -CacheRoot $CacheRoot -Unattended:$Unattended
    $executable=Join-Path $source 'audiocpp_server.exe'
    if(-not(Test-Path -LiteralPath $executable)){
        $legacy=@(Get-ChildItem -LiteralPath $source -Filter 'audiocpp_server_voice_stable*_speaker_direct.exe' -File)
        if($legacy.Count -ne 1){throw '完成品フォルダーのサーバーEXEを一意に確認できません。'}
        $executable=$legacy[0].FullName
    }
    $identities=@((Get-FileHash -LiteralPath $executable).Hash)+@(Get-ChildItem -LiteralPath $source -Filter '*.dll' -File | Sort-Object Name | ForEach-Object {$_.Name+':'+(Get-FileHash -LiteralPath $_.FullName).Hash})
    $sha=[Security.Cryptography.SHA256]::Create()
    try{$hash=[BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes(($identities -join '|')))).Replace('-','').Substring(0,16)}finally{$sha.Dispose()}
    $candidate=Join-Path $StateRoot ('stable-runtime-'+$hash)
    if([IO.Path]::GetFullPath($source) -ne [IO.Path]::GetFullPath($candidate)){
        if([IO.Path]::GetFileName($executable) -eq 'audiocpp_server.exe'){
            Copy-BackendRuntime $source $candidate
        }else{
            New-Item -ItemType Directory -Force $candidate | Out-Null
            Copy-Item -LiteralPath $executable -Destination (Join-Path $candidate 'audiocpp_server.exe') -Force
            Get-ChildItem -LiteralPath $source -Filter '*.dll' -File | Copy-Item -Destination $candidate -Force
        }
        if(Test-Path (Join-Path $source 'build.json')){Copy-Item (Join-Path $source 'build.json') $candidate -Force}
    }
    # Both routes must actually parse the native options and execute kernels.
    # Metadata/alias names/health/WAV headers alone do not prove stable support.
    if(-not(Test-StableValidation $candidate $Backend $Arch $Model)){
        Test-StableCandidate $candidate $Model $Backend $Port $Python
        Save-Json (Join-Path $candidate 'stable-validation.json') @{model=$Model;fingerprint=(Get-StableRuntimeFingerprint $candidate $Backend $Arch $Model)}
    }else{Write-Host '実行ファイル・DLL・検証条件が同じため、検証済み完成品を再利用します。'}
    return $candidate
}
