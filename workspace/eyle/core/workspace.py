"""Discover and open the live source workspace directly."""
from __future__ import annotations
import os

def discover_project(base_dir: str):
    candidates=[os.path.join(base_dir,"workspace"), base_dir]
    for path in candidates:
        if not os.path.isdir(path): continue
        if path == base_dir and not any(os.path.isfile(os.path.join(path,n)) for n in ("app.py","main.py","pyproject.toml","package.json")):
            continue
        return {"caminho_origem":os.path.realpath(path),"nome":os.path.basename(os.path.realpath(path)),"auto_discovered":True}
    return None
