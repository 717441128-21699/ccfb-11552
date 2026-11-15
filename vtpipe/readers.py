"""数据源读取:Shapefile (pyshp) 与 GeoJSON,统一转成 Web 墨卡托输出。"""
from __future__ import annotations

import json
import os

import numpy as np
import shapely
from shapely.geometry import shape as geojson_shape

from .crs import resolve, to_mercator


def _read_prj(shp_path: str) -> str | None:
    prj = os.path.splitext(shp_path)[0] + ".prj"
    if os.path.exists(prj):
        with open(prj, "r", encoding="utf-8", errors="ignore") as f:
            return f.read().strip()
    return None


def _transform_geoms(geoms, transformer):
    """批量坐标转换:get_coordinates 一次取出,转换后 set_coordinates 写回。"""
    arr = np.asarray(geoms, dtype=object)
    coords = shapely.get_coordinates(arr)
    if len(coords) == 0:
        return list(arr)
    x, y = transformer.transform(coords[:, 0], coords[:, 1])
    out = shapely.set_coordinates(arr, np.column_stack([x, y]))
    return list(out)


def read_features(path: str, crs_spec: str | None = None, fmt: str | None = None,
                  limit: int | None = None):
    """读取数据源,返回 (geoms_3857, props_list)。

    crs_spec 为 None 时:shapefile 取 .prj,GeoJSON 按 WGS84 处理。
    """
    fmt = (fmt or os.path.splitext(path)[1].lstrip(".")).lower()
    geoms, props = [], []

    if fmt in ("shp", "shapefile"):
        import shapefile  # pyshp

        reader = shapefile.Reader(path)
        fields = [f[0] for f in reader.fields[1:]]  # 跳过 DeletionFlag
        for sr in reader.iterShapeRecords():
            geoms.append(geojson_shape(sr.shape.__geo_interface__))
            props.append(dict(zip(fields, sr.record)))
            if limit and len(geoms) >= limit:
                break
        crs = resolve(crs_spec) or resolve(_read_prj(path))
        if crs is None:
            raise ValueError(f"{path}: 缺少 .prj 且登记时未指定坐标系")

    elif fmt in ("geojson", "json"):
        with open(path, "r", encoding="utf-8") as f:
            coll = json.load(f)
        for feat in coll.get("features", []):
            if not feat.get("geometry"):
                continue
            geoms.append(geojson_shape(feat["geometry"]))
            props.append(feat.get("properties") or {})
            if limit and len(geoms) >= limit:
                break
        crs = resolve(crs_spec) or resolve("WGS84")
    else:
        raise ValueError(f"不支持的格式: {fmt}")

    transformer = to_mercator(crs)
    geoms = _transform_geoms(geoms, transformer)
    return geoms, props


def layer_bbox(geoms) -> tuple[float, float, float, float]:
    bounds = shapely.total_bounds(np.asarray(geoms, dtype=object))
    return tuple(float(v) for v in bounds)
