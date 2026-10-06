[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet('run', 'start', 'stop', 'restart', 'status', 'check')]
    [string]$Action = 'run',
    [switch]$BuildEngine,
    [ValidateSet('Debug', 'Release')][string]$EngineConfiguration = 'Debug',
    [string]$LocalNetworkPath,
    [ValidateRange(1024, 65535)][int]$BackendPort = 8000,
    [ValidateRange(1024, 65535)][int]$FrontendPort = 5173
)

$ErrorActionPreference = 'Stop'
$repoRoot = [System.IO.Path]::GetFullPath((Split-Path -Parent $PSCommandPath))
$frontendDir = Join-Path $repoRoot 'frontend'
$python = Join-Path $repoRoot '.venv/Scripts/python.exe'
$logDir = Join-Path $repoRoot 'data/cache'
$statePath = Join-Path $logDir "demo-service-$BackendPort-$FrontendPort.json"
$apiUrl = "http://127.0.0.1:$BackendPort"
$frontendUrl = "http://127.0.0.1:$FrontendPort"
$ownedProcesses = @()
$ownedSessionId = $null
$environmentBackup = @{}
$exitCode = 0
$lockHeld = $false
$keepServices = $false

function Get-FullRepoPath([string]$path) {
    if (-not [System.IO.Path]::IsPathRooted($path)) { $path = Join-Path $repoRoot $path }
    return [System.IO.Path]::GetFullPath($path)
}

function Get-TextHash([string]$value) {
    $hash = [System.Security.Cryptography.SHA256]::Create()
    try {
        return [System.BitConverter]::ToString(
            $hash.ComputeHash([System.Text.Encoding]::UTF8.GetBytes($value))).Replace('-', '')
    } finally { $hash.Dispose() }
}

function Set-LaunchEnvironment([string]$name, [string]$value) {
    if (-not $environmentBackup.ContainsKey($name)) {
        $environmentBackup[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
    }
    [Environment]::SetEnvironmentVariable($name, $value, 'Process')
}

function Test-PortOpen([int]$port) {
    $client = New-Object System.Net.Sockets.TcpClient
    try { $client.Connect('127.0.0.1', $port); return $true }
    catch { return $false }
    finally { $client.Dispose() }
}

function Invoke-ApiJson([string]$uri, [string]$method = 'Get', $body = $null) {
    $options = @{ Uri = $uri; Method = $method; TimeoutSec = 5; ErrorAction = 'Stop' }
    if ($null -ne $body) {
        $options.ContentType = 'application/json; charset=utf-8'
        $options.Body = [System.Text.Encoding]::UTF8.GetBytes(
            ($body | ConvertTo-Json -Depth 12 -Compress))
    }
    try { return Invoke-RestMethod @options }
    catch {
        $detail = $_.ErrorDetails.Message
        if (-not $detail) { $detail = $_.Exception.Message }
        throw "API request failed ($uri): $detail"
    }
}

function Test-ApiReady {
    try { $health = Invoke-ApiJson "$apiUrl/api/v1/health"; return $health.status -eq 'ok' }
    catch { return $false }
}

function Test-FrontendReady {
    try {
        $response = Invoke-WebRequest -Uri "$frontendUrl/" -UseBasicParsing -TimeoutSec 2
        return $response.StatusCode -eq 200 -and $response.Content.Contains('/src/entry.js')
    } catch { return $false }
}

function Get-ProcessRecord($process, [string]$role, [switch]$Primary) {
    $process.Refresh()
    return [pscustomobject]@{
        id = $process.Id
        startTicks = $process.StartTime.ToUniversalTime().Ticks.ToString()
        executable = $process.Path
        role = $role
        primary = [bool]$Primary
    }
}

function Get-MatchingProcess($record) {
    $process = Get-Process -Id ([int]$record.id) -ErrorAction SilentlyContinue
    if ($null -eq $process) { return $null }
    try {
        if ($process.StartTime.ToUniversalTime().Ticks.ToString() -ne $record.startTicks -or
            $process.Path -ne $record.executable) { return $null }
        return $process
    } catch { return $null }
}

function Read-ServiceState {
    if (-not (Test-Path -LiteralPath $statePath -PathType Leaf)) { return $null }
    $state = Get-Content -LiteralPath $statePath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($state.version -ne 1 -or $state.repoRoot -ne $repoRoot -or
        $state.backendPort -ne $BackendPort -or $state.frontendPort -ne $FrontendPort) {
        throw "Invalid launcher state: $statePath. No process was stopped."
    }
    return $state
}

function Save-ServiceState($state) {
    $temporary = "$statePath.pending"
    [System.IO.File]::WriteAllText($temporary,
        ($state | ConvertTo-Json -Depth 8), (New-Object System.Text.UTF8Encoding($false)))
    Move-Item -LiteralPath $temporary -Destination $statePath -Force
}

function Get-DescendantRecords($record, $snapshot) {
    $parent = Get-MatchingProcess $record
    if ($null -eq $parent) { return }
    foreach ($child in @($snapshot | Where-Object { $_.ParentProcessId -eq $parent.Id })) {
        $childProcess = Get-Process -Id ([int]$child.ProcessId) -ErrorAction SilentlyContinue
        if ($null -eq $childProcess) { continue }
        try {
            if ($childProcess.StartTime.ToUniversalTime() -lt $parent.StartTime.ToUniversalTime()) { continue }
            # A transient child's PID may have been reused after the CIM snapshot.
            if (-not $child.CreationDate -or [Math]::Abs(
                ($childProcess.StartTime.ToUniversalTime() - $child.CreationDate.ToUniversalTime()).TotalMilliseconds) -gt 1) {
                continue
            }
            $childRecord = Get-ProcessRecord $childProcess $record.role
        } catch { continue }
        Get-DescendantRecords $childRecord $snapshot
        $childRecord
    }
}

function Stop-TrackedProcesses($records) {
    # Capture descendants first: Windows venv python.exe can own a second Python process.
    $snapshot = @(Get-CimInstance Win32_Process -ErrorAction Stop)
    $targets = @()
    foreach ($record in @($records)) {
        $targets += @(Get-DescendantRecords $record $snapshot)
        $targets += $record
    }
    $seen = @{}
    foreach ($record in $targets) {
        $key = "$($record.id):$($record.startTicks)"
        if ($seen.ContainsKey($key)) { continue }
        $seen[$key] = $true
        $process = Get-MatchingProcess $record
        if ($null -eq $process) { continue }
        try { Stop-Process -InputObject $process -ErrorAction Stop }
        catch {
            if ($null -ne (Get-MatchingProcess $record)) { throw }
        }
    }
}

function Stop-Service {
    $state = Read-ServiceState
    if ($null -eq $state) {
        Write-Host 'No service owned by this launcher. Existing services were left untouched.'
        return
    }
    Stop-TrackedProcesses $state.processes
    Remove-Item -LiteralPath $statePath -Force
    Write-Host 'Stopped only the processes recorded by this launcher.'
}

function Get-ConfigurationFingerprint([string]$localPath, [string]$syntheticPath) {
    $files = @(
        Get-Item -LiteralPath $PSCommandPath, $localPath, $syntheticPath
        Get-ChildItem -LiteralPath (Join-Path $repoRoot 'backend/app') -File -Filter '*.py'
        Get-ChildItem -LiteralPath (Join-Path $repoRoot 'cpp-engine/src') -File -Filter '*.cpp'
        Get-ChildItem -LiteralPath (Join-Path $repoRoot 'cpp-engine/include') -Recurse -File
        Get-Item -LiteralPath (Join-Path $repoRoot 'cpp-engine/CMakeLists.txt'),
            (Join-Path $frontendDir 'vite.config.js'), (Join-Path $frontendDir 'scripts/demo-vite.mjs'),
            (Join-Path $repoRoot 'backend/scripts/demo_preflight.py')
    )
    $description = $files | Sort-Object FullName -Unique | ForEach-Object {
        "$($_.FullName)|$($_.Length)|$($_.LastWriteTimeUtc.Ticks)"
    }
    return Get-TextHash (($description -join ";") + "|local=$localPath|synthetic=$syntheticPath")
}

function Quote-NativeArgument([string]$value) {
    $escaped = [regex]::Replace($value, '(\\*)"', '$1$1\"')
    $escaped = [regex]::Replace($escaped, '(\\+)$', '$1$1')
    return '"' + $escaped + '"'
}

function Start-LoggedProcess([string]$executable, [string[]]$arguments, [string]$directory,
                             [string]$role, [switch]$Wait) {
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss-fff'
    $stdout = Join-Path $logDir "demo-$role-$stamp.out.log"
    $stderr = Join-Path $logDir "demo-$role-$stamp.err.log"
    $argumentLine = ($arguments | ForEach-Object { Quote-NativeArgument $_ }) -join ' '
    $options = @{
        FilePath = $executable; ArgumentList = $argumentLine; WorkingDirectory = $directory
        WindowStyle = 'Hidden'; PassThru = $true
        RedirectStandardOutput = $stdout; RedirectStandardError = $stderr
    }
    if ($Wait) { $options.Wait = $true }
    $process = Start-Process @options
    if (-not $Wait) {
        # Cache the handle before Refresh: PowerShell 5.1 can otherwise lose ExitCode.
        $null = $process.Handle
    }
    if ($Wait) {
        if ($process.ExitCode -ne 0) {
            $tail = Get-Content -LiteralPath $stderr -Tail 15 -ErrorAction SilentlyContinue
            throw ("$role failed (exit $($process.ExitCode)). Log: $stderr" +
                [Environment]::NewLine + ($tail -join [Environment]::NewLine))
        }
    } else { Write-Host "$role log: $stderr" }
    return $process
}

function Resolve-Engine {
    $sources = @(
        Get-ChildItem -LiteralPath (Join-Path $repoRoot 'cpp-engine/src') -File -Filter '*.cpp'
        Get-ChildItem -LiteralPath (Join-Path $repoRoot 'cpp-engine/include') -Recurse -File
        Get-Item -LiteralPath (Join-Path $repoRoot 'cpp-engine/CMakeLists.txt')
    )
    $latestSource = ($sources | Sort-Object LastWriteTimeUtc -Descending | Select-Object -First 1).LastWriteTimeUtc
    $buildRoot = Join-Path $repoRoot "cpp-engine/build/demo-launcher/$EngineConfiguration"
    $candidatePaths = @(
        (Join-Path $repoRoot "cpp-engine/build/poi-route/$EngineConfiguration/isochrone_engine.exe"),
        (Join-Path $buildRoot 'isochrone_engine.exe'),
        (Join-Path $buildRoot 'cmake/isochrone_engine.exe'),
        (Join-Path $buildRoot "cmake/$EngineConfiguration/isochrone_engine.exe"),
        (Join-Path $repoRoot "cpp-engine/build/$EngineConfiguration/isochrone_engine.exe")
    )
    if ($env:CPP_ENGINE_PATH) { $candidatePaths += Get-FullRepoPath $env:CPP_ENGINE_PATH }
    $candidate = $candidatePaths | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } |
        Where-Object {
            try { (& $_ --health | ConvertFrom-Json).buildMode -eq $EngineConfiguration }
            catch { $false }
        } | ForEach-Object { Get-Item -LiteralPath $_ } | Sort-Object LastWriteTimeUtc -Descending |
        Select-Object -First 1
    if (-not $BuildEngine -and $candidate -and $candidate.LastWriteTimeUtc -ge $latestSource) {
        return $candidate.FullName
    }
    if ($Action -eq 'check') { throw 'Engine is missing or out of date. Run start to rebuild it.' }
    New-Item -ItemType Directory -Path $buildRoot -Force | Out-Null
    $cmake = Get-Command cmake -ErrorAction SilentlyContinue
    if ($cmake) {
        $cmakeBuild = Join-Path $buildRoot 'cmake'
        Write-Host "Building the $EngineConfiguration engine with CMake (isolated launcher build directory)..."
        Start-LoggedProcess $cmake.Source @('-S', (Join-Path $repoRoot 'cpp-engine'), '-B',
            $cmakeBuild, "-DCMAKE_BUILD_TYPE=$EngineConfiguration") $repoRoot 'cmake-configure' -Wait | Out-Null
        Start-LoggedProcess $cmake.Source @('--build', $cmakeBuild, '--config', $EngineConfiguration,
            '--target', 'isochrone_engine') $repoRoot 'cmake-build' -Wait | Out-Null
        foreach ($path in @((Join-Path $cmakeBuild "$EngineConfiguration/isochrone_engine.exe"),
                            (Join-Path $cmakeBuild 'isochrone_engine.exe'))) {
            if (Test-Path -LiteralPath $path -PathType Leaf) { return $path }
        }
        throw 'CMake completed but the engine executable was not found.'
    }
    $compiler = Get-Command g++ -ErrorAction SilentlyContinue
    if (-not $compiler) { throw 'Install CMake plus a C++ compiler, or put MinGW g++ on PATH.' }
    $version = & $compiler.Source -dumpversion
    $major = [int](($version | Select-Object -First 1).Split('.')[0])
    $standard = if ($major -ge 10) { '-std=c++20' } else { '-std=c++2a' }
    $output = Join-Path $buildRoot 'isochrone_engine.exe'
    $pending = Join-Path $buildRoot 'isochrone_engine.pending.exe'
    $flags = if ($EngineConfiguration -eq 'Debug') { @('-O0', '-g') } else { @('-O2', '-DNDEBUG') }
    $arguments = @($standard) + $flags + @('-Wall', '-Wextra', '-Wpedantic',
        '-I', (Join-Path $repoRoot 'cpp-engine/include'))
    $arguments += @(Get-ChildItem -LiteralPath (Join-Path $repoRoot 'cpp-engine/src') -File -Filter '*.cpp' |
        Sort-Object Name | Select-Object -ExpandProperty FullName)
    $arguments += @('-o', $pending)
    Write-Host 'CMake not found; building the current dependency-free engine with MinGW g++...'
    Start-LoggedProcess $compiler.Source $arguments $repoRoot 'gcc-build' -Wait | Out-Null
    Move-Item -LiteralPath $pending -Destination $output -Force
    return $output
}

function Test-EngineContract([string]$engine) {
    $info = New-Object System.Diagnostics.ProcessStartInfo
    $info.FileName = $engine
    $info.WorkingDirectory = $repoRoot
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.RedirectStandardInput = $true
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    $info.StandardOutputEncoding = [System.Text.Encoding]::UTF8
    $info.StandardErrorEncoding = [System.Text.Encoding]::UTF8
    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = $info
    try {
        $process.Start() | Out-Null
        $output = $process.StandardOutput.ReadToEndAsync()
        $errors = $process.StandardError.ReadToEndAsync()
        $fixturePath = Join-Path $repoRoot 'contracts/engine-local-experiment.input.example.json'
        $fixture = Get-Content -LiteralPath $fixturePath -Raw -Encoding UTF8
        $bytes = [System.Text.Encoding]::UTF8.GetBytes($fixture)
        $process.StandardInput.BaseStream.Write($bytes, 0, $bytes.Length)
        $process.StandardInput.Close()
        if (-not $process.WaitForExit(30000)) {
            $process.Kill()
            throw 'Engine contract check timed out.'
        }
        $result = $output.GetAwaiter().GetResult() | ConvertFrom-Json
        if ($process.ExitCode -ne 0 -or $result.success -ne $true -or
            $result.schemaVersion -ne 2 -or
            $result.result.PSObject.Properties.Name -notcontains 'localGrayZones') {
            throw "Engine lacks the current local-experiment contract. Rebuild with -BuildEngine. $($errors.GetAwaiter().GetResult())"
        }
    } finally { $process.Dispose() }
}

function Wait-ForReady([scriptblock]$check, $process, [string]$name) {
    $timer = [System.Diagnostics.Stopwatch]::StartNew()
    while ($timer.Elapsed.TotalSeconds -lt 30) {
        $process.Refresh()
        if ($process.HasExited) {
            $process.WaitForExit()
            throw "$name exited early (code $($process.ExitCode)); see its log."
        }
        if (& $check) { return }
        Start-Sleep -Milliseconds 250
    }
    throw "$name did not become ready within 30 seconds; see its log."
}

function Test-AnalysisRoundTrip($request, [string]$endpoint, [string]$resultEndpoint,
                                 [string]$expectedMode) {
    $accepted = Invoke-ApiJson "$frontendUrl/api/v1/$endpoint" 'Post' $request
    if (-not $accepted.analysisId) { throw "$endpoint did not return analysisId." }
    $timer = [System.Diagnostics.Stopwatch]::StartNew()
    while ($timer.Elapsed.TotalSeconds -lt 35) {
        $state = Invoke-ApiJson "$frontendUrl/api/v1/$resultEndpoint/$($accepted.analysisId)"
        if ($state.status -eq 'failed') { throw "$endpoint failed: $($state.error)" }
        if ($state.status -eq 'completed') {
            if ($expectedMode -and ($state.result.mode -ne $expectedMode -or
                $state.result.thresholdSeconds -ne 180 -or $state.result.notForMainReport -ne $true)) {
                throw 'Local response is not the independent 180-second experiment.'
            }
            return
        }
        Start-Sleep -Milliseconds 250
    }
    throw "$endpoint did not complete within 35 seconds (engine limit: 25 seconds)."
}

$mutexKey = Get-TextHash "$repoRoot|$BackendPort|$FrontendPort"
$mutex = New-Object System.Threading.Mutex($false, "Local\LivingCircleDemo-$mutexKey")
try {
    if ($BackendPort -eq $FrontendPort) { throw 'Backend and frontend ports must be different.' }
    try { $lockHeld = $mutex.WaitOne(0) } catch [System.Threading.AbandonedMutexException] { $lockHeld = $true }
    if (-not $lockHeld) { throw 'Another launcher command is in progress. Try again shortly.' }
    if ($Action -eq 'stop') { Stop-Service; return }
    if ($Action -eq 'status') {
        $state = Read-ServiceState
        if ($null -eq $state) {
            Write-Host 'No managed service. Ports may be occupied by separately started services.'
        } else {
            foreach ($record in $state.processes) {
                $status = if ($null -ne (Get-MatchingProcess $record)) { 'running' } else { 'exited/PID changed' }
                $label = if ($record.primary) { $record.role } else { "$($record.role) child" }
                Write-Host "${label}: $status (PID $($record.id))"
            }
            Write-Host "Page: $frontendUrl/?mode=local"
            Write-Host "Engine: $($state.engine)"
            Write-Host "Local network: $($state.localNetwork)"
        }
        Write-Host "Port $BackendPort listening: $(Test-PortOpen $BackendPort)"
        Write-Host "Port $FrontendPort listening: $(Test-PortOpen $FrontendPort)"
        return
    }
    if ($Action -eq 'restart') { Stop-Service }
    $syntheticPath = if ($env:SYNTHETIC_NETWORK_PATH) {
        Get-FullRepoPath $env:SYNTHETIC_NETWORK_PATH
    } else { Join-Path $repoRoot 'data/networks/synthetic-preview.json' }
    $localPath = if ($LocalNetworkPath) { Get-FullRepoPath $LocalNetworkPath }
        elseif ($env:LOCAL_EXPERIMENT_NETWORK_PATH) { Get-FullRepoPath $env:LOCAL_EXPERIMENT_NETWORK_PATH }
        else { $syntheticPath }
    foreach ($path in @($python, (Join-Path $frontendDir 'node_modules/vite/bin/vite.js'),
                         $localPath, $syntheticPath)) {
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "Required file missing: $path" }
    }
    $nodeCommand = Get-Command node -ErrorAction SilentlyContinue
    if (-not $nodeCommand) { throw 'Node.js is not on PATH.' }
    $fingerprint = Get-ConfigurationFingerprint $localPath $syntheticPath
    $existing = Read-ServiceState
    if ($existing) {
        $live = @($existing.processes | Where-Object { $null -ne (Get-MatchingProcess $_) })
        if ($live.Count -gt 0) {
            $livePrimary = @($live | Where-Object { $_.primary })
            if ($livePrimary.Count -eq 2 -and -not $BuildEngine -and $existing.fingerprint -eq $fingerprint -and
                (Test-ApiReady) -and (Test-FrontendReady)) {
                Write-Host "Managed services are already running: $frontendUrl/?mode=local"
                $keepServices = $true
                return
            }
            if ($BuildEngine -or $existing.fingerprint -ne $fingerprint) {
                throw 'Source code or network configuration changed. Run restart to reload it.'
            }
            throw 'A managed service exited or is unhealthy. Check the logs and run restart.'
        }
        Remove-Item -LiteralPath $statePath -Force
    }
    if ($Action -ne 'check') {
        foreach ($port in @($BackendPort, $FrontendPort)) {
            if (Test-PortOpen $port) {
                throw "Port $port is occupied by a service not owned by this launcher. Stop it yourself or select other ports. It was NOT reused or stopped."
            }
        }
    }
    New-Item -ItemType Directory -Path $logDir -Force | Out-Null
    $engine = Resolve-Engine
    Test-EngineContract $engine
    Set-LaunchEnvironment 'CPP_ENGINE_PATH' $engine
    Set-LaunchEnvironment 'SYNTHETIC_NETWORK_PATH' $syntheticPath
    Set-LaunchEnvironment 'LOCAL_EXPERIMENT_NETWORK_PATH' $localPath
    Set-LaunchEnvironment 'DEMO_BACKEND_PORT' $BackendPort.ToString()
    Set-LaunchEnvironment 'DEMO_FRONTEND_PORT' $FrontendPort.ToString()
    Set-LaunchEnvironment 'PYTHONIOENCODING' 'utf-8'
    $preflightText = & $python -B (Join-Path $repoRoot 'backend/scripts/demo_preflight.py')
    if ($LASTEXITCODE -ne 0) { throw 'Python/network preflight failed; see the preceding error.' }
    $preflight = $preflightText | ConvertFrom-Json
    Write-Host "Engine contract verified: $engine"
    Write-Host "Local network: $localPath ($($preflight.localSource); no unverified real-world claims)"
    if ($Action -eq 'check') { Write-Host 'Dependencies, network inputs and engine contract passed.'; return }
    $state = [pscustomobject]@{
        version = 1; repoRoot = $repoRoot; backendPort = $BackendPort; frontendPort = $FrontendPort
        sessionId = [Guid]::NewGuid().ToString(); fingerprint = $fingerprint
        engine = $engine; localNetwork = $localPath; processes = @()
    }
    $ownedSessionId = $state.sessionId
    $backend = Start-LoggedProcess $python @('-B', '-m', 'uvicorn', 'app.main:app',
        '--app-dir', 'backend', '--host', '127.0.0.1', '--port', $BackendPort.ToString()) $repoRoot 'backend'
    $ownedProcesses += Get-ProcessRecord $backend 'backend' -Primary
    $state.processes = $ownedProcesses
    Save-ServiceState $state
    Wait-ForReady { Test-ApiReady } $backend 'Python API'
    $frontend = Start-LoggedProcess $nodeCommand.Source @((Join-Path $frontendDir 'scripts/demo-vite.mjs')) $frontendDir 'frontend'
    $ownedProcesses += Get-ProcessRecord $frontend 'frontend' -Primary
    # Persist known descendants too, so an exited venv shim does not hide its Python child.
    $snapshot = @(Get-CimInstance Win32_Process -ErrorAction Stop)
    $descendants = @()
    foreach ($record in $ownedProcesses) { $descendants += @(Get-DescendantRecords $record $snapshot) }
    $ownedProcesses += $descendants
    $state.processes = $ownedProcesses
    Save-ServiceState $state
    Wait-ForReady { Test-FrontendReady } $frontend 'Vite frontend'
    $proxyHealth = Invoke-ApiJson "$frontendUrl/api/v1/health"
    if ($proxyHealth.status -ne 'ok') { throw 'Frontend proxy did not reach the healthy backend.' }
    Test-AnalysisRoundTrip $preflight.syntheticRequest 'synthetic-analyses' 'analyses' ''
    Test-AnalysisRoundTrip $preflight.localRequest 'local-experiments' 'local-experiments' 'local_experiment'
    Write-Host 'Verified: frontend proxy -> Python -> C++, for both synthetic and local modes.'
    Write-Host "Open $frontendUrl/?mode=local"
    Write-Host 'These demo data do not establish a verified real-world facility report.'
    $mutex.ReleaseMutex()
    $lockHeld = $false
    if ($Action -eq 'run') {
        Write-Host 'Press Ctrl+C to stop the services started by this invocation.'
        while ($true) {
            Start-Sleep -Seconds 1
            foreach ($service in @($backend, $frontend)) {
                $service.Refresh()
                if ($service.HasExited) { throw "Service process $($service.Id) exited." }
            }
        }
    }
    $keepServices = $true
    Write-Host 'Services remain in the background. Use start-demo.bat stop to stop them.'
} catch {
    $exitCode = 1
    Write-Host ("ERROR: " + $_.Exception.Message) -ForegroundColor Red
    Write-Host "Logs: $logDir"
} finally {
    if (-not $keepServices -and $ownedProcesses.Count -gt 0) {
        try {
            if (-not $lockHeld) {
                try { $lockHeld = $mutex.WaitOne(5000) }
                catch [System.Threading.AbandonedMutexException] { $lockHeld = $true }
                if (-not $lockHeld) { throw 'Another launcher operation is in progress; retry stop afterward.' }
            }
            Stop-TrackedProcesses $ownedProcesses
            $currentState = Read-ServiceState
            if ($currentState -and $currentState.sessionId -eq $ownedSessionId) {
                Remove-Item -LiteralPath $statePath -Force
            }
        } catch { Write-Warning "Cleanup incomplete: $($_.Exception.Message). Retry the stop command." }
    }
    foreach ($name in $environmentBackup.Keys) {
        [Environment]::SetEnvironmentVariable($name, $environmentBackup[$name], 'Process')
    }
    if ($lockHeld) { $mutex.ReleaseMutex() }
    $mutex.Dispose()
}
exit $exitCode
