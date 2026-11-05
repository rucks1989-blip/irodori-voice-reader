param([string]$StateRoot=(Join-Path $env:LOCALAPPDATA 'IrodoriVoiceReader\Installer'),[switch]$NoOpen)
. (Join-Path $PSScriptRoot 'common.ps1')
function Redact([string]$Text){
    $safe=$Text
    # A PC username can coincide with a public repository owner (including this
    # application's official URL). Preserve only the manifest's public HTTPS
    # URLs; never preserve URLs with embedded credentials or private query keys.
    $publicUrls=@('https://github.com/rucks1989-blip/irodori-voice-reader')
    $definition=Get-Content (Join-Path (Split-Path -Parent $PSScriptRoot) 'distribution.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    $targets=@($definition.model,$definition.audioCppSource)+@($definition.assets)+@($definition.pythonWheels)
    foreach($asset in $definition.assets){if($asset.PSObject.Properties['dependencies']){$targets+=@($asset.dependencies)}}
    foreach($target in $targets){foreach($key in @('url','pageUrl','alternateUrl')){
        $url=Get-DownloadProperty $target $key
        $parsed=$null
        if($url -and [Uri]::TryCreate($url,[UriKind]::Absolute,[ref]$parsed) -and $parsed.Scheme -eq 'https' -and -not $parsed.UserInfo -and $url -notmatch '(?i)[?&](?:token|key|signature|x-amz-)'){$publicUrls+=$url}
    }}
    $preserved=@{};$index=0
    foreach($url in @($publicUrls | Sort-Object Length -Descending | Select-Object -Unique)){
        $token=[string][char](0xE000+$index);$index++
        if($safe.Contains($url)){$safe=$safe.Replace($url,$token);$preserved[$token]=$url}
    }
    foreach($value in @($env:USERPROFILE,$env:USERNAME,$env:COMPUTERNAME)){
        if($value -and $value.Length -gt 2){$safe=$safe -replace [regex]::Escape($value),'<private>'}
    }
    $safe=$safe -replace '(?i)(?<![A-Z0-9])[A-Z]:[\\/][^\r\n"<>]*','<local-path>'
    $safe=$safe -replace '(?i)(authorization\s*[:=]\s*|(?:api[_-]?key|secret|token|password)\s*[:=]\s*)[^\s,;]+','$1<redacted>'
    $safe=$safe -replace '\b(?:gh[pousr]_[A-Za-z0-9_]+|github_pat_[A-Za-z0-9_]+|hf_[A-Za-z0-9]+)\b','<redacted>'
    $safe=$safe -replace '(?i)([?&](?:access_token|token|api_key|signature|x-amz-[^=&\s]+)=)[^&\s]+','$1<redacted>'
    $safe=$safe -replace '(?i)https://[^/\s:@]+:[^/\s@]+@','https://<redacted>@'
    $safe=$safe -replace '\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b','<email>'
    foreach($token in $preserved.Keys){$safe=$safe.Replace($token,$preserved[$token])}
    return $safe
}
if(-not $PSBoundParameters.ContainsKey('StateRoot')){
    $sessionFiles=@(Get-ChildItem -LiteralPath $StateRoot -Filter '*session.json' -File -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending)
    $sessionFile=if($sessionFiles.Count){$sessionFiles[0].FullName}else{Join-Path $StateRoot 'chrome-session.json'}
    if(Test-Path $sessionFile){
        $session=Get-Content $sessionFile -Raw -Encoding UTF8 | ConvertFrom-Json
        $candidateRoot=Join-Path $session.installRoot 'installer-state'
        # Failures before file placement are recorded in the launcher's state
        # directory. Do not hide them behind an older installation's state.
        $locations=@($StateRoot,$candidateRoot) | Select-Object -Unique
        $records=@(foreach($location in $locations){foreach($name in @('state.json','browser-state.json')){
            $path=Join-Path $location $name
            if(Test-Path -LiteralPath $path){Get-Item -LiteralPath $path}
        }})
        if($records.Count){$StateRoot=Split-Path -Parent ($records | Sort-Object LastWriteTime -Descending | Select-Object -First 1).FullName}
    }
}
$statePath=Join-Path $StateRoot 'state.json'
if(Test-Path (Join-Path $StateRoot 'browser-state.json')){
    if(-not(Test-Path $statePath) -or (Get-Item (Join-Path $StateRoot 'browser-state.json')).LastWriteTime -gt (Get-Item $statePath).LastWriteTime){$statePath=Join-Path $StateRoot 'browser-state.json'}
}
$state=$null
if(Test-Path $statePath){$state=Get-Content $statePath -Raw -Encoding UTF8 | ConvertFrom-Json}
$gpu=Get-Gpu
$contextText='インストール先／問題のあるブラウザー: 記録なし。ご自身で追記してください。'
$contextPath=Join-Path $StateRoot 'setup-context.json'
if(Test-Path $contextPath){
    $context=Get-Content $contextPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $driveName=if($context.drive){([string]$context.drive).Substring(0,1)+'ドライブ'}else{'未確定'}
    $contextText="インストール先（個人パスは伏せ字）: $($context.installRoot)`r`n保存先ドライブ: $driveName`r`n対象ブラウザー: $($context.browsers)`r`nAPIポート: $($context.apiPort) / audio.cppポート: $($context.backendPort)`r`n容量確認: $($context.storage)"
}
$version=(Get-Content (Join-Path (Split-Path -Parent $PSScriptRoot) 'distribution.json') -Raw -Encoding UTF8 | ConvertFrom-Json).version
$step=if($state){$state.step}else{'記録なし'}
$errorText=if($state){Redact ([string]$state.error)}else{'記録なし'}
$downloadText=''
$downloadPath=Join-Path $StateRoot 'download-error.json'
if(Test-Path $downloadPath){
    $download=Get-Content $downloadPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $target=$download.target
    $downloadText=@"
取得対象の正式名称: $(Get-DownloadProperty $target 'title' (Get-DownloadProperty $target 'name'))
実際のファイル名: $(Get-DownloadProperty $target 'name')
必要バージョン: $(Get-DownloadProperty $target 'version' 'ファイル名・固定URLの版')
用途: $(Get-DownloadProperty $target 'purpose' (Get-DownloadProperty $target 'title'))
種類（完成品／ソース等）: $(Get-DownloadProperty $target 'artifactType' '下記操作手順を参照')
用途・操作手順: $(Get-DownloadProperty $target 'instructions')
必要サイズ(bytes): $(Get-DownloadProperty $target 'bytes' '不明')
検証SHA-256: $(Get-DownloadProperty $target 'sha256')
正式ダウンロードURL: $(Get-DownloadProperty $target 'url' '公開単独URLなし（配布物同梱）')
代替URL: $(Get-DownloadProperty $target 'alternateUrl' '検証済みの別URLなし。安全な別のHTTPS実装を使用可能')
配布ページ: $(Get-DownloadProperty $target 'pageUrl')
検索ワード: $(Get-DownloadProperty $target 'searchWords')
現在の状況: 配置先ファイルあり=$($download.installedFilePresent)、途中ファイルあり=$($download.partialFilePresent)
エラーの分類: $($download.reason)
実際の取得エラー（終了コード・エラーメッセージ）:
$(Redact ([string]$download.error))

このモデルまたはaudio.cppを利用したいのですが、上記の取得エラーで困っています。
初心者なので、どのページを開き、何をクリックし、どのファイルをダウンロードし、
セットアップの「ダウンロード済みファイルを指定する」→「参照」からどう指定するか、具体的に説明してください。
URLが404になっている場合も含め、このバージョン・ハッシュに一致する現在入手可能な場所を探してください。勝手に非互換な最新版へ変更しないでください。
GGUFモデル、ソースコードZIP、完成済み実行ファイルZIP、依存DLLのZIPを混同しないでください。
保存先は自由です。セットアップが検証・コピー・展開するため、複雑な手動配置を要求しないでください。
展開済みZIPフォルダーも、セットアップの「フォルダーを参照」から指定できます。
"@
}
$prompt=@"
Irodori Voice Reader の導入で困っています。Windows初心者向けに一つずつ説明してください。
公式の試験用リポジトリ: https://github.com/rucks1989-blip/irodori-voice-reader
バージョン: $version
Windows: $([Environment]::OSVersion.Version)
GPU: $($gpu.name)
CUDA architecture: $($gpu.arch)
NVIDIA driver: $($gpu.driver)
GPU名が取得できない場合は、タスク マネージャーの「パフォーマンス」→「GPU」などで確認し、コピー後にChatGPTへの貼り付け先で手入力してください: [GPU名を記入]
$contextText
個人パスの伏せ字により保存先が分からない場合は、個人情報を除いた保存先・ドライブを追記してください。
停止した工程: $step
実際のエラーメッセージ（個人パス等は伏せ字）:
$errorText

$downloadText

まず原因と不足情報を確認し、既存モデル・Voice・設定を削除せずに復旧する方法を案内してください。
リポジトリを閲覧できない場合も、上記の正式ファイル名・URL・検証情報を使って説明してください。不足情報は推測しないでください。
APIとaudio.cppは今回の導入先に記録された専用ループバックポートを使用します。通常版/声質安定版は同じ環境内のサーバーです。
セキュリティ機能の無効化や、未確認の管理者コマンドは提案しないでください。
"@
$prompt=Redact $prompt
New-Item -ItemType Directory -Force -Path $StateRoot | Out-Null
$out=Join-Path $StateRoot 'AI-support.txt'
[IO.File]::WriteAllText($out,$prompt,$Utf8)
if($NoOpen){Write-Output $out;return}
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$form=New-Object Windows.Forms.Form
$form.Text='AIへ相談する前に内容を確認';$form.Width=760;$form.Height=580;$form.StartPosition='CenterScreen'
$text=New-Object Windows.Forms.TextBox
$text.Multiline=$true;$text.ReadOnly=$true;$text.ScrollBars='Both';$text.Dock='Fill';$text.Text=$prompt
$panel=New-Object Windows.Forms.FlowLayoutPanel
$panel.Dock='Bottom';$panel.Height=80
$copy=New-Object Windows.Forms.Button;$copy.Text='相談文をコピー';$copy.Width=150;$copy.Height=35
$copy.Add_Click({[Windows.Forms.Clipboard]::SetText($text.Text);$copy.Text='コピーしました'})
$open=New-Object Windows.Forms.Button;$open.Text='ChatGPTを開く';$open.Width=150;$open.Height=35
$open.Add_Click({Start-Process 'https://chatgpt.com/'})
$note=New-Object Windows.Forms.Label;$note.Text='自動送信しません。内容を確認してから、ご自身で貼り付けてください。';$note.AutoSize=$true
$panel.Controls.AddRange(@($copy,$open,$note));$form.Controls.Add($text);$form.Controls.Add($panel)
[void]$form.ShowDialog()
