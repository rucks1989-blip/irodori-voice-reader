[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$InstallRoot,[switch]$Unattended,[switch]$NoOpenGuide,[string]$StableBackendDirectory='')
$ErrorActionPreference='Stop'
$sessionRoot=Join-Path $InstallRoot 'installer-session'
$installation=Get-Content (Join-Path $sessionRoot 'installation.json') -Raw -Encoding UTF8 | ConvertFrom-Json
if([IO.Path]::GetFullPath($installation.installRoot).TrimEnd('\') -ne [IO.Path]::GetFullPath($InstallRoot).TrimEnd('\')){throw '保存された導入先と異なります。インストール先フォルダー内のBATを実行してください。'}
. (Join-Path $PSScriptRoot 'common.ps1')
. (Join-Path $PSScriptRoot 'stable-addon.ps1')
$state=Join-Path $InstallRoot 'installer-state'
New-Item -ItemType Directory -Force $state | Out-Null
$env:IVR_SETUP_LOG_ROOT=$state
$guard=$null
try {
    try{$guard=[IO.File]::Open((Join-Path $state 'stable-add.lock'),'OpenOrCreate','ReadWrite','None')}catch{throw '声質安定版の追加が別の画面で実行中です。'}
    $api=Join-Path $InstallRoot 'irodori-api'
    $python=Join-Path $api '.venv\Scripts\python.exe'
    if(-not(Test-Path $python)){throw '通常版の専用Python環境がありません。先に通常版の導入を完了してください。'}
    $identity=Get-Content (Join-Path $InstallRoot 'chrome-identity.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    # The repair never stops/restarts or rewrites a live instance.
    Assert-FreePorts @([int]$identity.apiPort,[int]$identity.backendPort)
    $worker=Join-Path $PSScriptRoot 'stable-addon.py'
    Run $python @($worker,'recover','--root',$InstallRoot)
    $marker=Get-Content (Join-Path $InstallRoot '.ivr-install.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    if($marker.state -ne 'runtime-ready'){throw '通常版の準備が未完了です。'}
    $server=Get-Content (Join-Path $api 'config\audio_cpp_server.shared.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    $normal=Get-Content (Join-Path $api 'config\config.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    $runtime=Get-Content (Join-Path $api 'config\runtime.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    $model=@($server.models | Where-Object {$_.id -eq $normal.irodori_audiocpp_model}) | Select-Object -First 1
    if(-not $model){throw '通常版のモデル定義が見つかりません。'}
    $modelPath=[string]$model.path
    if(-not[IO.Path]::IsPathRooted($modelPath)){$modelPath=Join-Path $api $modelPath}
    if(-not(Test-Path -LiteralPath $modelPath)){throw '既存モデルが見つかりません。'}
    $exe=[string]$runtime.audio_cpp_shared_executable
    if(-not[IO.Path]::IsPathRooted($exe)){$exe=Join-Path $api $exe}
    if(-not $Unattended){Start-SetupCancellation}
    $gpu=Get-Gpu
    $candidate=Prepare-StableBackend -SourceRoot (Split-Path $PSScriptRoot) -StateRoot $state -Python $python -Backend $server.backend -Arch $gpu.arch -Directory $StableBackendDirectory -ExistingDirectories @((Split-Path $exe)) -CacheRoot (Join-Path (Split-Path $InstallRoot) '.ivr-build-cache') -Model $modelPath -Port ([int]$identity.backendPort) -Unattended:$Unattended
    Run $python @($worker,'check','--root',$InstallRoot,'--candidate',$candidate)
    Assert-FreePorts @([int]$identity.apiPort,[int]$identity.backendPort)
    # Applying/recovery must finish even if the progress UI is closed.
    Stop-SetupCancellation
    Run $python @($worker,'apply','--root',$InstallRoot,'--candidate',$candidate)
    Write-Host '声質安定版を追加しました。通常版／声質安定版を切り替えられます。モデル・Voice・API設定・ブラウザー登録は保持しました。' -ForegroundColor Green
}catch {
    Write-Host $_.Exception.Message -ForegroundColor Red
    Write-Host '追加を完了できませんでした。対象エンジンを停止してから再実行できます。モデル・Voice・API設定は変更していません。'
    exit 1
}finally{if($guard){$guard.Dispose()};Stop-SetupCancellation}
