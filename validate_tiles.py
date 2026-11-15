"""用 mapbox 官方 vector-tile 解析库逐块校验产物瓦片。

检查:能解析、图层/要素结构完整、坐标在 extent+缓冲范围内、文件未超 500KB。
运行: py -3.12 validate_tiles.py [tiles_dir]
"""
from __future__ import annotations

import os
import sys

from mapbox_vector_tile import decode as mvt_decode

MAX_TILE_BYTES = 500 * 1024
BUFFER = 64


def validate_dir(tiles_dir: str) -> int:
    n_files = n_features = 0
    bad = []
    oversize = []
    sizes = []
    for root, _, files in os.walk(tiles_dir):
        for fn in files:
            if not fn.endswith(".pbf"):
                continue
            path = os.path.join(root, fn)
            rel = os.path.relpath(path, tiles_dir)
            size = os.path.getsize(path)
            sizes.append(size)
            if size > MAX_TILE_BYTES:
                oversize.append((rel, size))
            n_files += 1
            try:
                with open(path, "rb") as f:
                    tile = mvt_decode(f.read())
            except Exception as e:  # noqa: BLE001
                bad.append((rel, f"解析失败: {e}"))
                continue
            for lname, layer in tile.items():
                if layer.get("version") != 2:
                    bad.append((rel, f"{lname}: version={layer.get('version')}"))
                extent = layer.get("extent", 4096)
                for feat in layer.get("features", []):
                    if "geometry" not in feat or "type" not in feat["geometry"]:
                        bad.append((rel, f"{lname}: 要素缺几何"))
                        break
                    n_features += 1
                    # 坐标应落在 [-BUFFER, extent+BUFFER] 内(允许 1 单位舍入)
                    def coords(g):
                        t = g["type"]
                        c = g["coordinates"]
                        if t == "Point":
                            yield c
                        elif t in ("MultiPoint", "LineString"):
                            yield from c
                        elif t in ("MultiLineString", "Polygon"):
                            for part in c:
                                yield from part
                        elif t == "MultiPolygon":
                            for poly in c:
                                for ring in poly:
                                    yield from ring
                    for pt in coords(feat["geometry"]):
                        for v in pt[:2]:
                            if v < -BUFFER - 1 or v > extent + BUFFER + 1:
                                bad.append((rel, f"{lname}: 坐标越界 {pt}"))
                                break
    print(f"校验 {n_files} 块瓦片, {n_features:,} 个要素")
    if sizes:
        sizes.sort()
        print(f"瓦片大小: 中位 {sizes[len(sizes)//2]/1024:.1f}KB, "
              f"最大 {sizes[-1]/1024:.1f}KB")
    if oversize:
        print(f"⚠ {len(oversize)} 块超过 500KB(应已触发降精度告警):")
        for rel, s in oversize[:10]:
            print(f"   {rel}  {s/1024:.0f}KB")
    if bad:
        print(f"✘ {len(bad)} 处不合法:")
        for rel, msg in bad[:20]:
            print(f"   {rel}: {msg}")
        return 1
    print("✔ 全部瓦片合法")
    return 0


if __name__ == "__main__":
    sys.exit(validate_dir(sys.argv[1] if len(sys.argv) > 1
                          else os.path.join("data", "tiles")))
