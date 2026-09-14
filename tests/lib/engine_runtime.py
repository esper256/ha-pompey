"""Digest-keyed upstream artifacts for tests; never starts a torrent client."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'pompey/rootfs/usr/local/bin'))
import engine_manager


def artifact(name):
    manifest=json.loads(engine_manager.manifest_path().read_text())
    entry=manifest['engines'][name]
    identity=hashlib.sha256((json.dumps(entry,sort_keys=True)+engine_manager.target()).encode()).hexdigest()
    root=Path(os.environ.get('POMPEY_ARTIFACT_CACHE', Path.home()/'.cache/pompey/verified'))
    root.mkdir(parents=True,exist_ok=True)
    cached=root/(name+'-'+identity)
    if cached.exists():return cached
    with tempfile.TemporaryDirectory(prefix='.stage-',dir=root) as temp:
        staged=engine_manager.stage(name,entry,Path(temp)/name)
        staged.replace(cached)
    return cached
