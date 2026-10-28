# Download a parser fixture only. Never install its service or use it for production.
$ErrorActionPreference = 'Stop'
$root = Join-Path $PSScriptRoot '..\.ci-bin'
$null = New-Item -ItemType Directory -Path $root -Force
$zip = Join-Path $root 'openssh-test.zip'
Invoke-WebRequest 'https://github.com/PowerShell/Win32-OpenSSH/releases/download/10.4.0.0p1-Preview/OpenSSH-Win64.zip' -OutFile $zip
if ((Get-FileHash $zip -Algorithm SHA256).Hash -ine 'b67ff8becd203502190ec42ac7787c24c2cd3325ed7700485fe7a1c83d2a7957') { throw 'Official Windows SSH parser fixture hash mismatch' }
Expand-Archive -LiteralPath $zip -DestinationPath $root
$sshd = (Resolve-Path (Join-Path $root 'OpenSSH-Win64\sshd.exe')).Path
if ($env:GITHUB_ENV) { Add-Content -LiteralPath $env:GITHUB_ENV -Value "WG_SSHD_TEST_BINARY=$sshd" }
Write-Output 'WINDOWS_PARSER_FIXTURE_READY; no system service installed'
