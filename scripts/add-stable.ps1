[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$InstallRoot,[switch]$Unattended,[switch]$NoOpenGuide,[string]$StableBackendDirectory='')
$ErrorActionPreference='Stop'
$sessionRoot=Join-Path $InstallRoot 'installer-session'
$installation=Get-Content (Join-Path $sessionRoot 'installation.json') -Raw -Encoding UTF8 | ConvertFrom-Json
if([IO.Path]::GetFullPath($installation.installRoot).TrimEnd('\') -ne [IO.Path]::GetFullPath($InstallRoot).TrimEnd('\')){throw '保存された導入先と異なります。インストール先フォルダー内のBATを実行してください。'}
# Reuse the existing repair path, GPU detection, candidate test and fallback.
# Never select a new environment or initialize Voice/browser settings here.
$global:LASTEXITCODE=0
# Delegate completed-runtime / optional-build selection and GPU detection to
# setup's existing UI instead of duplicating its matching rules here.
$stableMode=if($StableBackendDirectory -or $Unattended){'prebuilt'}else{'prompt'}
& (Join-Path $PSScriptRoot 'setup.ps1') -Repair -Browsers $installation.browsers -SessionDirectory $sessionRoot -StableMode $stableMode -StableBackendDirectory $StableBackendDirectory -Unattended:$Unattended -NoOpenGuide:$NoOpenGuide
if($LASTEXITCODE -ne 0){exit $LASTEXITCODE}
$capabilities=Get-Content (Join-Path $InstallRoot 'irodori-api\config\capabilities.json') -Raw -Encoding UTF8 | ConvertFrom-Json
if(-not $capabilities.voice_stable){throw '声質安定版は追加されていません。上の案内を確認してください。通常版は引き続き利用できます。'}
Write-Host '声質安定版を追加しました。拡張機能のエンジン選択で通常版／声質安定版を切り替えられます。' -ForegroundColor Green
