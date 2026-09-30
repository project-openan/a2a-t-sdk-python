"""Verify Langfuse cloud received traces — with observation-level detail."""
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

# Traces
traces = api("/api/public/traces?limit=10")["data"]
print(f"=== traces in Langfuse cloud: {len(traces)} ===")
for t in traces[:6]:
    print(f"  {t['id'][:16]}...  name={t.get('name', '?')}  ts={t.get('timestamp', '?')[:19]}")

# Find the latest SendStreamingMessage trace and get its observations
target = next((t for t in traces if t.get("name") == "SendStreamingMessage"), None)
if target:
    tid = target["id"]
    print(f"\n=== observations for trace {tid[:16]}... ===")
    obs_data = api(f"/api/public/observations?traceId={tid}&limit=50")
    obs = obs_data.get("data", [])
    print(f"observations: {len(obs)}")
    for o in obs:
        parent = str(o.get("parentObservationId", "?"))[:8]
        print(f"  {o.get('type', '?'):10}  {o.get('name', '?'):45}  parent={parent}...")

# Find negotiation spans
neg_traces = [t for t in traces if "negotiation" in str(t.get("name", "")).lower()]
neg_obs = api("/api/public/observations?name=SendStreamingMessage-negotiation&limit=5")["data"]
print(f"\n=== negotiation spans in Langfuse: {len(neg_obs)} ===")
for o in neg_obs[:3]:
    attrs = o.get("attributes", {})
    print(f"  name={o.get('name')}  negotiation.id={attrs.get('gen_ai.agent.a2at.negotiation.id', '?')}"
          f"  round={attrs.get('gen_ai.agent.a2at.negotiation.round', '?')}"
          f"  performative={attrs.get('gen_ai.agent.a2at.negotiation.performative', '?')}")

# Metrics check
metrics = api("/api/public/metrics?limit=10")
print(f"\n=== (metrics endpoint status: {len(metrics.get('data', []))} items) ===")
