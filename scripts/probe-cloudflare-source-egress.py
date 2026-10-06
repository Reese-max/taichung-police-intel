#!/usr/bin/env python3
"""Create an expiring private diagnostic Worker, probe fixed origins, then delete it."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import sys
import time

import requests

ROOT = Path(__file__).resolve().parents[1]
ACCOUNT = "03d40efd3c60181edea80af8d927ddc1"
API = f"https://api.cloudflare.com/client/v4/accounts/{ACCOUNT}"


def main():
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    if os.environ.get("GITHUB_REF") != "refs/heads/main" or not re.fullmatch(r"\d+", run_id):
        raise SystemExit("This diagnostic runs only from the main workflow.")
    credential = os.environ.get("CLOUDFLARE_API_TOKEN")
    if not credential:
        raise SystemExit("Cloudflare deployment credential is unavailable.")
    token = secrets.token_hex(32)
    attempt = os.environ.get("GITHUB_RUN_ATTEMPT", "1")
    if not re.fullmatch(r"\d+", attempt): raise SystemExit("Invalid workflow attempt")
    name = f"govintel-source-egress-probe-{run_id}-{attempt}"
    session = requests.Session()
    session.headers.update(Authorization=f"Bearer {credential}")
    receipt = {"schema_version":1,"scope":"TEMPORARY_AUTHENTICATED_TRANSPORT_PROBE_NOT_PRODUCTION",
               "code_sha":os.environ.get("GITHUB_SHA"),"workflow_run_id":run_id,"started_at":datetime.now(timezone.utc).isoformat(),
               "worker_name":name,"observations":[],"cleanup":"NOT_NEEDED","production_worker_modified":False,
               "source_promoted":False,"raw_origin_bytes_in_artifact":False}
    upload_attempted = False
    def api(method, path, **kwargs):
        response = session.request(method, API+path, timeout=(5,20), **kwargs)
        response.raise_for_status()
        result = response.json()
        if not result.get("success"):
            raise RuntimeError("Cloudflare API did not accept the diagnostic operation")
        return result["result"]
    try:
        source = (ROOT / "workers/source-egress-probe/src/index.js").read_bytes()
        receipt["probe_code_sha256"] = hashlib.sha256(source).hexdigest()
        expiry = int(time.time()*1000) + 10*60*1000
        metadata = {"main_module":"probe.js","compatibility_date":"2026-09-18",
                    "bindings":[{"name":"PROBE_TOKEN","type":"secret_text","text":token},
                                {"name":"EXPIRES_AT","type":"plain_text","text":str(expiry)}]}
        upload_attempted = True
        api("PUT", f"/workers/scripts/{name}", files={"metadata":(None,json.dumps(metadata),"application/json"), "probe.js":("probe.js",source,"application/javascript+module")})
        subdomain = api("GET", "/workers/subdomain")["subdomain"]
        if not re.fullmatch(r"[a-z0-9-]+",subdomain): raise ValueError("invalid account subdomain")
        api("POST", f"/workers/scripts/{name}/subdomain", json={"enabled":True,"previews_enabled":False})
        base = f"https://{name}.{subdomain}.workers.dev/probe"
        # Account API authentication never goes to a public Worker.
        with requests.Session() as client:
            for sid in ("S-029","S-001","S-019"):
                for retry in range(3):
                    response = client.get(base,params={"source":sid},headers={"Authorization":f"Bearer {token}"},timeout=(5,25),allow_redirects=False)
                    if response.status_code != 404 or retry == 2: break
                    time.sleep(2)
                response.raise_for_status()
                result = response.json()
                if result.get("source_id") != sid or result.get("scope") != "TRANSPORT_ONLY_NOT_SOURCE_RECOVERY": raise ValueError("unexpected diagnostic response")
                receipt["observations"].append(result)
        receipt["status"] = "PROBE_EXECUTED"
    except Exception as error:
        # No request headers, credential, response body or raw exception is printed.
        receipt.update(status="PROBE_FAILED",error_type=type(error).__name__)
        if isinstance(error,requests.HTTPError):receipt["failed_http_status"] = error.response.status_code
    finally:
        if upload_attempted:
            try:api("DELETE",f"/workers/scripts/{name}");receipt["cleanup"]="DELETED"
            except requests.HTTPError as error:
                if error.response.status_code == 404:receipt["cleanup"]="ALREADY_ABSENT"
                else:receipt.update(cleanup="DELETE_FAILED_EXPIRES_CLOSED",cleanup_error_type=type(error).__name__)
            except Exception as error:receipt.update(cleanup="DELETE_FAILED_EXPIRES_CLOSED",cleanup_error_type=type(error).__name__)
        session.close()
        receipt["finished_at"] = datetime.now(timezone.utc).isoformat()
        Path("source-egress-probe.json").write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+"\n")
        print(json.dumps({key:receipt[key] for key in ("status","cleanup","workflow_run_id")},ensure_ascii=False))
    return 0 if receipt["status"]=="PROBE_EXECUTED" and receipt["cleanup"]=="DELETED" else 1


if __name__ == "__main__":raise SystemExit(main())
