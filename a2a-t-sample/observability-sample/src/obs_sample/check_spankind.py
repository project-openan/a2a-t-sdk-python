# Copyright (c) 2026 Huawei Technologies Co., Ltd.
# All Rights Reserved.
#
# SPDX-License-Identifier: Apache-2.0
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

"""Check how Langfuse displays SpanKind from our A2A-T spans."""
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
