param([ValidateSet('Install','Remove')][string]$Action = 'Install', [switch]$ValidateOnly)
$ErrorActionPreference = 'Stop'
$appExe = Join-Path $PSScriptRoot 'campus-auto-login.exe'
$configFile = Join-Path $PSScriptRoot '.env'
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$taskName = 'Campus Auto Login-' + $identity.User.Value
$shortcutFile = Join-Path ([Environment]::GetFolderPath('Startup')) 'Campus Auto Login.lnk'
$shellObject = New-Object -ComObject WScript.Shell

# Never take over a task or shortcut belonging to a different installation.
$existingTask = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($existingTask) {
    $actions = @($existingTask.Actions)
    if ($actions.Count -ne 1 -or $actions[0].Execute -ne $appExe -or $actions[0].Arguments) {
        throw 'Another installation owns this startup task. Remove it from that folder first.'
    }
}
if (Test-Path -LiteralPath $shortcutFile) {
    $existing = $shellObject.CreateShortcut($shortcutFile)
    if ($existing.TargetPath -ne $appExe) {
        throw 'Another installation owns this startup shortcut. Remove it from that folder first.'
    }
}
if ($Action -eq 'Remove') {
    if ($ValidateOnly) { Write-Host 'Startup removal validated; no changes made.'; exit 0 }
    if (-not $ValidateOnly) {
        if ($existingTask) { Unregister-ScheduledTask -TaskName $taskName -Confirm:$false }
        if (Test-Path -LiteralPath $shortcutFile) { Remove-Item -LiteralPath $shortcutFile }
    }
    Write-Host 'Startup disabled (or already absent). Running application is not stopped.'
    exit 0
}
if (-not (Test-Path -LiteralPath $appExe)) { throw 'Extract the Windows ZIP first; campus-auto-login.exe is missing.' }
if (-not (Test-Path -LiteralPath $configFile)) { throw 'Run configure.cmd and fill in .env first.' }
if ($ValidateOnly) { Write-Host 'Startup paths validated; no changes made.'; exit 0 }

$taskAction = New-ScheduledTaskAction -Execute $appExe -WorkingDirectory $PSScriptRoot
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $identity.Name
$trigger.Delay = 'PT0S'
$principal = New-ScheduledTaskPrincipal -UserId $identity.Name -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
$task = New-ScheduledTask -Action $taskAction -Trigger $trigger -Principal $principal -Settings $settings `
    -Description 'Campus authentication at user sign-in, with no startup delay or network prerequisite.'
Register-ScheduledTask -TaskName $taskName -InputObject $task -Force | Out-Null

$check = Get-ScheduledTask -TaskName $taskName
if ($check.Actions.Execute -ne $appExe -or $check.Actions.WorkingDirectory -ne $PSScriptRoot `
    -or $check.Triggers.Delay -notin @('PT0S', $null, '') -or $check.Settings.RunOnlyIfNetworkAvailable) {
    throw 'Startup task verification failed; the previous shortcut has been kept.'
}
# Migrate only after the replacement task has been successfully registered.
if (Test-Path -LiteralPath $shortcutFile) { Remove-Item -LiteralPath $shortcutFile }
Write-Host 'Installed for this Windows user. Starts immediately at next sign-in, including on battery.'
Write-Host 'Keep the entire folder (including _internal) in place. Logs: logs\campus.log'
