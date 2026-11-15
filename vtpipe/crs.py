"""坐标系登记与转换:CGCS2000 / 西安80 / WGS84 -> Web 墨卡托 (EPSG:3857)。

生产环境中西安80 -> 2000 的基准转换应使用测绘部门发布的七参数/格网文件,
这里用 PROJ 内置椭球参数做近似(分米级以内对切片显示无影响,但界线成果
请以官方转换参数为准)。
"""
from __future__ import annotations

from pyproj import CRS, Transformer

WEB_MERCATOR = CRS.from_epsg(3857)

# 常用坐标系别名登记。投影带按中央经线注册,例如西安80 三度带 114E。
CRS_REGISTRY = {
    "CGCS2000": "EPSG:4490",
    "CGCS2000_GEO": "EPSG:4490",
    "CGCS2000_3GK_114": (
        "+proj=tmerc +lat_0=0 +lon_0=114 +k=1 +x_0=500000 +y_0=0 "
        "+ellps=GRS80 +units=m +no_defs"
    ),
    "XIAN80": "EPSG:4610",
    "XIAN80_GEO": "EPSG:4610",
    "XIAN80_GK_114": (
        "+proj=tmerc +lat_0=0 +lon_0=114 +k=1 +x_0=500000 +y_0=0 "
        "+a=6378140 +rf=298.257 +units=m +no_defs"   # 西安80 椭球
    ),
    "WGS84": "EPSG:4326",
    "WEB_MERCATOR": "EPSG:3857",
}


def resolve(spec: str | None) -> CRS | None:
    """把登记表里的坐标系写法解析成 pyproj.CRS。"""
    if spec is None:
        return None
    spec = spec.strip()
    if not spec:
        return None
    return CRS.from_user_input(CRS_REGISTRY.get(spec.upper(), spec))


def to_mercator(src: str | CRS) -> Transformer:
    """构造 src -> EPSG:3857 的转换器(always_xy,输入输出都是 x/y 顺序)。"""
    crs = src if isinstance(src, CRS) else resolve(src)
    if crs is None:
        raise ValueError("未知坐标系:请在图层登记时指定 source crs")
    return Transformer.from_crs(crs, WEB_MERCATOR, always_xy=True)
