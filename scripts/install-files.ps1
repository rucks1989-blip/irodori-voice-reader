# Validates the whole package before touching an installation directory.
function Get-PackageFiles([string]$SourceRoot) {
    $sourceFull=[IO.Path]::GetFullPath($SourceRoot).TrimEnd('\')+'\'
    $inventory=Get-Content (Join-Path $SourceRoot 'package-files.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    $seen=@{}
    foreach($item in $inventory.files){
        if([IO.Path]::IsPathRooted($item.path) -or $item.path -match '(^|[\\/])\.\.([\\/]|$)' -or $item.path -match ':') {throw '配布ファイル一覧の相対パスが不正です。'}
        $src=[IO.Path]::GetFullPath((Join-Path $SourceRoot $item.path))
        if(-not $src.StartsWith($sourceFull,[StringComparison]::OrdinalIgnoreCase) -or $seen.ContainsKey($src)){throw '配布一覧に重複または範囲外のパスがあります。'}
        $seen[$src]=$true
        if(-not(Test-Path -LiteralPath $src -PathType Leaf) -or (Get-Item -LiteralPath $src).Attributes -band [IO.FileAttributes]::ReparsePoint -or (Get-FileHash -LiteralPath $src -Algorithm SHA256).Hash -ne $item.sha256){throw "配布ソースの検査失敗: $($item.path)"}
    }
    return $inventory.files
}
function Copy-MaintenanceFiles([string]$SourceRoot,[string]$InstallRoot,[switch]$SkipAssets) {
    $files=@(Get-PackageFiles $SourceRoot)
    # Repairs launched inside the installation use a pristine source snapshot,
    # not the configured extension files (whose hashes necessarily differ).
    $repairSource=Join-Path $InstallRoot 'installer-source'
    if([IO.Path]::GetFullPath($SourceRoot).TrimEnd('\') -eq [IO.Path]::GetFullPath($repairSource).TrimEnd('\')){return}
    foreach($item in $files){
        $dest=Join-Path $repairSource $item.path
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $dest) | Out-Null
        Copy-Item -LiteralPath (Join-Path $SourceRoot $item.path) -Destination $dest -Force
    }
    Copy-Item (Join-Path $SourceRoot 'package-files.json') $repairSource -Force
    $assetSource=Join-Path $SourceRoot 'assets'
    if(-not $SkipAssets -and (Test-Path $assetSource)){Copy-Item $assetSource $repairSource -Recurse -Force}
    Copy-Item -LiteralPath (Join-Path $SourceRoot '声質安定版追加する.bat') -Destination $InstallRoot -Force
}
function Copy-Application([string]$SourceRoot,[string]$InstallRoot,[string]$Version) {
    $files=@(Get-PackageFiles $SourceRoot)
    $marker=Join-Path $InstallRoot '.ivr-install.json'
    if(Test-Path -LiteralPath $InstallRoot){
        if(-not(Test-Path -LiteralPath $marker)){throw '保存先には既存ファイルがあります。変更せず停止します。'}
        $previous=Get-Content $marker -Raw -Encoding UTF8 | ConvertFrom-Json
        if($previous.version -ne $Version){throw '保存先の版が異なります。変更せず停止します。'}
    }
    if([IO.Path]::GetFullPath($SourceRoot).TrimEnd('\') -eq [IO.Path]::GetFullPath((Join-Path $InstallRoot 'installer-source')).TrimEnd('\')){return}
    Save-Json $marker @{version=$Version;state='preparing'}
    Copy-MaintenanceFiles $SourceRoot $InstallRoot
    foreach($item in $files){
        if($item.path -notmatch '^(chrome-extension|firefox-extension|native-host|irodori-api|docs)/' -and $item.path -notin @('LICENSE','THIRD_PARTY_NOTICES.md','README.md')){continue}
        $dest=Join-Path $InstallRoot $item.path
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $dest) | Out-Null
        Copy-Item -LiteralPath (Join-Path $SourceRoot $item.path) -Destination $dest -Force
    }
}
