Write-Host "Starting services (docker compose up)"

# Forward any provided environment (Docker Compose reads .env automatically)
docker compose up --build --force-recreate -d

Write-Host "Services started. Use scripts\down.ps1 to stop them." 
