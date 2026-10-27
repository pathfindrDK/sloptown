#!/usr/bin/env python3
"""sloptown-healthcheck <url>: exit 0 if GET <url> answers 2xx within 2 s.

Exists so quadlet HealthCmd= lines need no shell quoting (quadlet mangles nested quotes).
"""
import sys
import urllib.request

try:
    with urllib.request.urlopen(sys.argv[1], timeout=2) as r:
        sys.exit(0 if 200 <= r.status < 300 else 1)
except Exception as e:  # noqa: BLE001 - any failure is unhealthy
    print(e, file=sys.stderr)
    sys.exit(1)
