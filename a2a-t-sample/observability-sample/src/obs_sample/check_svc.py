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
