"""End-to-end smoke test against a running deployment.

    python scripts/smoke_test.py --base-url https://<service>.run.app --api-key "$APP_API_KEY"

Checks, in order (each prints PASS/FAIL/SKIP):

1. ingest      ``/health`` is ok/degraded, DB up, at least one source has a successful run
2. data        ``/data`` returns rows with ``request_id`` and ``api_latency_ms``
3. stats       ``/stats`` reports processed records from ``etl_runs``
4. metrics     ``/metrics`` (keyed) is Prometheus text and contains no API key
5. resume      (only with ``--crash-cmd`` and ``--resume-cmd``) the crash command kills an
               ETL run mid-way; after the resume command the crashed run is ``failed``
               (abandoned/injected) and a newer run of the same source succeeded and reused
               the checkpoint (``records_unchanged > 0``)
6. ratelimit   (skip with ``--skip-rate-limit``) hammering ``/runs`` yields 429 + Retry-After.
               This uses up the key's budget for the current window, so it runs last.

Commands are run through the shell, e.g. for docker compose::

    --crash-cmd  "docker compose run --rm -e ETL_SOURCES=coinpaprika -e ETL_BATCH_SIZE=10 \
                  -e FAIL_AFTER_N=25 -e FAIL_MODE=exit api python scripts/run_etl.py"
    --resume-cmd "docker compose run --rm -e ETL_SOURCES=coinpaprika api python scripts/run_etl.py"

and for Cloud Run jobs see docs/DEPLOY_GCP.md. Exit code is non-zero if any check fails.
"""

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any


class SmokeFailure(Exception):
    pass


class Client:
    def __init__(self, base_url: str, api_key: str, timeout: float = 20.0):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    def get(self, path: str, auth: bool = True) -> tuple[int, dict[str, str], bytes]:
        headers = {"X-API-KEY": self.api_key} if auth else {}
        req = urllib.request.Request(self.base_url + path, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return resp.status, dict(resp.headers), resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, dict(exc.headers), exc.read()

    def json(self, path: str, auth: bool = True, ok: tuple[int, ...] = (200,)) -> Any:
        status, _, body = self.get(path, auth)
        if status not in ok:
            raise SmokeFailure(f"GET {path} -> HTTP {status}: {body[:200]!r}")
        return json.loads(body)


def check_ingest(c: Client) -> str:
    health = c.json("/health", auth=False, ok=(200,))
    if health.get("status") not in {"ok", "degraded"} or not health.get("db"):
        raise SmokeFailure(f"unhealthy: {health}")
    good = [s["source"] for s in health["etl"]["sources"] if s["status"] in ("success", "partial")]
    if not good:
        raise SmokeFailure(f"no successful ETL run yet: {health['etl']['sources']}")
    return f"status={health['status']} sources ok={good}"


def check_data(c: Client) -> str:
    body = c.json("/data?limit=5&sort=market_cap")
    if not body.get("request_id") or "api_latency_ms" not in body or not body.get("data"):
        raise SmokeFailure(f"unexpected /data body: {str(body)[:200]}")
    return f"total_records={body['meta']['total_records']} latency_ms={body['api_latency_ms']}"


def check_stats(c: Client) -> str:
    etl = c.json("/stats")["etl"]
    if etl["records_processed"] <= 0 or etl["total_runs"] <= 0:
        raise SmokeFailure(f"no processed records: {etl}")
    return f"records_processed={etl['records_processed']} runs={etl['total_runs']}"


def check_metrics(c: Client) -> str:
    status, headers, body = c.get("/metrics")
    text = body.decode()
    if status != 200 or "kasparro_etl_runs_total" not in text:
        raise SmokeFailure(f"/metrics -> {status}")
    if c.api_key and c.api_key in text:
        raise SmokeFailure("/metrics leaks the API key")
    return f"{len(text.splitlines())} lines"


def _shell(cmd: str) -> int:
    print(f"    $ {cmd}", flush=True)
    return subprocess.run(cmd, shell=True, env=os.environ.copy()).returncode  # noqa: S602 - operator-supplied


def check_resume(c: Client, crash_cmd: str, resume_cmd: str, source: str) -> str:
    before = {r["id"] for r in c.json(f"/runs?limit=100&source={source}")["runs"]}
    code = _shell(crash_cmd)
    if code == 0:
        raise SmokeFailure("crash command exited 0; expected the injected crash to fail it")
    if _shell(resume_cmd) != 0:
        raise SmokeFailure("resume command failed")
    new = [r for r in c.json(f"/runs?limit=100&source={source}")["runs"] if r["id"] not in before]
    crashed = [r for r in new if r["status"] == "failed"]
    resumed = [r for r in new if r["status"] in ("success", "partial")]
    if not crashed or not resumed:
        raise SmokeFailure(f"expected a failed and a successful {source} run, got {[r['status'] for r in new]}")
    latest, dead = resumed[0], crashed[-1]
    if latest["id"] < dead["id"] or latest["records_unchanged"] <= 0:
        raise SmokeFailure(f"resume did not reuse the checkpoint: {latest}")
    return (
        f"crashed run {dead['id']} ({(dead['error'] or '')[:40]}), committed={dead['records_in']}; "
        f"resumed run {latest['id']}: unchanged={latest['records_unchanged']} inserted={latest['records_inserted']}"
    )


def check_rate_limit(c: Client, max_requests: int) -> str:
    for i in range(1, max_requests + 1):
        status, headers, _ = c.get("/runs?limit=1")
        if status == 429:
            retry = headers.get("Retry-After") or headers.get("retry-after")
            if not retry:
                raise SmokeFailure("429 without Retry-After")
            return f"429 after {i} requests, Retry-After={retry}s"
        if status != 200:
            raise SmokeFailure(f"/runs -> HTTP {status}")
    raise SmokeFailure(f"no 429 within {max_requests} requests")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-url", default=os.getenv("BASE_URL", "http://localhost:8001"))
    parser.add_argument("--api-key", default=os.getenv("APP_API_KEY"))
    parser.add_argument("--crash-cmd", help="shell command that runs the ETL with FAIL_AFTER_N + FAIL_MODE=exit")
    parser.add_argument("--resume-cmd", help="shell command that runs the ETL normally")
    parser.add_argument("--resume-source", default="coinpaprika", help="source the crash/resume commands run")
    parser.add_argument("--skip-rate-limit", action="store_true")
    parser.add_argument("--max-requests", type=int, default=200)
    args = parser.parse_args(argv)
    if not args.api_key:
        print("APP_API_KEY / --api-key is required", file=sys.stderr)
        return 2

    c = Client(args.base_url, args.api_key)
    checks: list[tuple[str, Callable[[], str] | None]] = [
        ("ingest", lambda: check_ingest(c)),
        ("data", lambda: check_data(c)),
        ("stats", lambda: check_stats(c)),
        ("metrics", lambda: check_metrics(c)),
        (
            "resume",
            (lambda: check_resume(c, args.crash_cmd, args.resume_cmd, args.resume_source))
            if args.crash_cmd and args.resume_cmd
            else None,
        ),
        ("ratelimit", None if args.skip_rate_limit else (lambda: check_rate_limit(c, args.max_requests))),
    ]
    failed = 0
    for name, check in checks:
        if check is None:
            print(f"SKIP {name}")
            continue
        try:
            print(f"PASS {name}: {check()}", flush=True)
        except (SmokeFailure, OSError, ValueError, KeyError) as exc:
            failed += 1
            print(f"FAIL {name}: {exc}", flush=True)
    print("Smoke test passed" if not failed else f"Smoke test FAILED ({failed} check(s))")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
