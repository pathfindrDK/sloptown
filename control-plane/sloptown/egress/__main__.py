"""`python -m sloptown.egress` runs the proxy on :8200 (single worker; limits are in-memory)."""

from .app import main

main()
