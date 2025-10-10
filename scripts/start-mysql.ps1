Param(
    [string]$ServiceName = "MySQLDon_Room",
    [int]$TimeoutSeconds = 15
)

function Test-IsAdmin {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

if (-not (Test-IsAdmin)) {
    $argsList = @(
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", "`"$PSCommandPath`"",
        "-ServiceName", "`"$ServiceName`"",
        "-TimeoutSeconds", $TimeoutSeconds
    )
    Start-Process -FilePath "powershell.exe" -Verb RunAs -ArgumentList $argsList -Wait
    exit $LASTEXITCODE
}

try {
    $service = Get-Service -Name $ServiceName -ErrorAction Stop
} catch {
    Write-Error "Service '$ServiceName' not found."
    exit 2
}

if ($service.Status -ne 'Running') {
    try {
        Start-Service -Name $ServiceName -ErrorAction Stop
    } catch {
        Write-Error "Failed to start service '$ServiceName': $_"
        exit 3
    }
}

$deadline = (Get-Date).AddSeconds($TimeoutSeconds)
while ((Get-Service -Name $ServiceName).Status -ne 'Running') {
    if (Get-Date -gt $deadline) {
        Write-Error "Service '$ServiceName' did not reach Running within $TimeoutSeconds seconds."
        exit 4
    }
    Start-Sleep -Milliseconds 250
}

Write-Output "Service '$ServiceName' is running."
exit 0
