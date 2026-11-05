param([Parameter(Mandatory=$true)][string]$TestRoot,[string]$SourceRoot='')
# Interactive Windows regression: close a real, isolated console window, not
# the installer process. Never install/download/register anything on the PC.
$ErrorActionPreference='Stop'
if(-not $SourceRoot){$SourceRoot=Split-Path -Parent $PSScriptRoot}
[Console]::OutputEncoding=[Text.Encoding]::UTF8
if(Test-Path -LiteralPath $TestRoot){throw 'Use a new, empty test root; existing files will not be overwritten.'}
New-Item -ItemType Directory -Path $TestRoot | Out-Null
$utf8=New-Object Text.UTF8Encoding($true)
Add-Type @'
using System;using System.Text;using System.Collections.Generic;using System.Runtime.InteropServices;
public static class TerminalCloseFixture {
    public class Window { public IntPtr Handle;public string Title;public string ClassName; }
    delegate bool Callback(IntPtr h,IntPtr p);
    [DllImport("user32.dll")] static extern bool EnumWindows(Callback cb,IntPtr p);
    [DllImport("user32.dll")] static extern bool EnumChildWindows(IntPtr h,Callback cb,IntPtr p);
    [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr h,out int pid);
    [DllImport("user32.dll",CharSet=CharSet.Unicode)] static extern int GetWindowText(IntPtr h,StringBuilder s,int n);
    [DllImport("user32.dll",CharSet=CharSet.Unicode)] static extern int GetClassName(IntPtr h,StringBuilder s,int n);
    [DllImport("user32.dll")] public static extern IntPtr GetDlgItem(IntPtr h,int id);
    [DllImport("user32.dll")] public static extern bool IsWindowEnabled(IntPtr h);
    [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
    [DllImport("user32.dll")] public static extern IntPtr SendMessage(IntPtr h,uint m,IntPtr w,IntPtr l);
    [DllImport("user32.dll")] public static extern bool PostMessage(IntPtr h,uint m,IntPtr w,IntPtr l);
    static string Text(IntPtr h){var b=new StringBuilder(16384);GetWindowText(h,b,b.Capacity);return b.ToString();}
    public static Window Describe(IntPtr h){var c=new StringBuilder(256);GetClassName(h,c,c.Capacity);return new Window{Handle=h,Title=Text(h),ClassName=c.ToString()};}
    public static Window[] Windows(int processId){
        var found=new List<Window>();
        EnumWindows((h,p)=>{int pid;GetWindowThreadProcessId(h,out pid);if(pid==processId){var c=new StringBuilder(256);GetClassName(h,c,c.Capacity);found.Add(new Window{Handle=h,Title=Text(h),ClassName=c.ToString()});}return true;},IntPtr.Zero);
        return found.ToArray();
    }
    public static string Contents(IntPtr parent){
        var b=new StringBuilder(Text(parent));EnumChildWindows(parent,(h,p)=>{b.AppendLine(Text(h));return true;},IntPtr.Zero);return b.ToString();
    }
    public static IntPtr Button(IntPtr parent,string text){
        IntPtr result=IntPtr.Zero;EnumChildWindows(parent,(h,p)=>{if(Text(h)==text){result=h;return false;}return true;},IntPtr.Zero);return result;
    }
}
'@
$results=@()
$foreign=Start-Process powershell.exe -ArgumentList '-NoProfile -Command Start-Sleep -Seconds 180' -PassThru -WindowStyle Hidden
[void]$foreign.Handle
try{
    foreach($mode in @('active','cancel-pending')){
        $root=Join-Path $TestRoot $mode
        $source=Join-Path $root 'source';$scripts=Join-Path $source 'scripts'
        New-Item -ItemType Directory -Path $scripts -Force | Out-Null
        foreach($name in @('setup.ps1','common.ps1','downloads.ps1','install-files.ps1','browser-setup.ps1','stable-addon.ps1','setup-dialog.cs','setup-job.cs')){
            Copy-Item -LiteralPath (Join-Path $SourceRoot ('scripts\'+$name)) -Destination $scripts
        }
        foreach($name in @('setup.bat','distribution.json')){Copy-Item -LiteralPath (Join-Path $SourceRoot $name) -Destination $source}
        # Pause the real entrypoint immediately before file placement. All
        # dialogs, the progress GUI, parent watcher, and job cleanup are real.
        $guard=@'

[IO.File]::WriteAllText((Join-Path $env:IVR_TERMINAL_TEST_ROOT 'setup.pid'),[string]$PID)
Add-Type 'public static class FixtureConsoleHandle { [System.Runtime.InteropServices.DllImport("kernel32.dll")] public static extern System.IntPtr GetConsoleWindow(); }'
[IO.File]::WriteAllText((Join-Path $env:IVR_TERMINAL_TEST_ROOT 'console.hwnd'),[string]([FixtureConsoleHandle]::GetConsoleWindow().ToInt64()))
function Get-ExistingEnvironment { 'Isolated terminal-close fixture; no live environment inspection required.' }
function Assert-FreePorts {
    [IO.File]::WriteAllText((Join-Path $env:IVR_TERMINAL_TEST_ROOT 'paused-before-install'),'no downloads, copies, or registrations')
    if($env:IVR_TERMINAL_TEST_MODE -eq 'cancel-pending'){
        [Threading.Thread]::Sleep(120000)
    }else{
        Run 'powershell.exe' @('-NoProfile','-Command','Start-Sleep -Seconds 120')
    }
    throw 'Fixture pause ended; installation is prohibited in this test.'
}
'@
        [IO.File]::AppendAllText((Join-Path $scripts 'common.ps1'),$guard,$utf8)
        $title='IVR-Terminal-Close-'+[guid]::NewGuid().ToString('N')
        $wrapper=Join-Path $root 'terminal.cmd'
        $lines=@('@echo off',('title '+$title),('set "LOCALAPPDATA='+(Join-Path $root 'local')+'"'),('set "IVR_TERMINAL_TEST_ROOT='+$root+'"'),('set "IVR_TERMINAL_TEST_MODE='+$mode+'"'),('call "'+(Join-Path $source 'setup.bat')+'" -Browsers chrome -NewEnvironment -InstallRoot "'+(Join-Path $root 'requested')+'" -SessionDirectory "'+(Join-Path $root 'session')+'" -StableMode skip -NoOpenGuide'))
        [IO.File]::WriteAllText($wrapper,($lines -join "`r`n"),[Text.Encoding]::ASCII)
        $terminal=$null;$setup=$null;$gui=$null;$children=@{};$console=$null;$progress=$null
        try{
            # An explicit conhost creates a dedicated classic Windows console,
            # never a new tab in the user's existing Windows Terminal window.
            $terminal=Start-Process (Join-Path $env:WINDIR 'System32\conhost.exe') -ArgumentList ('-ForceNoHandoff cmd.exe /d /k "'+$wrapper+'"') -PassThru -WindowStyle Normal
            [void]$terminal.Handle
            $deadline=[DateTime]::UtcNow.AddSeconds(45)
            while([DateTime]::UtcNow -lt $deadline){
                if(-not $setup -and (Test-Path (Join-Path $root 'setup.pid'))){$setup=[Diagnostics.Process]::GetProcessById([int](Get-Content (Join-Path $root 'setup.pid')));[void]$setup.Handle}
                if($setup){
                    foreach($child in @(Get-CimInstance Win32_Process -Filter ("ParentProcessId = "+$setup.Id))){
                        if(-not $children.ContainsKey([int]$child.ProcessId)){
                            try{$p=[Diagnostics.Process]::GetProcessById([int]$child.ProcessId);[void]$p.Handle;$children[$p.Id]=$p}catch{}
                        }
                    }
                    foreach($p in @($children.Values)){
                        if($p.HasExited){continue}
                        foreach($window in [TerminalCloseFixture]::Windows($p.Id)){
                            $text=[TerminalCloseFixture]::Contents($window.Handle)
                            $button=[IntPtr]::Zero
                            if($text -match '新しい環境を作成しますか' -or ($text -match '最終的な保存先:' -and $text -match 'この保存先で続行')){$button=[TerminalCloseFixture]::GetDlgItem($window.Handle,6)}
                            elseif($text -match 'NVIDIA GPUを検出しました|NVIDIA GPUを自動検出できませんでした'){
                                $button=[TerminalCloseFixture]::Button($window.Handle,'OK')
                                if($button -eq [IntPtr]::Zero){$button=[TerminalCloseFixture]::GetDlgItem($window.Handle,1)}
                            }
                            elseif($text -match '既存のAI相談文の確認画面'){$button=[TerminalCloseFixture]::GetDlgItem($window.Handle,7)}
                            elseif($text -match '安全に中止'){$gui=$p;$progress=$window}
                            [IO.File]::WriteAllText((Join-Path $root ('last-window-'+$p.Id+'-'+$window.Handle.ToInt64()+'.txt')),$text,$utf8)
                            if($button -ne [IntPtr]::Zero){[void][TerminalCloseFixture]::SendMessage($button,0xf5,[IntPtr]::Zero,[IntPtr]::Zero)}
                        }
                    }
                }
                # The host may use another conhost PID and replace the title.
                # Read the exact console HWND from our own setup PowerShell.
                if(Test-Path (Join-Path $root 'console.hwnd')){
                    $console=[TerminalCloseFixture]::Describe([IntPtr]([long](Get-Content (Join-Path $root 'console.hwnd'))))
                    [IO.File]::WriteAllText((Join-Path $root 'console-window.json'),(@{title=$console.Title;class=$console.ClassName;visible=[TerminalCloseFixture]::IsWindowVisible($console.Handle)} | ConvertTo-Json),$utf8)
                    if(-not [TerminalCloseFixture]::IsWindowVisible($console.Handle)){throw 'The setup console HWND is not a visible terminal window; see console-window.json.'}
                }
                if($console -and $progress -and (Test-Path (Join-Path $root 'paused-before-install'))){break}
                Start-Sleep -Milliseconds 100
            }
            if(-not $console -or -not $progress -or -not(Test-Path (Join-Path $root 'paused-before-install'))){throw "Real console/progress checkpoint missing: console=$([bool]$console), GUI=$([bool]$progress), paused=$([bool](Test-Path (Join-Path $root 'paused-before-install')))"}
            $pendingText=''
            if($mode -eq 'cancel-pending'){
                $cancel=[TerminalCloseFixture]::Button($progress.Handle,'安全に中止')
                if($cancel -eq [IntPtr]::Zero){throw 'Cancel button missing'}
                [void][TerminalCloseFixture]::SendMessage($cancel,0xf5,[IntPtr]::Zero,[IntPtr]::Zero)
                $pendingText=[TerminalCloseFixture]::Contents($progress.Handle)
                if($pendingText -notmatch '今回起動した処理を終了しています' -or [TerminalCloseFixture]::IsWindowEnabled($cancel)){throw 'Reported cancellation-wait state was not reproduced.'}
            }
            $watch=[Diagnostics.Stopwatch]::StartNew()
            # WM_SYSCOMMAND / SC_CLOSE is the title-bar Close operation. This
            # is deliberately NOT Process.Kill or Stop-Process on the parent.
            if(-not [TerminalCloseFixture]::PostMessage($console.Handle,0x112,[IntPtr]0xf060,[IntPtr]::Zero)){throw 'Console Close operation failed'}
            $times=@{};$deadline=[DateTime]::UtcNow.AddSeconds(20)
            while([DateTime]::UtcNow -lt $deadline){
                foreach($pair in @(@{name='terminal';p=$terminal},@{name='setup';p=$setup},@{name='gui';p=$gui})){
                    if(-not $times.ContainsKey($pair.name) -and $pair.p.HasExited){$times[$pair.name]=$watch.ElapsedMilliseconds}
                }
                if($times.Count -eq 3){break}
                Start-Sleep -Milliseconds 50
            }
            if($times.Count -ne 3){throw ('Window Close left a process behind: '+($times.Keys -join ','))}
            if($foreign.HasExited){throw 'Unrelated fixture process was stopped'}
            foreach($p in @($children.Values)){if(-not $p.HasExited -and -not $p.WaitForExit(3000)){throw 'Owned fixture child survived terminal closure'}}
            $result=@{mode=$mode;method='Real conhost/cmd window: WM_SYSCOMMAND SC_CLOSE';consoleClass=$console.ClassName;setupPid=$setup.Id;guiPid=$gui.Id;exitMilliseconds=$times;cancelWaitReproduced=($mode -eq 'cancel-pending');unrelatedProcessPreserved=$true;noInstallation=$true}
            $results+=,$result
            Write-Output ($result | ConvertTo-Json -Depth 5 -Compress)
        }finally{
            # Failure cleanup is restricted to exact test-owned process handles.
            foreach($p in @($children.Values)+@($setup,$terminal)){if($p){try{if(-not $p.HasExited){$p.Kill();[void]$p.WaitForExit(5000)}}catch{}}}
        }
    }
    [IO.File]::WriteAllText((Join-Path $TestRoot 'results.json'),($results | ConvertTo-Json -Depth 10),$utf8)
}finally{if(-not $foreign.HasExited){$foreign.Kill();[void]$foreign.WaitForExit(5000)}}
