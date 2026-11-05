"""Installer regression tests: temporary files/mocked registry, no live installs."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import unittest

from test_installer_windows import ROOT, ps


def q(value):
    return "'" + str(value).replace("'", "''") + "'"


LOAD_SETUP_FUNCTIONS = r"""
$ast=[Management.Automation.Language.Parser]::ParseFile((Resolve-Path './scripts/setup.ps1'),[ref]$null,[ref]$null)
foreach($name in @('Get-StorageSummary','Select-InstallBase')){
    $fn=$ast.Find({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq $name},$true)
    Invoke-Expression $fn.Extent.Text
}
"""

# The production registration logic runs unchanged except its common.ps1 import.
# All registry access is redirected to this in-memory dictionary, never HKCU/HKLM.
REGISTRY_MOCK = r"""
$script:registry=@{};$script:writes=0;$script:confirmed=0;$script:late=$false
function Test-Path {
    param($Path,$LiteralPath,$PathType)
    $p=if($LiteralPath){$LiteralPath}else{$Path}
    if($p -match '^HK[CL][UM]:'){return $script:registry.ContainsKey($p)}
    if($PathType){return Microsoft.PowerShell.Management\Test-Path -LiteralPath $p -PathType $PathType}
    return Microsoft.PowerShell.Management\Test-Path -LiteralPath $p
}
function Get-Item {
    param($Path,$LiteralPath)
    $p=if($LiteralPath){$LiteralPath}else{$Path}
    if($p -match '^HK[CL][UM]:'){
        $item=[pscustomobject]@{Value=$script:registry[$p]}
        $item | Add-Member ScriptMethod GetValue {param($name) $this.Value}
        return $item
    }
    Microsoft.PowerShell.Management\Get-Item -LiteralPath $p
}
function New-Item {
    param($Path,$ItemType,[switch]$Force)
    if($Path -match '^HK[CL][UM]:'){$script:registry[$Path]='';return}
    Microsoft.PowerShell.Management\New-Item -Path $Path -ItemType $ItemType -Force:$Force
}
function Set-Item {
    param($Path,$Value)
    if($Path -notmatch '^HK[CL][UM]:'){throw 'Unexpected write'}
    $script:registry[$Path]=$Value;$script:writes++
}
function Copy-Item {
    param($Path,$Destination,[switch]$Force)
    Microsoft.PowerShell.Management\Copy-Item -Path $Path -Destination $Destination -Force:$Force
    if($script:late){$script:registry[$script:conflictKey]='changed-after-confirmation-during-file-placement'}
}
function Confirm($Message){
    $script:confirmed++
    if($Message -notmatch 'Firefox|Chrome' -or $Message -notmatch '既存の登録先:' -or $Message -notmatch '新しく登録する先:'){throw 'Missing conflict detail'}
    if($script:race){$script:registry[$script:conflictKey]='changed-during-confirmation'}
    return $script:approve
}
$registration=Get-Content './scripts/register-hosts.ps1' -Raw -Encoding UTF8
$registration=$registration.Replace(". (Join-Path `$PSScriptRoot 'common.ps1')",'')
$register=[scriptblock]::Create($registration)
"""


@unittest.skipUnless(os.name == 'nt', 'Windows setup regressions')
class SetupReworkTests(unittest.TestCase):
    def fixture(self, root):
        (root/'irodori-api/.venv/Scripts').mkdir(parents=True)
        (root/'irodori-api/.venv/Scripts/python.exe').write_bytes(b'not executed')
        (root/'native-host').mkdir()
        for name in ('irodori_native_host.py', 'chrome_native_host.py'):
            (root/'native-host'/name).write_text('# fixture', encoding='utf8')
        identity = dict(extensionId='a'*32, hostName='io.github.irodori.voice_reader.chrome.i'+'a'*32,
                        apiPort=19876, backendPort=19877)
        (root/'chrome-identity.json').write_text(json.dumps(identity), encoding='utf8')
        return identity

    def test_registration_additions_conflicts_and_no_unattended_overwrite(self):
        scenarios = [
            ('chrome', '', False, False, False, 1),
            ('firefox', '', False, False, False, 1),
            ('both', '', False, False, False, 2),
            ('both', 'user', False, True, False, 0),
            ('firefox', 'empty', False, True, False, 0),
            ('both', 'user', True, False, False, 0),
            ('both', 'user', True, True, False, 2),
            ('firefox', 'machine', False, True, False, 0),
            ('firefox', 'machine', True, True, False, 1),
            ('both', 'user', True, True, True, 0),
            ('firefox', 'late', True, True, False, 0),
        ]
        for browser, conflict, approve, interactive, race, writes in scenarios:
            with self.subTest(browser=browser, conflict=conflict, approve=approve, interactive=interactive, race=race):
                with tempfile.TemporaryDirectory() as folder:
                    root=Path(folder)
                    self.fixture(root)
                    code, out, err = ps(". './scripts/common.ps1';" + REGISTRY_MOCK + rf"""
                    $script:approve=${str(approve).lower()};$script:race=${str(race).lower()}
                    $script:late='{conflict}' -eq 'late'
                    $script:conflictKey='HKCU:\Software\Mozilla\NativeMessagingHosts\io.github.irodori.voice_reader.firefox'
                    if('{conflict}' -eq 'machine'){{$script:conflictKey=$script:conflictKey.Replace('HKCU:','HKLM:')}}
                    if('{conflict}'){{$script:registry[$script:conflictKey]=$(if('{conflict}' -eq 'empty'){{''}}else{{'old-environment.json'}})}}
                    $failed=$false
                    try{{& $register -InstallRoot {q(root)} -Browsers {browser} -ConfirmSwitch:${str(interactive).lower()}}}
                    catch{{$failed=$true;Write-Output $_.Exception.Message}}
                    if($script:writes -ne {writes}){{throw 'Unexpected registry writes'}}
                    if({writes} -eq 0 -and -not $failed){{throw 'Conflict not rejected'}}
                    if({writes} -eq 0 -and (Test-Path {q(root/'chrome-host')})){{throw 'Partial Chrome host write before conflict resolution'}}
                    if('{conflict}' -eq 'machine' -and $script:registry[$script:conflictKey] -ne 'old-environment.json'){{throw 'Machine registration overwritten'}}
                    Write-Output 'registration safety verified'
                    """)
                    self.assertEqual(code, 0, out+err)
                    self.assertIn('registration safety verified', out)
                    if conflict and not writes:
                        self.assertIn('Firefox', out)
                        if not race and conflict!='late':
                            self.assertIn('未設定' if conflict=='empty' else 'old-environment.json', out)
                            self.assertIn('新しく登録する先:', out)

    def test_same_registration_keeps_live_host_bytes(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            self.fixture(root)
            host=root/'firefox-host'
            host.mkdir()
            config=host/'config.json'
            config.write_bytes(b'preserve live custom host config')
            code, out, err=ps(". './scripts/common.ps1';"+REGISTRY_MOCK+rf"""
            $script:approve=$false;$script:race=$false
            $key='HKCU:\Software\Mozilla\NativeMessagingHosts\io.github.irodori.voice_reader.firefox'
            $script:registry[$key]={q(host/'io.github.irodori.voice_reader.firefox.json')}
            & $register -InstallRoot {q(root)} -Browsers firefox
            if($script:writes -or $script:confirmed){{throw 'Idempotent registration changed'}}
            """)
            self.assertEqual(code,0,out+err)
            self.assertEqual(config.read_bytes(),b'preserve live custom host config')

    def test_discovery_is_independent_of_selected_browser(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); sessions=root/'sessions';sessions.mkdir()
            installed=root/'installed';installed.mkdir();self.fixture(installed)
            value=dict(installRoot=str(installed),apiPort=19876,backendPort=19877,browsers='chrome',version='fixture')
            (sessions/'chrome-session.json').write_text(json.dumps(value),encoding='utf8')
            (sessions/'both-session.json').write_text(json.dumps(value),encoding='utf8')
            code,out,err=ps(rf"""
            . './scripts/common.ps1'; . './scripts/browser-setup.ps1'
            function Get-ChildItem {{param($Path,$LiteralPath,$Filter,[switch]$File)
                if($Path -match '^HK'){{return}}
                Microsoft.PowerShell.Management\Get-ChildItem -LiteralPath $LiteralPath -Filter $Filter -File
            }}
            $items=@(Get-BrowserEnvironments {q(sessions)})
            if($items.Count -ne 1 -or $items[0].browsers -ne 'chrome'){{throw 'Existing environment lost or duplicated'}}
            Write-Output $items[0].installRoot
            """)
            self.assertEqual(code,0,out+err)
            self.assertIn(str(installed.resolve()),out)

    def test_discovery_returns_canonical_roots_for_equivalent_session_paths(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); sessions=root/'sessions';sessions.mkdir()
            installed=root/'installed';installed.mkdir();self.fixture(installed)
            # GitHub's Windows TEMP can use an 8.3 profile alias. A trailing
            # dot gives the same raw-vs-canonical mismatch without requiring
            # 8.3 name creation to be enabled on the developer's drive.
            value=dict(installRoot=str(installed)+r'\.',apiPort=19876,backendPort=19877,browsers='chrome',version='fixture')
            (sessions/'both-session.json').write_text(json.dumps(value),encoding='utf8')
            value['installRoot']=str(installed.resolve())
            (sessions/'chrome-session.json').write_text(json.dumps(value),encoding='utf8')
            code,out,err=ps(rf"""
            . './scripts/common.ps1'; . './scripts/browser-setup.ps1'
            function Get-ChildItem {{param($Path,$LiteralPath,$Filter,[switch]$File)
                if($Path -match '^HK'){{return}}
                Microsoft.PowerShell.Management\Get-ChildItem -LiteralPath $LiteralPath -Filter $Filter -File
            }}
            $expected=Assert-LocalDirectory {q(installed)}
            $items=@(Get-BrowserEnvironments {q(sessions)})
            if($items.Count -ne 1){{throw 'Equivalent session paths were not deduplicated'}}
            if($items[0].installRoot -ne $expected){{throw 'Discovery returned a raw path instead of its canonical form'}}
            $matches=@($items | Where-Object {{$_.installRoot -eq $expected}})
            if($matches.Count -ne 1){{throw 'Explicit installation root did not match the session'}}
            if($items[0].apiPort -ne 19876 -or $items[0].backendPort -ne 19877){{throw 'Ports changed'}}
            Write-Output 'canonical discovery verified'
            """)
            self.assertEqual(code,0,out+err)

    def test_gpu_messages_storage_and_low_space_are_actionable(self):
        code,out,err=ps(". './scripts/common.ps1';"+LOAD_SETUP_FUNCTIONS+r"""
        function Get-FreeSpace($Path){if($Path -eq 'C:\'){return 0.25GB};return 10GB}
        $summary=Get-StorageSummary 'D:\chosen\installed'
        if($summary -notmatch 'Cドライブ' -or $summary -notmatch 'Dドライブ' -or $summary -notmatch '0.25' -or $summary -notmatch '外部ツール'){throw 'Missing storage advice'}
        Write-Output $summary
        Write-Output (Get-GpuSetupMessage ([pscustomobject]@{arch='86'}))
        Write-Output (Get-GpuSetupMessage ([pscustomobject]@{arch=''}))
        function Get-FreeSpace($Path){return 1GB}
        $Unattended=$true
        try{Select-InstallBase 'D:\chosen' -KeepRoot;throw 'Low space accepted'}catch{
            if($_.Exception.Message -notmatch 'D:\\chosen' -or $_.Exception.Message -notmatch 'AI-support.bat'){throw}
        }
        Write-Output 'capacity verified'
        """)
        self.assertEqual(code,0,out+err)
        self.assertIn('NVIDIA GPUを検出しました。GPU対応版の音声エンジンを準備します。',out)
        self.assertIn('NVIDIA GPUを自動検出できませんでした。',out)
        self.assertIn('長い時間',out)
        self.assertIn('手入力',out)

    def test_support_includes_browser_destination_and_real_error(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            (root/'browser-state.json').write_text(json.dumps(dict(step='ブラウザー追加',error='Native Host登録の衝突: Firefox\n既存の登録先: old\n新しく登録する先: new',completed=False)),encoding='utf8')
            (root/'setup-context.json').write_text(json.dumps(dict(installRoot=folder,drive='D:\\',browsers='firefox',apiPort=19876,backendPort=19877,storage='Cドライブの空き容量: 0.25 GB')),encoding='utf8')
            code,out,err=ps(f"& './scripts/support.ps1' -StateRoot {q(root)} -NoOpen")
            self.assertEqual(code,0,out+err)
            prompt=(root/'AI-support.txt').read_text(encoding='utf8')
            for text in ('停止した工程: ブラウザー追加','Native Host登録の衝突: Firefox','対象ブラウザー: firefox','保存先ドライブ: Dドライブ','19876','0.25 GB','手入力'):
                self.assertIn(text,prompt)
            self.assertNotIn(folder,prompt)

    def test_completed_environment_browser_changes_never_prepare_engine(self):
        # Run the real setup entrypoint and real host file generation, with a
        # fake registry, an inert XPI, and hard failures on all engine commands.
        cases=[('chrome','firefox',''),('chrome','both',''),('firefox','chrome',''),('firefox','both',''),('both','both','')]
        cases += [('chrome','both',fault) for fault in ('missing-runtime','missing-engine','changed-port','changed-identity')]
        for previous, requested, fault in cases:
            with self.subTest(previous=previous,requested=requested,fault=fault), tempfile.TemporaryDirectory() as folder:
                base=Path(folder).resolve(); source=base/'source'; scripts=source/'scripts';scripts.mkdir(parents=True)
                target=base/'installed';target.mkdir();self.fixture(target)
                session_root=base/'sessions';session_root.mkdir()
                for name in ('setup.ps1','browser-setup.ps1','register-hosts.ps1','downloads.ps1','stable-addon.ps1'):
                    shutil.copyfile(ROOT/'scripts'/name,scripts/name)
                # This test covers browser reuse/metadata faults, not the
                # runner's available disk space (tested separately above).
                setup=(scripts/'setup.ps1').read_text(encoding='utf-8-sig')
                setup,count=re.subn(r'(?ms)^function Get-FreeSpace\(\[string\]\$Path\)\{.*?^\}',
                                    'function Get-FreeSpace([string]$Path){return 10GB}',setup)
                self.assertEqual(count,1,'Disk-space fixture did not find the production probe')
                (scripts/'setup.ps1').write_text(setup,encoding='utf-8-sig')
                common=(ROOT/'scripts/common.ps1').read_text(encoding='utf-8-sig')
                mocks=REGISTRY_MOCK.split('$registration=')[0]
                common+='\n'+mocks+r"""
function Get-Gpu { [pscustomobject]@{name='fixture GPU';arch='86';driver='fixture'} }
function Get-Python { throw 'Browser addition must not prepare Python' }
function Assert-FreePorts { throw 'Browser addition must not restart API/engine' }
function Run($Exe,$Arguments){
    if(($Arguments -join ' ') -notmatch 'verify-firefox-xpi.py'){throw 'Unexpected engine/build/download command'}
}
"""
                (scripts/'common.ps1').write_text(common,encoding='utf-8-sig')
                (scripts/'install-files.ps1').write_text("function Get-PackageFiles {}\nfunction Copy-MaintenanceFiles {}\nfunction Copy-Application { throw 'Browser addition must not copy application' }",encoding='utf-8-sig')
                manifest=json.loads((ROOT/'distribution.json').read_text(encoding='utf8'))
                spec=next(a for a in manifest['assets'] if a.get('role')=='signed-install')
                xpi=target/spec['name'];xpi.write_bytes(b'inert browser-route fixture, not a signed XPI')
                spec['sha256']=hashlib.sha256(xpi.read_bytes()).hexdigest();spec['bytes']=xpi.stat().st_size
                (source/'distribution.json').write_text(json.dumps(manifest),encoding='utf8')
                (target/'.ivr-install.json').write_text(json.dumps(dict(state='runtime-ready',version=manifest['version'],voiceStable=True,voiceTest=True)),encoding='utf8')
                config=target/'irodori-api/config';config.mkdir()
                engine=target/'engine.exe';engine.write_bytes(b'existing engine, must not execute')
                (config/'runtime.json').write_text(json.dumps(dict(audio_cpp_shared_executable=str(engine))),encoding='utf8')
                for name in ('api.json','voices.json','config.json','config.voice_stable.json','capabilities.json'):
                    (config/name).write_text('preserve '+name,encoding='utf8')
                (config/'api.json').write_text('{"host":"127.0.0.1","port":19876}',encoding='utf8')
                model=target/'model.gguf';model.write_bytes(b'existing model')
                if fault=='missing-runtime':
                    (config/'runtime.json').unlink()
                elif fault=='missing-engine':
                    engine.unlink()
                elif fault=='changed-port':
                    (config/'api.json').write_text('{"host":"127.0.0.1","port":19878}',encoding='utf8')
                elif fault=='changed-identity':
                    identity=json.loads((target/'chrome-identity.json').read_text(encoding='utf8'))
                    identity['apiPort']=19879
                    (target/'chrome-identity.json').write_text(json.dumps(identity),encoding='utf8')
                protected=[p for p in [engine,model,target/'chrome-identity.json',xpi,*config.iterdir()] if p.exists()]
                before={p:p.read_bytes() for p in protected}
                session=dict(installRoot=str(target),apiPort=19876,backendPort=19877,browsers=previous,version=manifest['version'])
                (session_root/(previous+'-session.json')).write_text(json.dumps(session),encoding='utf8')
                code,out,err=ps(f"& {q(scripts/'setup.ps1')} -InstallRoot {q(target)} -Browsers {requested} -SessionDirectory {q(session_root)} -Unattended -NoOpenGuide")
                if fault:
                    self.assertNotEqual(code,0,out+err)
                    message={'missing-runtime':'既存の実行設定が見つかりません','missing-engine':'既存のエンジンが見つかりません','changed-port':'ポート (19878)','changed-identity':'識別情報と導入記録のポートが異なります'}[fault]
                    self.assertIn(message,out+err)
                    self.assertFalse((target/'chrome-host').exists())
                    self.assertFalse((target/'firefox-host').exists())
                else:
                    self.assertEqual(code,0,out+err)
                    self.assertIn('再取得・再ビルドは行いません',out)
                self.assertEqual(before,{p:p.read_bytes() for p in protected})
                self.assertEqual({p.name for p in base.iterdir() if p.is_dir()},{'source','installed','sessions'})
                for browser in (() if fault else ('chrome','firefox') if requested=='both' else (requested,)):
                    host_config=json.loads((target/(browser+'-host')/'config.json').read_text(encoding='utf8'))
                    self.assertEqual(host_config['health_url'],'http://127.0.0.1:19876/health')
                    self.assertEqual(host_config['backend_health_url'],'http://127.0.0.1:19877/health')
                    self.assertEqual(host_config['irodori_root'],str(target/'irodori-api'))

    def test_support_chooses_latest_early_or_installed_failure(self):
        for newest in ('launcher','installed'):
            with self.subTest(newest=newest), tempfile.TemporaryDirectory() as folder:
                local=Path(folder)
                launcher=local/'IrodoriVoiceReader/Installer';launcher.mkdir(parents=True)
                installed=local/'existing';state=installed/'installer-state';state.mkdir(parents=True)
                (launcher/'firefox-session.json').write_text(json.dumps(dict(installRoot=str(installed))),encoding='utf8')
                for name,location in (('launcher',launcher),('installed',state)):
                    record=location/'state.json'
                    record.write_text(json.dumps(dict(step=name,error='actual-'+name,completed=False)),encoding='utf8')
                    os.utime(record,(200 if name==newest else 100,)*2)
                code,out,err=ps(f"$env:LOCALAPPDATA={q(local)}; & './scripts/support.ps1' -NoOpen")
                self.assertEqual(code,0,out+err)
                selected=launcher if newest=='launcher' else state
                prompt=(selected/'AI-support.txt').read_text(encoding='utf8')
                self.assertIn('停止した工程: '+newest,prompt)
                self.assertIn('actual-'+newest,prompt)

    def test_dialog_exits_on_parent_normal_error_kill_and_done(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);exe=root/'dialog.exe'
            code,out,err=ps(f"Add-Type -Path './scripts/setup-dialog.cs' -ReferencedAssemblies @('System.Windows.Forms','System.Drawing','System.Web.Extensions') -OutputAssembly {q(exe)} -OutputType WindowsApplication")
            self.assertEqual(code,0,out+err)
            for mode in ('normal','error','kill','done','cancel','stalled-cancel'):
                with self.subTest(mode=mode):
                    code,out,err=ps(rf"""
                    $root={q(root)};$exe={q(exe)};$mode='{mode}'
                    $parentScript=Join-Path $root 'parent.ps1'
                    $text=@'
                    param($Root,$Exe)
                    $request=Join-Path $Root 'request.json'
                    @{{kind='progress';message='';parentPid=[string]$PID;parentStartTicks=[string]([Diagnostics.Process]::GetCurrentProcess().StartTime.ToUniversalTime().Ticks)}} | ConvertTo-Json | Set-Content $request -Encoding UTF8
                    $gui=Start-Process $Exe -ArgumentList ('"'+$request+'"') -PassThru
                    [IO.File]::WriteAllText((Join-Path $Root 'gui.pid'),[string]$gui.Id)
                    $deadline=[DateTime]::UtcNow.AddMinutes(2)
                    $finish=Join-Path $Root 'finish'
                    while(-not(Test-Path $finish) -and [DateTime]::UtcNow -lt $deadline){{Start-Sleep -Milliseconds 50}}
                    if(Test-Path $finish){{exit ([int](Get-Content $finish))}}
'@
                    [IO.File]::WriteAllText($parentScript,$text,(New-Object Text.UTF8Encoding($true)))
                    Remove-Item (Join-Path $root 'gui.pid'),(Join-Path $root 'request.json.done'),(Join-Path $root 'request.json.cancel'),(Join-Path $root 'finish') -ErrorAction SilentlyContinue
                    $parent=Start-Process powershell.exe -ArgumentList ('-NoProfile -File "'+$parentScript+'" "'+$root+'" "'+$exe+'"') -PassThru -WindowStyle Hidden
                    $gui=$null
                    try{{
                        $deadline=[DateTime]::UtcNow.AddSeconds(10)
                        while(-not(Test-Path (Join-Path $root 'gui.pid'))){{if([DateTime]::UtcNow -gt $deadline){{throw 'GUI not launched'}};Start-Sleep -Milliseconds 50}}
                        $gui=[Diagnostics.Process]::GetProcessById([int](Get-Content (Join-Path $root 'gui.pid')))
                        [void]$gui.Handle
                        while($gui.MainWindowHandle -eq [IntPtr]::Zero){{$gui.Refresh();if($gui.HasExited -or [DateTime]::UtcNow -gt $deadline){{throw 'GUI not shown'}};Start-Sleep -Milliseconds 50}}
                        if($mode -in @('done','cancel','stalled-cancel')){{
                            if($mode -in @('cancel','stalled-cancel')){{
                                Add-Type 'public static class CloseFixture {{ [System.Runtime.InteropServices.DllImport("user32.dll")] public static extern bool PostMessage(System.IntPtr h, uint m, System.IntPtr w, System.IntPtr l); }}'
                                [void][CloseFixture]::PostMessage($gui.MainWindowHandle,0x0010,[IntPtr]::Zero,[IntPtr]::Zero)
                                Start-Sleep -Milliseconds 300
                                if(-not(Test-Path (Join-Path $root 'request.json.cancel'))){{throw 'Cancel not requested'}}
                            }}
                            if($mode -ne 'stalled-cancel'){{[IO.File]::WriteAllText((Join-Path $root 'request.json.done'),'done')}}
                        }}else{{
                            # Fixture owner only; never stop the real installer.
                            if($mode -eq 'kill'){{$parent.Kill()}}else{{
                                [IO.File]::WriteAllText((Join-Path $root 'finish'),$(if($mode -eq 'error'){{'7'}}else{{'0'}}))
                                if(-not $parent.WaitForExit(5000)){{throw 'Fixture parent did not exit'}}
                            }}
                        }}
                        $wait=if($mode -eq 'stalled-cancel'){{35000}}else{{5000}}
                        if(-not $gui.WaitForExit($wait)){{throw 'Orphan GUI survived'}}
                        if($mode -in @('done','cancel','stalled-cancel') -and $parent.HasExited){{throw 'GUI killed its parent'}}
                        Write-Output 'GUI lifecycle verified'
                    }}finally{{if($gui -and -not $gui.HasExited){{$gui.Kill()}};if(-not $parent.HasExited){{$parent.Kill()}}}}
                    """)
                    self.assertEqual(code,0,out+err)

    def test_environment_dialog_selects_existing_or_independent_environment(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);exe=root/'dialog.exe'
            code,out,err=ps(rf"""
            . './scripts/common.ps1'
            Add-Type -Path './scripts/setup-dialog.cs' -ReferencedAssemblies @('System.Windows.Forms','System.Drawing','System.Web.Extensions') -OutputAssembly {q(exe)} -OutputType WindowsApplication
            Add-Type @'
            using System;using System.Text;using System.Runtime.InteropServices;
            public static class EnvironmentFixtureUI {{
                delegate bool Callback(IntPtr h,IntPtr p);
                [DllImport("user32.dll")] static extern bool EnumChildWindows(IntPtr h,Callback cb,IntPtr p);
                [DllImport("user32.dll",CharSet=CharSet.Unicode)] static extern int GetClassName(IntPtr h,StringBuilder s,int n);
                [DllImport("user32.dll",CharSet=CharSet.Unicode)] static extern int GetWindowText(IntPtr h,StringBuilder s,int n);
                [DllImport("user32.dll")] public static extern IntPtr SendMessage(IntPtr h,uint m,IntPtr w,IntPtr l);
                public static IntPtr Find(IntPtr parent,string classPart,string text) {{
                    IntPtr result=IntPtr.Zero;
                    EnumChildWindows(parent,(h,p)=>{{
                        var c=new StringBuilder(256);var t=new StringBuilder(512);
                        GetClassName(h,c,c.Capacity);GetWindowText(h,t,t.Capacity);
                        if(c.ToString().ToUpperInvariant().Contains(classPart) && (text=="" || t.ToString()==text)){{result=h;return false;}}
                        return true;
                    }},IntPtr.Zero);
                    return result;
                }}
            }}
'@
            $roots=@('D:\fixture-chrome','D:\fixture-firefox')
            foreach($index in @(0,1,2)){{
                $request=Join-Path {q(root)} ($index.ToString()+'.json')
                Save-Json $request @{{kind='environment';message='Choose an existing environment or keep an independent one';default=(ConvertTo-Json -InputObject $roots -Compress);parentPid=[string]$PID;parentStartTicks=[string]([Diagnostics.Process]::GetCurrentProcess().StartTime.ToUniversalTime().Ticks)}}
                $gui=Start-Process {q(exe)} -ArgumentList ('"'+$request+'"') -PassThru
                try{{
                    $deadline=[DateTime]::UtcNow.AddSeconds(8)
                    while($gui.MainWindowHandle -eq [IntPtr]::Zero){{$gui.Refresh();if([DateTime]::UtcNow -gt $deadline){{throw 'Dialog not shown'}};Start-Sleep -Milliseconds 50}}
                    [void]$gui.WaitForInputIdle(5000)
                    # WinForms accessibility providers can expose this as a
                    # custom control. Use only this test-owned window's HWNDs.
                    $list=[EnvironmentFixtureUI]::Find($gui.MainWindowHandle,'LISTBOX','')
                    $button=[EnvironmentFixtureUI]::Find($gui.MainWindowHandle,'BUTTON','この接続先を使う')
                    if($list -eq [IntPtr]::Zero -or $button -eq [IntPtr]::Zero){{throw 'Test dialog controls missing'}}
                    if($index -ne 0){{[void][EnvironmentFixtureUI]::SendMessage($list,0x186,[IntPtr]$index,[IntPtr]::Zero)}}
                    [void][EnvironmentFixtureUI]::SendMessage($button,0xf5,[IntPtr]::Zero,[IntPtr]::Zero)
                    if(-not $gui.WaitForExit(5000)){{throw 'Choice not accepted'}}
                    $result=Get-Content ($request+'.result') -Raw -Encoding UTF8 | ConvertFrom-Json
                    $expected=if($index -eq 2){{'new'}}else{{$roots[$index]}}
                    if($result.value -ne $expected){{throw ('Wrong environment selection: '+$result.value)}}
                }}finally{{if(-not $gui.HasExited){{$gui.Kill()}}}}
            }}
            Write-Output 'existing and independent choices verified'
            """)
            self.assertEqual(code,0,out+err)


class PackageNameTests(unittest.TestCase):
    def test_readme_starts_with_a_direct_published_bundle_link(self):
        intro=(ROOT/'README.md').read_text(encoding='utf8').split('## 一般ユーザー向けの導入手順',1)[0]
        url='https://github.com/rucks1989-blip/irodori-voice-reader/releases/download/v0.1.0-private.2/default.zip'
        self.assertIn(url,intro.splitlines()[2])
        self.assertIn('これをダウンロードする.zip',intro.splitlines()[2])
        self.assertIn('default.zip',intro)
        self.assertIn('setup.bat',intro)

    def test_only_bundle_name_and_utf8_checksums_change(self):
        package=(ROOT/'scripts/package-release.py').read_text(encoding='utf8')
        self.assertIn('destination = dist / "これをダウンロードする.zip"',package)
        self.assertIn('encoding="utf8"',package)
        manifest=json.loads((ROOT/'distribution.json').read_text(encoding='utf8'))
        names={asset['name'] for asset in manifest['assets']}
        self.assertTrue({'audio-cpp-stable-cpu-x64.zip','audio-cpp-stable-cuda-sm86-x64.zip','IrodoriVoiceReader-Firefox-1.2.27-signed.xpi'} <= names)
        for name in ('README.md','RELEASE_NOTES.md','scripts/package-release.py'):
            self.assertNotIn('IrodoriVoiceReader-Windows-test.zip',(ROOT/name).read_text(encoding='utf8'))
