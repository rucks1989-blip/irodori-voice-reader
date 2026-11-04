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
function Copy-Application([string]$SourceRoot,[string]$InstallRoot,[string]$Version) {
    $files=@(Get-PackageFiles $SourceRoot)
    $marker=Join-Path $InstallRoot '.ivr-install.json'
    if(Test-Path -LiteralPath $InstallRoot){
        if(-not(Test-Path -LiteralPath $marker)){throw '保存先には既存ファイルがあります。変更せず停止します。'}
        $previous=Get-Content $marker -Raw -Encoding UTF8 | ConvertFrom-Json
        if($previous.version -ne $Version){throw '保存先の版が異なります。変更せず停止します。'}
    }
    Save-Json $marker @{version=$Version;state='preparing'}
    foreach($item in $files){
        if($item.path -notmatch '^(chrome-extension|firefox-extension|native-host|irodori-api|docs)/' -and $item.path -notin @('LICENSE','THIRD_PARTY_NOTICES.md','README.md')){continue}
        $dest=Join-Path $InstallRoot $item.path
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $dest) | Out-Null
        Copy-Item -LiteralPath (Join-Path $SourceRoot $item.path) -Destination $dest -Force
    }
}
