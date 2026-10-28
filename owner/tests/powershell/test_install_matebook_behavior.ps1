param(
    [Parameter(Mandatory = $true)]
    [string]$Installer
)

$ErrorActionPreference = "Stop"

function Assert-True {
    param(
        [bool]$Condition,
        [string]$Message
    )
    if (-not $Condition) {
        throw "ASSERT: $Message"
    }
}

function Invoke-InstallerScenario {
    param(
        [string[]]$Discovered = @("/mock/pythonw-first.exe"),
        [string]$ExplicitPython = "",
        [bool]$PythonExists = $true,
        [bool]$SourceExists = $true
    )

    $global:Dashboard252Events = [System.Collections.Generic.List[string]]::new()
    $global:Dashboard252Discovered = $Discovered
    $global:Dashboard252PythonExists = $PythonExists
    $global:Dashboard252SourceExists = $SourceExists
    $global:Dashboard252RegistrationExecute = $null

    function Get-Command {
        param([string]$Name, $ErrorAction)
        $global:Dashboard252Events.Add("discover")
        foreach ($path in $global:Dashboard252Discovered) {
            [pscustomobject]@{ Source = $path }
        }
    }

    function Test-Path {
        param([string]$LiteralPath, $PathType)
        $global:Dashboard252Events.Add("test:$LiteralPath")
        if ([string]::IsNullOrWhiteSpace($LiteralPath)) {
            return $false
        }
        if ($LiteralPath -like "*fleetprobe.py") {
            return $global:Dashboard252SourceExists
        }
        return $global:Dashboard252PythonExists
    }

    function New-Item {
        param($ItemType, [switch]$Force, $Path)
        $global:Dashboard252Events.Add("mkdir:$Path")
        [pscustomobject]@{}
    }

    function Copy-Item {
        param($LiteralPath, $Destination, [switch]$Force)
        $global:Dashboard252Events.Add("copy:$LiteralPath->$Destination")
    }

    function Set-Content {
        param($Path, $Value, [switch]$NoNewline, $Encoding)
        $global:Dashboard252Events.Add("token:$Path")
    }

    function Get-Acl {
        param($Path)
        $acl = [pscustomobject]@{ Access = @() }
        $acl | Add-Member ScriptMethod SetAccessRuleProtection { param($a, $b) } -Force
        $acl | Add-Member ScriptMethod RemoveAccessRule { param($a) return $true } -Force
        $acl | Add-Member ScriptMethod AddAccessRule { param($a) } -Force
        return $acl
    }

    function New-Object {
        param(
            [Parameter(Position = 0)]$TypeName,
            [Parameter(ValueFromRemainingArguments = $true)]$Rest
        )
        return [pscustomobject]@{}
    }

    function Set-Acl {
        param($Path, $AclObject)
        $global:Dashboard252Events.Add("acl:$Path")
    }

    function New-ScheduledTaskAction {
        param($Execute, $Argument)
        $global:Dashboard252Events.Add("action:$Execute")
        $global:Dashboard252RegistrationExecute = $Execute
        [pscustomobject]@{}
    }

    function New-ScheduledTaskPrincipal {
        param($UserId, $LogonType, $RunLevel)
        [pscustomobject]@{}
    }

    function New-ScheduledTaskTrigger {
        param(
            [switch]$AtLogOn,
            $User,
            [switch]$Once,
            $At,
            $RepetitionInterval,
            $RepetitionDuration
        )
        [pscustomobject]@{}
    }

    function New-ScheduledTaskSettingsSet {
        param($ExecutionTimeLimit, $RestartCount, $RestartInterval, $MultipleInstances)
        [pscustomobject]@{}
    }

    function Register-ScheduledTask {
        param($TaskName, $Action, $Trigger, $Settings, $Principal, [switch]$Force)
        $global:Dashboard252Events.Add("register")
        [pscustomobject]@{}
    }

    function Stop-ScheduledTask {
        param($TaskName, $ErrorAction)
        $global:Dashboard252Events.Add("stop")
    }

    function Start-ScheduledTask {
        param($TaskName)
        $global:Dashboard252Events.Add("start")
    }

    function Start-Sleep {
        param($Seconds)
    }

    function Get-ScheduledTask {
        param($TaskName)
        [pscustomobject]@{ State = "Ready" }
    }

    function Get-NetTCPConnection {
        param($LocalPort, $State, $ErrorAction)
        @()
    }

    $identityProvider = {
        [pscustomobject]@{
            User = "S-1-5-21-test"
            Name = "MATEBOOK-DODO\massa"
        }
    }

    $caught = $null
    try {
        $arguments = @{
            Token = "behavior-test-token"
            Root = "/virtual/root"
            IdentityProvider = $identityProvider
        }
        if (-not [string]::IsNullOrWhiteSpace($ExplicitPython)) {
            $arguments.Python = $ExplicitPython
        }
        & $Installer @arguments | Out-Null
    }
    catch {
        $caught = $_
    }

    [pscustomobject]@{
        Error = $caught
        Events = @($global:Dashboard252Events)
        RegistrationExecute = $global:Dashboard252RegistrationExecute
    }
}

$zeroResult = Invoke-InstallerScenario -Discovered @()
Assert-True ($null -ne $zeroResult.Error) "zero discovery result must fail"
Assert-True (-not ($zeroResult.Events -contains "register")) "zero discovery must fail before registration"

$multipleResults = Invoke-InstallerScenario -Discovered @("/mock/first.exe", "/mock/second.exe")
Assert-True ($null -eq $multipleResults.Error) "multiple discovery results must be accepted"
Assert-True ($multipleResults.RegistrationExecute -eq "/mock/first.exe") "multiple discovery results must use the first path"

$explicitPython = Invoke-InstallerScenario -ExplicitPython "/explicit/pythonw.exe"
Assert-True ($null -eq $explicitPython.Error) "explicit Python must be accepted when it is a leaf"
Assert-True (-not ($explicitPython.Events -contains "discover")) "explicit Python must bypass discovery"
Assert-True ($explicitPython.Events -contains "test:/explicit/pythonw.exe") "explicit Python must be leaf-validated"
Assert-True ($explicitPython.RegistrationExecute -eq "/explicit/pythonw.exe") "explicit Python must reach task action unchanged"

$invalidExplicitPython = Invoke-InstallerScenario -ExplicitPython "/explicit/missing.exe" -PythonExists $false
Assert-True ($null -ne $invalidExplicitPython.Error) "missing explicit Python must fail"
Assert-True (-not ($invalidExplicitPython.Events -contains "discover")) "missing explicit Python must still bypass discovery"
Assert-True (-not ($invalidExplicitPython.Events -contains "register")) "missing explicit Python must fail before registration"

$missingSource = Invoke-InstallerScenario -SourceExists $false
Assert-True ($null -ne $missingSource.Error) "missing canonical source must fail"
Assert-True (-not ($missingSource.Events -contains "register")) "missing canonical source must fail before registration"

$ordered = Invoke-InstallerScenario
Assert-True ($null -eq $ordered.Error) "valid installer path must reach registration"
$copyEvent = $ordered.Events | Where-Object { $_ -like "copy:*" } | Select-Object -First 1
$copyIndex = [Array]::IndexOf($ordered.Events, $copyEvent)
$registerIndex = [Array]::IndexOf($ordered.Events, "register")
Assert-True ($copyIndex -ge 0) "canonical source must be copied"
Assert-True ($registerIndex -gt $copyIndex) "copy must occur before task registration"

Write-Output "PASS installer behavior: zero-discovery, multi-first, explicit-bypass+leaf, missing-source, copy-before-register"
