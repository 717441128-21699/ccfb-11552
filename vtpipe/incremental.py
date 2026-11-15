"""增量更新:源数据局部变化 -> 受影响的瓦片集合。"""
from __future__ import annotations

from .tiling import BUFFER, tiles_for_bbox


def affected_tiles(changed_geoms, zmin: int, zmax: int,
                   buffer_units: int = BUFFER * 2) -> dict[int, set]:
    """根据变化要素(3857 几何)计算每个级别需要重切的瓦片。

    外扩 2 倍缓冲宽度:跨边界要素的缓冲片段落在相邻瓦片里,
    只切严格相交的瓦片会在边界留下旧几何残影。
    """
    out: dict[int, set] = {}
    for z in range(zmin, zmax + 1):
        s: set = set()
        for g in changed_geoms:
            if g is None or g.is_empty:
                continue
            minx, miny, maxx, maxy = g.bounds
            for xy in tiles_for_bbox(minx, miny, maxx, maxy, z,
                                     expand_units=buffer_units):
                s.add(xy)
        out[z] = s
    return out


def affected_tiles_from_bbox(bbox3857, zmin: int, zmax: int,
                             buffer_units: int = BUFFER * 2) -> dict[int, set]:
    """直接按变化范围 bbox (minx, miny, maxx, maxy, 3857) 计算。"""
    out: dict[int, set] = {}
    for z in range(zmin, zmax + 1):
        out[z] = set(tiles_for_bbox(*bbox3857, z, expand_units=buffer_units))
    return out
