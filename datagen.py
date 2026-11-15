"""虚拟城市测试数据:20 万宗地 + 道路 + 管线 + 控规单元。

- parcels.shp    宗地     CGCS2000 经纬度 (EPSG:4490)   ~200,704 个面
- zones.shp      控规单元 CGCS2000 经纬度 (EPSG:4490)   196 个面
- roads.geojson  道路     西安80 高斯投影 (XIAN80_GK_114) 114 条线
- pipelines.geojson 管线  CGCS2000 经纬度               30 条线

--changed-district D07 会在该区内平移/改属性,用于增量切片演示。
"""
from __future__ import annotations

import argparse
import json
import os

import numpy as np
from pyproj import CRS, Transformer

MERC = CRS.from_epsg(3857)
CGCS2000 = CRS.from_epsg(4490)
XIAN80_GK = CRS.from_proj4(
    "+proj=tmerc +lat_0=0 +lon_0=114 +k=1 +x_0=500000 +y_0=0 "
    "+a=6378140 +rf=298.257 +units=m +no_defs")   # 西安80 椭球

BLOCKS = 56                 # 56x56 个街区
PARCELS_PER_BLOCK = 8       # 每个街区 8x8 宗地
PARCEL_PITCH = 22.0         # 宗地间距(米)
ROAD_WIDTH = 12.0
BLOCK_PITCH = PARCELS_PER_BLOCK * PARCEL_PITCH + ROAD_WIDTH
CENTER_LON, CENTER_LAT = 114.0, 30.5

LAND_USE = ["R", "C", "M", "G", "S"]  # 居住/商业/工业/绿地/市政


def city_origin():
    to_merc = Transformer.from_crs("EPSG:4326", MERC, always_xy=True)
    cx, cy = to_merc.transform(CENTER_LON, CENTER_LAT)
    half = BLOCKS * BLOCK_PITCH / 2
    return cx - half, cy - half


def district_of(bi, bj):
    return f"D{(bi // 12) * 5 + (bj // 12):02d}"


def parcel_rects(changed_district=None):
    """生成全部宗地(3857 坐标)。changed_district 内整体平移 8 米。"""
    ox, oy = city_origin()
    rects = []          # (x0, y0, x1, y1)
    attrs = []
    pid = 1
    rng = np.random.default_rng(42)
    for bi in range(BLOCKS):
        for bj in range(BLOCKS):
            dist = district_of(bi, bj)
            dx = 8.0 if dist == changed_district else 0.0
            bx = ox + bi * BLOCK_PITCH + ROAD_WIDTH / 2 + dx
            by = oy + bj * BLOCK_PITCH + ROAD_WIDTH / 2
            for pi in range(PARCELS_PER_BLOCK):
                for pj in range(PARCELS_PER_BLOCK):
                    w = PARCEL_PITCH - 2.0 - (pid % 3)
                    x0 = bx + pi * PARCEL_PITCH
                    y0 = by + pj * PARCEL_PITCH
                    rects.append((x0, y0, x0 + w, y0 + w))
                    lu = LAND_USE[(pid + bi) % len(LAND_USE)]
                    if dist == changed_district:
                        lu = "X"   # 变更区改用地性质
                    attrs.append({
                        "parcel_id": pid, "district": dist, "land_use": lu,
                        "area": round(w * w, 1),
                        "floor": int(rng.integers(1, 40)),
                    })
                    pid += 1
    return rects, attrs


def _to_lonlat(rects):
    tr = Transformer.from_crs(MERC, CGCS2000, always_xy=True)
    a = np.array(rects)
    lon0, lat0 = tr.transform(a[:, 0], a[:, 1])
    lon1, lat1 = tr.transform(a[:, 2], a[:, 3])
    return lon0, lat0, lon1, lat1


def write_parcels_shp(path, rects, attrs):
    import shapefile

    os.makedirs(os.path.dirname(path), exist_ok=True)
    lon0, lat0, lon1, lat1 = _to_lonlat(rects)
    w = shapefile.Writer(path, shapeType=shapefile.POLYGON)
    w.autoBalance = 1
    w.field("parcel_id", "N")
    w.field("district", "C", size=4)
    w.field("land_use", "C", size=2)
    w.field("area", "F", decimal=1)
    w.field("floor", "N")
    for i, a in enumerate(attrs):
        # shapefile 规范:外环必须顺时针
        ring = [
            [lon0[i], lat0[i]], [lon0[i], lat1[i]], [lon1[i], lat1[i]],
            [lon1[i], lat0[i]], [lon0[i], lat0[i]],
        ]
        w.poly([ring])
        w.record(a["parcel_id"], a["district"], a["land_use"], a["area"], a["floor"])
    w.close()
    with open(os.path.splitext(path)[0] + ".prj", "w") as f:
        f.write(CGCS2000.to_wkt())
    return len(attrs)


def write_zones_shp(path):
    """控规单元:4x4 街区一个单元。"""
    import shapefile

    ox, oy = city_origin()
    per = 4 * BLOCK_PITCH
    n = BLOCKS // 4
    tr = Transformer.from_crs(MERC, CGCS2000, always_xy=True)
    w = shapefile.Writer(path, shapeType=shapefile.POLYGON)
    w.autoBalance = 1
    w.field("zone_code", "C", size=8)
    w.field("name", "C", size=20)
    w.field("far", "F", decimal=1)   # 容积率
    count = 0
    for i in range(n):
        for j in range(n):
            x0, y0 = ox + i * per, oy + j * per
            x1, y1 = x0 + per, y0 + per
            pts = list(zip(*tr.transform(
                [x0, x0, x1, x1, x0], [y0, y1, y1, y0, y0])))  # 顺时针外环
            w.poly([[list(p) for p in pts]])
            w.record(f"ZQ{i:02d}{j:02d}", f"控规单元{i:02d}-{j:02d}",
                     round(1.0 + (i * j) % 30 / 10, 1))
            count += 1
    w.close()
    with open(os.path.splitext(path)[0] + ".prj", "w") as f:
        f.write(CGCS2000.to_wkt())
    return count


def write_roads_geojson(path):
    """道路:街区边界网格线,西安80 高斯投影坐标。"""
    ox, oy = city_origin()
    size = BLOCKS * BLOCK_PITCH
    tr = Transformer.from_crs(MERC, XIAN80_GK, always_xy=True)
    feats = []
    rid = 1
    for i in range(BLOCKS + 1):
        c = ox + i * BLOCK_PITCH - ROAD_WIDTH / 2
        for (x0, y0, x1, y1) in [(c, oy, c, oy + size), (ox, c, ox + size, c)]:
            xs, ys = tr.transform([x0, x1], [y0, y1])
            feats.append({
                "type": "Feature",
                "geometry": {"type": "LineString",
                             "coordinates": [[xs[0], ys[0]], [xs[1], ys[1]]]},
                "properties": {"road_id": rid,
                               "name": ("纬" if y0 == y1 else "经") + f"{i:02d}路",
                               "level": 1 if i % 4 == 0 else 2},
            })
            rid += 1
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"type": "FeatureCollection", "features": feats}, f)
    return len(feats)


def write_pipelines_geojson(path):
    """管线:沿部分道路的折线,CGCS2000 经纬度。"""
    ox, oy = city_origin()
    size = BLOCKS * BLOCK_PITCH
    tr = Transformer.from_crs(MERC, CGCS2000, always_xy=True)
    feats = []
    for k, i in enumerate(range(0, BLOCKS + 1, 4)):
        c = ox + i * BLOCK_PITCH - ROAD_WIDTH / 2 + 3
        xs, ys = tr.transform([c, c + 500, c + 500, c],
                              [oy, oy + size / 3, oy + size * 2 / 3, oy + size])
        feats.append({
            "type": "Feature",
            "geometry": {"type": "LineString",
                         "coordinates": [[x, y] for x, y in zip(xs, ys)]},
            "properties": {"pipe_id": k + 1, "material": "PE",
                           "diameter": 300 + (k % 4) * 100},
        })
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"type": "FeatureCollection", "features": feats}, f)
    return len(feats)


def generate(outdir, changed_district=None, suffix=""):
    os.makedirs(outdir, exist_ok=True)
    rects, attrs = parcel_rects(changed_district)
    n1 = write_parcels_shp(os.path.join(outdir, f"parcels{suffix}.shp"), rects, attrs)
    n2 = write_zones_shp(os.path.join(outdir, "zones.shp"))
    n3 = write_roads_geojson(os.path.join(outdir, "roads.geojson"))
    n4 = write_pipelines_geojson(os.path.join(outdir, "pipelines.geojson"))
    print(f"宗地 {n1:,} | 控规单元 {n2} | 道路 {n3} | 管线 {n4}")
    return {"parcels": n1, "zones": n2, "roads": n3, "pipelines": n4}


def district_bbox3857(dist: str):
    """某个变更区的 3857 范围(增量切片用)。"""
    idx = int(dist[1:])
    di, dj = idx // 5, idx % 5
    ox, oy = city_origin()
    x0 = ox + di * 12 * BLOCK_PITCH - 20
    y0 = oy + dj * 12 * BLOCK_PITCH - 20
    x1 = x0 + 12 * BLOCK_PITCH + 40
    y1 = y0 + 12 * BLOCK_PITCH + 40
    return (x0, y0, x1, y1)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data/source")
    ap.add_argument("--changed-district", default=None)
    ap.add_argument("--suffix", default="")
    args = ap.parse_args()
    generate(args.out, args.changed_district, args.suffix)
