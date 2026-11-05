"""Check latest 5 traces with observations and service.name."""
import base64
import json
import urllib.request

pk = "pk-lf-661d8d9f-d5e1-4bd4-ae9f-68c7f1c93b8a"
sk = "sk-lf-ff9d6ca0-4eb8-4288-9839-eca9e5a12a8f"
auth = base64.b64encode(f"{pk}:{sk}".encode()).decode()
BASE = "https://us.cloud.langfuse.com"

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
