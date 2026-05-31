param(
    [string]$ApiUrl = "http://localhost:8000",
    [switch]$NoApi
)

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$PythonExe = Join-Path $Root "backend\venv\Scripts\python.exe"
$DetectScript = Join-Path $Root "backend\pipeline\detect.py"
$OutputDir = Join-Path $Root "events_output"

if (-not (Test-Path $PythonExe)) {
    Write-Error "Python executable not found: $PythonExe. Run .\start.bat first or install the virtual environment."
    exit 1
}

if (-not (Test-Path $OutputDir)) {
    New-Item -ItemType Directory -Path $OutputDir | Out-Null
}

$clips = @(
    @{ Path = Join-Path $Root "CCTV Footage-20260529T160731Z-3-00144614ea (1)\CCTV Footage\CAM 3.mp4"; Camera = 'CAM_ENTRY_01'; Role = 'entry'; Output = 'events_CAM_ENTRY_01.jsonl' },
    @{ Path = Join-Path $Root "CCTV Footage-20260529T160731Z-3-00144614ea (1)\CCTV Footage\CAM 1.mp4"; Camera = 'CAM_FLOOR_01'; Role = 'floor'; Output = 'events_CAM_FLOOR_01.jsonl' },
    @{ Path = Join-Path $Root "CCTV Footage-20260529T160731Z-3-00144614ea (1)\CCTV Footage\CAM 2.mp4"; Camera = 'CAM_FLOOR_02'; Role = 'floor'; Output = 'events_CAM_FLOOR_02.jsonl' },
    @{ Path = Join-Path $Root "CCTV Footage-20260529T160731Z-3-00144614ea (1)\CCTV Footage\CAM 4.mp4"; Camera = 'CAM_STORAGE_01'; Role = 'storage'; Output = 'events_CAM_STORAGE_01.jsonl' },
    @{ Path = Join-Path $Root "CCTV Footage-20260529T160731Z-3-00144614ea (1)\CCTV Footage\CAM 5.mp4"; Camera = 'CAM_BILLING_01'; Role = 'billing'; Output = 'events_CAM_BILLING_01.jsonl' }
)

foreach ($clip in $clips) {
    if (-not (Test-Path $clip.Path)) {
        Write-Error "Video not found: $($clip.Path)"
        exit 1
    }

    $outputPath = Join-Path $OutputDir $clip.Output
    Write-Host "Processing $($clip.Camera) ($($clip.Role))..."
    $args = @(
        $DetectScript,
        '--clip', $clip.Path,
        '--camera', $clip.Camera,
        '--role', $clip.Role,
        '--output', $outputPath,
        '--skip', '3',
        '--conf', '0.35'
    )

    if (-not $NoApi) {
        $args += @('--api', $ApiUrl)
    }

    & $PythonExe @args
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Processing failed for $($clip.Camera). See output above."
        exit $LASTEXITCODE
    }

    Write-Host "  ✓ Finished $($clip.Camera)"
    Write-Host ""
}

Write-Host "All clips processed. Events saved to: $OutputDir"
