Write-Host "Running tests inside api service (docker compose run --rm api pytest)"

docker compose run --rm api pytest -q

Write-Host "Tests finished." 
