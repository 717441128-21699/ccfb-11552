"""MVT (Mapbox Vector Tile) 编码器,遵循规范 2.1。

- 几何:MoveTo/LineTo/ClosePath 命令整数,坐标增量编码 + zigzag + varint
- 属性:图层级 keys/values 字典,要素只存字典下标(键值字典去重)
- 面要素环方向:外环顺时针、内环逆时针(瓦片坐标系,y 向下)

手写 protobuf 线格式,不依赖 protoc;产物用 mapbox 官方解析库校验。
"""
from __future__ import annotations

import struct

MOVETO, LINETO, CLOSEPATH = 1, 2, 7
POINT, LINESTRING, POLYGON = 1, 2, 3


# ---------------------------------------------------------------- protobuf
def _varint(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def _tag(field: int, wire: int) -> bytes:
    return _varint((field << 3) | wire)


def _uint_field(field: int, n: int) -> bytes:
    return _tag(field, 0) + _varint(n)


def _bytes_field(field: int, data: bytes) -> bytes:
    return _tag(field, 2) + _varint(len(data)) + data


def _str_field(field: int, s: str) -> bytes:
    return _bytes_field(field, s.encode("utf-8"))


def zigzag(n: int) -> int:
    """32/64 位有符号 -> 无符号 zigzag 映射(坐标增量都在 int64 范围内)。"""
    return (n << 1) ^ (n >> 63)


# ---------------------------------------------------------------- values
def _value_msg(v) -> bytes:
    if isinstance(v, bool):
        return _uint_field(7, 1 if v else 0)                 # bool_value
    if isinstance(v, int):
        return _uint_field(4, v & 0xFFFFFFFFFFFFFFFF)        # int64_value
    if isinstance(v, float):
        return _tag(3, 1) + struct.pack("<d", v)             # double_value
    return _str_field(1, str(v))                             # string_value


# ---------------------------------------------------------------- geometry
def _signed_area(ring) -> float:
    s = 0
    for (x1, y1), (x2, y2) in zip(ring, ring[1:]):
        s += x1 * y2 - x2 * y1
    return s / 2.0


def _emit_point(cmds, x, y, cursor):
    dx, dy = x - cursor[0], y - cursor[1]
    cmds.append(zigzag(dx))
    cmds.append(zigzag(dy))
    cursor[0], cursor[1] = x, y


def encode_geometry(gtype: int, parts) -> list[int]:
    """把整数坐标几何编成命令序列。

    parts: POINT -> [(x,y),...]; LINESTRING -> [[(x,y),...],...];
           POLYGON  -> [ring, ring, ...](ring 不含重复闭合点;每个多边形
           的 shell 紧跟其 holes;外环鞋带面积为正、内环为负,由调用方定向)
    """
    cmds: list[int] = []
    cursor = [0, 0]

    if gtype == POINT:
        pts = [p for p in parts]
        if not pts:
            return []
        cmds.append((len(pts) << 3) | MOVETO)
        for x, y in pts:
            _emit_point(cmds, x, y, cursor)

    elif gtype == LINESTRING:
        for line in parts:
            pts = _dedupe(line)
            if len(pts) < 2:
                continue
            cmds.append((1 << 3) | MOVETO)
            _emit_point(cmds, *pts[0], cursor)
            cmds.append(((len(pts) - 1) << 3) | LINETO)
            for p in pts[1:]:
                _emit_point(cmds, *p, cursor)

    elif gtype == POLYGON:
        # 调用方保证:每个多边形的 shell 紧跟其 holes,且已按瓦片坐标系
        # 定向(外环鞋带面积为正、内环为负)。这里只剔除退化环。
        for r in parts:
            r = _dedupe(r)
            if len(r) < 3 or _signed_area(r + [r[0]]) == 0:
                continue
            cmds.append((1 << 3) | MOVETO)
            _emit_point(cmds, *r[0], cursor)
            cmds.append(((len(r) - 1) << 3) | LINETO)
            for p in r[1:]:
                _emit_point(cmds, *p, cursor)
            cmds.append((1 << 3) | CLOSEPATH)

    return cmds


def _dedupe(line):
    """去掉连续重复点(整数化后可能塌缩)和闭合重复点。"""
    out = []
    for p in line:
        if not out or p != out[-1]:
            out.append(p)
    if len(out) > 1 and out[0] == out[-1]:
        out.pop()
    return out


# ---------------------------------------------------------------- layers
def encode_layer(name: str, features, extent: int = 4096) -> bytes:
    """features: iterable of (fid, gtype, parts, props)。返回 tile.layers 字段字节。"""
    keys: list[str] = []
    key_idx: dict[str, int] = {}
    vals: list = []
    val_idx: dict = {}
    feat_msgs = []

    for fid, gtype, parts, props in features:
        geom = encode_geometry(gtype, parts)
        if not geom:
            continue
        tags = []
        for k, v in (props or {}).items():
            if v is None:
                continue
            ki = key_idx.get(k)
            if ki is None:
                ki = len(keys)
                key_idx[k] = ki
                keys.append(k)
            vkey = (type(v).__name__, v)
            vi = val_idx.get(vkey)
            if vi is None:
                vi = len(vals)
                val_idx[vkey] = vi
                vals.append(v)
            tags += [ki, vi]

        msg = b""
        if fid is not None:
            msg += _uint_field(1, int(fid) & 0xFFFFFFFFFFFFFFFF)
        msg += _bytes_field(2, b"".join(_varint(t) for t in tags))
        msg += _uint_field(3, gtype)
        msg += _bytes_field(4, b"".join(_varint(g) for g in geom))
        feat_msgs.append(msg)

    body = _uint_field(15, 2)              # version = 2(规范要求)
    body += _str_field(1, name)
    for fm in feat_msgs:
        body += _bytes_field(2, fm)
    for k in keys:
        body += _str_field(3, k)
    for v in vals:
        body += _bytes_field(4, _value_msg(v))
    body += _uint_field(5, extent)
    return body


def encode_tile(layers) -> bytes:
    """layers: iterable of (name, features) 或 (name, features, extent)。"""
    out = b""
    for item in layers:
        if len(item) == 3:
            name, features, extent = item
        else:
            name, features = item
            extent = 4096
        out += _bytes_field(3, encode_layer(name, features, extent))
    return out
