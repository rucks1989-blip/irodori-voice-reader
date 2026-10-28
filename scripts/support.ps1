param([string]$StateRoot=(Join-Path $env:LOCALAPPDATA 'IrodoriVoiceReader\Installer'),[switch]$NoOpen)
. (Join-Path $PSScriptRoot 'common.ps1')
function Redact([string]$Text){
    $safe=$Text
    foreach($value in @($env:USERPROFILE,$env:USERNAME,$env:COMPUTERNAME)){
        if($value -and $value.Length -gt 2){$safe=$safe -replace [regex]::Escape($value),'<private>'}
    }
    $safe=$safe -replace '(?i)[A-Z]:[\\/][^\r\n"<>]*','<local-path>'
    $safe=$safe -replace '(?i)(authorization\s*[:=]\s*|(?:api[_-]?key|secret|token|password)\s*[:=]\s*)[^\s,;]+','$1<redacted>'
    $safe=$safe -replace '\b(?:gh[pousr]_[A-Za-z0-9_]+|github_pat_[A-Za-z0-9_]+|hf_[A-Za-z0-9]+)\b','<redacted>'
    return $safe
}
$statePath=Join-Path $StateRoot 'state.json'
$state=$null
if(Test-Path $statePath){$state=Get-Content $statePath -Raw -Encoding UTF8 | ConvertFrom-Json}
$gpu=Get-Gpu
$version=(Get-Content (Join-Path (Split-Path -Parent $PSScriptRoot) 'distribution.json') -Raw -Encoding UTF8 | ConvertFrom-Json).version
$step=if($state){$state.step}else{'記録なし'}
$errorText=if($state){Redact ([string]$state.error)}else{'記録なし'}
$prompt=@"
Irodori Voice Reader の導入で困っています。Windows初心者向けに一つずつ説明してください。
公式の試験用リポジトリ: https://github.com/rucks1989-blip/irodori-voice-reader
バージョン: $version
Windows: $([Environment]::OSVersion.Version)
GPU: $($gpu.name)
CUDA architecture: $($gpu.arch)
NVIDIA driver: $($gpu.driver)
停止した工程: $step
実際のエラーメッセージ（個人パス等は伏せ字）:
$errorText

まず原因と不足情報を確認し、既存モデル・Voice・設定を削除せずに復旧する方法を案内してください。
非公開リポジトリなので、閲覧できない場合は内容を推測せず、必要なファイルを尋ねてください。
API入口は127.0.0.1:8765、audio.cppは共有8091、通常版/声質安定版は同じサーバーです。
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
