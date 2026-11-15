"""图层登记:JSON 持久化,记录数据源、坐标系、属性键、版本列表。"""
from __future__ import annotations

import json
import os
import threading

_lock = threading.Lock()


class Registry:
    def __init__(self, data_dir: str):
        self.data_dir = data_dir
        self.path = os.path.join(data_dir, "registry.json")
        os.makedirs(data_dir, exist_ok=True)
        if not os.path.exists(self.path):
            self._write({"layers": {}})

    def _read(self) -> dict:
        with open(self.path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _write(self, data: dict):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def list_layers(self) -> dict:
        return self._read()["layers"]

    def get(self, layer_id: str) -> dict | None:
        return self._read()["layers"].get(layer_id)

    def register(self, layer_id: str, *, name: str, path: str, fmt: str,
                 crs: str, geom_type: str, feature_count: int = 0,
                 bbox3857=None, attr_keys=None, merge_small: bool = False):
        with _lock:
            data = self._read()
            layers = data["layers"]
            existing = layers.get(layer_id, {})
            layers[layer_id] = {
                "id": layer_id,
                "name": name,
                "path": path,
                "format": fmt,
                "crs": crs,
                "geom_type": geom_type,
                "feature_count": feature_count,
                "bbox3857": bbox3857,
                "attr_keys": attr_keys or [],
                "merge_small": merge_small,
                "versions": existing.get("versions", []),
            }
            self._write(data)

    def add_version(self, layer_id: str, version: int):
        with _lock:
            data = self._read()
            layer = data["layers"][layer_id]
            if version not in layer["versions"]:
                layer["versions"].append(version)
                layer["versions"].sort()
            self._write(data)

    def update_path(self, layer_id: str, path: str, feature_count: int):
        with _lock:
            data = self._read()
            data["layers"][layer_id]["path"] = path
            data["layers"][layer_id]["feature_count"] = feature_count
            self._write(data)
