import argparse
import json
import os
import sys
from urllib import error, request


def fetch_json(url: str, headers: dict[str, str] | None = None) -> dict:
    req = request.Request(url, headers=headers or {})
    with request.urlopen(req, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke test the Kasparro API")
    parser.add_argument("--base-url", default=os.getenv("BASE_URL", "http://localhost:8000"))
    parser.add_argument("--api-key", default=os.getenv("APP_API_KEY"))
    args = parser.parse_args()

    if not args.api_key:
        print("APP_API_KEY is required", file=sys.stderr)
        return 1

    base_url = args.base_url.rstrip("/")
    headers = {"X-API-KEY": args.api_key}

    try:
        health = fetch_json(f"{base_url}/health")
        data = fetch_json(f"{base_url}/data", headers=headers)
        stats = fetch_json(f"{base_url}/stats", headers=headers)
    except error.URLError as exc:
        print(f"Smoke test failed: {exc}", file=sys.stderr)
        return 1

    if health.get("status") not in {"ok", "degraded"}:
        print(f"Unexpected health status: {health}", file=sys.stderr)
        return 1

    if "request_id" not in data or "meta" not in data:
        print(f"Unexpected /data response: {data}", file=sys.stderr)
        return 1

    if "etl" not in stats or "price_stats" not in stats:
        print(f"Unexpected /stats response: {stats}", file=sys.stderr)
        return 1

    print("Smoke test passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())