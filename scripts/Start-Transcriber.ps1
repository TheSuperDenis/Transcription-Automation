[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet('auto', 'cpu', 'gpu')]
    [string]$Mode = 'auto'
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$ComposeFile = Join-Path $ProjectRoot 'compose.yaml'

function Write-Step {
    param([string]$Message)
    Write-Host "[Transcriber] $Message" -ForegroundColor Cyan
}

function Test-LocalPortAvailable {
    param([int]$Port)

    $listener = [System.Net.Sockets.TcpListener]::new(
        [System.Net.IPAddress]::Loopback,
        $Port
    )

    try {
        $listener.Start()
        return $true
    }
    catch {
        return $false
    }
    finally {
        $listener.Stop()
    }
}

function Get-ConfiguredPort {
    $defaultPort = 43127
    $envFile = Join-Path $ProjectRoot '.env'

    if (-not (Test-Path -LiteralPath $envFile)) {
        return $defaultPort
    }

    $portLine = Get-Content -LiteralPath $envFile |
        Where-Object { $_ -match '^\s*TRANSCRIBER_PORT\s*=\s*(\d+)\s*$' } |
        Select-Object -Last 1

    if ($portLine -and $portLine -match '(\d+)') {
        return [int]$Matches[1]
    }

    return $defaultPort
}

function Get-TranscriberUriIfHealthy {
    param([int]$Port)

    $baseUri = "http://127.0.0.1:$Port"
    try {
        $health = Invoke-RestMethod -Uri "$baseUri/healthz" -TimeoutSec 2
        if (($health.status -eq 'ok') -and ($health.service -eq 'local-whisper-transcriber')) {
            return $baseUri
        }
    }
    catch {
        # A closed port or a different service is handled by the caller.
    }

    return $null
}

function Find-AvailablePort {
    param([int]$PreferredPort)

    if (Test-LocalPortAvailable -Port $PreferredPort) {
        return $PreferredPort
    }

    foreach ($candidate in (($PreferredPort + 1)..($PreferredPort + 50))) {
        if (Test-LocalPortAvailable -Port $candidate) {
            Write-Warning "Port $PreferredPort is occupied. Using $candidate for this launch."
            return $candidate
        }
    }

    throw "No free localhost port was found between $PreferredPort and $($PreferredPort + 50)."
}

function Invoke-Compose {
    param(
        [Parameter(Mandatory)]
        [string[]]$Arguments,
        [switch]$AllowFailure
    )

    & docker compose --file $ComposeFile @Arguments
    $exitCode = $LASTEXITCODE
    if (($exitCode -ne 0) -and (-not $AllowFailure)) {
        throw "docker compose failed with exit code $exitCode."
    }
}

function Test-DockerGpu {
    Write-Step 'Building the NVIDIA image and checking CUDA access...'
    Invoke-Compose -Arguments @('--profile', 'gpu', 'build', 'transcriber-gpu')

    $probe = & docker compose --file $ComposeFile --profile gpu run --rm --no-deps -T `
        --entrypoint python transcriber-gpu `
        -c "import sys, torch; ok=torch.cuda.is_available() and torch.cuda.device_count()>0; print('CUDA_OK=' + str(ok) + ';GPU_COUNT=' + str(torch.cuda.device_count())); sys.exit(0 if ok else 1)" 2>&1
    $probeExit = $LASTEXITCODE
    $probeText = ($probe | Out-String).Trim()

    if ($probeText) {
        Write-Host $probeText
    }

    return ($probeExit -eq 0 -and $probeText -match 'CUDA_OK=True')
}

try {
    Set-Location -LiteralPath $ProjectRoot

    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        throw 'Docker was not found. Install and start Docker Desktop, then try again.'
    }

    Write-Step 'Checking Docker Desktop...'
    & docker info --format '{{.ServerVersion}}' *> $null
    if ($LASTEXITCODE -ne 0) {
        throw 'Docker Desktop is not running. Start Docker Desktop and wait until it is ready.'
    }

    $preferredPort = Get-ConfiguredPort
    $existingUri = Get-TranscriberUriIfHealthy -Port $preferredPort
    if ($existingUri -and $Mode -eq 'auto') {
        Write-Step "The local transcriber is already healthy at $existingUri."
        Start-Process $existingUri
        exit 0
    }

    # Also recognize a prior launch that had to choose a nearby port.
    $runningContainers = @(& docker compose --file $ComposeFile --profile cpu --profile gpu `
        ps --status running --quiet 2>$null)
    foreach ($containerId in $runningContainers) {
        if (-not $containerId) {
            continue
        }

        $publishedPort = & docker inspect --format `
            '{{with (index .NetworkSettings.Ports "8000/tcp")}}{{(index . 0).HostPort}}{{end}}' `
            $containerId 2>$null | Select-Object -First 1
        $publishedPort = ([string]$publishedPort).Trim()
        if ($publishedPort -match '^\d+$') {
            $existingUri = Get-TranscriberUriIfHealthy -Port ([int]$publishedPort)
            if ($existingUri -and $Mode -eq 'auto') {
                Write-Step "The local transcriber is already healthy at $existingUri."
                Start-Process $existingUri
                exit 0
            }
        }
    }

    # Profiles share the same host port and are intentionally mutually exclusive.
    if ($runningContainers.Count -gt 0) {
        Write-Step 'Replacing an unhealthy or incomplete prior container...'
        Invoke-Compose -Arguments @('--profile', 'cpu', '--profile', 'gpu', 'down', '--remove-orphans') | Out-Null
    }

    $port = Find-AvailablePort -PreferredPort $preferredPort
    $env:TRANSCRIBER_PORT = [string]$port

    $selectedMode = $Mode
    if ($Mode -eq 'auto') {
        try {
            if (Test-DockerGpu) {
                $selectedMode = 'gpu'
            }
            else {
                Write-Warning 'Docker could not use CUDA. Falling back to the CPU image.'
                $selectedMode = 'cpu'
            }
        }
        catch {
            Write-Warning "GPU detection failed: $($_.Exception.Message)"
            Write-Warning 'Falling back to the CPU image.'
            $selectedMode = 'cpu'
        }
    }
    elseif ($Mode -eq 'gpu') {
        if (-not (Test-DockerGpu)) {
            throw 'GPU mode was requested, but PyTorch could not access an NVIDIA GPU inside Docker.'
        }
    }

    $service = if ($selectedMode -eq 'gpu') { 'transcriber-gpu' } else { 'transcriber-cpu' }
    Write-Step "Starting $selectedMode mode on http://127.0.0.1:$port ..."
    Invoke-Compose -Arguments @('--profile', $selectedMode, 'up', '--detach', '--build', $service)

    $deadline = (Get-Date).AddMinutes(3)
    $healthy = $false

    while ((Get-Date) -lt $deadline) {
        if (Get-TranscriberUriIfHealthy -Port $port) {
            $healthy = $true
            break
        }
        Start-Sleep -Seconds 2
    }

    if (-not $healthy) {
        Write-Host ''
        & docker compose --file $ComposeFile --profile $selectedMode logs --tail 100 $service
        throw 'The server did not become healthy within three minutes. Recent logs are shown above.'
    }

    Write-Step "Ready in $selectedMode mode. Finished transcripts are saved in $ProjectRoot\transcripts."
    Start-Process "http://127.0.0.1:$port"
}
catch {
    Write-Host "[Transcriber] ERROR: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
