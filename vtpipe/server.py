"""Flask 后台:图层登记 / 切片任务 / 瓦片预览 / 样式检查 / 版本对比。"""
from __future__ import annotations

import json
import os
import threading
import time
import traceback

from flask import Flask, abort, jsonify, redirect, render_template, request, send_file

from . import versions as vm
from .incremental import affected_tiles_from_bbox
from .pipeline import Tiler
from .readers import read_features
from .registry import Registry

DATA_DIR = os.environ.get("VTPIPE_DATA", os.path.join(os.getcwd(), "data"))


def create_app(data_dir: str = DATA_DIR) -> Flask:
    app = Flask(__name__)
    registry = Registry(data_dir)
    tiler = Tiler(data_dir)
    tasks: dict[str, dict] = {}
    tasks_lock = threading.Lock()

    # ---------------------------------------------------------- 工具
    def _run_task(task_id, layer_id, zmin, zmax, mode, bbox3857):
        task = tasks[task_id]
        try:
            layer = registry.get(layer_id)
            if layer is None:
                raise ValueError(f"图层未登记: {layer_id}")
            version = vm.next_version(registry, layer_id)
            only = None
            if mode == "incremental":
                prev = max(layer["versions"]) if layer["versions"] else None
                if prev is None:
                    raise ValueError("没有上一版本,无法增量,请先全量切片")
                vm.copy_version(data_dir, layer_id, prev, version)
                only = affected_tiles_from_bbox(bbox3857, zmin, zmax)
                task["affected"] = sum(len(s) for s in only.values())
            task["version"] = version
            result = tiler.run(layer, zmin, zmax, version,
                               only_tiles=only, progress=task)
            registry.add_version(layer_id, version)
            task.update(status="done", result=result, version=version)
        except Exception as e:  # noqa: BLE001 - 任务失败要完整呈现给前端
            task.update(status="failed", error=str(e),
                        trace=traceback.format_exc())

    def _start_task(layer_id, zmin, zmax, mode, bbox3857=None):
        task_id = f"t{int(time.time()*1000)}"
        tasks[task_id] = {
            "id": task_id, "layer": layer_id, "zmin": zmin, "zmax": zmax,
            "mode": mode, "status": "running", "done": 0, "z": None,
            "started": time.strftime("%H:%M:%S"),
        }
        th = threading.Thread(target=_run_task,
                              args=(task_id, layer_id, zmin, zmax, mode, bbox3857),
                              daemon=True)
        th.start()
        return task_id

    # ---------------------------------------------------------- 页面
    @app.route("/")
    def index():
        return redirect("/layers")

    @app.route("/layers")
    def page_layers():
        return render_template("layers.html", layers=registry.list_layers())

    @app.route("/tasks")
    def page_tasks():
        return render_template("tasks.html", layers=registry.list_layers())

    @app.route("/preview")
    def page_preview():
        layers = registry.list_layers()
        layer_id = request.args.get("layer") or (next(iter(layers)) if layers else "")
        layer = layers.get(layer_id, {})
        version = int(request.args.get("version") or
                      (max(layer["versions"]) if layer.get("versions") else 1))
        return render_template("preview.html", layers=layers,
                               layer_id=layer_id, version=version)

    @app.route("/style")
    def page_style():
        layers = registry.list_layers()
        layer_id = request.args.get("layer") or (next(iter(layers)) if layers else "")
        return render_template("style.html", layers=layers, layer_id=layer_id)

    @app.route("/diff")
    def page_diff():
        layers = registry.list_layers()
        layer_id = request.args.get("layer") or (next(iter(layers)) if layers else "")
        return render_template("diff.html", layers=layers, layer_id=layer_id)

    # ---------------------------------------------------------- API:图层
    @app.route("/api/layers")
    def api_layers():
        return jsonify(registry.list_layers())

    @app.route("/api/layers/register", methods=["POST"])
    def api_register():
        f = request.form
        layer_id = f["id"].strip()
        if not layer_id:
            abort(400, "缺少图层 id")
        geoms, props = read_features(f["path"].strip(), f.get("crs") or None,
                                     f.get("format") or None, limit=500)
        from .readers import layer_bbox
        import numpy as np, shapely
        # bbox 用抽样估计即可;正式环境可全量扫描
        bbox = layer_bbox(geoms)
        keys = sorted({k for p in props for k in p})
        # 全量要素数:快速统计
        if f.get("format", "").lower() in ("geojson", "json") or \
           f["path"].lower().endswith((".geojson", ".json")):
            with open(f["path"], encoding="utf-8") as fp:
                count = len(json.load(fp).get("features", []))
        else:
            import shapefile
            count = len(shapefile.Reader(f["path"]))
        registry.register(
            layer_id, name=f.get("name") or layer_id, path=f["path"].strip(),
            fmt=f.get("format") or os.path.splitext(f["path"])[1].lstrip("."),
            crs=f.get("crs") or "", geom_type=f.get("geom_type") or "Polygon",
            feature_count=count, bbox3857=list(bbox), attr_keys=keys,
            merge_small=bool(f.get("merge_small")))
        return redirect("/layers")

    # ---------------------------------------------------------- API:任务
    @app.route("/api/tasks")
    def api_tasks():
        with tasks_lock:
            return jsonify([{k: v for k, v in t.items() if k != "trace"}
                            for t in sorted(tasks.values(),
                                            key=lambda t: t["id"], reverse=True)])

    @app.route("/api/tasks/start", methods=["POST"])
    def api_start_task():
        f = request.form
        zmin, zmax = int(f["zmin"]), int(f["zmax"])
        mode = f.get("mode", "full")
        bbox3857 = None
        if mode == "incremental":
            # 前端传经纬度 bbox,转 3857
            from pyproj import Transformer
            lon0, lat0, lon1, lat1 = [float(v) for v in f["bbox"].split(",")]
            tr = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
            x0, y0 = tr.transform(lon0, lat0)
            x1, y1 = tr.transform(lon1, lat1)
            bbox3857 = (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
        task_id = _start_task(f["layer"], zmin, zmax, mode, bbox3857)
        return jsonify({"task_id": task_id})

    # ---------------------------------------------------------- API:瓦片
    @app.route("/tiles/<layer_id>/<int:version>/<int:z>/<int:x>/<int:y>.pbf")
    def serve_tile(layer_id, version, z, x, y):
        path = os.path.join(tiler.version_dir(layer_id, version),
                            str(z), str(x), f"{y}.pbf")
        if not os.path.exists(path):
            return "", 204  # 空瓦片:204 比 404 对前端更友好
        return send_file(path, mimetype="application/x-protobuf",
                         max_age=3600)

    # ---------------------------------------------------------- API:样式检查
    @app.route("/api/style/check", methods=["POST"])
    def api_style_check():
        layer_id = request.form["layer"]
        layer = registry.get(layer_id)
        if layer is None:
            abort(404, "图层未登记")
        issues = []
        try:
            style = json.loads(request.form["style"])
        except json.JSONDecodeError as e:
            return jsonify({"ok": False, "issues": [f"JSON 解析失败: {e}"]})

        known_keys = set(layer.get("attr_keys", []))
        source_layers = {layer_id}

        def walk_expr(expr, where):
            """递归收集表达式里的属性引用。"""
            if isinstance(expr, list) and expr:
                op = expr[0]
                if op == "get" and len(expr) >= 2 and isinstance(expr[1], str):
                    prop = expr[1]
                    if prop not in known_keys:
                        issues.append(f"{where}: 引用了图层不存在的属性 '{prop}'")
                for sub in expr[1:]:
                    walk_expr(sub, where)

        for ly in style.get("layers", []):
            lid = ly.get("id", "?")
            if ly.get("type") == "background":
                continue
            sl = ly.get("source-layer")
            if sl and sl not in source_layers:
                issues.append(f"图层 '{lid}': source-layer '{sl}' 不存在"
                              f"(可用: {sorted(source_layers)})")
            if "filter" in ly:
                # filter 里的属性名是第 2 个元素:["==","land_use","R"]
                def walk_filter(fexpr):
                    if isinstance(fexpr, list) and fexpr:
                        if fexpr[0] in ("==", "!=", "in", "!in", ">", "<", ">=", "<=") \
                           and len(fexpr) >= 2 and isinstance(fexpr[1], str) \
                           and not fexpr[1].startswith("$"):
                            if fexpr[1] not in known_keys:
                                issues.append(
                                    f"图层 '{lid}' filter: 属性 '{fexpr[1]}' 不存在")
                        for sub in fexpr[1:]:
                            walk_filter(sub)
                walk_filter(ly["filter"])
            for section in ("paint", "layout"):
                for prop, val in ly.get(section, {}).items():
                    walk_expr(val, f"图层 '{lid}' {section}.{prop}")
        return jsonify({"ok": not issues, "issues": issues or ["检查通过"]})

    # ---------------------------------------------------------- API:版本对比
    @app.route("/api/diff")
    def api_diff():
        layer_id = request.args["layer"]
        v1, v2 = int(request.args["v1"]), int(request.args["v2"])
        return jsonify(vm.diff_versions(data_dir, layer_id, v1, v2))

    return app


app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, threaded=True)
