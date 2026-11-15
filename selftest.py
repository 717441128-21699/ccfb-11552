"""自检:MVT 编码往返、跨瓦片无缝、超限降精度、增量范围、拓扑修复。

运行: py -3.12 selftest.py
"""
from __future__ import annotations

import sys

import shapely
from mapbox_vector_tile import decode as mvt_decode
from shapely.geometry import LineString, Polygon, box

from vtpipe import mvt
from vtpipe.incremental import affected_tiles_from_bbox
from vtpipe.pipeline import _tile_converter, encode_tile_bytes, encode_with_size_guard
from vtpipe.simplify import prepare_geometry
from vtpipe.tiling import BUFFER, EXTENT, tile_bounds

FAILURES = []


def check(name, cond, detail=""):
    tag = "PASS" if cond else "FAIL"
    print(f"[{tag}] {name}" + (f"  {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


# 1) MVT 编码 -> mapbox 官方库解析往返 ------------------------------------
feats = [
    (1, mvt.POLYGON, [[(0, 0), (100, 0), (100, 100), (0, 100)]],
     {"name": "测试", "area": 12.5, "flag": True, "n": 7}),
    (2, mvt.LINESTRING, [[(0, 0), (500, 500), (4096, 4096)]], {"level": 2}),
    (3, mvt.POINT, [(2048, 2048)], {}),
]
pbf = mvt.encode_tile([("test", feats, 4096)])
d = mvt_decode(pbf)
check("MVT 往返:图层名", "test" in d)
check("MVT 往返:要素数", len(d["test"]["features"]) == 3,
      str(len(d.get("test", {}).get("features", []))))
props = d["test"]["features"][0]["properties"]
check("MVT 往返:属性字典",
      props.get("name") == "测试" and props.get("n") == 7
      and abs(props.get("area", 0) - 12.5) < 1e-9 and props.get("flag") is True,
      str(props))
geom0 = d["test"]["features"][0]["geometry"]
check("MVT 往返:面几何类型", geom0["type"] == "Polygon")

# 2) 跨瓦片无缝 ----------------------------------------------------------
# 2a) 全局整数网格:同一世界点在相邻两瓦片的局部坐标必须相差恰好 EXTENT
z = 18
minx, miny, maxx, maxy = tile_bounds(z, 100000, 60000)
conv_l = _tile_converter(z, 100000, 60000, EXTENT)
conv_r = _tile_converter(z, 100001, 60000, EXTENT)
wx, wy = maxx + 3.7, (miny + maxy) / 2      # 边界右侧 3.7 米处一点
xl, yl = conv_l(wx, wy)
xr, yr = conv_r(wx, wy)
check("无缝:相邻瓦片局部坐标相差恰为 EXTENT",
      xl - xr == EXTENT and yl == yr, f"({xl},{yl}) vs ({xr},{yr})")

# 2b) 跨界矩形被两瓦片裁剪后,重叠区边缘共线且 y 范围一致
span = maxx - minx
buf = BUFFER * span / EXTENT
rect = box(maxx - 50, miny + 10, maxx + 50, maxy - 10)
gl = shapely.clip_by_rect(rect, minx - buf, miny - buf, maxx + buf, maxy + buf)
gr = shapely.clip_by_rect(rect, maxx - buf, miny - buf,
                          maxx + span + buf, maxy + buf)
xs_l = [conv_l(*c)[0] for c in gl.exterior.coords]
xs_r = [conv_r(*c)[0] for c in gr.exterior.coords]
ys_l = sorted(conv_l(*c)[1] for c in gl.exterior.coords)
ys_r = sorted(conv_r(*c)[1] for c in gr.exterior.coords)
check("无缝:左瓦片切到缓冲边 EXTENT+BUFFER", max(xs_l) == EXTENT + BUFFER,
      str(xs_l))
check("无缝:右瓦片切到缓冲边 -BUFFER", min(xs_r) == -BUFFER, str(xs_r))
check("无缝:重叠区 y 范围一致", ys_l == ys_r, f"{ys_l} vs {ys_r}")

# 3) 超限降精度:密集要素触发 extent 阶梯 + 告警 --------------------------
dense = []
for i in range(200):
    for j in range(200):
        x0 = minx + i * (maxx - minx) / 200
        y0 = miny + j * (maxy - miny) / 200
        w = (maxx - minx) / 400
        dense.append((box(x0, y0, x0 + w, y0 + w), i * 200 + j,
                      {"id": i * 200 + j}))
alerts = []
pbf, nfeat, extent = encode_with_size_guard(dense, z, 100000, 60000, "dense", alerts)
check("超限:触发告警", len(alerts) > 0, "未产生告警")
check("超限:降了精度", extent < EXTENT, f"extent={extent}")
check("超限:产物仍可被解析", "dense" in mvt_decode(pbf))
print(f"       (密集瓦片 {len(pbf)//1024}KB, extent={extent}, 告警 {len(alerts)} 条)")

# 4) 增量:受影响瓦片集合 -------------------------------------------------
aff = affected_tiles_from_bbox((minx, miny, maxx, maxy), 15, 18, buffer_units=0)
check("增量:z18 恰好 4 块", len(aff[18]) == 4, str(aff[18]))
check("增量:z15 覆盖且不外溢", 1 <= len(aff[15]) <= 4, str(aff[15]))

# 5) 拓扑修复:自相交(蝴蝶结)多边形 -> 合法 ------------------------------
bowtie = Polygon([(0, 0), (2, 2), (2, 0), (0, 2), (0, 0)])
check("拓扑:蝴蝶结本身非法", not bowtie.is_valid)
fixed = prepare_geometry(bowtie, 18)
check("拓扑:修复后合法", fixed is not None and fixed.is_valid)

# 6) 小图斑丢弃:低级别下小面被过滤 ---------------------------------------
tiny = box(0, 0, 0.5, 0.5)   # 0.25 平方米
check("抽稀:z10 丢弃小面", prepare_geometry(tiny, 10) is None)
check("抽稀:z18 保留小面", prepare_geometry(tiny, 18) is not None)

print()
if FAILURES:
    print(f"共 {len(FAILURES)} 项失败: {FAILURES}")
    sys.exit(1)
print("全部自检通过")
