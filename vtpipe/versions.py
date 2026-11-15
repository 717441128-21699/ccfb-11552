"""瓦片版本管理与差异对比。"""
from __future__ import annotations

import json
import os
import shutil


def version_dir(store_dir: str, layer_id: str, version: int) -> str:
    return os.path.join(store_dir, "tiles", layer_id, f"v{version}")


def next_version(registry, layer_id: str) -> int:
    layer = registry.get(layer_id)
    versions = (layer or {}).get("versions", [])
    return (max(versions) + 1) if versions else 1


def copy_version(store_dir: str, layer_id: str, src_v: int, dst_v: int):
    """以上一版本为底,增量重切只覆盖受影响瓦片。"""
    src, dst = version_dir(store_dir, layer_id, src_v), version_dir(store_dir, layer_id, dst_v)
    if os.path.exists(dst):
        shutil.rmtree(dst)
    shutil.copytree(src, dst)


def load_manifest(store_dir: str, layer_id: str, version: int) -> dict:
    path = os.path.join(version_dir(store_dir, layer_id, version), "manifest.json")
    if not os.path.exists(path):
        return {"tiles": {}}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def merge_manifest(store_dir: str, layer_id: str, version: int, new_tiles: dict):
    """增量重切后,把新切的瓦片条目合并进清单。"""
    m = load_manifest(store_dir, layer_id, version)
    m.setdefault("tiles", {}).update(new_tiles)
    path = os.path.join(version_dir(store_dir, layer_id, version), "manifest.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(m, f, ensure_ascii=False)


def diff_versions(store_dir: str, layer_id: str, v1: int, v2: int) -> dict:
    """对比两个版本:变化的 / 新增的 / 删除的瓦片,以及要素数变化。"""
    m1 = load_manifest(store_dir, layer_id, v1).get("tiles", {})
    m2 = load_manifest(store_dir, layer_id, v2).get("tiles", {})
    changed, added, removed = [], [], []
    for key in sorted(m2):
        if key not in m1:
            added.append(key)
        elif m1[key]["sha1"] != m2[key]["sha1"]:
            changed.append({
                "tile": key,
                "features_before": m1[key]["features"],
                "features_after": m2[key]["features"],
                "size_before": m1[key]["size"],
                "size_after": m2[key]["size"],
            })
    for key in sorted(m1):
        if key not in m2:
            removed.append(key)
    return {
        "layer": layer_id, "v1": v1, "v2": v2,
        "tiles_v1": len(m1), "tiles_v2": len(m2),
        "changed": changed, "added": added, "removed": removed,
        "n_changed": len(changed), "n_added": len(added), "n_removed": len(removed),
    }
