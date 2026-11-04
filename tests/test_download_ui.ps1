param([Parameter(Mandatory=$true)][string]$TestRoot,[switch]$ContinueFirst,[switch]$AtRecovery,[switch]$ResumeOnly,[switch]$ModelFileOpen,[switch]$CpuRecovery,[switch]$AfterWav)
# Interactive Windows integration test. Uses only its prepared isolated source,
# session, install root and fault-injection curl; no global PATH/registry edits.
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=[Text.Encoding]::UTF8
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
$inputData=Get-Content (Join-Path $TestRoot 'test-inputs.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$script:events=New-Object 'Collections.Generic.List[object]'
if(Test-Path (Join-Path $TestRoot 'ui-events.json')){
    foreach($event in @(Get-Content (Join-Path $TestRoot 'ui-events.json') -Raw -Encoding UTF8 | ConvertFrom-Json)){$script:events.Add($event)}
}
$script:closedWindows=New-Object 'Collections.Generic.HashSet[string]'
function Record([string]$Event){
    $script:events.Add(@{event=$Event;time=[DateTime]::UtcNow.ToString('o')})
    $script:events | ConvertTo-Json -Depth 10 | Set-Content (Join-Path $TestRoot 'ui-events.json') -Encoding UTF8
    Write-Output $Event
}
Add-Type -TypeDefinition @'
using System;using System.Runtime.InteropServices;using System.Windows.Automation;using System.Threading;
public static class IvrTestUI {
 [DllImport("user32.dll",CharSet=CharSet.Unicode)] public static extern IntPtr SendMessage(IntPtr h,uint m,IntPtr w,string l);
 [DllImport("user32.dll")] public static extern IntPtr SendMessage(IntPtr h,uint m,IntPtr w,IntPtr l);
 [DllImport("user32.dll",CharSet=CharSet.Unicode)] static extern int GetClassName(IntPtr h,System.Text.StringBuilder name,int count);
 public static bool IsButton(AutomationElement e){var name=new System.Text.StringBuilder(256);GetClassName((IntPtr)e.Current.NativeWindowHandle,name,256);return name.ToString()=="Button";}
 [DllImport("kernel32.dll")] static extern IntPtr OpenProcess(uint access,bool inherit,int pid);
 [DllImport("kernel32.dll")] static extern IntPtr VirtualAllocEx(IntPtr p,IntPtr address,UIntPtr size,uint type,uint protect);
 [DllImport("kernel32.dll")] static extern bool WriteProcessMemory(IntPtr p,IntPtr address,byte[] data,UIntPtr size,out UIntPtr written);
 [DllImport("kernel32.dll")] static extern bool VirtualFreeEx(IntPtr p,IntPtr address,UIntPtr size,uint type);
 [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr p);
 public static void SelectFolder(AutomationElement dialog,string path){
   // WM_USER pointers are NOT marshalled across processes. Allocate the
   // selection string in this test-owned dialog, not in the controller.
   var data=System.Text.Encoding.Unicode.GetBytes(path+"\0");
   var p=OpenProcess(0x28,false,dialog.Current.ProcessId);
   if(p==IntPtr.Zero)throw new Exception("Cannot access test folder dialog");
   IntPtr remote=IntPtr.Zero;
   try{remote=VirtualAllocEx(p,IntPtr.Zero,(UIntPtr)data.Length,0x3000,4);UIntPtr written;
     if(remote==IntPtr.Zero || !WriteProcessMemory(p,remote,data,(UIntPtr)data.Length,out written))throw new Exception("Cannot set test folder selection");
     SendMessage((IntPtr)dialog.Current.NativeWindowHandle,0x467,(IntPtr)1,remote);
   }finally{if(remote!=IntPtr.Zero)VirtualFreeEx(p,remote,UIntPtr.Zero,0x8000);CloseHandle(p);}
 }
 public static void InvokeAsync(AutomationElement e){var t=new Thread(()=>{try{((InvokePattern)e.GetCurrentPattern(InvokePattern.Pattern)).Invoke();}catch{}});t.IsBackground=true;t.SetApartmentState(ApartmentState.MTA);t.Start();}
}
'@ -ReferencedAssemblies @([System.Windows.Automation.AutomationElement].Assembly.Location,[System.Windows.Automation.ControlType].Assembly.Location)
function Windows {
    $root=[System.Windows.Automation.AutomationElement]::RootElement
    $pids=@(Get-Process -ErrorAction SilentlyContinue | Where-Object {$_.ProcessName -like 'SetupDialog-*'} | ForEach-Object {$_.Id})
    return @($root.FindAll([System.Windows.Automation.TreeScope]::Children,[System.Windows.Automation.Condition]::TrueCondition) | Where-Object {
        ($_.Current.ProcessId -in $pids -or $_.Current.Name -eq 'AIへ相談する前に内容を確認') -and
        -not $script:closedWindows.Contains(($_.Current.ProcessId.ToString()+':'+$_.Current.NativeWindowHandle))
    })
}
function Wait-Control([string]$Id,[string]$Name='', [int]$Seconds=120){
    $until=[DateTime]::UtcNow.AddSeconds($Seconds)
    while([DateTime]::UtcNow -lt $until){
        foreach($window in (Windows)){
            try{
                $property=if($Id){[System.Windows.Automation.AutomationElement]::AutomationIdProperty}else{[System.Windows.Automation.AutomationElement]::NameProperty}
                $value=if($Id){$Id}else{$Name}
                $condition=New-Object System.Windows.Automation.PropertyCondition($property,$value)
                $element=$window.FindFirst([System.Windows.Automation.TreeScope]::Descendants,$condition)
                if($Id -eq '1'){$element=@($window.FindAll([System.Windows.Automation.TreeScope]::Descendants,$condition) | Where-Object {$_.Current.ControlType -eq [System.Windows.Automation.ControlType]::Button -or [IvrTestUI]::IsButton($_)}) | Select-Object -First 1}
                if($element -and $element.Current.IsEnabled){return [pscustomobject]@{Window=$window;Element=$element}}
            }catch{}
        }
        [Threading.Thread]::Sleep(100)
    }
    throw "UI wait timed out: id=$Id name=$Name"
}
function Click($Element){
    try{$pattern=$Element.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern)}catch{$pattern=$null}
    if($pattern){$pattern.Invoke()}elseif([IvrTestUI]::IsButton($Element)){[void][IvrTestUI]::SendMessage([IntPtr]$Element.Current.NativeWindowHandle,0xf5,[IntPtr]::Zero,[IntPtr]::Zero)}else{throw 'Control is not an invokable button'}
}
function Set-Combo($Element,[int]$Index){[void][IvrTestUI]::SendMessage([IntPtr]$Element.Current.NativeWindowHandle,0x14e,[IntPtr]$Index,[IntPtr]::Zero)}
function Recovery([string]$Expected){
    $r=Wait-Control 'downloadAction' '' 180
    # Read-only WinForms text can expose Text/Name rather than Edit/Value,
    # depending on the Windows accessibility provider used by this session.
    $controls=$r.Window.FindAll([System.Windows.Automation.TreeScope]::Descendants,[System.Windows.Automation.Condition]::TrueCondition)
    $text=($controls | ForEach-Object {$_.Current.Name;try{$_.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern).Current.Value}catch{}}) -join "`n"
    if($text -notmatch [regex]::Escape($Expected) -or $text -notmatch 'SHA-256' -or $text -notmatch '検索ワード'){throw 'Recovery did not display exact file/hash/search instructions'}
    $r | Add-Member NoteProperty Text $text -Force
    return $r
}
function Choose-Recovery($Recovery,[int]$Index){
    Set-Combo $Recovery.Element $Index
    $next=(Wait-Control 'runDownloadAction').Element
    [void]$script:closedWindows.Add(($Recovery.Window.Current.ProcessId.ToString()+':'+$Recovery.Window.Current.NativeWindowHandle))
    Click $next
}
function Pick-File([string]$Path,[switch]$AlreadyOpen){
    if(-not $AlreadyOpen){$browse=(Wait-Control 'browseFile').Element;[IvrTestUI]::InvokeAsync($browse)}
    $filename=Wait-Control '1148' '' 20
    $edit=$filename.Element
    if($edit.Current.ControlType -ne [System.Windows.Automation.ControlType]::Edit){$edit=$edit.FindFirst([System.Windows.Automation.TreeScope]::Descendants,(New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ControlTypeProperty,[System.Windows.Automation.ControlType]::Edit)))}
    $edit.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern).SetValue($Path)
    Click (Wait-Control '1').Element
    Click (Wait-Control 'useSelectedFile').Element
}
function Pick-Folder([string]$Path){
    [IvrTestUI]::InvokeAsync((Wait-Control 'browseFile').Element)
    $tree=$null;$deadline=[DateTime]::UtcNow.AddSeconds(20)
    while(-not $tree -and [DateTime]::UtcNow -lt $deadline){
        foreach($window in (Windows)){
            $element=$window.FindFirst([System.Windows.Automation.TreeScope]::Descendants,(New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ControlTypeProperty,[System.Windows.Automation.ControlType]::Tree)))
            if($element){
                $owner=$element
                while($owner -and $owner.Current.ControlType -ne [System.Windows.Automation.ControlType]::Window){$owner=[System.Windows.Automation.TreeWalker]::ControlViewWalker.GetParent($owner)}
                $tree=[pscustomobject]@{Window=$owner;Element=$element};break
            }
        }
        if(-not $tree){[Threading.Thread]::Sleep(100)}
    }
    if(-not $tree){throw 'Native folder picker did not open'}
    [IvrTestUI]::SelectFolder($tree.Window,$Path)
    Click (Wait-Control '1').Element
    Click (Wait-Control 'useSelectedFile').Element
}
function Launch([bool]$New,[string]$Log,[switch]$Unattended){
    $env:IVR_TEST_REAL_CURL=$inputData.realCurl
    $env:PATH=(Join-Path $TestRoot 'fault-bin')+';'+$env:PATH
    $cmd='"'+(Join-Path $inputData.source 'setup.bat')+'" -Browsers chrome -Backend cpu -StableMode skip -NoOpenGuide -SessionDirectory "'+(Join-Path $TestRoot 'session')+'" -ReferenceWav "'+$inputData.reference+'"'
    if($New){$cmd+=' -NewEnvironment'}
    if($Unattended){$cmd+=' -Unattended'}
    $process=Start-Process 'cmd.exe' -ArgumentList ('/d /c "'+$cmd+'"') -WorkingDirectory $inputData.source -PassThru -WindowStyle Hidden -RedirectStandardOutput (Join-Path $TestRoot $Log) -RedirectStandardError (Join-Path $TestRoot ($Log+'.stderr'))
    [void]$process.Handle
    return $process
}

if(-not $ResumeOnly -and -not $ModelFileOpen -and -not $CpuRecovery -and -not $AfterWav){
if($ContinueFirst -or $AtRecovery){
    $process=Get-CimInstance Win32_Process | Where-Object {$_.Name -eq 'cmd.exe' -and $_.CommandLine.Contains((Join-Path $inputData.source 'setup.bat'))} | Select-Object -First 1
    if(-not $process){throw 'Prepared first setup process is not running'}
    $first=Get-Process -Id $process.ProcessId;[void]$first.Handle
}else{
    $first=Launch $true 'first.log'
    $base=Wait-Control 'installBasePath'
    $base.Element.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern).SetValue((Join-Path $TestRoot 'chosen-install-location'))
    Click (Wait-Control '' 'この保存先を使う').Element
    Click (Wait-Control '6').Element
}
# The standard installer first offers reuse of an existing model. Decline that
# optional picker here so the actual download/failure/recovery path is exercised.
if(-not $AtRecovery){Click (Wait-Control '' '指定せず進む').Element}
$r=Recovery 'irodori-tts-500m-v3-q8_0.gguf';Record 'real_setup_inventory_passed_and_download_recovery_opened'
if($r.Text -notmatch '404' -or (-not $AtRecovery -and $r.Text -notmatch '0x80092013')){throw 'Injected curl/TLS and real HTTP 404 details are missing'}
Choose-Recovery $r 0
$r=Recovery 'irodori-tts-500m-v3-q8_0.gguf';Record 'immediate_retry_returned_to_recovery'
Choose-Recovery $r 1
$r=Recovery 'irodori-tts-500m-v3-q8_0.gguf';Record 'alternative_retry_returned_to_recovery'
Choose-Recovery $r 5
$ai=Wait-Control '' '相談文をコピー' 30
$promptPath=(Get-ChildItem (Join-Path $TestRoot 'chosen-install-location') -Recurse -Filter 'AI-support.txt' | Select-Object -First 1).FullName
$prompt=Get-Content $promptPath -Raw -Encoding UTF8
$repository='https://github.com/rucks1989-blip/irodori-voice-reader'
if($prompt -notmatch '404' -or $prompt -notmatch 'irodori-tts-500m-v3-q8_0.gguf' -or -not $prompt.Contains($repository) -or $prompt.Replace($repository,'') -match [regex]::Escape($env:USERNAME)){throw 'AI prompt is incomplete or includes private account name'}
$ai.Window.GetCurrentPattern([System.Windows.Automation.WindowPattern]::Pattern).Close()
$r=Recovery 'irodori-tts-500m-v3-q8_0.gguf';Record 'specific_redacted_ai_prompt_and_recovery_return_verified'
Choose-Recovery $r 6
if(-not $first.WaitForExit(30000)){throw 'Cancel did not stop setup'}
$session=Get-Content (Join-Path $TestRoot 'session\chrome-session.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$state=Get-Content (Join-Path $session.installRoot 'installer-state\state.json') -Raw -Encoding UTF8 | ConvertFrom-Json
if(-not $state.canceled){throw 'Cancellation/resume state was not recorded'}
Record 'cancel_kept_session_install_location_and_downloads'
}
$session=Get-Content (Join-Path $TestRoot 'session\chrome-session.json') -Raw -Encoding UTF8 | ConvertFrom-Json

if($ModelFileOpen -or $CpuRecovery -or $AfterWav){
    $running=Get-CimInstance Win32_Process | Where-Object {$_.Name -eq 'cmd.exe' -and $_.CommandLine.Contains((Join-Path $inputData.source 'setup.bat'))} | Select-Object -First 1
    $second=Get-Process -Id $running.ProcessId;[void]$second.Handle
}else{
$second=Launch $false 'resume.log'
$resume=Wait-Control 'setupChoice'
Set-Combo $resume.Element 0
Click (Wait-Control 'continueSetup').Element
Click (Wait-Control '' '指定せず進む').Element
$r=Recovery 'irodori-tts-500m-v3-q8_0.gguf'
Choose-Recovery $r 4
Pick-Folder (Join-Path $TestRoot 'wrong-folder')
$r=Recovery 'irodori-tts-500m-v3-q8_0.gguf';Record 'wrong_model_folder_rejected_without_exit'
Choose-Recovery $r 3
}
if(-not $CpuRecovery -and -not $AfterWav){
    Pick-File $inputData.model -AlreadyOpen:$ModelFileOpen
    Record 'D_drive_single_GGUF_selected_through_native_file_browser'
}
if(-not $AfterWav){
$r=Recovery 'audio-v0.9.3-bin-windows-x64-cpu-portable.zip'
Choose-Recovery $r 4
Pick-Folder $inputData.cpuFolder
Record 'C_drive_expanded_CPU_folder_selected_through_native_folder_browser'
Click (Wait-Control '6' '' 30).Element
# Voice is registered from the supplied authorized reference. Real CPU WAV
# synthesis can take minutes; these are UI-condition waits, not retry cooldowns.
Click (Wait-Control '7' '' 1200).Element
Click (Wait-Control '6' '' 60).Element
}
Click (Wait-Control '' 'OK' 60).Element
if(-not $second.WaitForExit(120000)){throw 'Setup did not finish after final notices'}
$marker=Get-Content (Join-Path $session.installRoot '.ivr-install.json') -Raw -Encoding UTF8 | ConvertFrom-Json
if($marker.state -ne 'runtime-ready' -or -not $marker.voiceTest){throw 'Actual normal WAV generation/setup did not complete'}
if(Test-Path (Join-Path $session.installRoot 'irodori-api\audio-cpp\bin\untrusted-extra.exe')){throw 'Uncatalogued executable was imported'}
Record 'normal_CPU_WAV_generated_and_setup_completed_no_untrusted_extra_imported'

$config=Join-Path $session.installRoot 'irodori-api\config\api.json'
$api=Get-Content $config -Raw -Encoding UTF8 | ConvertFrom-Json
$api.max_input_chars=19000
$api | ConvertTo-Json | Set-Content $config -Encoding UTF8
$protected=Get-ChildItem (Join-Path $session.installRoot 'irodori-api\config') -Filter '*.json' -File | ForEach-Object {@{path=$_.FullName;hash=(Get-FileHash $_.FullName).Hash}}
$third=Launch $false 'complete-reuse.log' -Unattended
if(-not $third.WaitForExit(120000)){throw 'Completed environment reuse did not finish'}
if($third.ExitCode -ne 0){throw 'Completed reuse returned a failure code'}
foreach($file in $protected){if((Get-FileHash $file.path).Hash -ne $file.hash){throw 'Completed rerun changed an existing configuration'}}
Record 'completed_rerun_reused_files_and_preserved_custom_API_and_Voice_configs'
Write-Output ('Verified isolated installation: '+$session.installRoot)
