"""Ekran görüntüsü çekirdeği: yakalama, pano, PNG, yazı işleme, genel kısayol ve yükleme.

Yalnızca standart kütüphane ve Windows GDI. Hiçbir zamanlayıcı yoktur: kısayol iş parçacığı
GetMessage'da uyur, tuşa basılmadıkça işlemci kullanmaz.
"""
import ctypes
import math
import re
import struct
import threading
import time
import zlib
from ctypes import wintypes

from . import __version__

SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN, SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 76, 77, 78, 79
SRCCOPY, CAPTUREBLT = 0x00CC0020, 0x40000000
CF_DIB, GMEM_MOVEABLE = 8, 0x0002
MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_NOREPEAT = 0x1, 0x2, 0x4, 0x4000
WM_HOTKEY, WM_QUIT, WM_APP = 0x0312, 0x0012, 0x8000
VK_SNAPSHOT = 0x2C
DEFAULT_HOTKEYS = {"region": [MOD_CONTROL | MOD_SHIFT, 0x31], "screen": [MOD_CONTROL | MOD_SHIFT, 0x32],
                   "all": [MOD_CONTROL | MOD_SHIFT, 0x33]}
DEFAULT_PROVIDER = "tmpfiles"
PROVIDERS = {
    "tmpfiles": {"label": "tmpfiles (2 gün sonra silinir)", "field": "file",
                 "url": "https://tmpfiles.org/api/v1/upload", "fields": {"expire": "172800"}},
    "uguu": {"label": "Uguu (kısa süre sonra silinir)", "field": "files[]", "url": "https://uguu.se/upload", "fields": {}},
}


def provider_key(key):
    """Ayardaki sağlayıcı; artık yoksa (kaldırılan servis, eski ayar) varsayılan."""
    return key if key == "custom" or key in PROVIDERS else DEFAULT_PROVIDER

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

_H, _I = wintypes.HANDLE, ctypes.c_int
for fn, args, res in (
        (user32.GetDC, [wintypes.HWND], _H), (user32.ReleaseDC, [wintypes.HWND, _H], _I),
        (user32.OpenClipboard, [wintypes.HWND], wintypes.BOOL), (user32.EmptyClipboard, [], wintypes.BOOL),
        (user32.CloseClipboard, [], wintypes.BOOL), (user32.SetClipboardData, [wintypes.UINT, _H], _H),
        (user32.GetCursorPos, [ctypes.POINTER(wintypes.POINT)], wintypes.BOOL),
        (user32.MonitorFromPoint, [wintypes.POINT, wintypes.DWORD], _H),
        (user32.GetMonitorInfoW, [_H, ctypes.c_void_p], wintypes.BOOL),
        (user32.RegisterHotKey, [wintypes.HWND, _I, wintypes.UINT, wintypes.UINT], wintypes.BOOL),
        (user32.UnregisterHotKey, [wintypes.HWND, _I], wintypes.BOOL),
        (user32.GetMessageW, [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT], _I),
        (user32.PostThreadMessageW, [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM], wintypes.BOOL),
        (user32.PeekMessageW, [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT, wintypes.UINT],
         wintypes.BOOL),
        (user32.MapVirtualKeyW, [wintypes.UINT, wintypes.UINT], wintypes.UINT),
        (user32.GetKeyNameTextW, [wintypes.LONG, wintypes.LPWSTR, _I], _I),
        (user32.RegisterClipboardFormatW, [wintypes.LPCWSTR], wintypes.UINT),
        (user32.SetWindowDisplayAffinity, [wintypes.HWND, wintypes.DWORD], wintypes.BOOL),
        (gdi32.CreateCompatibleDC, [_H], _H), (gdi32.DeleteDC, [_H], wintypes.BOOL),
        (gdi32.SelectObject, [_H, _H], _H), (gdi32.DeleteObject, [_H], wintypes.BOOL),
        (gdi32.CreateDIBSection, [_H, ctypes.c_void_p, wintypes.UINT, ctypes.POINTER(ctypes.c_void_p), _H,
                                  wintypes.DWORD], _H),
        (gdi32.BitBlt, [_H, _I, _I, _I, _I, _H, _I, _I, wintypes.DWORD], wintypes.BOOL),
        (gdi32.CreateFontW, [_I] * 5 + [wintypes.DWORD] * 8 + [wintypes.LPCWSTR], _H),
        (gdi32.SetTextColor, [_H, wintypes.DWORD], wintypes.DWORD), (gdi32.SetBkMode, [_H, _I], _I),
        (gdi32.TextOutW, [_H, _I, _I, wintypes.LPCWSTR, _I], wintypes.BOOL),
        (kernel32.GlobalAlloc, [wintypes.UINT, ctypes.c_size_t], _H), (kernel32.GlobalLock, [_H], ctypes.c_void_p),
        (kernel32.GlobalUnlock, [_H], wintypes.BOOL), (kernel32.GlobalFree, [_H], _H),
        (kernel32.GetCurrentThreadId, [], wintypes.DWORD)):
    fn.argtypes, fn.restype = args, res


class ScreenshotError(RuntimeError):
    pass


class _BitmapInfoHeader(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG), ("biYPelsPerMeter", wintypes.LONG),
                ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]


class _MonitorInfo(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT),
                ("dwFlags", wintypes.DWORD)]


class Shot:
    """Yakalanan görüntü. x, y: ekrandaki konumu; bgra: üstten alta, satır başına w*4 bayt."""

    def __init__(self, x, y, w, h, bgra, areas=None):
        self.x, self.y, self.w, self.h, self.bgra = x, y, w, h, bgra
        # Ekranların görüntüdeki yerleri (x1, y1, x2, y2). None: görüntünün tamamı dolu. Farklı boyda
        # ekranlarda aradaki boşluk hiçbir ekrana ait değildir: PNG'de saydam, düz görüntüde koyu gri olur.
        self.areas = areas

    def _alpha(self):
        """Satır satır saydamlık (255 dolu, 0 boş). Aynı satırlar bir kez hesaplanır."""
        rows, known = [], {}
        for y in range(self.h):
            spans = tuple((a[0], a[2]) for a in self.areas if a[1] <= y < a[3])
            if spans not in known:
                row = bytearray(self.w)
                for x1, x2 in spans:
                    row[x1:x2] = b"\xff" * (x2 - x1)
                known[spans] = bytes(row)
            rows.append(known[spans])
        return rows

    def crop(self, x1, y1, x2, y2):
        """Görüntü içi koordinatlarla kırpar."""
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(self.w, x2), min(self.h, y2)
        w, h = max(0, x2 - x1), max(0, y2 - y1)
        stride = self.w * 4
        rows = [self.bgra[y * stride + x1 * 4:y * stride + x2 * 4] for y in range(y1, y2)]
        areas = None
        if self.areas:
            areas = [(max(a[0], x1) - x1, max(a[1], y1) - y1, min(a[2], x2) - x1, min(a[3], y2) - y1) for a in self.areas
                     if a[0] < x2 and a[2] > x1 and a[1] < y2 and a[3] > y1]
        return Shot(self.x + x1, self.y + y1, w, h, b"".join(rows), areas)

    def rgb(self):
        out = bytearray(self.w * self.h * 3)
        out[0::3], out[1::3], out[2::3] = self.bgra[2::4], self.bgra[1::4], self.bgra[0::4]
        return bytes(out)

    def ppm(self, table=None):
        """Tk'nin en hızlı okuduğu biçim. table: bytes.translate tablosu (karartma)."""
        data = self.rgb()
        return b"P6\n%d %d\n255\n" % (self.w, self.h) + (data.translate(table) if table else data)

    def png(self):
        rgb = self.rgb()
        stride = self.w * 3
        color_type = 2
        if self.areas:
            color_type = 6
            alpha = self._alpha()
            rows = []
            for y in range(self.h):
                row = bytearray(self.w * 4)
                row[0::4], row[1::4], row[2::4] = rgb[y * stride::3][:self.w], rgb[y * stride + 1::3][:self.w], rgb[y * stride + 2::3][:self.w]
                row[3::4] = alpha[y]
                rows.append(b"\0" + bytes(row))
            raw = b"".join(rows)
        else:
            raw = b"".join(b"\0" + rgb[y * stride:(y + 1) * stride] for y in range(self.h))

        def chunk(kind, data):
            return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

        return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", self.w, self.h, 8, color_type, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))

    def dib(self):
        """Pano biçimi: 24 bit, alttan üste, satırlar 4 bayta tamamlanır (saydamlık sorunu olmasın)."""
        bgr = bytearray(self.w * self.h * 3)
        bgr[0::3], bgr[1::3], bgr[2::3] = self.bgra[0::4], self.bgra[1::4], self.bgra[2::4]
        stride = self.w * 3
        if self.areas:  # boşluk koyu gri: saydamlığı bilmeyen uygulamalarda göz yormasın
            for y, row in enumerate(self._alpha()):
                x = 0
                while x < self.w:
                    if row[x]:
                        x = row.find(0, x)
                        if x < 0:
                            break
                        continue
                    end = row.find(255, x)
                    end = self.w if end < 0 else end
                    bgr[y * stride + x * 3:y * stride + end * 3] = b"\x20\x20\x20" * (end - x)
                    x = end
        pad = bytes((4 - stride % 4) % 4)
        pixels = b"".join(bgr[y * stride:(y + 1) * stride] + pad for y in range(self.h - 1, -1, -1))
        header = _BitmapInfoHeader(ctypes.sizeof(_BitmapInfoHeader), self.w, self.h, 1, 24, 0, len(pixels))
        return bytes(header) + pixels


def virtual_rect():
    m = user32.GetSystemMetrics
    return m(SM_XVIRTUALSCREEN), m(SM_YVIRTUALSCREEN), m(SM_CXVIRTUALSCREEN), m(SM_CYVIRTUALSCREEN)


def monitor_at_cursor(work=False):
    """İmlecin olduğu ekran (x, y, w, h)."""
    point = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(point))
    info = _MonitorInfo(cbSize=ctypes.sizeof(_MonitorInfo))
    user32.GetMonitorInfoW(user32.MonitorFromPoint(point, 2), ctypes.byref(info))
    r = info.rcWork if work else info.rcMonitor
    return r.left, r.top, r.right - r.left, r.bottom - r.top


def monitors():
    """Bütün ekranlar: [(x, y, w, h)]."""
    found = []
    proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)

    def collect(_monitor, _dc, rect, _data):
        r = rect.contents
        found.append((r.left, r.top, r.right - r.left, r.bottom - r.top))
        return True

    callback = proc(collect)
    user32.EnumDisplayMonitors.argtypes = [wintypes.HANDLE, ctypes.c_void_p, proc, wintypes.LPARAM]
    user32.EnumDisplayMonitors(None, None, callback, 0)
    return found


def monitor_scale():
    """İmlecin olduğu ekranın Windows ölçeği (1.0 = %100)."""
    point = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(point))
    x, y = wintypes.UINT(96), wintypes.UINT(96)
    try:
        api = ctypes.windll.shcore.GetDpiForMonitor
        api.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.POINTER(wintypes.UINT), ctypes.POINTER(wintypes.UINT)]
        api(user32.MonitorFromPoint(point, 2), 0, ctypes.byref(x), ctypes.byref(y))
    except (AttributeError, OSError):
        pass
    return x.value / 96


def _dib_section(dc, w, h):
    header = _BitmapInfoHeader(ctypes.sizeof(_BitmapInfoHeader), w, -h, 1, 32, 0)  # eksi: üstten alta
    bits = ctypes.c_void_p()
    bitmap = gdi32.CreateDIBSection(dc, ctypes.byref(header), 0, ctypes.byref(bits), None, 0)
    if not bitmap or not bits.value:
        raise ScreenshotError(f"Görüntü belleği ayrılamadı ({w}x{h}).")
    return bitmap, bits


def capture(rect):
    x, y, w, h = rect
    screen = user32.GetDC(None)
    dc = gdi32.CreateCompatibleDC(screen)
    bitmap, bits = _dib_section(dc, w, h)
    old = gdi32.SelectObject(dc, bitmap)
    try:
        if not gdi32.BitBlt(dc, 0, 0, w, h, screen, x, y, SRCCOPY | CAPTUREBLT):
            raise ScreenshotError("Ekran okunamadı.")
        return Shot(x, y, w, h, ctypes.string_at(bits, w * h * 4))
    finally:
        gdi32.SelectObject(dc, old)
        gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(dc)
        user32.ReleaseDC(None, screen)


def capture_kind(kind):
    """region ve all bütün ekranları alır (bölge sonradan seçilir); screen imlecin olduğu ekranı."""
    if kind == "screen":
        return capture(monitor_at_cursor())
    rect = virtual_rect()
    shot = capture(rect)
    screens = monitors()
    shot.areas = [(x - rect[0], y - rect[1], x - rect[0] + w, y - rect[1] + h) for x, y, w, h in screens]
    if sum(w * h for _, _, w, h in screens) == rect[2] * rect[3]:
        shot.areas = None  # boşluk yok
    return shot


class _GdiplusInput(ctypes.Structure):
    _fields_ = [("version", ctypes.c_uint32), ("callback", ctypes.c_void_p), ("no_thread", wintypes.BOOL),
                ("no_codecs", wintypes.BOOL)]


class _PointF(ctypes.Structure):
    _fields_ = [("x", ctypes.c_float), ("y", ctypes.c_float)]


_gdiplus = None


def _plus():
    """GDI+: kenarları yumuşatılmış çizgi için (düz GDI çizgisi tırtıklı olur). İlk çizimde yüklenir."""
    global _gdiplus
    if _gdiplus is None:
        lib = ctypes.WinDLL("gdiplus")
        token = ctypes.c_size_t()
        lib.GdiplusStartup(ctypes.byref(token), ctypes.byref(_GdiplusInput(1, None, False, False)), None)
        _gdiplus = lib
    return _gdiplus


def _argb(color):
    return ctypes.c_uint32(0xFF000000 | int(color[1:], 16))


def _stroke(dc, points, color, width, head=False):
    lib = _plus()
    graphics, pen = ctypes.c_void_p(), ctypes.c_void_p()
    lib.GdipCreateFromHDC(wintypes.HDC(dc), ctypes.byref(graphics))
    lib.GdipSetSmoothingMode(graphics, 4)
    lib.GdipCreatePen1(_argb(color), ctypes.c_float(width), 2, ctypes.byref(pen))
    lib.GdipSetPenStartCap(pen, 2)
    lib.GdipSetPenEndCap(pen, 2)
    lib.GdipSetPenLineJoin(pen, 2)
    line = list(points)
    if head:
        (x1, y1), (x2, y2) = line
        size = max(math.hypot(x2 - x1, y2 - y1), 1)
        ux, uy = (x2 - x1) / size, (y2 - y1) / size
        length, half = width * 4.5, width * 2.2
        bx, by = x2 - ux * length, y2 - uy * length
        line = [(x1, y1), (bx, by)]
        tip = (_PointF * 3)((x2, y2), (bx - uy * half, by + ux * half), (bx + uy * half, by - ux * half))
        brush = ctypes.c_void_p()
        lib.GdipCreateSolidFill(_argb(color), ctypes.byref(brush))
        lib.GdipFillPolygon2(graphics, brush, tip, 3)
        lib.GdipDeleteBrush(brush)
    array = (_PointF * len(line))(*[tuple(q) for q in line])
    if len(line) == 1:
        lib.GdipDrawLine(graphics, pen, ctypes.c_float(line[0][0]), ctypes.c_float(line[0][1]),
                         ctypes.c_float(line[0][0] + 0.1), ctypes.c_float(line[0][1]))
    else:
        lib.GdipDrawLines(graphics, pen, array, len(line))
    lib.GdipDeletePen(pen)
    lib.GdipDeleteGraphics(graphics)


def draw_annotations(shot, annotations, dx=0, dy=0, font="Segoe UI"):
    """Görüntüye kalıcı çizer. annotations (sırayla):
    ("text", x, y, metin, "#RRGGBB", piksel), ("arrow", x1, y1, x2, y2, renk, kalınlık),
    ("pen", [(x, y), ...], renk, kalınlık), ("blur", x1, y1, x2, y2): alanı geri döndürülemez şekilde
    kareler. dx, dy: koordinatlara eklenir (kırpılmış parça için)."""
    if not annotations:
        return shot
    pixels = _pixelate(shot, [a[1:] for a in annotations if a[0] == "blur"], dx, dy)
    dc = gdi32.CreateCompatibleDC(None)
    bitmap, bits = _dib_section(dc, shot.w, shot.h)
    old = gdi32.SelectObject(dc, bitmap)
    try:
        ctypes.memmove(bits, bytes(pixels), len(pixels))
        gdi32.SetBkMode(dc, 1)  # saydam zemin
        for a in annotations:
            if a[0] == "text":
                _, x, y, text, color, px = a
                handle = gdi32.CreateFontW(-px, 0, 0, 0, 700, 0, 0, 0, 1, 0, 0, 4, 0, font)  # 4: kenar yumuşatma
                previous = gdi32.SelectObject(dc, handle)
                r, g, b = (int(color[i:i + 2], 16) for i in (1, 3, 5))
                gdi32.SetTextColor(dc, r | g << 8 | b << 16)
                gdi32.TextOutW(dc, x + dx, y + dy, text, len(text))
                gdi32.SelectObject(dc, previous)
                gdi32.DeleteObject(handle)
            elif a[0] == "blur":
                continue
            elif a[0] == "arrow":
                _, x1, y1, x2, y2, color, width = a
                _stroke(dc, [(x1 + dx, y1 + dy), (x2 + dx, y2 + dy)], color, width, head=True)
            else:
                _, points, color, width = a
                _stroke(dc, [(x + dx, y + dy) for x, y in points], color, width)
        return Shot(shot.x, shot.y, shot.w, shot.h, ctypes.string_at(bits, shot.w * shot.h * 4), shot.areas)
    finally:
        gdi32.SelectObject(dc, old)
        gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(dc)


def _pixelate(shot, boxes, dx, dy, block=18):
    """Her kareyi tek renge indirir: düz bulanıklaştırmanın aksine yazı geri çıkarılamaz."""
    if not boxes:
        return shot.bgra
    data = bytearray(shot.bgra)
    stride = shot.w * 4
    for x1, y1, x2, y2 in boxes:
        x1, x2 = max(0, x1 + dx), min(shot.w, x2 + dx)
        y1, y2 = max(0, y1 + dy), min(shot.h, y2 + dy)
        for by in range(y1, y2, block):
            bh = min(block, y2 - by)
            for bx in range(x1, x2, block):
                bw = min(block, x2 - bx)
                # kaydırılmış parçada da aynı kareler çıksın diye yalnızca kendi alanından örnek alınır
                center = (by + bh // 2) * stride + (bx + bw // 2) * 4
                row = bytes(data[center:center + 4]) * bw
                for y in range(by, by + bh):
                    data[y * stride + bx * 4:y * stride + (bx + bw) * 4] = row
    return bytes(data)


def copy_to_clipboard(shot):
    """Görüntüyü panoya koyar; Ctrl+V ile yapıştırılır. Pano başka programdaysa kısa süre yeniden dener."""
    data = shot.dib()
    memory = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
    if not memory:
        raise ScreenshotError("Pano için bellek ayrılamadı.")
    target = kernel32.GlobalLock(memory)
    ctypes.memmove(target, data, len(data))
    kernel32.GlobalUnlock(memory)
    for _ in range(20):
        if user32.OpenClipboard(None):
            break
        time.sleep(0.05)
    else:
        kernel32.GlobalFree(memory)
        raise ScreenshotError("Pano başka bir program tarafından kullanılıyor.")
    try:
        user32.EmptyClipboard()
        if not user32.SetClipboardData(CF_DIB, memory):
            kernel32.GlobalFree(memory)
            raise ScreenshotError("Görüntü panoya yazılamadı.")
        if shot.areas:  # destekleyen uygulamalar boşluğu saydam görsün
            png = shot.png()
            extra = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(png))
            if extra:
                target = kernel32.GlobalLock(extra)
                ctypes.memmove(target, png, len(png))
                kernel32.GlobalUnlock(extra)
                if not user32.SetClipboardData(user32.RegisterClipboardFormatW("PNG"), extra):
                    kernel32.GlobalFree(extra)
    finally:
        user32.CloseClipboard()


def hide_from_capture(hwnd):
    """Bu pencere ekran görüntülerinde çıkmaz (bildirim kutusu sonraki görüntüye girmesin)."""
    user32.SetWindowDisplayAffinity(hwnd, 0x11)


def key_name(mods, vk):
    parts = []
    for bit, name in ((MOD_CONTROL, "Ctrl"), (MOD_ALT, "Alt"), (MOD_SHIFT, "Shift")):
        if mods & bit:
            parts.append(name)
    if vk == VK_SNAPSHOT:
        parts.append("PrtScn")
    elif 0x30 <= vk <= 0x5A:
        parts.append(chr(vk))
    elif 0x70 <= vk <= 0x87:
        parts.append(f"F{vk - 0x6F}")
    else:
        buffer = ctypes.create_unicode_buffer(32)
        scan = user32.MapVirtualKeyW(vk, 0)
        parts.append(buffer.value if user32.GetKeyNameTextW(scan << 16, buffer, 32) else f"Tuş {vk}")
    return "+".join(parts)


class Hotkeys:
    """Genel kısayollar. RegisterHotKey iş parçacığına bağlıdır; bu iş parçacığı GetMessage'da uyur."""

    def __init__(self, on_press, on_result):
        self.on_press, self.on_result = on_press, on_result  # on_result(alınamayan adlar)
        self.bindings = {}
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self._ready.wait(2)

    def set(self, bindings):
        self.bindings = dict(bindings)
        user32.PostThreadMessageW(self._id, WM_APP, 0, 0)

    def stop(self):
        user32.PostThreadMessageW(self._id, WM_QUIT, 0, 0)
        self._thread.join(1)

    def _register(self, names):
        for i in range(len(names)):
            user32.UnregisterHotKey(None, i + 1)
        names[:] = list(self.bindings)
        failed = []
        for i, name in enumerate(names):
            mods, vk = self.bindings[name]
            if not user32.RegisterHotKey(None, i + 1, mods | MOD_NOREPEAT, vk):
                failed.append(name)
        self.on_result(failed)

    def _run(self):
        self._id = kernel32.GetCurrentThreadId()
        msg = wintypes.MSG()
        user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0)  # iş parçacığının mesaj kuyruğu oluşsun
        self._ready.set()
        names = []
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_APP:
                self._register(names)
            elif msg.message == WM_HOTKEY and 0 < msg.wParam <= len(names):
                self.on_press(names[msg.wParam - 1])
        for i in range(len(names)):
            user32.UnregisterHotKey(None, i + 1)


def upload(png, provider, custom=None, timeout=60):
    """PNG'yi yükler, bağlantıyı döndürür. provider: PROVIDERS anahtarı ya da "custom"
    (custom: {"url", "field"}: dosyayı çok parçalı form ile alıp yanıtta bağlantı veren sunucu)."""
    import urllib.error
    import urllib.request
    if provider == "custom":
        custom = custom or {}
        target = {"url": custom.get("url", ""), "field": custom.get("field") or "file", "fields": {}}
        if not target["url"].startswith(("http://", "https://")):
            raise ScreenshotError("Özel sunucu adresi http:// ya da https:// ile başlamalı.")
    else:
        target = PROVIDERS[provider]
    boundary = "lightcore" + hex(int(time.time() * 1000))[2:]
    body = bytearray()
    for name, value in target["fields"].items():
        body += f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
    body += (f'--{boundary}\r\nContent-Disposition: form-data; name="{target["field"]}"; '
             f'filename="ekran.png"\r\nContent-Type: image/png\r\n\r\n').encode()
    body += png + f"\r\n--{boundary}--\r\n".encode()
    request = urllib.request.Request(target["url"], data=bytes(body), method="POST", headers={
        "Content-Type": f"multipart/form-data; boundary={boundary}", "User-Agent": f"LightCore/{__version__}"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as reply:
            text = reply.read(65536).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        detail = e.read(200).decode("utf-8", "replace").strip()
        raise ScreenshotError(f"Sunucu reddetti (HTTP {e.code}): {detail or e.reason}. Başka bir sağlayıcı dene.")
    except (OSError, ValueError) as e:
        raise ScreenshotError(f"Sunucuya ulaşılamadı: {e}")
    match = re.search(r"https?://[^\s\"'<>\\]+", text.replace("\\/", "/"))
    if not match:
        raise ScreenshotError(f"Sunucu bağlantı döndürmedi: {text[:200]}")
    return match.group(0)
