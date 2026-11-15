"""瓦片金字塔数学:Web 墨卡托 XYZ 方案 + 全局整数捕捉网格。

无缝的关键做法:所有几何顶点在切片前一次性捕捉到"全局整数网格"
(最细级别瓦片的一个 extent 单位对应一格),之后每个瓦片只做
整数平移/缩放,跨瓦片共享的顶点在每个相邻瓦片里得到完全相同的
整数坐标,边界处不会出现裂缝。
"""
from __future__ import annotations

EARTH = 40075016.68557849          # 2*pi*6378137,Web 墨卡托赤道周长(米)
HALF = EARTH / 2.0
EXTENT = 4096                      # MVT 默认坐标范围
BUFFER = 64                        # 瓦片边缘缓冲(extent 单位)
MAX_SNAP_ZOOM = 22                 # 捕捉网格对齐到的最细级别

# 全局捕捉网格边长(米):z22 级一个 extent 单位
GRID = EARTH / (EXTENT * (1 << MAX_SNAP_ZOOM))


def tile_span(z: int) -> float:
    """z 级单块瓦片的边长(米,Web 墨卡托)。"""
    return EARTH / (1 << z)


def unit_size(z: int, extent: int = EXTENT) -> float:
    """z 级一个 extent 单位对应的地面距离(米)。"""
    return tile_span(z) / extent


def tile_bounds(z: int, x: int, y: int) -> tuple[float, float, float, float]:
    """瓦片 (z,x,y) 的 Web 墨卡托范围 (minx, miny, maxx, maxy)。"""
    span = tile_span(z)
    minx = -HALF + x * span
    maxy = HALF - y * span
    return minx, maxy - span, minx + span, maxy


def tiles_for_bbox(minx, miny, maxx, maxy, z, expand_units: int = 0):
    """覆盖 bbox 的所有瓦片行列号。expand_units 按 extent 单位外扩。"""
    span = tile_span(z)
    n = 1 << z
    pad = expand_units * span / EXTENT
    x0 = int((minx - pad + HALF) // span)
    x1 = int((maxx + pad + HALF) // span)
    y0 = int((HALF - maxy - pad) // span)
    y1 = int((HALF - miny + pad) // span)
    x0, y0 = max(x0, 0), max(y0, 0)
    x1, y1 = min(x1, n - 1), min(y1, n - 1)
    for x in range(x0, x1 + 1):
        for y in range(y0, y1 + 1):
            yield (x, y)


def snap(x: float, y: float) -> tuple[float, float]:
    """把单个顶点捕捉到全局整数网格(标量版,测试用)。"""
    return round(x / GRID) * GRID, round(y / GRID) * GRID


def snap_coords(coords):
    """numpy 向量化捕捉,配合 shapely.transform 使用(交错坐标 (n,2))。"""
    import numpy as np

    return np.rint(coords / GRID) * GRID


def world_to_tile(z: int, x: int, y: int, extent: int = EXTENT):
    """返回把世界坐标(已捕捉)换算成瓦片局部整数坐标的函数。

    全部走整数运算:全局格号 -> 瓦片内格号,不存在浮点舍入分叉。
    """
    shift = MAX_SNAP_ZOOM - z
    if shift < 0:
        raise ValueError(f"zoom {z} 超过捕捉网格级别 {MAX_SNAP_ZOOM}")
    cells_per_tile = EXTENT << shift           # 一块瓦片占的全局格数
    gx0 = x * cells_per_tile                   # 瓦片原点(全局格号)
    gy0 = y * cells_per_tile
    scale_den = 1 << shift                     # 全局格 -> 瓦片格的除数(2 的幂)

    def convert(wx, wy):
        gx = round((wx + HALF) / GRID)
        gy = round((HALF - wy) / GRID)         # y 轴翻转
        tx = (gx - gx0) // scale_den if scale_den > 1 else gx - gx0
        ty = (gy - gy0) // scale_den if scale_den > 1 else gy - gy0
        return tx, ty

    # 整数除法对负数会向下取整,瓦片局部坐标允许为负(缓冲区),用 // 即可
    return convert
