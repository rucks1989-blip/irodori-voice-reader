param(
    [Parameter(Mandatory=$true)][string]$SourceRoot,
    [Parameter(Mandatory=$true)][string]$TestRoot,
    [Parameter(Mandatory=$true)][string]$ExistingApiRoot,
    [Parameter(Mandatory=$true)][string]$ReferenceWav,
    [Parameter(Mandatory=$true)][string]$ExpectedFirefoxHost
)
# Opt-in: the caller must back up/restore the Firefox registration in finally.
# Only the isolated installer's own dialogs are operated. No builds are allowed.
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=[Text.Encoding]::UTF8
if(Test-Path -LiteralPath $TestRoot){throw 'Choose a new test folder.'}
$key='HKCU:\Software\Mozilla\NativeMessagingHosts\io.github.irodori.voice_reader.firefox'
if((Get-Item $key).GetValue('') -ne $ExpectedFirefoxHost){throw 'Firefox registration changed before the authorized test.'}
New-Item -ItemType Directory -Path $TestRoot | Out-Null
$server=Get-Content (Join-Path $ExistingApiRoot 'config\audio_cpp_server.shared.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$runtime=Get-Content (Join-Path $ExistingApiRoot 'config\runtime.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$model=@($server.models | Where-Object id -eq 'irodori-500m-v3-q8')[0].path
if(-not [IO.Path]::IsPathRooted($model)){$model=Join-Path $ExistingApiRoot $model}
$backend=Split-Path -Parent $runtime.audio_cpp_shared_executable
. (Join-Path $SourceRoot 'scripts\common.ps1')
$ast=[Management.Automation.Language.Parser]::ParseFile((Join-Path $SourceRoot 'tests\test_terminal_close.ps1'),[ref]$null,[ref]$null)
$type=$ast.Find({param($n) $n -is [Management.Automation.Language.CommandAst] -and $n.GetCommandName() -eq 'Add-Type' -and $n.Extent.Text -match 'class TerminalCloseFixture'},$true)
Invoke-Expression $type.Extent.Text
$sessionRoot=Join-Path $TestRoot 'session'
$arguments=@('-NoProfile','-ExecutionPolicy','Bypass','-File',(Join-Path $SourceRoot 'scripts\setup.ps1'),
    '-Browsers','firefox','-NewEnvironment','-InstallRoot',(Join-Path $TestRoot '日本語の導入先\requested'),
    '-SessionDirectory',$sessionRoot,'-Backend',$server.backend,'-BackendDirectory',$backend,'-ModelPath',$model,
    '-ReferenceWav',$ReferenceWav,'-StableMode','skip','-NoOpenGuide')
$oldLocal=$env:LOCALAPPDATA
$env:LOCALAPPDATA=Join-Path $TestRoot 'local'
$process=$null;$children=@{};$handled=New-Object 'Collections.Generic.HashSet[long]'
try{
    $process=Start-Process powershell.exe -ArgumentList (($arguments | ForEach-Object {Quote-WindowsArgument $_}) -join ' ') -WorkingDirectory $SourceRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $TestRoot 'setup.stdout.log') -RedirectStandardError (Join-Path $TestRoot 'setup.stderr.log')
    [void]$process.Handle
    $deadline=[DateTime]::UtcNow.AddMinutes(20)
    while(-not $process.HasExited){
        if([DateTime]::UtcNow -gt $deadline){throw 'Isolated installation timed out.'}
        foreach($child in @(Get-CimInstance Win32_Process -Filter ("ParentProcessId = "+$process.Id))){
            if($child.Name -notlike 'SetupDialog-*'){continue}
            if(-not $children.ContainsKey([int]$child.ProcessId)){try{$p=[Diagnostics.Process]::GetProcessById([int]$child.ProcessId);[void]$p.Handle;$children[$p.Id]=$p}catch{}}
        }
        foreach($p in @($children.Values)){
            if($p.HasExited){continue}
            foreach($window in [TerminalCloseFixture]::Windows($p.Id)){
                if($handled.Contains($window.Handle.ToInt64())){continue}
                $text=[TerminalCloseFixture]::Contents($window.Handle)
                $id=0
                if($text -match '新しい環境を作成しますか' -or $text -match 'この保存先で続行しますか'){$id=6}
                elseif($text -match 'NVIDIA GPUを検出しました|NVIDIA GPUを自動検出できませんでした'){$id=1}
                elseif($text -match 'Voice「my-voice」'){$id=6}
                elseif($text -match 'テスト音声を再生しますか'){$id=7}
                elseif($text -match '専用接続ヘルパーを現在のWindowsユーザーに登録しますか'){$id=6}
                elseif($text -match 'Native Host登録の衝突: Firefox'){
                    if(-not $text.Contains($ExpectedFirefoxHost) -or -not $text.Contains($TestRoot)){throw 'Unexpected Native Host switch target; refusing confirmation.'}
                    $id=6
                }
                elseif($text -match '声質安定版はインストールされていません'){$id=1}
                elseif($text -match '既存のAI相談文|エラー内容を含むAI相談文'){$id=7}
                elseif($text -match 'winget|ビルドを開始|Build Tools|CUDA Toolkit|ダウンロードの復旧'){
                    throw 'Unexpected tool installation/build/download recovery requires manual review; nothing was approved.'
                }
                if($id){
                    $button=[TerminalCloseFixture]::GetDlgItem($window.Handle,$id)
                    if($id -eq 1 -and $button -eq [IntPtr]::Zero){$button=[TerminalCloseFixture]::Button($window.Handle,'OK')}
                    if($button -ne [IntPtr]::Zero){
                        [void]$handled.Add($window.Handle.ToInt64())
                        [void][TerminalCloseFixture]::SendMessage($button,0xf5,[IntPtr]::Zero,[IntPtr]::Zero)
                    }
                }
            }
        }
        Start-Sleep -Milliseconds 150
        $process.Refresh()
    }
    $process.WaitForExit()
    if($process.ExitCode -ne 0){throw ('Isolated setup failed. See logs in '+$TestRoot)}
    $session=Get-Content (Join-Path $sessionRoot 'firefox-session.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    $marker=Get-Content (Join-Path $session.installRoot '.ivr-install.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    if($marker.state -ne 'runtime-ready' -or -not $marker.voiceTest -or $marker.voiceStable){throw 'Unexpected setup completion state.'}
    $expected=Join-Path $session.installRoot 'firefox-host\io.github.irodori.voice_reader.firefox.json'
    if((Get-Item $key).GetValue('') -ne $expected){throw 'Authorized isolated host was not registered.'}
    Save-Json (Join-Path $TestRoot 'installed.json') $session
    Write-Output ('Completed isolated ZIP installation: '+$session.installRoot)
}finally{
    $env:LOCALAPPDATA=$oldLocal
    if($process -and -not $process.HasExited){$process.Kill();[void]$process.WaitForExit(10000)}
    foreach($p in @($children.Values)){if(-not $p.HasExited){$p.Kill();[void]$p.WaitForExit(5000)}}
}
