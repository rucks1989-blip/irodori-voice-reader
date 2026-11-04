# Download/validation helpers. Loaded by common.ps1; no TLS setting is weakened.
function Test-DownloadFile([string]$Path,[string]$Sha256,[long]$Bytes=0){
    return ((Test-Path -LiteralPath $Path -PathType Leaf) -and
        ($Bytes -le 0 -or (Get-Item -LiteralPath $Path).Length -eq $Bytes) -and
        (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash -eq $Sha256)
}
function Invoke-DownloadLock([string]$Destination,[scriptblock]$Action){
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Destination) | Out-Null
    $handle=$null
    try{
        try{$handle=[IO.File]::Open(($Destination+'.download.lock'),'OpenOrCreate','ReadWrite','None')}
        catch{throw '同じファイルを別の取得処理が使用中です。同時書き込みは行いません。完了後、今すぐ再ダウンロード／再確認を選べます。待機時間や再試行回数の制限はありません。'}
        & $Action
    }finally{if($handle){$handle.Dispose()}}
}
function Import-VerifiedFile([string]$Selected,[string]$Destination,[string]$Sha256,[long]$Bytes=0){
    Invoke-DownloadLock $Destination {Import-VerifiedFileUnlocked $Selected $Destination $Sha256 $Bytes}
}
function Import-VerifiedFileUnlocked([string]$Selected,[string]$Destination,[string]$Sha256,[long]$Bytes=0){
    if(-not(Test-DownloadFile $Selected $Sha256 $Bytes)){throw '選択したファイルは必要な容量/SHA-256と一致しません。ファイル名だけでなく版を確認してください。選択元は変更していません。'}
    if(Test-DownloadFile $Destination $Sha256 $Bytes){return}
    if([IO.Path]::GetFullPath($Selected) -eq [IO.Path]::GetFullPath($Destination)){throw '配置済みファイルの検証に失敗しました。別のファイルを選択してください。'}
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Destination) | Out-Null
    $staged=$Destination+'.import-'+[guid]::NewGuid().ToString('N')
    Copy-Item -LiteralPath $Selected -Destination $staged
    if(-not(Test-DownloadFile $staged $Sha256 $Bytes)){throw 'コピー後の検証に失敗しました。既存ファイルは変更していません。'}
    # Never overwrite a good installed file. Preserve an invalid one too, and
    # only move it aside after a verified replacement has been staged.
    if(Test-Path -LiteralPath $Destination){Move-Item -LiteralPath $Destination -Destination ($Destination+'.rejected-'+[guid]::NewGuid().ToString('N'))}
    Move-Item -LiteralPath $staged -Destination $Destination
}
function Get-Verified([string]$Url,[string]$Destination,[string]$Sha256,[long]$Bytes=0,[string]$Python='', [switch]$PythonFirst,[string[]]$AlternateUrls=@(),[switch]$Restart){
    Invoke-DownloadLock $Destination {Get-VerifiedUnlocked $Url $Destination $Sha256 $Bytes $Python -PythonFirst:$PythonFirst -AlternateUrls $AlternateUrls -Restart:$Restart}
}
function Get-VerifiedUnlocked([string]$Url,[string]$Destination,[string]$Sha256,[long]$Bytes=0,[string]$Python='', [switch]$PythonFirst,[string[]]$AlternateUrls=@(),[switch]$Restart){
    if($Url -notmatch '^https://' -or $Sha256 -notmatch '^[0-9a-fA-F]{64}$'){throw '検証可能なHTTPS URLとSHA-256が必要です。'}
    if(Test-DownloadFile $Destination $Sha256 $Bytes){Write-Host ('検証済みファイルを再利用: '+[IO.Path]::GetFileName($Destination));return}
    if(Test-Path -LiteralPath $Destination){
        if($Restart){Move-Item -LiteralPath $Destination -Destination ($Destination+'.rejected-'+[guid]::NewGuid().ToString('N'))}
        else{throw ('既存ファイルが必要な容量/SHA-256と異なります。上書きしません: '+[IO.Path]::GetFileName($Destination))}
    }
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Destination) | Out-Null
    $partial=$Destination+'.partial'
    if($Restart -and (Test-Path -LiteralPath $partial)){Move-Item -LiteralPath $partial -Destination ($partial+'.previous-'+[guid]::NewGuid().ToString('N'))}
    if(Test-DownloadFile $partial $Sha256 $Bytes){Move-Item -LiteralPath $partial -Destination $Destination;return}
    if($Bytes -gt 0 -and (Test-Path $partial) -and (Get-Item $partial).Length -ge $Bytes){
        Move-Item -LiteralPath $partial -Destination ($partial+'.rejected-'+[guid]::NewGuid().ToString('N'))
    }
    if(-not $Python){$Python=Get-Python}
    $curl=Get-Command curl.exe -ErrorAction SilentlyContinue
    $methods=if($PythonFirst){@('python','curl')}else{@('curl','python')}
    $errors=New-Object 'Collections.Generic.List[string]'
    $urls=@(@($Url)+$AlternateUrls | Where-Object {$_} | Select-Object -Unique)
    foreach($downloadUrl in $urls){
    $uri=$null
    if(-not [Uri]::TryCreate($downloadUrl,[UriKind]::Absolute,[ref]$uri) -or $uri.Scheme -ne 'https' -or $uri.UserInfo){throw '証明書検証付きHTTPS（認証情報を含まないURL）のみ利用できます。'}
    foreach($method in $methods){
        Assert-SetupActive
        $watch=[Diagnostics.Stopwatch]::StartNew()
        try{
            if($method -eq 'curl'){
                if(-not $curl){throw 'curl.exeが見つかりません。'}
                # Do not repeatedly retry deterministic certificate failures.
                Run $curl.Source @('--location','--fail','--connect-timeout','30','--speed-time','60','--speed-limit','1024','--proto','=https','--proto-redir','=https','--continue-at','-','--output',$partial,$downloadUrl)
            }else{
                if(-not $Python){throw '検証付きHTTPSの代替取得にはPython 3.12が必要です。'}
                Write-Host '別の証明書検証付きHTTPS実装（Python標準TLS）で取得します。TLS検証は無効にしません。' -ForegroundColor Cyan
                Run $Python @((Join-Path $PSScriptRoot 'download-file.py'),'--url',$downloadUrl,'--partial',$partial,'--sha256',$Sha256,'--bytes',[string]$Bytes)
            }
            if(-not(Test-DownloadFile $partial $Sha256 $Bytes)){
                if(Test-Path $partial){Move-Item -LiteralPath $partial -Destination ($partial+'.rejected-'+[guid]::NewGuid().ToString('N'))}
                throw '取得ファイルの容量/SHA-256が一致しません。破損ファイルを隔離しました。'
            }
            Move-Item -LiteralPath $partial -Destination $Destination
            $record=@{method=$method;url=$downloadUrl;ok=$true;exitCode=0;seconds=$watch.Elapsed.TotalSeconds;timestamp=[DateTime]::UtcNow.ToString('o')} | ConvertTo-Json -Compress
            [IO.File]::AppendAllText(($Destination+'.attempts.jsonl'),($record+"`n"),$Utf8)
            return
        }catch{
            Assert-SetupActive
            $errors.Add($method+': '+$_.Exception.Message)
            $record=@{method=$method;url=$downloadUrl;ok=$false;exitCode=$_.Exception.Data['ExitCode'];error=$_.Exception.Message;seconds=$watch.Elapsed.TotalSeconds;timestamp=[DateTime]::UtcNow.ToString('o')} | ConvertTo-Json -Compress
            [IO.File]::AppendAllText(($Destination+'.attempts.jsonl'),($record+"`n"),$Utf8)
            Write-Host ($method+'での取得に失敗しました。既存の正常なファイルは変更していません。') -ForegroundColor Yellow
            if((Test-Path $partial) -and $Bytes -gt 0 -and (Get-Item $partial).Length -ge $Bytes -and -not(Test-DownloadFile $partial $Sha256 $Bytes)){
                Move-Item -LiteralPath $partial -Destination ($partial+'.rejected-'+[guid]::NewGuid().ToString('N'))
            }
            # A second TLS implementation cannot fix a removed URL. Keep the
            # exact version/hash and try only registered alternate URLs next.
            if($_.Exception.Message -match '\b404\b'){break}
        }
    }
    }
    throw ($errors -join "`r`n")
}
function Invoke-FolderImport($Spec,[string]$Selected,[string]$Destination,[string]$Python='', [switch]$Place,[string]$StripRoot=''){
    if(-not $Python){$Python=Get-Python}
    if(-not $Python){throw 'フォルダー内のファイル検証にはPython 3.12が必要です。'}
    return (Invoke-DownloadLock $Destination {
        $request=$Destination+'.selection-'+[guid]::NewGuid().ToString('N')+'.json'
        $result=$request+'.result'
        Save-Json $request $Spec
        try{
            $args=@((Join-Path $PSScriptRoot 'import-download.py'),'--spec',$request,'--catalog',(Join-Path $PSScriptRoot 'download-catalog.json'),'--selected',$Selected,'--destination',$Destination,'--result',$result)
            if($Place){$args+='--place'}
            if($StripRoot){$args+=@('--strip-root',$StripRoot)}
            Run $Python $args
            return [string](Get-Content $result -Raw -Encoding UTF8 | ConvertFrom-Json).path
        }finally{
            foreach($file in @($request,$result)){if(Test-Path -LiteralPath $file){Remove-Item -LiteralPath $file}}
        }
    })
}
function Expand-Download([string]$Payload,$Spec,[string]$Destination,[string]$Python='', [string]$StripRoot=''){
    if(-not $Python){$Python=Get-Python}
    if(Test-Path -LiteralPath $Payload -PathType Container){[void](Invoke-FolderImport $Spec $Payload $Destination $Python -Place -StripRoot $StripRoot);return}
    $size=[long](Get-DownloadProperty $Spec 'bytes' '0')
    if(-not(Test-DownloadFile $Payload $Spec.sha256 $size)){throw '展開前のZIPの容量/SHA-256検証に失敗しました。'}
    $cache=Join-Path (Split-Path -Parent $Destination) ('payload-'+$Spec.sha256.Substring(0,16))
    if(Test-Path -LiteralPath $cache){
        try{[void](Invoke-FolderImport $Spec $cache $Destination $Python -Place -StripRoot $StripRoot);return}
        catch{Assert-SetupActive;Write-Host '展開キャッシュが不完全なため、検証済みZIPを別フォルダーに展開し直します。'}
        $cache+='-'+[guid]::NewGuid().ToString('N')
    }
    Run $Python @((Join-Path $PSScriptRoot 'extract-archive.py'),$Payload,$cache)
    [void](Invoke-FolderImport $Spec $cache $Destination $Python -Place -StripRoot $StripRoot)
}
function Get-DownloadProperty($Spec,[string]$Name,[string]$Fallback=''){
    if($Spec -and $Spec.PSObject.Properties[$Name] -and -not [string]::IsNullOrWhiteSpace([string]$Spec.$Name)){return [string]$Spec.$Name}
    return $Fallback
}
function Get-RequiredDownload($Spec,[string]$Destination,[switch]$NonInteractive,[string]$Python='', [string]$Selected=''){
    $name=Get-DownloadProperty $Spec 'name'
    $title=Get-DownloadProperty $Spec 'title' $name
    $bytes=[long](Get-DownloadProperty $Spec 'bytes' '0')
    $page=Get-DownloadProperty $Spec 'pageUrl' (Get-DownloadProperty $Spec 'url')
    $instructions=Get-DownloadProperty $Spec 'instructions' '配布ページで上記と完全に同じファイル名を選び、ダウンロードしてください。展開や手動配置は不要です。保存先は自由です。取得後に「ダウンロード済みファイルを指定する」から「参照」を押してください。'
    $alternate=Get-DownloadProperty $Spec 'alternateUrl'
    $version=Get-DownloadProperty $Spec 'version' 'ファイル名・固定URLに示す版（最新版へ自動変更しません）'
    $purpose=Get-DownloadProperty $Spec 'purpose' $title
    $type=Get-DownloadProperty $Spec 'artifactType' $(if($name -like '*.gguf'){'GGUF単体モデル'}elseif($name -like '*.whl'){'Python依存ファイル（展開不要）'}else{'ZIP（完成品／ソースの区別は下記手順を確認）'})
    $search=Get-DownloadProperty $Spec 'searchWords' $name
    if(Test-DownloadFile $Destination $Spec.sha256 $bytes){return $Destination}
    if(-not $Selected -and (Test-Path -LiteralPath ($Destination+'.verified-folder') -PathType Container)){
        try{return (Invoke-FolderImport $Spec ($Destination+'.verified-folder') $Destination $Python)}catch{Assert-SetupActive;Write-Host '展開済みキャッシュは再検証に合格しませんでした。取得・参照をやり直せます。'}
    }
    $errorText='';$next='automatic'
    if($Selected){$next='import'}
    while($true){
        Assert-SetupActive
        try{
            if($next -eq 'import'){
                if(Test-Path -LiteralPath $Selected -PathType Container){return (Invoke-FolderImport $Spec $Selected $Destination $Python)}
                Import-VerifiedFile $Selected $Destination $Spec.sha256 $bytes
            }
            elseif($next -eq 'alternative'){
                Get-Verified $Spec.url $Destination $Spec.sha256 $bytes $Python -PythonFirst -AlternateUrls @($alternate)
            }else{Get-Verified $Spec.url $Destination $Spec.sha256 $bytes $Python -AlternateUrls @($alternate) -Restart:($next -eq 'restart')}
            return $Destination
        }catch{
            Assert-SetupActive
            $errorText=$_.Exception.Message
            $reason=if($errorText -match 'CRYPT_E_REVOCATION_OFFLINE|0x80092013'){ 'Windowsの証明書失効確認先に接続できませんでした。別の検証付きHTTPS実装も試行済みです。セキュリティを無効にせず、ブラウザーからの取得も選べます。' }
                elseif($errorText -match '\b404\b'){ '指定版のURLが404（見つからない）です。勝手に最新版へ変更しません。登録済み代替URL、配布ページ、検索ワード、手動ファイル／展開済みフォルダー参照から復旧できます。' }
                elseif($errorText -match '(?i)certificate|schannel|CERT_|SSL'){ '証明書/TLS接続の問題です。PCの日時、Windows Update、会社・学校のネットワーク制限を確認してください。' }
                elseif($errorText -match 'SHA-256|ハッシュ|容量'){ '必要な版と異なるか、ファイルが破損しています。下記と同じ名前・サイズのファイルを取得してください。' }
                else{ 'ネットワーク・配布元またはファイル取得で問題が発生しました。下の選択肢から復旧できます。' }
            $logRoot=if($env:IVR_SETUP_LOG_ROOT){$env:IVR_SETUP_LOG_ROOT}else{Split-Path -Parent $Destination}
            Save-Json (Join-Path $logRoot 'download-error.json') @{target=$Spec;error=$errorText;reason=$reason;fileName=$name;installedFilePresent=(Test-Path $Destination);partialFilePresent=(Test-Path ($Destination+'.partial'))}
            if($NonInteractive){throw ($title+': '+$reason+"`r`n"+$errorText+"`r`n"+$page)}
        }
        :recovery while($true){
            $message="$reason`r`n`r`n正式名称: $title`r`n必要なファイル: $name`r`n必要バージョン: $version`r`n用途: $purpose`r`n種類: $type`r`nサイズ: $bytes bytes`r`nSHA-256: $($Spec.sha256)`r`n配布ページ: $page`r`n直接URL: $($Spec.url)`r`n代替URL: $(if($alternate){$alternate}else{'検証済みの別URLなし'})`r`n検索ワード: $search`r`n`r`n$instructions`r`n`r`n好きな場所に保存・展開できます。参照から指定すれば検証と配置は自動です。再試行回数・強制待機の制限はありません。`r`n`r`n実際のエラー:`r`n$errorText"
            $choice=Show-SetupDialog 'download' $message
            switch($choice){
                'retry' {$next='restart';break}
                'alternative' {$next='alternative';break}
                'page' {
                    if($page -match '^https://'){Start-Process $page}else{[void](Show-SetupDialog 'notice' $instructions)}
                    continue recovery
                }
                'file' {
                    $Selected=Show-SetupDialog 'file' ($title+' — '+$name+' を選択（展開せず、そのまま指定）') ('必要なファイル|'+$name+'|すべてのファイル|*.*')
                    if($Selected -eq 'cancel'){continue recovery}
                    $next='import';break
                }
                'folder' {
                    $Selected=Show-SetupDialog 'folder' ($title+' — 保存済み／ZIP展開済みフォルダーを参照（固定版の必要ファイルを検証します）')
                    if($Selected -eq 'cancel'){continue recovery}
                    $next='import';break
                }
                'ai' {& (Join-Path $PSScriptRoot 'support.ps1') -StateRoot $logRoot;continue recovery}
                default {
                    if($env:IVR_SETUP_CANCEL_REQUEST){[IO.File]::WriteAllText(($env:IVR_SETUP_CANCEL_REQUEST+'.cancel'),'cancel')}
                    throw [OperationCanceledException]::new('取得を中断しました。検証済みファイルと途中ファイルを保持しています。setup.batから再開できます。')
                }
            }
            break
        }
    }
}
