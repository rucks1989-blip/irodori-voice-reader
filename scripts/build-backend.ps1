[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$Python,
    [Parameter(Mandatory=$true)][string]$WorkRoot,
    [ValidateSet('cpu','cuda')][string]$Backend='cpu',
    [string]$CudaArch='',
    [switch]$StandardOnly,
    [switch]$AllowToolInstall
)
. (Join-Path $PSScriptRoot 'common.ps1')
$Root = Split-Path -Parent $PSScriptRoot
$manifest = Get-Content (Join-Path $Root 'distribution.json') -Raw -Encoding UTF8 | ConvertFrom-Json
function Find-Vs {
    $where = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
    if (Test-Path $where) {
        $found = & $where -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 Microsoft.VisualStudio.Component.VC.CMake.Project -property installationPath
        if ($found) { return [string]($found | Select-Object -First 1) }
    }
    return ''
}
function Find-Cuda {
    if (-not $CudaArch -or $CudaArch -notmatch '^\d{2,3}$') { throw 'NVIDIA GPUの世代を確認できません。CPU方式を選択してください。' }
    $roots = @($env:CUDA_PATH)
    $roots += @(Get-ChildItem 'C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA' -Directory -ErrorAction SilentlyContinue | Sort-Object Name -Descending | ForEach-Object { $_.FullName })
    foreach ($r in $roots) {
        if (-not $r) { continue }
        $nvcc = Join-Path $r 'bin\nvcc.exe'
        if (Test-Path $nvcc) {
            $archs = @(& $nvcc --list-gpu-arch 2>$null)
            if ($archs -contains "compute_$CudaArch") { return $r }
        }
    }
    return ''
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
$patchFiles = @('audio-cpp-stable-patch\stable-rescue.patch','audio-cpp-stable-patch\speaker-embedding-dump.patch','audio-cpp-patch\apply_speaker_embedding_patch.py','audio-cpp-patch\speaker_embedding_loader.inc')
$patchInputs=@($patchFiles | ForEach-Object {Join-Path $Root $_})
$patchInputs+=@(Get-ChildItem (Join-Path $Root 'audio-cpp-stable-patch\added-files') -Recurse -File | Sort-Object FullName | ForEach-Object {$_.FullName})
$patchHash = ($patchInputs | ForEach-Object { (Get-FileHash $_ -Algorithm SHA256).Hash }) -join ''
$digest = [BitConverter]::ToString([Security.Cryptography.SHA256]::Create().ComputeHash([Text.Encoding]::UTF8.GetBytes($patchHash))).Replace('-','').Substring(0,16)
$cache = Join-Path $WorkRoot ('b\'+$manifest.audioCppCommit.Substring(0,7)+$(if($StandardOnly){'-standard'}else{''})+'-'+$digest.Substring(0,8))
$source = Join-Path $cache 'source'
$marker = Join-Path $cache 'patched.json'
if (-not (Test-Path $marker)) {
    # Never reset or clean an existing source directory. A failed partial source
    # stays available for diagnostics, and the next attempt uses a fresh directory.
    if (Test-Path $source) { $source = Join-Path $cache ('source-'+[guid]::NewGuid().ToString('N')) }
    New-Item -ItemType Directory -Force -Path $source | Out-Null
    Run $git.Source @('init',$source)
    Run $git.Source @('-C',$source,'remote','add','origin','https://github.com/0xShug0/audio.cpp.git')
    Run $git.Source @('-C',$source,'fetch','--depth','1','origin',$manifest.audioCppCommit)
    Run $git.Source @('-C',$source,'checkout','--detach','FETCH_HEAD')
    $head = & $git.Source -C $source rev-parse HEAD
    if ($head.Trim() -ne $manifest.audioCppCommit) { throw 'audio.cppのコミット検査に失敗しました。' }
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
    Save-Json $marker @{ source=$source; commit=$head.Trim(); patchHash=$digest }
} else {
    $source = (Get-Content $marker -Raw -Encoding UTF8 | ConvertFrom-Json).source
    if(-not $StandardOnly){Run $Python @((Join-Path $Root 'audio-cpp-patch\apply_speaker_embedding_patch.py'),$source,'--check')}
}
$sharedPatch=Join-Path $Root 'audio-cpp-patch\apply_shared_session_patch.py'
if(-not $StandardOnly){Run $Python @($sharedPatch,$source);Run $Python @($sharedPatch,$source,'--check')}
$preset=if($Backend -eq 'cuda'){'windows-cuda-release'}else{'windows-cpu-release'}
$buildArguments=@('-NoProfile','-ExecutionPolicy','Bypass','-File',(Join-Path $source 'scripts\build_windows.ps1'),'-Preset',$preset,'-Target','audiocpp_server','-VsInstall',$vs,'-Jobs','2')
$buildArguments+=@('-CpuArch','baseline','-NativeCpu','OFF','-Llamafile','OFF')
if($Backend -eq 'cuda'){$buildArguments+=@('-CudaArchitectures',($CudaArch+'-real'))}
Run 'powershell.exe' $buildArguments
$bin=Join-Path $source "build\$preset\bin"
$exe=Join-Path $bin 'audiocpp_server.exe'
if(-not(Test-Path $exe)){throw 'ビルド結果の実行ファイルが見つかりません。'}
$output=Join-Path $WorkRoot ('build-output\'+$Backend+$(if($CudaArch){'-sm'+$CudaArch}else{''}))
New-Item -ItemType Directory -Force -Path $output | Out-Null
Get-ChildItem $bin -File | Where-Object {$_.Extension -in @('.exe','.dll')} | Copy-Item -Destination $output -Force
if($Backend -eq 'cuda'){
    foreach($pattern in @('cudart64_*.dll','cublas64_*.dll','cublasLt64_*.dll','cufft64_*.dll')){
        Get-ChildItem (Join-Path $cuda 'bin') -Filter $pattern -File | Copy-Item -Destination $output -Force
    }
}
Save-Json (Join-Path $output 'build.json') @{ standardOnly=[bool]$StandardOnly; stableSupported=(-not $StandardOnly); commit=$manifest.audioCppCommit; patchHash=$digest; sharedSessionPatchSha256=(Get-FileHash $sharedPatch -Algorithm SHA256).Hash; backend=$Backend; arch=$CudaArch; exeSha256=(Get-FileHash $exe -Algorithm SHA256).Hash }
Write-Output $output
