[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$Python,
    [Parameter(Mandatory=$true)][string]$WorkRoot,
    [ValidateSet('cpu','cuda')][string]$Backend='cpu',
    [string]$CudaArch='',
    [switch]$StandardOnly,
    [switch]$AllowToolInstall,
    [ValidateRange(0,64)][int]$Jobs=0,
    [string]$CacheRoot='',
    [string]$VsInstall='',
    [string]$SourceArchive='',
    [switch]$PortableCuda,
    [switch]$ConfigureOnly
)
. (Join-Path $PSScriptRoot 'common.ps1')
$Root = Split-Path -Parent $PSScriptRoot
$manifest = Get-Content (Join-Path $Root 'distribution.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$portableArchitectures=@('61','70','75','80','86','89','90','100','120')
if($PortableCuda -and $Backend -ne 'cuda'){throw '配布用の複数世代CUDAビルドは-Backend cudaで指定してください。CPU完成品と既存キャッシュは変更しません。'}
function Find-Vs {
    if($VsInstall){
        if(-not(Test-Path (Join-Path $VsInstall 'VC\Tools\MSVC'))){throw '指定したC++ビルド環境が見つかりません。'}
        return $VsInstall
    }
    $where = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
    if (Test-Path $where) {
        $found = & $where -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 Microsoft.VisualStudio.Component.VC.CMake.Project -property installationPath
        if ($found) { return [string]($found | Select-Object -First 1) }
    }
    return ''
}
function Find-Cuda {
    if (-not $PortableCuda -and (-not $CudaArch -or $CudaArch -notmatch '^\d{2,3}$')) { throw 'NVIDIA GPUの世代を確認できません。CPU方式を選択してください。' }
    $roots = @($env:CUDA_PATH,$env:CUDAToolkit_ROOT)
    $roots += @(Get-ChildItem 'C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA' -Directory -ErrorAction SilentlyContinue | Sort-Object Name -Descending | ForEach-Object { $_.FullName })
    foreach ($r in $roots) {
        if (-not $r) { continue }
        $nvcc = Join-Path $r 'bin\nvcc.exe'
        if (Test-Path $nvcc) {
            $archs = @(& $nvcc --list-gpu-arch 2>$null)
            if($PortableCuda){
                $missing=@($portableArchitectures | Where-Object {$archs -notcontains ('compute_'+$_)})
                if(-not $missing.Count){return $r}
                continue
            }
            if ($archs -contains "compute_$CudaArch") { return $r }
        }
    }
    return ''
}
$watch=[Diagnostics.Stopwatch]::StartNew()
$patchFiles=if($StandardOnly){@()}else{@('audio-cpp-stable-patch\stable-rescue.patch','audio-cpp-stable-patch\speaker-embedding-dump.patch','audio-cpp-patch\apply_speaker_embedding_patch.py','audio-cpp-patch\speaker_embedding_loader.inc')}
$legacyInputs=@($patchFiles | ForEach-Object {Join-Path $Root $_})
if(-not $StandardOnly){$legacyInputs+=@(Get-ChildItem (Join-Path $Root 'audio-cpp-stable-patch\added-files') -Recurse -File | Sort-Object FullName | ForEach-Object {$_.FullName})}
$legacyHash=($legacyInputs | ForEach-Object {(Get-FileHash $_ -Algorithm SHA256).Hash}) -join ''
function Hash-Text([string]$Text){return [BitConverter]::ToString([Security.Cryptography.SHA256]::Create().ComputeHash([Text.Encoding]::UTF8.GetBytes($Text))).Replace('-','')}
$legacyDigest=(Hash-Text $legacyHash).Substring(0,16)
$sharedPatch=Join-Path $Root 'audio-cpp-patch\apply_shared_session_patch.py'
$patchHash=$legacyHash+$(if(-not $StandardOnly){(Get-FileHash $sharedPatch).Hash}else{''})
$digest=(Hash-Text $patchHash).Substring(0,16)
if(-not $CacheRoot){$CacheRoot=Join-Path $WorkRoot 'b'}
$mode=if($StandardOnly){'standard'}else{'stable'}
$arch=if($PortableCuda){'portable'}elseif($Backend -eq 'cuda'){$CudaArch}else{''}
$recipe="schema=2;commit=$($manifest.audioCppCommit);patch=$digest;mode=$mode;backend=$Backend;arch=$arch;cpu=baseline;native=OFF;llamafile=OFF;target=audiocpp_server;config=Release"
if($PortableCuda){$recipe+=';sass='+($portableArchitectures -join ',')+';ptx=120'}
$legacyRuntime=Join-Path $CacheRoot ($manifest.audioCppCommit.Substring(0,7)+'-'+$mode+'-'+$digest.Substring(0,8)+'-'+$Backend+$(if($arch){'-sm'+$arch}else{''}))
# Short paths also save Windows CMake/Ninja object-file path budget. All recipe
# fields remain in complete.json rather than being repeated in directory names.
$cache=Join-Path $CacheRoot ((Hash-Text $recipe).Substring(0,12))
if(-not(Test-Path (Join-Path $cache 'complete.json')) -and (Test-Path (Join-Path $legacyRuntime 'complete.json'))){$cache=$legacyRuntime}
$runtime=Join-Path $cache 'runtime'
$completion=Join-Path $cache 'complete.json'
$output=Join-Path $WorkRoot ('build-output\'+$Backend+$(if($arch){'-sm'+$arch}else{''}))
function Source-Fingerprint([string]$Directory){
    $result=& $Python (Join-Path $PSScriptRoot 'build-fingerprint.py') $Directory
    if($LASTEXITCODE -ne 0){throw 'ソースキャッシュの検査に失敗しました。'}
    return [string]$result
}
function Get-BuildJobs([int]$Cores,[long]$FreeBytes,[string]$Kind,[int]$Requested=0){
    $perJob=if($Kind -eq 'cuda'){1536MB}else{768MB}
    $memoryJobs=[Math]::Max(1,[Math]::Floor(($FreeBytes-1GB)/$perJob))
    $count=[int][Math]::Max(1,[Math]::Min([Math]::Max(1,$Cores-1),$memoryJobs))
    if($Requested -gt 0){$count=[int][Math]::Min($Requested,$count)}
    return $count
}
if(-not $ConfigureOnly -and (Test-Path $completion)){
    $saved=Get-Content $completion -Raw -Encoding UTF8 | ConvertFrom-Json
    $valid=$saved.recipe -eq $recipe
    foreach($file in $saved.files){if(-not(Test-DownloadFile (Join-Path $runtime $file.name) $file.sha256 $file.bytes)){$valid=$false}}
    if(-not @($saved.files | Where-Object {$_.name -eq 'audiocpp_server.exe'}).Count){$valid=$false}
    foreach($tool in $saved.tools){if((Test-Path $tool.path) -and (Get-FileHash $tool.path).Hash -ne $tool.sha256){$valid=$false}}
    if((Test-Path $saved.source) -and (Source-Fingerprint $saved.source) -ne $saved.sourceFingerprint){$valid=$false}
    if($valid){
        Copy-BackendRuntime $runtime $output
        Copy-Item (Join-Path $runtime 'build.json') (Join-Path $output 'build.json') -Force
        Save-Json (Join-Path $WorkRoot 'last-build.json') @{cacheHit=$true;seconds=$watch.Elapsed.TotalSeconds;recipe=$recipe;jobs=0;compiled=$false}
        Write-Host ('検証済み完成品キャッシュを再利用。取得・展開・パッチ・CMake・コンパイルは不要です。秒数: '+$watch.Elapsed.TotalSeconds.ToString('F2'))
        Write-Output $output
        exit 0
    }
    Write-Host '入力または完成品が変化したため、既存ソースとNinja中間成果物を使って差分ビルドします。'
}
$vs = Find-Vs
if (-not $vs) {
    if (-not $AllowToolInstall -or -not (Confirm '音声生成プログラムの作成にMicrosoft Visual Studio 2022 Build Tools (C++、Windows SDK、CMake)が必要です。数GB以上の取得と管理者確認が必要になる場合があります。Microsoftの利用条件を確認し、winget経由でインストールしますか？')) { throw 'ビルド環境の導入が許可されませんでした。' }
    Install-Winget 'Microsoft.VisualStudio.2022.BuildTools' @('--override','--wait --passive --norestart --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended')
    $vs = Find-Vs
    if (-not $vs) { throw 'Build Toolsをまだ確認できません。再起動を求められた場合は再起動し、setup.batを再実行してください。' }
}
if ($Backend -eq 'cuda') {
    $cuda = Find-Cuda
    if (-not $cuda) {
        if (-not $AllowToolInstall -or -not (Confirm 'このGPUに対応するCUDA Toolkitがありません。NVIDIA CUDA Toolkit 12.8をwingetで追加しますか？ 数GBの取得と管理者確認が必要な場合があります。ドライバーは自動更新しません。')) { throw 'CUDA Toolkitの導入が許可されませんでした。CPU方式も選択できます。' }
        Install-Winget 'Nvidia.CUDA' @('--version','12.8')
        $cuda = Find-Cuda
        if (-not $cuda) { throw '対応CUDA Toolkitを確認できません。PC再起動後に再実行するか、CPU方式を選択してください。' }
    }
    $env:CUDA_PATH=$cuda; $env:CUDAToolkit_ROOT=$cuda
    $env:PATH=(Join-Path $cuda 'bin')+';'+$env:PATH
}
$git = Get-Command git.exe -ErrorAction SilentlyContinue
if (-not $git) {
    if (-not $AllowToolInstall -or -not (Confirm '固定されたaudio.cppソースを取得するため、Git for Windowsをwingetでインストールしますか？')) { throw 'Gitの導入が許可されませんでした。' }
    Install-Winget 'Git.Git'
    foreach ($bin in @('C:\Program Files\Git\cmd',(Join-Path $env:LOCALAPPDATA 'Programs\Git\cmd'))) {
        if (Test-Path (Join-Path $bin 'git.exe')) { $env:PATH=$bin+';'+$env:PATH; break }
    }
    $git = Get-Command git.exe -ErrorAction SilentlyContinue
    if (-not $git) { throw 'Gitを確認できません。setup.batを再実行してください。' }
}
$source = Join-Path $cache 's'
$marker = Join-Path $cache 'patched.json'
if (-not (Test-Path $marker)) {
    # Adopt our previous managed, matching patch cache in place: its absolute
    # source path matters to CMake/Ninja. Never reset/clean a user's source.
    $legacy=Join-Path $CacheRoot ($manifest.audioCppCommit.Substring(0,7)+$(if($StandardOnly){'-standard'}else{''})+'-'+$legacyDigest.Substring(0,8)+'\patched.json')
    $adopted=$false
    if(-not $PortableCuda -and (Test-Path $legacy)){
        $old=Get-Content $legacy -Raw -Encoding UTF8 | ConvertFrom-Json
        if($old.commit -eq $manifest.audioCppCommit -and $old.patchHash -eq $legacyDigest -and (Test-Path $old.source)){
            $head=& $git.Source -C $old.source rev-parse HEAD
            if($LASTEXITCODE -eq 0 -and $head.Trim() -eq $manifest.audioCppCommit){$source=$old.source;$adopted=$true;Write-Host '既存の一致するソース/CMake/Ninjaキャッシュを再利用します。'}
        }
    }
    if($adopted -and -not $StandardOnly){
        Run $Python @((Join-Path $Root 'audio-cpp-patch\apply_speaker_embedding_patch.py'),$source,'--check')
        # Later embedding/CMake fixes overlap the original patch hunks, so a
        # reverse-apply check would reject a valid layered cache. Verify its
        # marker, copied inputs and embedding implementation without rewriting
        # those sources; the full source fingerprint is retained on completion.
        foreach($file in Get-ChildItem (Join-Path $Root 'audio-cpp-stable-patch\added-files') -Recurse -File){
            $relative=$file.FullName.Substring((Join-Path $Root 'audio-cpp-stable-patch\added-files').Length).TrimStart('\')
            if(-not(Test-DownloadFile (Join-Path $source $relative) (Get-FileHash $file.FullName).Hash $file.Length)){throw '以前のソースキャッシュに変更があります。上書きせず停止します。別のCacheRootを指定してください。'}
        }
    }
    if(-not $adopted){
    # Never reset or clean an existing source directory. A failed partial source
    # stays available for diagnostics, and the next attempt uses a fresh directory.
    if (Test-Path $source) { $source = Join-Path $cache ('source-'+[guid]::NewGuid().ToString('N')) }
    $archive=Join-Path $CacheRoot ('downloads\'+$manifest.audioCppSource.name)
    [void](Get-RequiredDownload $manifest.audioCppSource $archive -Python $Python -Selected $SourceArchive)
    New-Item -ItemType Directory -Force -Path $cache | Out-Null
    Expand-Download $archive $manifest.audioCppSource $source $Python -StripRoot ('audio.cpp-'+$manifest.audioCppCommit)
    if(-not(Test-Path (Join-Path $source 'scripts\build_windows.ps1'))){throw '固定ソースZIPの構造が想定と異なります。'}
    if(-not $StandardOnly){
    foreach ($relative in @('audio-cpp-stable-patch\stable-rescue.patch','audio-cpp-stable-patch\speaker-embedding-dump.patch')) {
        $file = Join-Path $Root $relative
        Run $git.Source @('-C',$source,'apply','--check',$file)
        Run $git.Source @('-C',$source,'apply','--whitespace=nowarn',$file)
    }
    Copy-Item (Join-Path $Root 'audio-cpp-stable-patch\added-files\*') -Destination $source -Recurse -Force
    Run $Python @((Join-Path $Root 'audio-cpp-patch\apply_speaker_embedding_patch.py'),$source)
    Run $Python @((Join-Path $Root 'audio-cpp-patch\apply_speaker_embedding_patch.py'),$source,'--check')
    }
    }
    Save-Json $marker @{ source=$source; commit=$manifest.audioCppCommit; patchHash=$digest }
} else {
    $source = (Get-Content $marker -Raw -Encoding UTF8 | ConvertFrom-Json).source
    if(-not $StandardOnly){Run $Python @((Join-Path $Root 'audio-cpp-patch\apply_speaker_embedding_patch.py'),$source,'--check')}
}
if(-not $StandardOnly){Run $Python @($sharedPatch,$source);Run $Python @($sharedPatch,$source,'--check')}
$preset=if($Backend -eq 'cuda'){'windows-cuda-release'}else{'windows-cpu-release'}
$cpu=[Environment]::ProcessorCount
$free=[long](Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory*1KB
$effectiveJobs=Get-BuildJobs $cpu $free $Backend $Jobs
Write-Host "ビルド並列数: $effectiveJobs / 論理CPU: $cpu / 空きRAM: $([Math]::Round($free/1GB,2)) GB（OS用1GBとコンパイラ用メモリを確保）"
$buildArguments=@('-NoProfile','-ExecutionPolicy','Bypass','-File',(Join-Path $source 'scripts\build_windows.ps1'),'-Preset',$preset,'-Target','audiocpp_server','-VsInstall',$vs,'-Jobs',[string]$effectiveJobs)
$buildArguments+=@('-CpuArch','baseline','-NativeCpu','OFF','-Llamafile','OFF')
if($PortableCuda){
    $portableTargets=@($portableArchitectures | ForEach-Object {$_+'-real'})+@('120-virtual')
    Write-Host ('配布用共通CUDAビルド（単一GPU専用ではありません）: '+($portableTargets -join ';'))
    $buildArguments+=@('-CudaArchitectures',($portableTargets -join ';'))
}elseif($Backend -eq 'cuda'){$buildArguments+=@('-CudaArchitectures',($CudaArch+'-real'))}
if($ConfigureOnly){$buildArguments+='-ConfigureOnly'}
Run 'powershell.exe' $buildArguments
if($ConfigureOnly){Write-Host ('固定ソースの取得・パッチ・CMake設定を確認しました。コンパイルは要求されていません。秒数: '+$watch.Elapsed.TotalSeconds.ToString('F2'));exit 0}
$bin=Join-Path $source "build\$preset\bin"
$exe=Join-Path $bin 'audiocpp_server.exe'
if(-not(Test-Path $exe)){throw 'ビルド結果の実行ファイルが見つかりません。'}
New-Item -ItemType Directory -Force -Path $runtime | Out-Null
Get-ChildItem $bin -File | Where-Object {$_.Extension -in @('.exe','.dll')} | Copy-Item -Destination $runtime -Force
if($Backend -eq 'cuda'){
    foreach($pattern in @('cudart64_*.dll','cublas64_*.dll','cublasLt64_*.dll','cufft64_*.dll')){
        Get-ChildItem (Join-Path $cuda 'bin') -Filter $pattern -File | Copy-Item -Destination $runtime -Force
    }
}
Save-Json (Join-Path $runtime 'build.json') @{ standardOnly=[bool]$StandardOnly; stableSupported=(-not $StandardOnly); commit=$manifest.audioCppCommit; patchHash=$digest; sharedSessionPatchSha256=$(if(-not $StandardOnly){(Get-FileHash $sharedPatch).Hash}else{''}); backend=$Backend; arch=$arch; portableCuda=[bool]$PortableCuda;supportedArchitectures=$(if($PortableCuda){$portableArchitectures}elseif($arch){@($arch)}else{@()});exeSha256=(Get-FileHash $exe -Algorithm SHA256).Hash }
$toolPaths=@((Get-ChildItem (Join-Path $vs 'VC\Tools\MSVC\*\bin\Hostx64\x64\cl.exe') | Sort-Object FullName -Descending | Select-Object -First 1).FullName)
if($Backend -eq 'cuda'){$toolPaths+=Join-Path $cuda 'bin\nvcc.exe'}
$tools=@($toolPaths | ForEach-Object {@{path=$_;sha256=(Get-FileHash $_).Hash}})
$files=@(Get-ChildItem $runtime -File | Where-Object {$_.Extension -in @('.exe','.dll') -or $_.Name -eq 'build.json'} | ForEach-Object {@{name=$_.Name;sha256=(Get-FileHash $_.FullName).Hash;bytes=$_.Length}})
Save-Json $completion @{recipe=$recipe;source=$source;sourceFingerprint=(Source-Fingerprint $source);files=$files;tools=$tools}
Copy-BackendRuntime $runtime $output
Copy-Item (Join-Path $runtime 'build.json') (Join-Path $output 'build.json') -Force
Save-Json (Join-Path $WorkRoot 'last-build.json') @{cacheHit=$false;seconds=$watch.Elapsed.TotalSeconds;recipe=$recipe;jobs=$effectiveJobs;compiled=$true}
Write-Host ('ビルドと配置の合計秒数: '+$watch.Elapsed.TotalSeconds.ToString('F2'))
Write-Output $output
