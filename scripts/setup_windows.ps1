#Requires -Version 5.1
<# Configure ONLY a Windows SSH backend. No application installation or account tokens. #>
[CmdletBinding()]
param([switch]$OnServer, [string]$Setup, [switch]$CreateUser, [switch]$RestartSshd)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Assert-TailnetIPv4([string]$Value) {
    if ($Value -notmatch '^100\.(?:\d{1,3})\.(?:\d{1,3})\.(?:\d{1,3})$') { throw 'Expected a Tailscale IPv4 address' }
    $ip = [System.Net.IPAddress]::Parse($Value)
    $b = $ip.GetAddressBytes()
    if ($b.Length -ne 4 -or $b[1] -lt 64 -or $b[1] -gt 127 -or $ip.ToString() -cne $Value) { throw 'Expected a canonical Tailscale IPv4 address' }
    return $Value
}

function Get-PublicKey([string]$Value) {
    $parts = $Value.Trim() -split '\s+'
    if ($parts.Count -ne 2 -or $parts[0] -cne 'ssh-ed25519') { throw 'Supply an Ed25519 PUBLIC key without a comment' }
    $blob = [Convert]::FromBase64String($parts[1])
    $prefix = [byte[]](0,0,0,11,115,115,104,45,101,100,50,53,53,49,57,0,0,0,32)
    if ($blob.Length -ne 51) { throw 'Malformed Ed25519 public key' }
    for ($i = 0; $i -lt $prefix.Length; $i++) { if ($blob[$i] -ne $prefix[$i]) { throw 'Malformed Ed25519 public key' } }
    return 'ssh-ed25519 ' + [Convert]::ToBase64String($blob)
}

function Read-Setup([string]$Path) {
    $s = Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json
    $allowed = @('schemaVersion', 'serverUser', 'vpsTailnetIp', 'serverTailnetIp', 'gatewayPublicKey')
    if (@($s.PSObject.Properties.Name | Where-Object { $_ -notin $allowed }).Count) { throw 'Unknown setup field; use config/windows.example.json' }
    if (($s.schemaVersion -isnot [int] -and $s.schemaVersion -isnot [long]) -or $s.schemaVersion -ne 1) { throw 'Unsupported schemaVersion' }
    if ($s.serverUser -isnot [string] -or $s.serverUser -cnotmatch '^[a-z_][a-z0-9_-]{0,19}$') { throw 'Use a lowercase local Windows account name, max 20 characters' }
    $vps = Assert-TailnetIPv4 $s.vpsTailnetIp
    $server = if ($s.serverTailnetIp) { Assert-TailnetIPv4 $s.serverTailnetIp } else { $null }
    $key = Get-PublicKey $s.gatewayPublicKey
    return [pscustomobject]@{ schemaVersion=1; serverUser=$s.serverUser; vpsTailnetIp=$vps; serverTailnetIp=$server; gatewayPublicKey=$key }
}

function Assert-NoReparse([string]$Path) {
    $cursor = [IO.Path]::GetFullPath($Path)
    while ($cursor) {
        if (Test-Path -LiteralPath $cursor) {
            if ((Get-Item -LiteralPath $cursor -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw "Refusing reparse point: $cursor" }
        }
        $parent = [IO.Path]::GetDirectoryName($cursor)
        if ($parent -eq $cursor) { break }
        $cursor = $parent
    }
}

function Set-ManagedAcl([string]$Path, [string]$ReaderSid = '') {
    $directory = (Get-Item -LiteralPath $Path -Force).PSIsContainer
    $acl = if ($directory) { New-Object Security.AccessControl.DirectorySecurity } else { New-Object Security.AccessControl.FileSecurity }
    $acl.SetAccessRuleProtection($true, $false)
    $admin = New-Object Security.Principal.SecurityIdentifier('S-1-5-32-544')
    $acl.SetOwner($admin)
    $inherit = if ($directory) { [Security.AccessControl.InheritanceFlags]'ContainerInherit,ObjectInherit' } else { [Security.AccessControl.InheritanceFlags]::None }
    foreach ($sid in @('S-1-5-18','S-1-5-32-544')) {
        $identity = New-Object Security.Principal.SecurityIdentifier($sid)
        $acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new($identity, 'FullControl', $inherit, 'None', 'Allow'))
    }
    if ($ReaderSid) {
        $identity = New-Object Security.Principal.SecurityIdentifier($ReaderSid)
        $acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new($identity, 'ReadAndExecute', $inherit, 'None', 'Allow'))
    }
    Set-Acl -LiteralPath $Path -AclObject $acl
}

function Write-Utf8([string]$Path, [string]$Value) {
    [IO.File]::WriteAllText($Path, $Value, [Text.UTF8Encoding]::new($false))
}

function Assert-AdminOwned([string]$Path) {
    $acl = Get-Acl -LiteralPath $Path
    $trusted = @('S-1-5-18','S-1-5-32-544')
    if ($acl.GetOwner([Security.Principal.SecurityIdentifier]).Value -notin $trusted) { throw "Untrusted owner: $Path" }
    $write = [Security.AccessControl.FileSystemRights]'Write,Delete,ChangePermissions,TakeOwnership,DeleteSubdirectoriesAndFiles'
    foreach ($rule in $acl.GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier])) {
        if ($rule.AccessControlType -eq 'Allow' -and $rule.IdentityReference.Value -notin $trusted -and ($rule.FileSystemRights -band $write)) { throw "Untrusted write permissions: $Path" }
    }
}

function Get-UserBlock([string]$UserName, [string]$KeyPath, [string]$ShellPath) {
    foreach ($p in @($KeyPath,$ShellPath)) { if ($p -match '[\r\n"%]') { throw 'Unsafe managed path' } }
    return @"
# BEGIN warpgate windows $UserName v1
Match User $UserName
    AuthorizedKeysFile "$($KeyPath.Replace('\','/'))"
    AuthenticationMethods publickey
    PubkeyAuthentication yes
    PasswordAuthentication no
    KbdInteractiveAuthentication no
    DisableForwarding yes
    PermitTTY yes
    PermitUserRC no
    ForceCommand "$($ShellPath.Replace('\','/'))" -NoLogo -NoProfile
Match all
# END warpgate windows $UserName v1
"@
}

function Merge-UserBlock([string]$Original, [string]$Block, [string]$UserName) {
    $normalized = $Original.Replace("`r`n", "`n")
    $desired = $Block.Replace("`r`n", "`n").TrimEnd()
    if ($normalized.Contains("# BEGIN warpgate windows $UserName v1")) {
        $pattern = '(?ms)^# BEGIN warpgate windows ' + [regex]::Escape($UserName) + ' v1\n.*?^# END warpgate windows ' + [regex]::Escape($UserName) + ' v1$'
        $matches = [regex]::Matches($normalized, $pattern)
        if ($matches.Count -ne 1 -or $matches[0].Value -cne $desired) { throw 'Conflicting managed SSH block; review migration instead of overwriting' }
        return $Original
    }
    if ($normalized.Contains("# END warpgate windows $UserName v1")) { throw 'Incomplete managed SSH block' }
    return $Original.TrimEnd() + "`r`n`r`n" + $desired.Replace("`n", "`r`n") + "`r`n"
}

function Invoke-Checked([string]$Exe, [string[]]$Arguments) {
    $out = & $Exe @Arguments 2>&1
    if ($LASTEXITCODE -ne 0) { throw "Command failed: $Exe (exit $LASTEXITCODE): $out" }
    return $out
}

function Install-WindowsBackend {
    if (-not $OnServer -or $env:OS -ne 'Windows_NT' -or -not [Environment]::Is64BitProcess) { throw 'Run 64-bit elevated Windows PowerShell ON THE SERVER with -OnServer' }
    $principal = [Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Administrator elevation is required' }
    if (-not $Setup) { throw '-Setup must name a private JSON file outside the checkout' }
    Assert-NoReparse $Setup
    $s = Read-Setup $Setup
    $ts = Join-Path $env:ProgramFiles 'Tailscale\tailscale.exe'
    if (-not (Test-Path -LiteralPath $ts)) { throw 'Install official Tailscale on this Windows server and authorize it first; see docs/WINDOWS.md' }
    $status = (Invoke-Checked $ts @('status','--json')) -join "`n" | ConvertFrom-Json
    if ($status.BackendState -ne 'Running') { throw 'Tailscale must be logged in and running' }
    $addresses = @($status.TailscaleIPs | Where-Object { $_ -match '^100\.' })
    if ($addresses.Count -ne 1) { throw 'Expected exactly one local Tailscale IPv4 address' }
    $actualIp = Assert-TailnetIPv4 $addresses[0]
    if ($s.serverTailnetIp -and $s.serverTailnetIp -ne $actualIp) { throw 'setup serverTailnetIp does not match THIS server; refusing wrong-machine setup' }
    if ($actualIp -eq $s.vpsTailnetIp) { throw 'Server and gateway must be different nodes' }
    $s.serverTailnetIp = $actualIp
    $user = Get-LocalUser -Name $s.serverUser -ErrorAction SilentlyContinue
    if (-not $user -and -not $CreateUser) { throw 'Standard local account is missing; create it locally or explicitly pass -CreateUser' }
    $admins = Get-LocalGroup -SID 'S-1-5-32-544'
    if ($user -and @((Get-LocalGroupMember -Group $admins) | Where-Object { $_.SID -eq $user.SID }).Count) { throw 'Refusing an administrator account' }
    if ($user -and -not $user.Enabled) { throw 'Account is disabled; review locally' }
    $root = Join-Path $env:ProgramData ('WarpgateSSH-' + $s.serverUser)
    $statePath = Join-Path $root 'binding.json'
    $keyPath = Join-Path $root 'gateway_authorized_keys'
    $configPath = Join-Path $env:ProgramData 'ssh\sshd_config'
    $sshDir = Join-Path $env:WINDIR 'System32\OpenSSH'
    $sshd = Join-Path $sshDir 'sshd.exe'
    $shellPath = Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\powershell.exe'
    foreach ($p in @($root,$statePath,$keyPath,$configPath,$sshd,$shellPath)) { Assert-NoReparse $p }
    $binding = $s | ConvertTo-Json -Compress
    if (Test-Path -LiteralPath $root) {
        Assert-AdminOwned $root
        if (Test-Path -LiteralPath $statePath) { Assert-AdminOwned $statePath }
        if (-not (Test-Path -LiteralPath $statePath) -or (Get-Content -LiteralPath $statePath -Raw).Trim() -cne $binding) { throw 'Existing managed directory has a different/incomplete binding; inspect before retrying' }
    }
    $service = Get-Service sshd -ErrorAction SilentlyContinue
    if ($service -and $service.Status -eq 'Running' -and -not $RestartSshd) { throw 'Existing sshd is running; use -RestartSshd during a maintenance window (may interrupt SSH)' }
    if ($service) {
        $serviceConfig = Get-CimInstance Win32_Service -Filter "Name='sshd'"
        if ($serviceConfig.PathName.Trim('"') -ine $sshd) { throw 'Custom sshd service command; manual integration required' }
    }
    if (-not $user) {
        $password = Read-Host "Choose a NEW password for local standard account $($s.serverUser) (never stored in setup.json)" -AsSecureString
        try { $user = New-LocalUser -Name $s.serverUser -Password $password -Description 'Warpgate SSH backend' }
        finally { if ($password) { $password.Dispose() }; Remove-Variable password -ErrorAction SilentlyContinue }
        $users = Get-LocalGroup -SID 'S-1-5-32-545'
        Add-LocalGroupMember -Group $users -Member $user
    }
    $newCapability = -not (Test-Path -LiteralPath $sshd)
    if ($newCapability) {
        $oldDefaultRule = Get-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' -ErrorAction SilentlyContinue
        $capability = @(Get-WindowsCapability -Online -Name 'OpenSSH.Server*')
        if ($capability.Count -ne 1) { throw 'OpenSSH Server capability unavailable; check Windows Features on Demand policy' }
        try { $result = Add-WindowsCapability -Online -Name $capability[0].Name }
        finally {
            # Do not leave a newly introduced broad firewall rule enabled, even on partial failure.
            if (-not $oldDefaultRule) { Get-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' -ErrorAction SilentlyContinue | Disable-NetFirewallRule }
        }
        if ($result.RestartNeeded) { throw 'Windows feature installation needs a reboot; reboot locally and rerun' }
    }
    if (-not (Test-Path -LiteralPath $sshd)) { throw 'Windows inbox OpenSSH Server installation is incomplete' }
    $null = New-Item -ItemType Directory -Path $root -Force
    Set-ManagedAcl $root $user.SID.Value
    if (-not (Test-Path -LiteralPath $statePath)) { Write-Utf8 $statePath $binding; Set-ManagedAcl $statePath }
    $keyLine = 'from="' + $s.vpsTailnetIp + '",restrict,pty ' + $s.gatewayPublicKey + "`n"
    if ((Test-Path -LiteralPath $keyPath) -and (Get-Content -LiteralPath $keyPath -Raw) -cne $keyLine) { throw 'Existing gateway public key binding differs' }
    Write-Utf8 $keyPath $keyLine
    Set-ManagedAcl $keyPath $user.SID.Value
    $sshData = Split-Path $configPath
    if (-not (Test-Path -LiteralPath $sshData)) {
        $null = New-Item -ItemType Directory -Path $sshData
        Set-ManagedAcl $sshData
    }
    $null = Invoke-Checked (Join-Path $sshDir 'ssh-keygen.exe') @('-A')
    $hadConfig = Test-Path -LiteralPath $configPath
    $original = if ($hadConfig) { [IO.File]::ReadAllText($configPath) } else { [IO.File]::ReadAllText((Join-Path $sshDir 'sshd_config_default')) }
    $originalBytes = if ($hadConfig) { [IO.File]::ReadAllBytes($configPath) } else { $null }
    $block = Get-UserBlock $s.serverUser $keyPath $shellPath
    $desired = Merge-UserBlock $original $block $s.serverUser
    $candidate = Join-Path $root 'sshd_config.candidate'
    Assert-NoReparse $candidate
    Write-Utf8 $candidate $desired
    Set-ManagedAcl $candidate
    $null = Invoke-Checked $sshd @('-t','-f',$candidate)
    $effective = Invoke-Checked $sshd @('-T','-f',$candidate,'-C',"user=$($s.serverUser),host=localhost,addr=$($s.vpsTailnetIp)")
    $expected = @('port 22','authenticationmethods publickey','pubkeyauthentication yes','passwordauthentication no','kbdinteractiveauthentication no','disableforwarding yes','permittty yes','permituserrc no')
    foreach ($line in $expected) { if ($line -notin $effective) { throw "Existing SSH policy overrides required setting: $line" } }
    if (@($effective | Where-Object { $_ -like 'port *' }).Count -ne 1) { throw 'Multiple SSH ports require manual integration' }
    $effectiveKey = (@($effective | Where-Object { $_ -like 'authorizedkeysfile *' }) -join '').Substring(19).Trim('"')
    if ($effectiveKey -ine $keyPath.Replace('\','/')) { throw 'Existing SSH policy overrides gateway key path' }
    $effectiveForce = (@($effective | Where-Object { $_ -like 'forcecommand *' }) -join '')
    if ($effectiveForce -ine ('forcecommand "' + $shellPath.Replace('\','/') + '" -NoLogo -NoProfile')) { throw 'Existing SSH policy overrides PowerShell terminal' }
    $ruleName = 'WarpgateSSH-' + $s.serverUser
    $existingRule = Get-NetFirewallRule -Name $ruleName -ErrorAction SilentlyContinue
    if ($existingRule) {
        $addr = $existingRule | Get-NetFirewallAddressFilter
        $port = $existingRule | Get-NetFirewallPortFilter
        if ($existingRule.Enabled -ne 'True' -or $existingRule.Direction -ne 'Inbound' -or $existingRule.Action -ne 'Allow' -or
            ($addr.LocalAddress -join ',') -ne $actualIp -or ($addr.RemoteAddress -join ',') -ne $s.vpsTailnetIp -or
            ($port.LocalPort -join ',') -ne '22' -or $port.Protocol -notin @('TCP','6')) { throw 'Conflicting firewall rule; manual review required' }
    }
    $backup = Join-Path $root ('sshd_config.before-' + [guid]::NewGuid().ToString('N'))
    if ($hadConfig -and $desired -cne $original) { [IO.File]::WriteAllBytes($backup,$originalBytes); Set-ManagedAcl $backup }
    $changed = $false; $newRule = $false
    $wasRunning = $service -and $service.Status -eq 'Running'
    try {
        if (-not $hadConfig -or $desired -cne $original) {
            Write-Utf8 $configPath $desired
            if (-not $hadConfig) { Set-ManagedAcl $configPath }
            $changed = $true
        }
        if (-not $existingRule) {
            $null = New-NetFirewallRule -Name $ruleName -DisplayName $ruleName -Enabled True -Direction Inbound -Action Allow -Protocol TCP -LocalPort 22 -LocalAddress $actualIp -RemoteAddress $s.vpsTailnetIp -Profile Any
            $newRule = $true
        }
        Set-Service sshd -StartupType Automatic
        if ($wasRunning -and $changed) { Restart-Service sshd } else { Start-Service sshd }
        $listener = $null
        for ($attempt=0; $attempt -lt 5; $attempt++) {
            $processId = (Get-CimInstance Win32_Service -Filter "Name='sshd'").ProcessId
            $listener = @(Get-NetTCPConnection -State Listen -LocalPort 22 -ErrorAction SilentlyContinue | Where-Object {
                $_.OwningProcess -eq $processId -and $_.LocalAddress -in @($actualIp,'0.0.0.0','::')
            })
            if ($listener.Count) { break }
            Start-Sleep -Seconds 1
        }
        if (-not $listener.Count) { throw 'sshd is not listening on the Tailscale address at TCP 22' }
    } catch {
        if ($changed) {
            if ($hadConfig) { [IO.File]::WriteAllBytes($configPath,$originalBytes) }
            else { Remove-Item -LiteralPath $configPath }
        }
        if ($newRule) { Remove-NetFirewallRule -Name $ruleName }
        if ($wasRunning) { Restart-Service sshd } else { Stop-Service sshd -ErrorAction SilentlyContinue }
        throw
    }
    $hostKeyPath = Join-Path $env:ProgramData 'ssh\ssh_host_ed25519_key.pub'
    $hostParts = (Get-Content -LiteralPath $hostKeyPath -Raw).Trim() -split '\s+'
    $hostKey = Get-PublicKey ($hostParts[0..1] -join ' ')
    $receipt = [ordered]@{ schema_version=1; backend_os='windows'; username=$s.serverUser; vps_tailnet_ip=$s.vpsTailnetIp; server_tailnet_ip=$actualIp; port=22; host_public_key=$hostKey; terminal_session=@{enabled=$false} }
    $receiptPath = Join-Path $root 'server-receipt.json'
    Assert-NoReparse $receiptPath
    Write-Utf8 $receiptPath ($receipt | ConvertTo-Json -Depth 4)
    Set-ManagedAcl $receiptPath
    Write-Output "WINDOWS_LOCAL_READY; receipt: $receiptPath"
    Write-Warning 'Existing firewall rules are preserved; a narrow allow rule does not revoke broader existing rules. Review network exposure separately.'
    Write-Output 'Gateway host-key trust, role authorization and real browser access still require acceptance. No persistent terminal/session restoration is installed.'
}

if ($MyInvocation.InvocationName -ne '.') { Install-WindowsBackend }
