"""Check how Langfuse distinguishes client vs server — look at trace_id and resource attrs."""
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

req = urllib.request.Request(
    f"{BASE}/api/public/traces?limit=2",
    headers={"Authorization": f"Basic {auth}"},
)
with urllib.request.urlopen(req, timeout=30) as r:
    traces = json.loads(r.read())["data"]

for t in traces[:2]:
    tid = t["id"]
    print(f"=== trace {tid[:16]}... name={t.get('name','?')} ===")
    # Get all observations for this trace
    req2 = urllib.request.Request(
        f"{BASE}/api/public/observations?traceId={tid}&limit=50",
        headers={"Authorization": f"Basic {auth}"},
    )
    with urllib.request.urlopen(req2, timeout=30) as r:
        obs = json.loads(r.read())["data"]
    for o in obs:
        ra = o.get("metadata", {}).get("resourceAttributes", {})
        svc = ra.get("service.name", "?")
        siid = str(ra.get("service.instance.id", "?"))[:8]
        name = o.get("name", "?")
        parent = str(o.get("parentObservationId", "ROOT"))
        print(f"  {name:50}  svc={svc:15}  svc-instance={siid}  parent={parent[:8]}...")
    print()
