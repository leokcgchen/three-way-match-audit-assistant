$ErrorActionPreference = "Stop"
$Image = "docker.aityp.com/chromadb/chroma:latest"
$Compose = Join-Path $PSScriptRoot "docker-compose.yml"

$docker = Get-Command docker -ErrorAction SilentlyContinue
if (-not $docker) {
    $guess = "C:\Program Files\Docker\Docker\resources\bin\docker.exe"
    if (Test-Path -LiteralPath $guess) {
        $dockerCmd = $guess
    } else {
        Write-Host "Docker 未安装或不在 PATH。安装 Docker Desktop 后执行："
        Write-Host "  docker pull $Image"
        Write-Host "  docker compose -f `"$Compose`" up -d"
        exit 1
    }
} else {
    $dockerCmd = $docker.Source
}

& $dockerCmd pull $Image
if ($LASTEXITCODE -ne 0) {
    Write-Host "latest 拉取失败，改试 docker.aityp.com/docker.io/chromadb/chroma:latest"
    $Image = "docker.aityp.com/docker.io/chromadb/chroma:latest"
    & $dockerCmd pull $Image
}
& $dockerCmd compose -f $Compose up -d
Write-Host "Chroma HTTP: http://127.0.0.1:8000"
