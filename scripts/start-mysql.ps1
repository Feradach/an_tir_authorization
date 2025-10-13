Param(
    [string]$ServiceName = "MySQLDon_Room",
    [int]$TimeoutSeconds = 60,
    [string]$DbHost = "127.0.0.1",
    [int]$DbPort = 3306
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
        "-TimeoutSeconds", $TimeoutSeconds,
        "-DbHost", "`"$DbHost`"",
        "-DbPort", $DbPort
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

Write-Output "Service '$ServiceName' is running. Waiting for $($DbHost):$DbPort to accept connections..."

# Wait for the TCP port to be open (service can be Running before socket is ready)
$deadline = (Get-Date).AddSeconds($TimeoutSeconds)
while ($true) {
    try {
        $client = New-Object System.Net.Sockets.TcpClient
        $iar = $client.BeginConnect($DbHost, $DbPort, $null, $null)
        $connected = $iar.AsyncWaitHandle.WaitOne(500)
        if ($connected -and $client.Connected) {
            $client.EndConnect($iar)
            $client.Close()
            break
        }
        $client.Close()
    } catch { }
    if (Get-Date -gt $deadline) {
        Write-Error "Service '$ServiceName' did not open $($DbHost):$DbPort within $TimeoutSeconds seconds."
        exit 5
    }
}

Write-Output "MySQL is accepting connections on $($DbHost):$DbPort."
exit 0
