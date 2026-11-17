#!/usr/bin/env python3
"""Ping the health endpoint of a running ReelRelay instance.

Usage:
    python health_check.py [http://127.0.0.1:8080/health]
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request


def check_health(url: str = "http://127.0.0.1:8080/health") -> bool:
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        print(f"Application returned HTTP {exc.code}")
        return False
    except Exception as exc:  # noqa: BLE001 - CLI helper
        print(f"Health check failed: {exc}")
        return False

    if payload.get("status") != "healthy":
        print(json.dumps(payload, indent=2))
        return False

    print("Application is healthy")
    print(json.dumps(payload, indent=2))
    return True


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8080/health"
    sys.exit(0 if check_health(target) else 1)
