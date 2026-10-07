#!/usr/bin/env python3
"""Read-only checks of the maintained Pages origin and its PAPER backend.

No authentication credentials, private accounts, or control endpoints are used.
Exit 0 requires correct CORS and the repaired shared PAPER training schema;
it does not prove private account access, reset completion, or financial edge.
"""
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import requests

PAGES = "https://angelmalevbiz-debug.github.io/neomemecoins/"
BACKEND = "https://neo-meme-api.169-58-211-177.sslip.io"
ORIGIN = "https://angelmalevbiz-debug.github.io"
SUPPORTED_ENTRY_POLICIES = {"ORDER_FLOW_VALIDATED_THRESHOLDS_V9", "WINNER_ENSEMBLE_ENTRY_V1"}


def check(backend=BACKEND, origin=ORIGIN, shared_backend=None):
    shared_backend = shared_backend or backend
    report = {"checked_at_utc": datetime.now(timezone.utc).isoformat(),
              "pages": PAGES, "backend": backend, "shared_backend": shared_backend, "origin": origin,
              "read_only": True, "authenticated_dashboard": "NOT_CHECKED",
              "all_account_reset": "NOT_PROVEN", "probes": []}
    with requests.Session() as http:
        http.headers.update({"User-Agent": "NEO-Pages-Backend-ReadOnly/1.0"})
        for method, path, expected in (
            ("OPTIONS", "/user/state", 204),
            ("GET", "/user/health", 200),
            ("GET", "/user/state", 401),
            ("GET", "/state", 200),
        ):
            base_url = shared_backend if path == "/state" else backend
            headers = {"Origin": origin}
            if method == "OPTIONS":
                headers.update({"Access-Control-Request-Method": "GET",
                                "Access-Control-Request-Headers": "authorization"})
            probe = {"method": method, "path": path, "expected_status": expected}
            try:
                response = http.request(method, base_url.rstrip("/") + path,
                                        headers=headers, timeout=(5, 12), allow_redirects=False)
                actual_origin = response.headers.get("Access-Control-Allow-Origin")
                probe.update(http_status=response.status_code, allowed_origin=actual_origin,
                             status_ok=response.status_code == expected,
                             cors_origin_ok=actual_origin == origin)
                if method == "OPTIONS":
                    allowed_headers = {h.strip().lower() for h in response.headers.get(
                        "Access-Control-Allow-Headers", "").split(",")}
                    probe["authorization_header_allowed"] = "authorization" in allowed_headers
                if path == "/state" and response.status_code == 200:
                    state = response.json()
                    config = state.get("config", {})
                    report["runtime"] = {
                        "entry_policy_version": config.get("entry_policy_version"),
                        "paper_only": config.get("paper_only"),
                        "paper_training_present": isinstance(state.get("paper_training"), dict),
                    }
            except (requests.RequestException, ValueError) as exc:
                probe.update(status_ok=False, cors_origin_ok=False,
                             error=type(exc).__name__)
            report["probes"].append(probe)
    # /state is a shared read-only diagnostic, not an authenticated fallback.
    user_probes = report["probes"][:3]
    report["user_gateway_cors_ready"] = all(p.get("status_ok") and p.get("cors_origin_ok")
                                            for p in user_probes) and bool(
                                                user_probes[0].get("authorization_header_allowed"))
    runtime = report.get("runtime", {})
    report["repaired_shared_backend_ready"] = bool(
        report["probes"][-1].get("status_ok") and runtime.get("paper_only") is True
        and runtime.get("paper_training_present")
        and runtime.get("entry_policy_version") in SUPPORTED_ENTRY_POLICIES)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", default=BACKEND,
                        help="Authenticated per-user gateway base URL")
    parser.add_argument("--shared-backend", default=None,
                        help="PAPER monitor base URL; defaults to --backend")
    parser.add_argument("--origin", default=ORIGIN)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    parsed = urlsplit(args.backend)
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        parser.error("Backend must be a base URL without credentials, query, or fragment")
    report = check(args.backend, args.origin, args.shared_backend)
    serialized = json.dumps(report, indent=2, ensure_ascii=False)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized + "\n", encoding="utf-8")
    print(serialized)
    return int(not (report["user_gateway_cors_ready"] and report["repaired_shared_backend_ready"]))


if __name__ == "__main__":
    raise SystemExit(main())
