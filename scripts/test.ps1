Write-Host "Running tests in the test image against compose Postgres"

docker compose run --rm --build test

Write-Host "Tests finished." 
