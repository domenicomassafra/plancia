# Registers fleetprobe as a per-user logon task on the MateBook.
# Usage: powershell -NoProfile -File install-matebook.ps1 -Token <token>
param(
    [Parameter(Mandatory = $true)][string]$Token,
    [string]$Root = (Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'ControlCenter'),
    [string]$Python = "",
    [string]$TaskName = "ControlCenter FleetProbe",
    [Parameter(DontShow = $true)]
    [scriptblock]$IdentityProvider = { [System.Security.Principal.WindowsIdentity]::GetCurrent() }
)

$ErrorActionPreference = "Stop"

$secrets = Join-Path $Root "secrets"
$tokenPath = Join-Path $secrets "fleetprobe-token"
$script = Join-Path $Root "fleetprobe\fleetprobe.py"
$sourceScript = Join-Path (Split-Path -Parent $PSScriptRoot) "fleetprobe.py"
$sourceDir = Split-Path -Parent $sourceScript
$agentModules = @("fleetprobe.py", "fleetprobe_support.py", "fleetprobe_linux.py", "fleetprobe_darwin.py", "fleetprobe_windows.py")

if ([string]::IsNullOrWhiteSpace($Python)) {
    $pythonCommand = Get-Command pythonw.exe -ErrorAction Stop | Select-Object -First 1
    $Python = $pythonCommand.Source
}
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw "pythonw.exe not found: $Python"
}
if (-not (Test-Path -LiteralPath $sourceScript -PathType Leaf)) {
    throw "fleetprobe source not found: $sourceScript"
}
foreach ($module in $agentModules) {
    if (-not (Test-Path -LiteralPath (Join-Path $sourceDir $module) -PathType Leaf)) {
        throw "fleetprobe module not found: $module"
    }
}

New-Item -ItemType Directory -Force -Path $secrets | Out-Null
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $script) | Out-Null
Copy-Item -LiteralPath $sourceScript -Destination $script -Force
foreach ($module in $agentModules | Where-Object { $_ -ne "fleetprobe.py" }) {
    Copy-Item -LiteralPath (Join-Path $sourceDir $module) -Destination (Join-Path (Split-Path -Parent $script) $module) -Force
}
Set-Content -Path $tokenPath -Value $Token -NoNewline -Encoding ascii

# Restrict the token to the owner account only.
# Use the account SID: a Microsoft-account login does not resolve through
# USERDOMAIN\USERNAME on this machine.
$sid = (& $IdentityProvider).User
$acl = Get-Acl $tokenPath
$acl.SetAccessRuleProtection($true, $false)
$acl.Access | ForEach-Object { $acl.RemoveAccessRule($_) | Out-Null }
$rule = New-Object System.Security.AccessControl.FileSystemAccessRule(
    $sid, "FullControl", "Allow")
$acl.AddAccessRule($rule)
Set-Acl -Path $tokenPath -AclObject $acl

$arguments = '"{0}" --serve --host-id matebook --bind auto --interval 60 --token-file "{1}"' -f $script, $tokenPath
$action = New-ScheduledTaskAction -Execute $Python -Argument $arguments
# USERDOMAIN is "WORKGROUP" on this machine, which does not resolve; the fully
# qualified identity name (MATEBOOK-DODO\massa) does.
$account = (& $IdentityProvider).Name
$principal = New-ScheduledTaskPrincipal -UserId $account -LogonType S4U -RunLevel Limited
$logonTrigger = New-ScheduledTaskTrigger -AtLogOn -User $account
$retryTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes 1) `
    -RepetitionDuration (New-TimeSpan -Days 3650)
$triggers = @($logonTrigger, $retryTrigger)
# Native defaults disallow starting on battery and stop the task after switching to battery.
$settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -MultipleInstances IgnoreNew

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $triggers `
    -Settings $settings -Principal $principal -Force | Out-Null

Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
Start-ScheduledTask -TaskName $TaskName
Start-Sleep -Seconds 8

$state = (Get-ScheduledTask -TaskName $TaskName).State
$listening = (Get-NetTCPConnection -LocalPort 9631 -State Listen -ErrorAction SilentlyContinue | Measure-Object).Count
Write-Output "task_state=$state listening=$listening"
