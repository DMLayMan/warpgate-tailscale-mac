#Requires -Version 5.1
# Isolated tests: never create system accounts, edit services or change firewall rules.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$source = Join-Path $PSScriptRoot '..\scripts\setup_windows.ps1'
$errors = $null; $tokens = $null
$null = [Management.Automation.Language.Parser]::ParseFile($source, [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw ($errors | Out-String) }
. $source
$script:count = 0
function Assert-True($Value, [string]$Message) { if (-not $Value) { throw $Message }; $script:count++ }
function Assert-Rejected([scriptblock]$Body) {
    $rejected = $false
    try { & $Body | Out-Null } catch { $rejected = $true }
    Assert-True $rejected 'Invalid input was accepted'
}
$dir = Join-Path ([IO.Path]::GetTempPath()) ('wg-unit-' + [guid]::NewGuid().ToString('N'))
$null = New-Item -ItemType Directory -Path $dir
try {
    Assert-True ((Assert-TailnetIPv4 '100.64.1.10') -eq '100.64.1.10') 'Valid tailnet address rejected'
    foreach ($bad in @('127.0.0.1','100.128.0.1','100.64.1.999','100.64.01.1',"100.64.1.10`n",'::1','100.64.1.1,127.0.0.1')) { Assert-Rejected { Assert-TailnetIPv4 $bad } }
    $blob = [byte[]](0,0,0,11,115,115,104,45,101,100,50,53,53,49,57,0,0,0,32) + [byte[]]::new(32)
    $key = 'ssh-ed25519 ' + [Convert]::ToBase64String($blob)
    Assert-True ((Get-PublicKey $key) -ceq $key) 'Valid synthetic Ed25519 key rejected'
    foreach ($bad in @('PRIVATE KEY','ssh-ed25519 AAAA',($key+' extra'),('from="x" '+$key))) { Assert-Rejected { Get-PublicKey $bad } }
    $p = Join-Path $dir 'input.json'
    $valid = [ordered]@{schemaVersion=1;serverUser='remoteoperator';vpsTailnetIp='100.64.1.10';serverTailnetIp=$null;gatewayPublicKey=$key}
    Write-Utf8 $p ($valid | ConvertTo-Json)
    $s = Read-Setup $p
    Assert-True ($s.serverUser -eq 'remoteoperator' -and -not $s.serverTailnetIp) 'Automatic local address input failed'
    foreach ($good in @('Administrator','Daily User','existing.admin')) {
        $valid.serverUser=$good; Write-Utf8 $p ($valid | ConvertTo-Json)
        Assert-True ((Read-Setup $p).serverUser -ceq $good.ToLowerInvariant()) 'Existing account rejected'
    }
    foreach ($bad in @('user*','user?',"safe`nMatch all",'domain\user',('x'*21),'trailing ')) {
        $valid.serverUser=$bad; Write-Utf8 $p ($valid | ConvertTo-Json)
        Assert-Rejected { Read-Setup $p }
    }
    $valid.serverUser='remoteoperator'
    $valid.schemaVersion=1.1; Write-Utf8 $p ($valid | ConvertTo-Json); Assert-Rejected { Read-Setup $p }
    $valid.schemaVersion=1; $valid['password']='synthetic-disallowed-field'; Write-Utf8 $p ($valid | ConvertTo-Json); Assert-Rejected { Read-Setup $p }
    $block=Get-UserBlock 'remoteoperator' 'C:\ProgramData\WarpgateSSH-test\gateway_authorized_keys' '100.64.1.10' @('.ssh/authorized_keys')
    $original="Port 22`r`nMatch Group administrators`r`n    AuthorizedKeysFile __PROGRAMDATA__/ssh/administrators_authorized_keys`r`n"
    $merged=Merge-UserBlock $original $block 'remoteoperator'
    Assert-True ($merged.IndexOf('Match User') -lt $merged.IndexOf('Match Group administrators')) 'User block must precede shared administrator key rules'
    Assert-True ($merged.Contains('AuthorizedKeysFile __PROGRAMDATA__/ssh/administrators_authorized_keys')) 'Existing administrator rule was removed'
    Assert-True ((Merge-UserBlock $merged $block 'remoteoperator') -ceq $merged) 'Repeated setup changed the SSH block'
    Assert-Rejected { Merge-UserBlock $merged ($block.Replace('PasswordAuthentication no','PasswordAuthentication yes')) 'remoteoperator' }
    Assert-Rejected { Merge-UserBlock ($merged+$merged) $block 'remoteoperator' }
    Assert-Rejected { Get-UserBlock 'remoteoperator' "bad`npath" '100.64.1.10' @('.ssh/authorized_keys') }
    foreach ($line in @('DisableForwarding yes','PasswordAuthentication no','PermitTTY yes','Match User "remoteoperator" Address 100.64.1.10')) { Assert-True ($block.Contains($line)) "Missing control: $line" }
    Assert-True (-not $block.Contains('ForceCommand')) 'Existing default shell must be preserved'
    $parsed=@(Get-KeyPaths @('authorizedkeysfile "C:/ProgramData/gateway_keys" .ssh/authorized_keys'))
    Assert-True ($parsed.Count -eq 2 -and $parsed[0] -eq 'C:/ProgramData/gateway_keys' -and $parsed[1] -eq '.ssh/authorized_keys') 'Existing key-list parsing failed'
    Assert-True (-not (Get-Command Install-WindowsBackend).ScriptBlock.ToString().Contains('New-LocalUser')) 'Installer must never create accounts'
    Assert-Rejected { Merge-UserBlock '# BEGIN warpgate windows remoteoperator v1' $block 'remoteoperator' }
    if ($env:OS -eq 'Windows_NT') {
        # The current user keeps read access, but only SYSTEM/Administrators can write.
        $f=Join-Path $dir 'acl.txt'; Write-Utf8 $f 'synthetic fixture'
        $sid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value
        Set-ManagedAcl $f $sid
        Assert-AdminOwned $f
        Assert-True ((Get-Content -LiteralPath $f -Raw) -eq 'synthetic fixture') 'Public key ACL made file unreadable'
        $sshd=if ($env:WG_SSHD_TEST_BINARY) { $env:WG_SSHD_TEST_BINARY } else { Join-Path $env:WINDIR 'System32\OpenSSH\sshd.exe' }
        if (Test-Path $sshd) {
            $hostPath=Join-Path $dir 'host'
            # Windows PowerShell 5.1 drops an empty native argv element; quoted-empty preserves -N.
            $emptyPassword = if ($PSVersionTable.PSVersion.Major -lt 7) { '""' } else { '' }
            $null=Invoke-Checked (Join-Path (Split-Path $sshd) 'ssh-keygen.exe') @('-q','-t','ed25519','-N',$emptyPassword,'-f',$hostPath)
            $native=Join-Path $dir 'sshd_config'
            $nativeUser=Get-LocalAccountName $env:USERNAME
            $baseConfig="Port 22`nHostKey " + $hostPath.Replace('\','/') + "`nPasswordAuthentication yes`n" + $original.Substring($original.IndexOf('Match Group'))
            Write-Utf8 $native $baseConfig
            $before=@(Invoke-Checked $sshd @('-T','-f',$native,'-C',"user=$nativeUser,host=localhost,addr=100.64.1.10"))
            $legacy=@(Get-KeyPaths $before)
            $nativeBlock=Get-UserBlock $nativeUser 'C:\ProgramData\WarpgateSSH-test\gateway_authorized_keys' '100.64.1.10' $legacy
            Write-Utf8 $native (Merge-UserBlock $baseConfig $nativeBlock $nativeUser)
            $null=Invoke-Checked $sshd @('-t','-f',$native)
            $out=@(Invoke-Checked $sshd @('-T','-f',$native,'-C',"user=$nativeUser,host=localhost,addr=100.64.1.10"))
            Assert-True ('passwordauthentication no' -in $out -and 'disableforwarding yes' -in $out) 'Windows sshd rejected effective controls'
            Assert-True ((Get-KeyPaths $out)[0] -eq 'C:/ProgramData/WarpgateSSH-test/gateway_authorized_keys') 'Administrator used shared key path instead of scoped gateway identity'
            Assert-True ((Get-KeyPaths $out | Select-Object -Skip 1) -join '|' -eq ($legacy -join '|')) 'Existing key paths were lost'
            $outside=@(Invoke-Checked $sshd @('-T','-f',$native,'-C',"user=$nativeUser,host=localhost,addr=100.64.1.99"))
            Assert-True ('passwordauthentication yes' -in $outside -and 'disableforwarding no' -in $outside) 'Other source access was modified'
            Assert-True ((Get-KeyPaths $outside) -join '|' -eq ($legacy -join '|')) 'Other source key paths were modified'
            $other=@(Invoke-Checked $sshd @('-T','-f',$native,'-C','user=unrelated,host=localhost,addr=100.64.1.10'))
            Assert-True (-not (($other -join ' ').Contains('gateway_authorized_keys'))) 'Gateway key leaked to another account'
        } else { Write-Output 'SKIP: Windows native sshd parser unavailable' }
    } else {
        Assert-Rejected { & $source -OnServer -Setup $p }
        Write-Output 'SKIP: Windows NTFS ACL and native sshd checks require Windows'
    }
    Write-Output "WINDOWS_UNIT_CHECKS_PASSED: $script:count assertions"
} finally { Remove-Item -LiteralPath $dir -Recurse -Force }
