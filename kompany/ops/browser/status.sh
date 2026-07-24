#!/bin/bash
# Print JSON status of all configured Kompany browser integrations.
# Output: [{"name":"linkedin","port":9335,"up":true,"ua":"Chrome/150..."}, ...]
set -u
python3 - <<PYEOF
import json, os, glob, subprocess, urllib.request
out = []
for cfg in sorted(glob.glob(os.path.expanduser("~/kompany-browser/config/*.env"))):
    name = os.path.splitext(os.path.basename(cfg))[0]
    env = {}
    for line in open(cfg):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line: continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip()
    port = env.get("KOMPANY_BROWSER_PORT")
    up, ua = False, ""
    if port:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=2) as r:
                ver = json.loads(r.read())
                up = True
                ua = ver.get("Browser", "")
        except Exception:
            pass
    out.append({"name": name, "port": int(port) if port else None, "up": up, "ua": ua})
print(json.dumps(out, indent=2))
PYEOF
