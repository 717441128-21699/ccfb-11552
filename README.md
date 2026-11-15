# 矢量切片生产线 (vtpipe)

把 shapefile / GeoJSON 图层切成 MVT 矢量瓦片,前端 MapLibre 直接加载。
Python 3.12,核心依赖:shapely、pyproj、pyshp、flask、mapbox-vector-tile(校验用)。

## 快速开始

```bash
pip install -r requirements.txt
py -3.12 selftest.py     # 编码器/无缝/超限/增量/拓扑 自检
py -3.12 demo.py         # 端到端:造 20 万宗地虚拟城市,切到 18 级并校验
py -3.12 -m vtpipe.server  # 启动后台,访问 http://localhost:5000
```

后台五个界面:**图层登记 / 切片任务 / 瓦片预览 / 样式检查 / 版本对比**。

## 七项需求的实现位置

| 需求 | 实现 |
|---|---|
| 1. 读 shp/GeoJSON,CGCS2000/西安80 → Web 墨卡托 | `vtpipe/readers.py` + `vtpipe/crs.py`(坐标系别名注册表,投影带按中央经线登记) |
| 2. 分级抽稀,小图斑合并/丢弃,杜绝自相交 | `vtpipe/simplify.py`:容差=0.5 个 extent 单位;面积阈值 2×2 单位;`make_valid` 拓扑修复;合并策略 `merge_small_into_neighbors`(并入共享边界最长的邻图斑) |
| 3. MVT 编码:增量+zigzag,属性键值字典 | `vtpipe/mvt.py`:手写 protobuf 线格式,命令整数 + delta + zigzag varint;keys/values 图层级字典去重 |
| 4. 边缘缓冲、跨瓦片无缝 | `vtpipe/tiling.py`:所有顶点切片前捕捉到**全局整数网格**(对齐 z22),瓦片内只做整数平移缩放,相邻瓦片共享顶点坐标逐位一致;缓冲 64/4096 |
| 5. 单块 >500KB 自动降精度+告警 | `vtpipe/pipeline.py`:`encode_with_size_guard` 按 extent 4096→2048→1024 阶梯降精度并加大抽稀,仍超限则告警,全部记入任务 |
| 6. 局部变更只重切受影响瓦片 | `vtpipe/incremental.py`:变化范围 → 各级瓦片集合(外扩 2 倍缓冲);`versions.copy_version` 以旧版为底,只覆盖受影响瓦片 |
| 7. 版本并排对比 | `vtpipe/versions.py` 按 manifest(每块瓦片的 sha1/要素数/大小)做 diff;`/diff` 页面双图联动 + 变化瓦片清单 |

## 数据流

```
源数据(shp/geojson, CGCS2000/西安80)
  → 坐标统一(→3857)→ 全局网格捕捉
  → 按 z 级:简化 → make_valid → 小图斑丢弃/合并
  → STRtree 索引 → 逐瓦片:缓冲裁剪 → 整数坐标 → MVT 编码
  → >500KB?→ 降精度阶梯 + 告警
  → tiles/<layer>/v<N>/<z>/<x>/<y>.pbf + manifest.json
```

## 测试数据

`datagen.py` 生成虚拟城市(中心 114°E, 30.5°N,约 10.5km²):

- `parcels.shp` — 200,704 宗地面,CGCS2000(EPSG:4490),含 district/land_use/area/floor
- `zones.shp` — 196 控规单元,CGCS2000
- `roads.geojson` — 228 条道路,**西安80 高斯投影**(验证投影源数据)
- `pipelines.geojson` — 15 条管线,CGCS2000

`demo.py` 全流程:登记 → 全量切 v1 → mapbox 官方库逐块校验 → 把 D07 区宗地
平移 8m 并改用地性质 → 只重切受影响瓦片得 v2 → 输出版本差异。

## 注意

- 西安80 → CGCS2000 的基准转换这里用 PROJ 椭球参数近似;界线成果请换用
  测绘部门发布的七参数/格网(`crs.py` 里替换 `CRS_REGISTRY` 即可)。
- 页面里 MapLibre 走 unpkg CDN;纯内网环境把 js/css 下载到 `vtpipe/static/`
  并改模板引用即可。
- 单进程顺序切片;要提速可在 `Tiler.run` 的级别循环上接多进程(瓦片之间无依赖)。
