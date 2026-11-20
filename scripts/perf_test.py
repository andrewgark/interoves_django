#!/usr/bin/env python3
"""
Production performance tests for slow pages.

Measures wall-clock response time for:
  1. /games/des167/tournament-results/  (anonymous)
  2. /admin/games/attempt/126704/change/ (admin login required)

Usage:
    python scripts/perf_test.py
    python scripts/perf_test.py --threshold 2.0   # fail if any page >N seconds
    python scripts/perf_test.py --runs 3           # average over N runs

Exit code 0 = all assertions passed, 1 = one or more failed.
"""

import argparse
import os
import sys
import time

import requests

BASE_URL = "https://interoves.com"
SECRETS_DIR = os.path.join(os.path.dirname(__file__), "..", "secrets")

TESTS = [
    {
        "name": "tournament results (des167)",
        "url": f"{BASE_URL}/games/des167/tournament-results/",
        "auth": None,
        "threshold_s": 3.0,
    },
    {
        "name": "admin attempt change (126704)",
        "url": f"{BASE_URL}/admin/games/attempt/126704/change/",
        "auth": "admin",
        "threshold_s": 3.0,
    },
]


def _read_secret(filename: str) -> str:
    path = os.path.join(SECRETS_DIR, filename)
    try:
        return open(path).read().strip()
    except FileNotFoundError:
        return ""


def _admin_session(username: str, password: str) -> requests.Session:
    """Log in to Django admin and return an authenticated session."""
    session = requests.Session()
    login_url = f"{BASE_URL}/admin/login/"

    # GET to obtain CSRF token
    r = session.get(login_url, timeout=15)
    r.raise_for_status()
    csrf = session.cookies.get("csrftoken")
    if not csrf:
        raise RuntimeError("No csrftoken cookie after GET /admin/login/")

    # POST credentials
    r = session.post(
        login_url,
        data={
            "username": username,
            "password": password,
            "csrfmiddlewaretoken": csrf,
            "next": "/admin/",
        },
        headers={"Referer": login_url},
        timeout=15,
        allow_redirects=True,
    )
    r.raise_for_status()
    if "csrftoken" not in session.cookies:
        raise RuntimeError("Login may have failed — no session cookie after POST")
    return session


def _measure(session: requests.Session, url: str, runs: int) -> dict:
    times = []
    status = None
    for _ in range(runs):
        t0 = time.perf_counter()
        r = session.get(url, timeout=30, allow_redirects=True)
        elapsed = time.perf_counter() - t0
        times.append(elapsed)
        status = r.status_code
    return {
        "status": status,
        "min": min(times),
        "max": max(times),
        "avg": sum(times) / len(times),
        "times": times,
    }


def main():
    parser = argparse.ArgumentParser(description="Production performance tests")
    parser.add_argument("--threshold", type=float, default=None,
                        help="Override threshold (seconds) for all tests")
    parser.add_argument("--runs", type=int, default=1,
                        help="Number of requests per test (default: 1)")
    args = parser.parse_args()

    admin_password = _read_secret("django_admin_password.txt")
    admin_session = None  # lazy init

    failed = []

    for test in TESTS:
        threshold = args.threshold if args.threshold is not None else test["threshold_s"]
        name = test["name"]
        url = test["url"]

        print(f"\n{'─'*60}")
        print(f"  {name}")
        print(f"  {url}")

        try:
            if test["auth"] == "admin":
                if admin_session is None:
                    print("  → logging in to admin ...")
                    admin_session = _admin_session("admin", admin_password)
                    print("  → logged in OK")
                session = admin_session
            else:
                session = requests.Session()

            result = _measure(session, url, args.runs)

            label_parts = [f"avg={result['avg']:.2f}s"]
            if args.runs > 1:
                label_parts += [f"min={result['min']:.2f}s", f"max={result['max']:.2f}s"]
            label = "  " + ",  ".join(label_parts)
            label += f"  (threshold: {threshold:.1f}s)  HTTP {result['status']}"

            ok = result["avg"] <= threshold
            if ok:
                print(f"  ✓ PASS  {label}")
            else:
                print(f"  ✗ FAIL  {label}")
                failed.append(name)

        except Exception as e:
            print(f"  ✗ ERROR  {e}")
            failed.append(name)

    print(f"\n{'─'*60}")
    if not failed:
        print(f"  All {len(TESTS)} test(s) passed.")
        sys.exit(0)
    else:
        print(f"  {len(failed)}/{len(TESTS)} test(s) FAILED: {', '.join(failed)}")
        sys.exit(1)


if __name__ == "__main__":
    main()
