"""切片主流程:分级抽稀 -> 捕捉 -> 裁剪(带缓冲)-> MVT 编码 -> 超限降精度。"""
from __future__ import annotations

import hashlib
import json
import os
import time

import numpy as np
import shapely
from shapely.geometry.polygon import orient as shapely_orient

from . import mvt
from .readers import layer_bbox, read_features
from .simplify import merge_small_into_neighbors, min_area, prepare_geometry, tolerance
from .tiling import (BUFFER, EXTENT, MAX_SNAP_ZOOM, snap_coords, tile_bounds,
                     tiles_for_bbox, unit_size)

MAX_TILE_BYTES = 500 * 1024          # 单块瓦片 500KB 上限
EXTENT_LADDER = [(EXTENT, 1), (2048, 2), (1024, 4)]  # (精度, 额外抽稀倍数)


# ------------------------------------------------------------ 几何 -> 瓦片坐标
def _tile_converter(z: int, x: int, y: int, extent: int):
    """世界坐标 -> 瓦片局部整数坐标(全程整数网格,跨瓦片一致)。"""
    if EXTENT % extent:
        raise ValueError("extent 必须整除 4096")
    shift = (MAX_SNAP_ZOOM - z) + (EXTENT // extent).bit_length() - 1
    from .tiling import GRID, HALF
    cells_per_tile = extent << shift
    gx0, gy0 = x * cells_per_tile, y * cells_per_tile

    def conv(wx, wy):
        gx = round((wx + HALF) / GRID) - gx0
        gy = round((HALF - wy) / GRID) - gy0
        return gx >> shift, gy >> shift

    return conv


def _geom_to_features(geom, conv):
    """shapely 几何 -> [(gtype, parts)],parts 为瓦片整数坐标。"""
    gt = geom.geom_type
    if gt == "GeometryCollection":
        out = []
        for g in geom.geoms:
            out.extend(_geom_to_features(g, conv))
        return out
    if gt == "Point":
        return [(mvt.POINT, [conv(geom.x, geom.y)])]
    if gt == "MultiPoint":
        return [(mvt.POINT, [conv(p.x, p.y) for p in geom.geoms])]
    if gt == "LineString":
        return [(mvt.LINESTRING, [[conv(*c) for c in geom.coords]])]
    if gt == "MultiLineString":
        return [(mvt.LINESTRING,
                 [[conv(*c) for c in line.coords] for line in geom.geoms])]
    if gt in ("Polygon", "MultiPolygon"):
        polys = [geom] if gt == "Polygon" else list(geom.geoms)
        rings = []
        for p in polys:
            # 世界坐标系下外环取顺时针,经 y 翻转后在瓦片坐标系鞋带面积为正
            p = shapely_orient(p, sign=-1.0)
            rings.append([conv(*c) for c in p.exterior.coords[:-1]])
            for hole in p.interiors:
                rings.append([conv(*c) for c in hole.coords[:-1]])
        return [(mvt.POLYGON, rings)]
    return []


# ------------------------------------------------------------ 单块瓦片编码
def encode_tile_bytes(candidates, z, x, y, extent, extra_tol, layer_name):
    """candidates: [(geom3857, fid, props)] 已按 z 级抽稀;返回 (pbf, n_features)。"""
    minx, miny, maxx, maxy = tile_bounds(z, x, y)
    buf = BUFFER * (maxx - minx) / EXTENT
    conv = _tile_converter(z, x, y, extent)
    features = []
    for geom, fid, props in candidates:
        g = geom
        if extra_tol > 0:
            g = shapely.simplify(g, extra_tol, preserve_topology=True)
            if g.is_empty:
                continue
        g = shapely.clip_by_rect(g, minx - buf, miny - buf, maxx + buf, maxy + buf)
        if g.is_empty:
            continue
        for gtype, parts in _geom_to_features(g, conv):
            features.append((fid, gtype, parts, props))
    pbf = mvt.encode_tile([(layer_name, features, extent)])
    return pbf, len(features)


def encode_with_size_guard(candidates, z, x, y, layer_name, alerts):
    """按精度阶梯编码,超过 500KB 自动降精度并记录告警。"""
    base_tol = tolerance(z)
    for extent, mult in EXTENT_LADDER:
        extra_tol = base_tol * (mult - 1) if mult > 1 else 0.0
        pbf, nfeat = encode_tile_bytes(candidates, z, x, y, extent,
                                       extra_tol, layer_name)
        if len(pbf) <= MAX_TILE_BYTES:
            if extent != EXTENT:
                alerts.append({
                    "tile": f"{z}/{x}/{y}", "layer": layer_name,
                    "action": f"降精度 extent {EXTENT}->{extent}, 抽稀x{mult}",
                    "size": len(pbf),
                })
            return pbf, nfeat, extent
    alerts.append({
        "tile": f"{z}/{x}/{y}", "layer": layer_name,
        "action": f"已降至最低精度仍超限 ({len(pbf)/1024:.0f}KB), 请检查数据或调低该层 minzoom",
        "size": len(pbf),
    })
    return pbf, nfeat, EXTENT_LADDER[-1][0]


# ------------------------------------------------------------ 主流程
class Tiler:
    def __init__(self, store_dir: str):
        self.store_dir = store_dir

    def version_dir(self, layer_id: str, version: int) -> str:
        return os.path.join(self.store_dir, "tiles", layer_id, f"v{version}")

    def run(self, layer: dict, zmin: int, zmax: int, version: int,
            only_tiles: dict[int, set] | None = None, progress=None) -> dict:
        """执行切片。only_tiles: {z: {(x,y),...}} 时只重切这些瓦片(增量)。"""
        t0 = time.time()
        layer_id = layer["id"]
        geoms, props = read_features(layer["path"], layer.get("crs"),
                                     layer.get("format"))

        # 1) 全层一次性捕捉到全局整数网格(无缝的基础)
        arr = np.asarray(geoms, dtype=object)
        geoms = list(shapely.transform(arr, snap_coords))

        alerts: list[dict] = []
        manifest = {"layer": layer_id, "version": version,
                    "zmin": zmin, "zmax": zmax, "tiles": {}}
        out_dir = self.version_dir(layer_id, version)
        os.makedirs(out_dir, exist_ok=True)
        if only_tiles:
            # 增量:以目录里已有(从上一版本复制)的清单为底,重切条目覆盖
            old_manifest_path = os.path.join(out_dir, "manifest.json")
            if os.path.exists(old_manifest_path):
                with open(old_manifest_path, "r", encoding="utf-8") as f:
                    manifest["tiles"] = json.load(f).get("tiles", {})

        bbox = layer_bbox(geoms)
        total_written = 0

        for z in range(zmin, zmax + 1):
            # 2) 按级别抽稀:简化 + 拓扑修复 + 小图斑处理
            if layer.get("merge_small"):
                prepared = [prepare_geometry(g, z, area_filter=False) for g in geoms]
                merged = merge_small_into_neighbors(
                    [(g, i) for i, g in enumerate(prepared)], min_area(z))
                gz = [g for g, _ in merged]
                zprops = [props[i] for _, i in merged]  # 属性取主图斑
                zfids = list(range(len(gz)))
            else:
                gz, zprops, zfids = [], [], []
                for i, g in enumerate(geoms):
                    pg = prepare_geometry(g, z)
                    if pg is not None:
                        gz.append(pg)
                        zprops.append(props[i])
                        zfids.append(i)
            if not gz:
                continue
            tree = shapely.STRtree(gz)

            # 3) 确定本级别要切的瓦片
            if only_tiles and z in only_tiles:
                tile_set = sorted(only_tiles[z])
            else:
                tile_set = sorted(tiles_for_bbox(*bbox, z))

            for ti, (x, y) in enumerate(tile_set):
                minx, miny, maxx, maxy = tile_bounds(z, x, y)
                buf = BUFFER * (maxx - minx) / EXTENT
                idx = tree.query(
                    shapely.box(minx - buf, miny - buf, maxx + buf, maxy + buf),
                    predicate="intersects")
                if len(idx) == 0:
                    continue
                candidates = [(gz[i], zfids[i], zprops[i]) for i in idx]
                pbf, nfeat, extent = encode_with_size_guard(
                    candidates, z, x, y, layer_id, alerts)
                rel = f"{z}/{x}/{y}"
                tdir = os.path.join(out_dir, str(z), str(x))
                tile_file = os.path.join(tdir, f"{y}.pbf")
                if nfeat == 0:
                    # 增量场景下要素可能整体消失:清掉旧瓦片
                    if only_tiles:
                        if os.path.exists(tile_file):
                            os.remove(tile_file)
                        manifest["tiles"].pop(rel, None)
                    continue

                os.makedirs(tdir, exist_ok=True)
                with open(tile_file, "wb") as f:
                    f.write(pbf)
                manifest["tiles"][rel] = {
                    "size": len(pbf), "features": nfeat, "extent": extent,
                    "sha1": hashlib.sha1(pbf).hexdigest(),
                }
                total_written += 1
                if progress is not None:
                    progress.update({"z": z, "done": total_written,
                                     "tile": rel})

        with open(os.path.join(out_dir, "manifest.json"), "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False)

        return {"layer": layer_id, "version": version, "tiles": total_written,
                "alerts": alerts, "seconds": round(time.time() - t0, 1)}
