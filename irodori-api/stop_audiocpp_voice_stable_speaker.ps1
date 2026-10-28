$ErrorActionPreference = "Stop"

# 旧 8093 専用サーバーは廃止され、irodori-UI と共有の 8091 サーバーに統合されました。
# 停止処理は stop_audio_cpp_shared.ps1 に集約しています。

$root = $PSScriptRoot
Write-Host "[audio.cpp] 声質安定版専用の 8093 サーバーは廃止されました。現在は irodori-UI と共有の 8091 サーバーを使用します。"
& (Join-Path $root "stop_audio_cpp_shared.ps1")