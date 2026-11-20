"""Simgeler; kodla çizilir, dosya gerekmez.

- Dört uyumlu logo (rgba / png_data / ico_bytes): koyu yeşil yuvarlak zemin, kendi şekli, ortada aynı parlak nokta.
- Aynı şekillerin zeminsiz, tek renkli hali (mono_png_data): simge rayı için.
- Cihaz simgeleri (device_png_data): Pano'daki cihaz daireleri için ince çizgili, tek renkli.
"""
import base64
import math
import struct
import zlib

BACK, EDGE, CORE = (22, 48, 42), (58, 92, 80), (255, 255, 255)
MINT, PEACH, SKY = (166, 224, 198), (245, 165, 108), (142, 190, 255)
NAMES = ("lightcore", "lightrgb", "lightfan", "lightsnap")
DEVICES = ("board", "ram", "gpu", "fan", "strip", "bulb", "keyboard", "mouse", "headset", "speaker", "case", "generic")
LINE = 0.13  # cihaz simgelerinde çizgi kalınlığı (-1..1 ölçeğinde)


def _clamp(v):
    return min(1.0, max(0.0, v))


def _box(px, py, half, r):
    qx, qy = abs(px) - half + r, abs(py) - half + r
    return math.hypot(max(qx, 0), max(qy, 0)) + min(max(qx, qy), 0) - r


def _rect(px, py, cx, cy, hx, hy, r):
    """Yuvarlak köşeli dikdörtgene uzaklık (içi eksi)."""
    qx, qy = abs(px - cx) - hx + r, abs(py - cy) - hy + r
    return math.hypot(max(qx, 0), max(qy, 0)) + min(max(qx, qy), 0) - r


def _frame(px, py, cx, cy, hx, hy, r):
    """İçi boş yuvarlak köşeli çerçeve."""
    return abs(_rect(px, py, cx, cy, hx, hy, r)) - LINE / 2


def _ring(px, py, cx, cy, radius, width):
    return abs(math.hypot(px - cx, py - cy) - radius) - width / 2


def _dot(px, py, cx, cy, radius):
    return math.hypot(px - cx, py - cy) - radius


def _seg(px, py, ax, ay, bx, by, width=LINE):
    """Yuvarlak uçlu doğru parçası."""
    dx, dy = bx - ax, by - ay
    t = _clamp(((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy))
    return math.hypot(px - ax - t * dx, py - ay - t * dy) - width / 2


def _arc(px, py, radius, width, start, end):
    """Yuvarlak uçlu yay; açılar derece."""
    a = math.degrees(math.atan2(py, px)) % 360
    if (a - start) % 360 <= (end - start) % 360:
        return abs(math.hypot(px, py) - radius) - width / 2
    d = float("inf")
    for angle in (start, end):
        qx, qy = radius * math.cos(math.radians(angle)), radius * math.sin(math.radians(angle))
        d = min(d, math.hypot(px - qx, py - qy) - width / 2)
    return d


def _blades(px, py, scale=1.0):
    d = float("inf")
    for i in range(3):
        angle = math.radians(i * 120 + 12)
        x = (px * math.cos(angle) + py * math.sin(angle)) / scale
        y = (-px * math.sin(angle) + py * math.cos(angle)) / scale
        blade = math.hypot((x - 0.36) / 0.3, (y - 0.09 * math.sin(x * 4)) / 0.145) - 1
        d = min(d, blade * 0.145 * scale)
    return d


def _shapes(name, px, py):
    """(uzaklık, renk) listesi, alttan üste. Koordinatlar -1..1."""
    if name == "lightcore":
        return [(_arc(px, py, 0.5, 0.15, 280, 40), MINT), (_arc(px, py, 0.5, 0.15, 40, 160), PEACH),
                (_arc(px, py, 0.5, 0.15, 160, 280), SKY)]
    if name == "lightrgb":
        r, d = 0.34, 0.2
        return [(_ring(px, py, 0, -d, r, 0.1), PEACH),
                (_ring(px, py, d * 0.87, d * 0.5, r, 0.1), MINT),
                (_ring(px, py, -d * 0.87, d * 0.5, r, 0.1), SKY)]
    if name == "lightfan":
        return [(_blades(px, py), MINT)]
    edge, length, width = 0.56, 0.3, 0.11  # lightsnap: dört köşe çerçevesi
    ax, ay = abs(px), abs(py)
    d = min(math.hypot(max(ax - edge, edge - length - ax, 0), abs(ay - edge)) - width / 2,
            math.hypot(abs(ax - edge), max(ay - edge, edge - length - ay, 0)) - width / 2)
    return [(d, PEACH)]


def _device(kind, px, py):
    """Cihaz simgesinin parçalarına uzaklıklar. Koordinatlar -1..1."""
    if kind == "board":
        return [_frame(px, py, 0, 0, 0.66, 0.66, 0.14), _frame(px, py, 0, 0, 0.26, 0.26, 0.06)]
    if kind == "ram":
        return [_frame(px, py, 0, -0.1, 0.8, 0.3, 0.08)] + [_seg(px, py, x, 0.34, x, 0.5) for x in (-0.48, -0.16, 0.16, 0.48)]
    if kind == "gpu":
        return [_frame(px, py, 0, 0, 0.82, 0.42, 0.12), _ring(px, py, -0.3, 0, 0.2, LINE), _ring(px, py, 0.38, 0, 0.1, LINE)]
    if kind == "fan":
        return [_ring(px, py, 0, 0, 0.78, LINE), _blades(px, py, 0.85), _dot(px, py, 0, 0, 0.1)]
    if kind == "strip":
        return [_frame(px, py, 0, 0, 0.82, 0.24, 0.24)] + [_dot(px, py, x, 0, 0.08) for x in (-0.46, 0, 0.46)]
    if kind == "bulb":
        return [_ring(px, py, 0, -0.2, 0.44, LINE), _seg(px, py, -0.2, 0.44, 0.2, 0.44), _seg(px, py, -0.12, 0.66, 0.12, 0.66)]
    if kind == "keyboard":
        return ([_frame(px, py, 0, 0, 0.82, 0.46, 0.12), _seg(px, py, -0.3, 0.2, 0.3, 0.2)]
                + [_dot(px, py, x, -0.14, 0.07) for x in (-0.48, -0.16, 0.16, 0.48)])
    if kind == "mouse":
        return [_frame(px, py, 0, 0, 0.42, 0.7, 0.42), _seg(px, py, 0, -0.42, 0, -0.18)]
    if kind == "headset":
        return [_arc(px, py - 0.1, 0.6, LINE, 180, 360), _rect(px, py, -0.6, 0.3, 0.13, 0.24, 0.1), _rect(px, py, 0.6, 0.3, 0.13, 0.24, 0.1)]
    if kind == "speaker":
        return [_frame(px, py, 0, 0, 0.46, 0.74, 0.12), _ring(px, py, 0, 0.24, 0.2, LINE), _dot(px, py, 0, -0.4, 0.08)]
    if kind == "case":
        return [_frame(px, py, 0, 0, 0.5, 0.76, 0.1), _seg(px, py, -0.2, -0.44, 0.2, -0.44), _dot(px, py, 0, 0.36, 0.09)]
    return [_ring(px, py, 0, 0, 0.56, LINE), _dot(px, py, 0, 0, 0.14)]


def rgba(name, size, core=CORE):
    """Boyut x boyut RGBA bayt (üstten alta). core: ortadaki noktanın rengi."""
    out = bytearray()
    aa = 1.0 / (size / 2)
    for y in range(size):
        py = (y + 0.5) / size * 2 - 1
        for x in range(size):
            px = (x + 0.5) / size * 2 - 1
            box = _box(px, py, 0.94, 0.26)
            alpha = _clamp(0.5 - box / aa)
            c = [BACK[i] + (EDGE[i] - BACK[i]) * _clamp(0.5 - abs(box + 0.02) / aa) for i in range(3)]
            for dist, color in _shapes(name, px, py):
                cover = _clamp(0.5 - dist / aa)
                c = [u + (v - u) * cover for u, v in zip(c, color)]
            center = math.hypot(px, py)
            glow = 0.3 * _clamp(1 - (center - 0.13) / 0.14) if center > 0.13 else 0
            c = [u + (v - u) * glow for u, v in zip(c, core)]
            cover = _clamp(0.5 - (center - 0.13) / aa)
            c = [u + (v - u) * cover for u, v in zip(c, core)]
            out += bytes((round(c[0]), round(c[1]), round(c[2]), round(255 * alpha)))
    return bytes(out)


def _flat(size, color, distances):
    """Tek renkli, zeminsiz RGBA: distances(px, py) parçalara uzaklıkları verir."""
    out = bytearray()
    aa = 1.0 / (size / 2)
    rgb = bytes(color)
    for y in range(size):
        py = (y + 0.5) / size * 2 - 1
        for x in range(size):
            px = (x + 0.5) / size * 2 - 1
            out += rgb + bytes((round(255 * _clamp(0.5 - min(distances(px, py)) / aa)),))
    return bytes(out)


def mono_rgba(name, size, color):
    """Logonun şekli, zeminsiz ve tek renkli (biraz büyütülmüş: zemin payı yok)."""
    return _flat(size, color, lambda px, py: [d for d, _c in _shapes(name, px * 0.72, py * 0.72)]
                 + ([_dot(px * 0.72, py * 0.72, 0, 0, 0.12)] if name != "lightsnap" else []))


def device_rgba(kind, size, color):
    return _flat(size, color, lambda px, py: _device(kind, px, py))


def _png(data, size):
    raw = b"".join(b"\0" + data[y * size * 4:(y + 1) * size * 4] for y in range(size))

    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


def png_bytes(name, size):
    return _png(rgba(name, size), size)


def png_data(name, size):
    """Tk PhotoImage(data=...) için."""
    return base64.b64encode(png_bytes(name, size))


def mono_png_data(name, size, color):
    return base64.b64encode(_png(mono_rgba(name, size, color), size))


def device_png_data(kind, size, color):
    return base64.b64encode(_png(device_rgba(kind, size, color), size))


def ico_bytes(name, sizes=(16, 32, 48, 256)):
    """Windows .ico dosyası: kısayol ve exe simgesi için."""
    images = [png_bytes(name, s) for s in sizes]
    out = struct.pack("<HHH", 0, 1, len(sizes))
    offset = 6 + 16 * len(sizes)
    for size, image in zip(sizes, images):
        out += struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(image), offset)
        offset += len(image)
    return out + b"".join(images)
