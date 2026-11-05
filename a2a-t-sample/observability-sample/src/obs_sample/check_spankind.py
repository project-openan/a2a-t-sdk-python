"""Check how Langfuse displays SpanKind from our A2A-T spans."""
import base64
import json
import urllib.request

pk = "pk-lf-661d8d9f-d5e1-4bd4-ae9f-68c7f1c93b8a"
sk = "sk-lf-ff9d6ca0-4eb8-4288-9839-eca9e5a12a8f"
auth = base64.b64encode(f"{pk}:{sk}".encode()).decode()
BASE = "https://us.cloud.langfuse.com"

req = urllib.request.Request(
    f"{BASE}/api/public/observations?limit=15",
    headers={"Authorization": f"Basic {auth}"},
)
with urllib.request.urlopen(req, timeout=30) as r:
    obs = json.loads(r.read())["data"]

print("=== Langfuse observations (latest 15) ===")
for o in obs:
    otype = o.get("type", "?")
    name = o.get("name", "?")
    span_kind = o.get("spanKind", "?")
    level = o.get("level", "?")
    parent = str(o.get("parentObservationId", "None"))[:12]
    print(f"  type={otype:8}  spanKind={str(span_kind):10}  parent={parent:14}  name={name[:55]}")

print("\n=== traces ===")
req2 = urllib.request.Request(
    f"{BASE}/api/public/traces?limit=5",
    headers={"Authorization": f"Basic {auth}"},
)
with urllib.request.urlopen(req2, timeout=30) as r:
    traces = json.loads(r.read())["data"]
for t in traces[:5]:
    print(f"  {t['id'][:16]}...  name={t.get('name','?')}")
