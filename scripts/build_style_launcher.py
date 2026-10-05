#!/usr/bin/env python3
"""Prepare a pinned template command without changing a Runpod template."""

import argparse
import base64
import hashlib
import json
from pathlib import Path
import re
import shlex
import subprocess

REMOTE_PREFIX = "https://raw.githubusercontent.com/trickcrafterites-create/api-lora-test.json/"
SOURCES = {"scripts/style_bootstrap.py": "style_bootstrap.py", "catalog/style-loras.json": "style-loras.json"}

LAUNCHER = '''import hashlib,json,os,pathlib,sys,tempfile,urllib.request
PINS=__PINS__
class NoRedirects(urllib.request.HTTPRedirectHandler):
 def redirect_request(self,req,fp,code,msg,headers,newurl):
  raise RuntimeError("redirect refused")
def original():
 os.environ.pop("AELIX_STYLE_BROKER_TOKEN",None)
 os.execv("/start.sh",["/start.sh"])
if os.environ.get("AELIX_STYLE_BOOTSTRAP")!="1":
 original()
try:
 installer=pathlib.Path("/opt/character-loras/download_loras.py")
 if hashlib.sha256(installer.read_bytes()).hexdigest()!=PINS["installerSha256"]:
  raise RuntimeError("existing verifier mismatch")
 opener=urllib.request.build_opener(NoRedirects())
 verified={}
 for source in PINS["sources"]:
  expected="https://raw.githubusercontent.com/trickcrafterites-create/api-lora-test.json/"+PINS["revision"]+"/"+source["path"]
  if source["url"]!=expected or source["path"] not in ("scripts/style_bootstrap.py","catalog/style-loras.json"):
   raise RuntimeError("source origin mismatch")
  request=urllib.request.Request(source["url"],headers={"Accept":"application/octet-stream","User-Agent":"aelix-style-launcher/1.0"})
  with opener.open(request,timeout=15) as response:
   if response.status!=200:
    raise RuntimeError("source unavailable")
   data=response.read(262145)
  if len(data)>262144 or hashlib.sha256(data).hexdigest()!=source["sha256"]:
   raise RuntimeError("source integrity mismatch")
  verified[source["filename"]]=data
 directory=pathlib.Path(tempfile.mkdtemp(prefix="aelix-style-bootstrap-"))
 for name,data in verified.items():
  (directory/name).write_bytes(data)
 sys.path.insert(0,"/opt/character-loras")
 filename=str(directory/"style_bootstrap.py")
 exec(compile(verified["style_bootstrap.py"],filename,"exec"),{"__name__":"__main__","__file__":filename})
except Exception as error:
 print("Aelix style launcher unavailable: "+type(error).__name__+". Starting existing worker.",file=sys.stderr,flush=True)
 original()
'''


def manifest(revision, blobs):
    if not re.fullmatch(r"[a-f0-9]{40}", revision):
        raise ValueError("A full lowercase Git commit SHA is required")
    if set(blobs) != {*SOURCES, "scripts/download_loras.py"}:
        raise ValueError("Unexpected source set")
    return {"revision": revision,
            "installerSha256": hashlib.sha256(blobs["scripts/download_loras.py"]).hexdigest(),
            "sources": [{"path": path, "filename": filename, "url": REMOTE_PREFIX + revision + "/" + path,
                         "sha256": hashlib.sha256(blobs[path]).hexdigest()} for path, filename in SOURCES.items()]}


def command(pins):
    source = LAUNCHER.replace("__PINS__", repr(pins))
    encoded = base64.b64encode(source.encode()).decode()
    argv = ["python", "-c", "import base64;exec(base64.b64decode('" + encoded + "'))"]
    return {"revision": pins["revision"], "dockerArgs": shlex.join(argv), "argv": argv,
            "pins": pins, "launcherSource": source}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if not re.fullmatch(r"[a-f0-9]{40}", args.revision):
        parser.error("--revision must be a full lowercase Git commit SHA")
    root = Path(__file__).resolve().parents[1]
    blobs = {path: subprocess.check_output(["git", "show", args.revision + ":" + path], cwd=root)
             for path in [*SOURCES, "scripts/download_loras.py"]}
    result = command(manifest(args.revision, blobs))
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print("Prepared pinned startup command; no remote configuration changed.")


if __name__ == "__main__":
    main()
