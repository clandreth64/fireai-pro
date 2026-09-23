# FireAI ground-truth review — one-command launcher (Windows / PowerShell).
#   powershell -ExecutionPolicy Bypass -File scripts\review.ps1
# Needs Docker Desktop running. Builds the fireai:dev image the first time (several minutes),
# then serves the review tool on http://127.0.0.1:8765 (this computer only) and opens it.
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$image = "fireai:dev"
docker image inspect $image *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Building $image (first run only)..."
    docker build -f "$repo\docker\Dockerfile" -t $image $repo
    if ($LASTEXITCODE -ne 0) { throw "docker build failed" }
}
Start-Job -ScriptBlock { Start-Sleep -Seconds 4; Start-Process "http://127.0.0.1:8765" } | Out-Null
Write-Host "Review tool: http://127.0.0.1:8765   (press Ctrl+C here to stop)"
# root inside the container so reviews can be written to the mounted repository folder;
# the port is published on 127.0.0.1 only.
docker run --rm -it --user root -p 127.0.0.1:8765:8765 -v "${repo}:/app" -w /app $image `
    python scripts/gt_review_server.py --host 0.0.0.0 --port 8765
