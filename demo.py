"""端到端演示:造数据 -> 登记 -> 全量切片 -> 校验 -> 局部变更 -> 增量重切 -> 版本对比。

运行: py -3.12 demo.py
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import datagen
from vtpipe.pipeline import Tiler
from vtpipe.readers import layer_bbox, read_features
from vtpipe.registry import Registry
from vtpipe import versions as vm
from vtpipe.incremental import affected_tiles_from_bbox
from validate_tiles import validate_dir

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
SRC = os.path.join(DATA, "source")

# 图层登记表:id / 名称 / 文件 / 格式 / 坐标系 / 几何类型 / 切片级别
LAYERS = [
    ("parcels",   "宗地",   "parcels.shp",        "shp",     "CGCS2000",     "Polygon",    15, 18),
    ("zones",     "控规单元", "zones.shp",          "shp",     "CGCS2000",     "Polygon",     8, 18),
    ("roads",     "道路",   "roads.geojson",      "geojson", "XIAN80_GK_114", "LineString", 10, 18),
    ("pipelines", "管线",   "pipelines.geojson",  "geojson", "CGCS2000",     "LineString", 12, 18),
]


def register_all(registry):
    for lid, name, fn, fmt, crs, gtype, zmin, zmax in LAYERS:
        path = os.path.join(SRC, fn)
        geoms, props = read_features(path, crs, fmt, limit=2000)
        keys = sorted({k for p in props for k in p})
        # 全量要素数
        if fmt == "shp":
            import shapefile
            count = len(shapefile.Reader(path))
        else:
            import json
            with open(path, encoding="utf-8") as f:
                count = len(json.load(f)["features"])
        registry.register(lid, name=name, path=path, fmt=fmt, crs=crs,
                          geom_type=gtype, feature_count=count,
                          bbox3857=list(layer_bbox(geoms)), attr_keys=keys)
        print(f"  登记 {name} ({lid}): {count:,} 要素, crs={crs}")


def main():
    t0 = time.time()
    os.makedirs(SRC, exist_ok=True)

    print("== 1. 生成虚拟城市(20 万宗地) ==")
    if not os.path.exists(os.path.join(SRC, "parcels.shp")):
        datagen.generate(SRC)
    else:
        print("  已存在,跳过")

    print("== 2. 图层登记 ==")
    registry = Registry(DATA)
    register_all(registry)

    print("== 3. 全量切片 (v1) ==")
    tiler = Tiler(DATA)
    for lid, name, fn, fmt, crs, gtype, zmin, zmax in LAYERS:
        layer = registry.get(lid)
        t = time.time()
        result = tiler.run(layer, zmin, zmax, version=1)
        registry.add_version(lid, 1)
        print(f"  {name}: z{zmin}-{zmax} -> {result['tiles']} 块, "
              f"{result['seconds']}s, 告警 {len(result['alerts'])} 条")
        for a in result["alerts"][:5]:
            print(f"    ⚠ {a['tile']} {a['action']}")

    print("== 4. mapbox vector-tile 库逐块校验 ==")
    rc = validate_dir(os.path.join(DATA, "tiles"))
    if rc:
        return rc

    print("== 5. 源数据局部变更: D07 区宗地平移+改用地性质 ==")
    datagen.generate(SRC, changed_district="D07", suffix="_v2")
    registry.update_path("parcels", os.path.join(SRC, "parcels_v2.shp"),
                         registry.get("parcels")["feature_count"])

    print("== 6. 增量重切: 只切受影响瓦片 (v2) ==")
    bbox = datagen.district_bbox3857("D07")
    only = affected_tiles_from_bbox(bbox, 15, 18)
    print(f"  受影响瓦片: " + ", ".join(f"z{z}={len(s)}块" for z, s in only.items()))
    vm.copy_version(DATA, "parcels", 1, 2)
    layer = registry.get("parcels")
    result = tiler.run(layer, 15, 18, version=2, only_tiles=only)
    registry.add_version("parcels", 2)
    print(f"  重切 {result['tiles']} 块, {result['seconds']}s")

    print("== 7. 版本对比 v1 vs v2 ==")
    diff = vm.diff_versions(DATA, "parcels", 1, 2)
    print(f"  v1 {diff['tiles_v1']} 块 -> v2 {diff['tiles_v2']} 块 | "
          f"变化 {diff['n_changed']}, 新增 {diff['n_added']}, 删除 {diff['n_removed']}")
    for c in diff["changed"][:5]:
        print(f"    {c['tile']}: {c['features_before']} -> {c['features_after']} 要素")

    print("== 8. 校验 v2 增量产物 ==")
    rc = validate_dir(os.path.join(DATA, "tiles", "parcels", "v2"))
    if rc:
        return rc

    print(f"\n全部完成, 总耗时 {time.time()-t0:.0f}s")
    print("启动后台: py -3.12 -m vtpipe.server  然后访问 http://localhost:5000/layers")
    return 0


if __name__ == "__main__":
    sys.exit(main())
