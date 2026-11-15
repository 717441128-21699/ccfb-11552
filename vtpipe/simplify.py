"""按缩放级别抽稀:简化、小图斑合并/丢弃、拓扑修复(杜绝自相交)。"""
from __future__ import annotations

import shapely
from shapely.geometry import GeometryCollection, MultiPolygon, Polygon

from .tiling import unit_size


def tolerance(z: int) -> float:
    """z 级简化容差(米):半个 extent 单位,视觉上不可察觉。"""
    return unit_size(z) * 0.5


def min_area(z: int) -> float:
    """z 级最小保留面积(平方米):不足 2x2 个 extent 单位的面丢弃/合并。"""
    u = unit_size(z) * 2.0
    return u * u


def _drop_small_parts(geom, area_threshold):
    """过滤 MultiPolygon/GeometryCollection 里面积过小的成员。"""
    if geom.is_empty:
        return None
    gt = geom.geom_type
    if gt == "Polygon":
        return geom if geom.area >= area_threshold else None
    if gt in ("MultiPolygon", "GeometryCollection"):
        polys = [
            g for g in getattr(geom, "geoms", [])
            if g.geom_type == "Polygon" and g.area >= area_threshold
        ]
        if not polys:
            return None
        return polys[0] if len(polys) == 1 else MultiPolygon(polys)
    return geom  # 线/点不按面积过滤


def prepare_geometry(geom, z: int, area_filter: bool = True):
    """单要素处理:简化 -> 拓扑修复 -> 小图斑丢弃。返回 None 表示本级别不输出。"""
    if geom is None or geom.is_empty:
        return None
    g = shapely.simplify(geom, tolerance(z), preserve_topology=True)
    if g.is_empty:
        return None
    if not g.is_valid:
        # make_valid 保证不出现自相交等非法拓扑
        g = shapely.make_valid(g)
        if g.is_empty:
            return None
    if area_filter and g.geom_type in ("Polygon", "MultiPolygon", "GeometryCollection"):
        g = _drop_small_parts(g, min_area(z))
    return g


def merge_small_into_neighbors(items, area_threshold):
    """小图斑合并:把面积不足的面并入共享边界最长的相邻面。

    items: [(geom, payload), ...];返回合并后的 [(geom, payload)],
    被吞并图斑的 payload 消失,主图斑保留自己的 payload。
    """
    items = [(g, p) for g, p in items if g is not None and not g.is_empty]
    changed = True
    while changed:
        changed = False
        geoms = [g for g, _ in items]
        tree = shapely.STRtree(geoms)
        for i, (g, _) in enumerate(items):
            if g is None or g.area >= area_threshold or g.geom_type not in (
                "Polygon", "MultiPolygon",
            ):
                continue
            best_j, best_len = None, 0.0
            for j in tree.query(g, predicate="intersects"):
                if j == i or items[j][0] is None:
                    continue
                shared = g.boundary.intersection(items[j][0].boundary).length
                if shared > best_len:
                    best_j, best_len = j, shared
            if best_j is not None:
                merged = shapely.union(items[best_j][0], g)
                if merged.is_valid:
                    items[best_j] = (merged, items[best_j][1])
                    items[i] = (None, None)
                    changed = True
        items = [(g, p) for g, p in items if g is not None]
    return items
