#!/usr/bin/env python3
"""Download pinned Mac model artifacts; never modify firmware dependencies."""
import hashlib
import json
from pathlib import Path

from huggingface_hub import snapshot_download
import requests

root = Path(__file__).resolve().parents[1]
manifest = json.loads((root/'emotion/models.json').read_text())
model = manifest['vision_language']
snapshot_download(model['repo_id'], revision=model['revision'], local_dir=root/model['local_dir'])
face = manifest['face_detector']
target = root/face['local_file']
if not target.exists() or hashlib.sha256(target.read_bytes()).hexdigest() != face['sha256']:
    response = requests.get(face['url'], timeout=60)
    response.raise_for_status()
    if hashlib.sha256(response.content).hexdigest() != face['sha256']:
        raise SystemExit('YuNet checksum mismatch; existing model not overwritten')
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(response.content)
print('Pinned models ready; run the local smoke tests before deployment.')
