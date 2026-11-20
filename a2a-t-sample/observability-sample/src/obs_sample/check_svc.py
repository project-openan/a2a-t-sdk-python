"""Check latest 5 traces with observations and service.name."""
import base64
import json
import os
import urllib.request

pk = os.environ.get("LANGFUSE_PUBLIC_KEY", "")
sk = os.environ.get("LANGFUSE_SECRET_KEY", "")
if not pk or not sk:
    raise SystemExit("Set LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY (optionally LANGFUSE_BASE_URL).")
auth = base64.b64encode(f"{pk}:{sk}".encode()).decode()
BASE = os.environ.get("LANGFUSE_BASE_URL", "https://us.cloud.langfuse.com").rstrip("/")

def api(path):
    req = urllib.request.Request(f"{BASE}{path}", headers={"Authorization": f"Basic {auth}"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())

traces = api("/api/public/traces?limit=5")["data"]
for t in traces:
    tid = t["id"]
    name = t.get("name", "?")
    print(f"=== trace {tid[:16]}... name={name} ===")
    obs = api(f"/api/public/observations?traceId={tid}&limit=50")["data"]
    for o in obs:
        ra = o.get("metadata", {}).get("resourceAttributes", {})
        svc = ra.get("service.name", "?")
        print(f"  {o.get('name', '?'):50}  svc={svc}")
    print()
