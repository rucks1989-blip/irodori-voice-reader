$ErrorActionPreference='Stop'
$root=Split-Path -Parent $PSScriptRoot
$issues=@()
Get-ChildItem $root -Recurse -Filter '*.ps1' -File | Where-Object {$_.FullName -notmatch '[\\/](build-cache|build-output|dist|\.git|\.venv)[\\/]'} | ForEach-Object {
    $errors=$null
    [void][Management.Automation.Language.Parser]::ParseFile($_.FullName,[ref]$null,[ref]$errors)
    foreach($error in $errors){$issues+=($_.Name+': '+$error.Message)}
    $bytes=[IO.File]::ReadAllBytes($_.FullName)
    if($bytes.Length -gt 0 -and [Text.Encoding]::UTF8.GetString($bytes) -match '[^\x00-\x7f]'){
        if($bytes.Length -lt 3 -or $bytes[0] -ne 239 -or $bytes[1] -ne 187 -or $bytes[2] -ne 191){$issues+=($_.Name+': Japanese PowerShell requires UTF-8 BOM for Windows PowerShell 5.1')}
    }
}
if($issues.Count){$issues | ForEach-Object {Write-Host $_};exit 1}
Write-Host 'PowerShell 5.1 parsing and encoding: passed'
