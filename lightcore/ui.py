"""Masaüstü arayüzü (tkinter), Windows 11 Ayarlar düzeninde.

Pencere çerçevesizdir (chrome.py). Solda: sayfalar (Işıklar, Fanlar, Log), cihaz listesi (aç/kapat anahtarlı) ve
Yeniden tara düğmesi. Sağda: seçili sayfanın kartları.

OpenRGB başlatma, cihaz taraması ve Hue işlemleri arka plan iş parçacıklarında yapılır;
pencere hemen açılır. Efekt motoru Runner ile tek sırada çalışır: her ayar değişiminde
eski iş durdurulur, yenisi eski iş tamamen bitince başlar (arayüz hiç beklemez).
"""
import base64
import colorsys
import gc
import json
import math
import os
import platform
import queue
import re
import select
import shutil
import struct
import sys
import threading
import time
import tkinter as tk
import zlib
from tkinter import font as tkfont

from . import __version__, dpi, icons, lang
from . import effects as fx
from .dpi import S
from .chrome import Window
from .engine import BUS_DEVICE_KINDS, DROPPED_TEXT, apply, apply_parallel, hold_color, jrgb_zones, resize_fan_zones
from . import ble as blestrip
from .ble import BleError, BleSink, BleStrip
from .hue import HueBridge, HueError, HueLinkButtonNotPressed, HueSink, discover
from .launcher import start_openrgb, stop_openrgb
from .openrgb_client import OpenRGBClient, ProtocolError
from .tray import Tray, already_running
from .usage import ProcessUsage, current_process, set_efficiency

dpi.install()

LOG_NAME = "lightcore_log.txt"
CONFIG_NAME = "lightcore_config.json"
THEMES = (("dark", "Koyu"), ("light", "Aydınlık"))


def saved_theme():
    """Tema ayar dosyasından okunur: renkler modül yüklenirken belirlenir, bu yüzden değişiklik
    programı yeniden açınca geçerli olur. Ayar yoksa koyu."""
    try:
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        with open(os.path.join(base, "LightCore", CONFIG_NAME), encoding="utf-8") as f:
            theme = json.load(f).get("theme")
    except (OSError, ValueError, AttributeError):
        theme = None
    return theme if theme in dict(THEMES) else "dark"


THEME = saved_theme()


def _pick(dark, light):
    return light if THEME == "light" else dark


# Koyu: çok koyu yeşil-siyah zemin, nane vurgu. Açık: nane zemin, beyaz kartlar, koyu yeşil vurgu.
BG = _pick("#0E1412", "#E2ECE7")
CARD = _pick("#18211D", "#FFFFFF")
CONTROL = _pick("#1F2A25", "#F0F5F2")
CONTROL_HOVER = _pick("#29362F", "#E4EDE8")
CONTROL_EDGE = _pick("#33433C", "#CFDBD5")
NAV_SELECTED = _pick("#22302A", "#D3E3DB")
TEXT = _pick("#EDF4F0", "#13221C")
MUTED = _pick("#93A59D", "#62736B")
DIM = _pick("#3A4A43", "#C4D1CA")
ACCENT = _pick("#6FD0A8", "#1E7A5C")
ON_ACCENT = _pick("#0B1F17", "#FFFFFF")
IOS_GREEN = "#34C759"  # iOS anahtarı açık
IOS_OFF = _pick("#4B5A53", "#B5C2BB")  # iOS anahtarı kapalı
GREEN = _pick("#6FD08F", "#1F8F55")
AMBER = _pick("#F0C14A", "#A66A00")
RED = _pick("#FF9A9A", "#C43D3D")
KNOB = _pick("#2A3731", "#FFFFFF")  # kaydırma çubuğunun düğmesi
# Simge rayı ve Pano'nun üst kartı: zeminden ayrışan koyu yeşil, üstünde açık yazı.
RAIL = _pick("#1A2B25", "#16302A")
RAIL_END = _pick("#22473B", "#215446")  # rayın alt ucu: yukarıdan aşağı hafif renk geçişi
RAIL_INK = "#EEF6F1"
RAIL_TEXT = _pick("#9DB5AB", "#A5BDB3")
RAIL_HOVER = _pick("#2A443A", "#24463D")
RAIL_ON = "#A6E0C6"
RAIL_ON_TEXT = "#10261F"
PEACH, ON_PEACH = "#F5A56C", "#3B1C07"  # tek vurgu düğmesi
FONT = "Segoe UI"
BOLD = "Segoe UI Semibold"

LOG_MAX_BYTES = 512 * 1024
STALL_SECONDS = 3.0  # hareketli efektte bu kadar süre yazılamayan cihaz "Takıldı" sayılır
FPS = 25  # hareketli efektler; LED geçişlerinde gözle akıcı, fazlası yalnızca CPU harcar
# Olaylar zamanlayıcı beklemez (WakeQueue). Zamanlayıcı yalnızca pencere açıkken, canlı gösterge içindir;
# pencere tepsideyken hiç zamanlayıcı yoktur.
POLL_ANIMATED_MS = 250  # efekt hareketli
POLL_STILL_MS = 1000  # sabit renk
LIVE_MS = 40  # canlı renk göstergeleri (önizleme şeridi, noktalar, Pano): efekt hareketliyken saniyede 25 kare
RGB_RECOVERY_SECONDS = 600.0  # cihaz düşünce OpenRGB en fazla bu aralıkla bir kez yeniden başlatılır
HOLD_SECONDS = 6.0  # kapanırken son rengi yazmak için en fazla bu kadar beklenir
MAX_COLORS = 14
LIGHT_KEYS = ("selected", "mode", "brightness", "speed", "hold", "fade", "off")

PALETTE = ["8B7CFF", "3B82F6", "2ECC8F", "FB923C", "F43F5E"]
def _cycle(*colors, brightness=90):
    return dict(selected=list(colors), mode="cycle", brightness=brightness, speed=3, hold=1.5, fade=3.0, off=False)


PRESETS = {
    "Oyun": dict(selected=["8B7CFF", "3B82F6", "FB923C"], mode="cycle",
                 brightness=100, speed=5, hold=1.0, fade=2.0, off=False),
    "Okyanus": dict(selected=["0EA5E9", "14B8A6", "3B82F6"], mode="cycle",
                    brightness=80, speed=3, hold=2.0, fade=3.0, off=False),
    "Gün batımı": dict(selected=["FB923C", "F43F5E", "8B5CF6"], mode="cycle",
                       brightness=85, speed=3, hold=2.0, fade=3.0, off=False),
    "Siber": _cycle("00F0FF", "FF00C8", "7B2FFF"),
    "Vaporwave": _cycle("FF71CE", "01CDFE", "B967FF"),
    "Galaksi": _cycle("6D28D9", "1E3A8A", "DB2777"),
    "Orman": _cycle("22C55E", "15803D", "A3E635", brightness=80),
    "Buzul": _cycle("E0F2FE", "7DD3FC", "3B82F6", brightness=80),
    "Sakura": _cycle("FFB7C5", "FF6FA3", "FFF0F5", brightness=80),
    "Şeker": _cycle("FF9CEE", "B28DFF", "85E3FF"),
    "Altın": _cycle("FFD700", "FFA500", "FFF4C2", brightness=85),
    "Retro": _cycle("FF6B35", "F7C59F", "2EC4B6"),
    "Matrix": dict(selected=["00FF41", "008F11"], mode="breathe", brightness=90, speed=4, off=False),
    "Şömine": dict(selected=[], mode="fire", brightness=90, speed=5, off=False),
    "Kuzey ışığı": dict(selected=[], mode="aurora", brightness=90, speed=4, off=False),
    "Gece": dict(selected=["FB923C"], mode="static", brightness=15, speed=3, off=False),
}
# Hepsi cihazın tamamını tek renkte sürer ve renk zamanla yumuşakça değişir;
# fanlar, Hue lambaları dahil her cihazda aynı şekilde çalışır.
MODES = (("static", "Sabit"), ("cycle", "Renk geçişi"), ("breathe", "Nefes"),
         ("flow", "Akış"), ("rainbow", "Gökkuşağı"), ("pastel", "Pastel"), ("pulse", "Nabız"), ("candle", "Mum"),
         ("neon", "Neon"), ("twinkle", "Yıldız"), ("disco", "Disko"), ("alarm", "Alarm"),
         ("fire", "Ateş"), ("lava", "Lav"), ("aurora", "Aurora"), ("ocean", "Okyanus"), ("storm", "Fırtına"))
MODE_NOTES = {
    "static": "Seçili ilk renk sabit yanar", "cycle": "Seçili renkler arasında yumuşakça geçer",
    "breathe": "Yavaşça yanıp söner; birden çok renk seçiliyse her nefeste sıradakine geçer",
    "flow": "Seçili renkler arasında beklemeden, sürekli akar",
    "rainbow": "Bütün renkler arasında döner", "pastel": "Gökkuşağının yumuşak tonları",
    "pulse": "Kalp atışı gibi iki vuruş", "candle": "Mum ışığı gibi hafif titrer",
    "neon": "Neon tabela gibi sabit yanar, ara sıra titrer", "twinkle": "Loş zeminde ara sıra parlar",
    "disco": "Renkler vuruşla değişir; 2 renkten azı seçiliyse rastgele renkler",
    "alarm": "İki renk arasında hızlı gidip gelir; 2 renk seçili değilse kırmızı-mavi",
    "fire": "Kızıl ve sarı arasında düzensiz titrer (kendi renkleri)",
    "lava": "Koyu kırmızı ve turuncu, ağır dalgalanma (kendi renkleri)",
    "aurora": "Kuzey ışıkları: yeşil, turkuaz, mor (kendi renkleri)",
    "ocean": "Lacivert ve turkuaz dalgalar (kendi renkleri)",
    "storm": "Koyu mavi zeminde ara sıra şimşek (kendi renkleri)"}
# OpenRGB cihaz türü -> liste başlığı
KIND_TITLES = {0: "Anakart", 1: "RAM", 2: "Ekran Kartı", 3: "Soğutucu", 4: "LED Şerit",
               5: "Klavye", 6: "Fare", 7: "Mousepad", 8: "Kulaklık", 11: "Işık", 12: "Hoparlör",
               15: "Kasa"}
# sayfa, simge, ad, ait olduğu özellik
PAGES = (("dash", "▦", "Pano", None), ("lights", "lightrgb", "LightRGB", "rgb"), ("fans", "lightfan", "LightFan", "fan"),
         ("shots", "lightsnap", "LightSnap", "snap"), ("settings", "⚙", "Ayarlar", None), ("log", "≣", "Log", None))


def program_dir():
    """Kayıt ve ayar dosyalarının yeri: lightcore klasörünün yanı. Çalışma klasörü
    kullanılmaz; yönetici olarak açılınca o C:\\Windows\\System32 olur."""
    if getattr(sys, "frozen", False):  # build_exe.bat ile üretilen exe
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def config_dir():
    """Ayarlar (Hue eşleşmesi, fan ayarları, kapalı cihazlar) kullanıcıya ait sabit bir
    yerde tutulur: yeni sürüm başka bir klasöre açılınca kaybolmasın."""
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    return os.path.join(base, "LightCore")


def load_config(path):
    """Ayar dosyasını okur; yoksa boş ayar. Okunamayan dosya silinmez, yanına .bozuk olarak taşınır:
    ilk kayıt onun üstüne boş ayar yazmasın, gerekirse elle kurtarılsın."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        pass
    try:
        os.replace(path, path + ".bozuk")
    except OSError:
        pass
    return {}


def _hex(rgb):
    return "#%02X%02X%02X" % tuple(rgb)


def _clamp(v):
    return min(1.0, max(0.0, v))


def _rgb(color):
    return [int(color[i:i + 2], 16) for i in (1, 3, 5)]


def _png(w, h, pixel):
    """pixel(x, y) -> (r, g, b, a) (0-255) ile saydam PNG verisi; yalnızca standart kütüphane."""
    rows = bytearray()
    for y in range(h):
        rows.append(0)
        for x in range(w):
            rows += bytes(pixel(x, y))

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    return base64.b64encode(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
                            + chunk(b"IDAT", zlib.compress(bytes(rows), 1)) + chunk(b"IEND", b""))


def _image(widget, key, w, h, pixel):
    """Kenarı yumuşatılmış (Tk'nin tırtıklı çiziminin yerine) resim; pencere başına bir kez üretilir."""
    root = widget._root()
    cache = root.__dict__.setdefault("_aa_images", {})
    if key not in cache:
        cache[key] = tk.PhotoImage(master=root, data=_png(w, h, pixel), format="png")
    return cache[key]


def _circle(widget, diameter, fill=None, ring=None, ring_width=0.0, back=None):
    """Daire; fill None ise içi boş halka. back: dairenin dışını boyar (renkli zeminin üstüne maske)."""
    def pixel(x, y):
        r = diameter / 2
        d = math.hypot(x + 0.5 - r, y + 0.5 - r)
        outer = _clamp(r - d + 0.5)
        if back:
            return (*_rgb(back), round(255 * (1 - outer)))
        inner = _clamp(r - ring_width - d + 0.5) if ring else 1.0
        if ring and fill:
            c = [u + (v - u) * inner for u, v in zip(_rgb(ring), _rgb(fill))]
            return (*[round(v) for v in c], round(255 * outer))
        if ring:
            return (*_rgb(ring), round(255 * outer * (1 - inner)))
        return (*_rgb(fill), round(255 * outer))

    return _image(widget, ("circle", diameter, fill, ring, ring_width, back), diameter, diameter, pixel)


def _corner(widget, r, border, fill, outline, right, bottom):
    def pixel(x, y):
        px = r - 1 - x if right else x
        py = r - 1 - y if bottom else y
        d = math.hypot(r - px - 0.5, r - py - 0.5)
        inner = _clamp(r - border - d + 0.5) if border else 1.0
        c = [u + (v - u) * inner for u, v in zip(_rgb(outline), _rgb(fill))]
        return (*[round(v) for v in c], round(255 * _clamp(r - d + 0.5)))

    return _image(widget, ("corner", r, border, fill, outline, right, bottom), r, r, pixel)


def _round_rect(canvas, x1, y1, x2, y2, r, fill="", outline="", tags=()):
    """Yumuşak köşeli dikdörtgen: gövde düz, yalnızca dört köşe resim (büyük kartlarda da ucuz)."""
    x1, y1, x2, y2 = round(x1), round(y1), round(x2), round(y2)
    outline = outline or fill
    border = S(1) if outline != fill else 0
    r = max(0, min(r, (x2 - x1) // 2, (y2 - y1) // 2))
    box = canvas.create_rectangle
    box(x1 + r, y1, x2 - r, y2, fill=outline, outline="", tags=tags)
    box(x1, y1 + r, x2, y2 - r, fill=outline, outline="", tags=tags)
    if border:
        box(x1 + r, y1 + border, x2 - r, y2 - border, fill=fill, outline="", tags=tags)
        box(x1 + border, y1 + r, x2 - border, y2 - r, fill=fill, outline="", tags=tags)
    if r:
        for right in (False, True):
            for bottom in (False, True):
                canvas.create_image(x2 - r if right else x1, y2 - r if bottom else y1, anchor="nw", tags=tags,
                                    image=_corner(canvas, r, border, fill, outline, right, bottom))


def _gradient(widget, key, w, h, colors, vertical=False):
    """Renk geçişi resmi (w x h). Tek sıra üretilip öbür yönde büyütülür: büyük kartta da ucuz.
    Her anahtar için yalnızca son üretilen saklanır (boyut değişince eskisi bırakılır)."""
    root = widget._root()
    cache = root.__dict__.setdefault("_gradients", {})
    params = (w, h, tuple(colors), vertical)
    if cache.get(key, (None, None))[0] != params:
        stops = [_rgb(c) for c in colors]
        count = max(2, h if vertical else w)

        def at(f):
            pos = f * (len(stops) - 1)
            i = min(int(pos), len(stops) - 2)
            return _hex([round(a + (b - a) * (pos - i)) for a, b in zip(stops[i], stops[i + 1])])

        cells = [at(i / (count - 1)) for i in range(count)]
        base = tk.PhotoImage(master=root, width=1 if vertical else count, height=count if vertical else 1)
        base.put(" ".join("{%s}" % c for c in cells) if vertical else "{" + " ".join(cells) + "}")
        cache[key] = (params, base.zoom(max(1, w), 1) if vertical else base.zoom(1, max(1, h)))
    return cache[key][1]


def _corner_mask(widget, r, right, bottom, back):
    """Köşenin yuvarlağın dışında kalan kısmını zemin rengine boyayan resim (renk geçişli kartlar için)."""
    def pixel(x, y):
        px = r - 1 - x if right else x
        py = r - 1 - y if bottom else y
        d = math.hypot(r - px - 0.5, r - py - 0.5)
        return (*_rgb(back), round(255 * (1 - _clamp(r - d + 0.5))))

    return _image(widget, ("cmask", r, right, bottom, back), r, r, pixel)


def _mask_corners(canvas, x1, y1, x2, y2, r, back, top=None, tags=()):
    """Dikdörtgen bir resmin dört köşesini yuvarlar. top: üst köşelerin zemin rengi alttakinden farklıysa."""
    r = max(0, min(r, (x2 - x1) // 2, (y2 - y1) // 2))
    if not r:
        return
    for right in (False, True):
        for bottom in (False, True):
            canvas.create_image(x2 - r if right else x1, y2 - r if bottom else y1, anchor="nw", tags=tags,
                                image=_corner_mask(canvas, r, right, bottom, back if bottom or top is None else top))


# ---------------------------------------------------------------- denetimler
class Pill(tk.Canvas):
    """Windows düğmesi. style: "button" (gri), "accent" (vurgu), "chip" (seçilebilir)."""

    def __init__(self, parent, text, command, bg, style="button", height=32, padx=14, font=(FONT, 10), width=None):
        text = lang.T(text)
        width = S(width) if width else tkfont.Font(font=font).measure(text) + 2 * S(padx)
        super().__init__(parent, width=width, height=S(height), bg=bg, highlightthickness=0, cursor="hand2")
        self.text, self.font, self.style, self.command, self.padx = text, font, style, command, S(padx)
        self.active = self.hover = False
        self.bind("<Button-1>", lambda _e: self.command())
        self.bind("<Enter>", lambda _e: self._set_hover(True))
        self.bind("<Leave>", lambda _e: self._set_hover(False))
        self._draw()

    def set_active(self, active):
        if active != self.active:
            self.active = active
            self._draw()

    def set_text(self, text, keep_width=False):
        """Metni değiştirir, genişliği metne uydurur (keep_width: düğme boyu sabit kalır)."""
        text = lang.T(text)
        if text != self.text:
            self.text = text
            if not keep_width:
                self.configure(width=tkfont.Font(font=self.font).measure(text) + 2 * self.padx)
            self._draw()

    def _set_hover(self, hover):
        self.hover = hover
        self._draw()

    def _draw(self):
        self.delete("all")
        w, h = int(self["width"]), int(self["height"])
        if self.style == "accent" or (self.style == "chip" and self.active):
            fill, outline, fg = ACCENT, ACCENT, ON_ACCENT
        else:
            fill, outline, fg = (CONTROL_HOVER if self.hover else CONTROL), CONTROL_EDGE, TEXT
        _round_rect(self, 1, 1, w - 1, h - 1, S(6), fill=fill, outline=outline)
        self.create_text(w // 2, h // 2, text=self.text, fill=fg, font=self.font)


def _blend(a, b, f):
    """İki "#RRGGBB" rengin arası (f: 0 -> a, 1 -> b)."""
    ca = [int(a[i:i + 2], 16) for i in (1, 3, 5)]
    cb = [int(b[i:i + 2], 16) for i in (1, 3, 5)]
    return _hex([round(x + (y - x) * f) for x, y in zip(ca, cb)])


def _toggle_rows(w, h, pos, bg):
    """Anahtarın kenarları yumuşatılmış resmi (Tk'nin çizimi tırtıklı olur). pos: 0 kapalı, 1 açık.
    Her piksel, şekillere olan uzaklığından karıştırılır; PhotoImage.put satır biçiminde döner."""
    def rgb(c):
        return [int(c[i:i + 2], 16) for i in (1, 3, 5)]

    def over(under, top, a):
        return [u + (v - u) * a for u, v in zip(under, top)]

    def clamp(v):
        return min(1.0, max(0.0, v))

    r = h / 2
    knob = r - 2.5 * dpi.SCALE
    cx = r + pos * (w - 2 * r)
    back, track = rgb(bg), rgb(_blend(IOS_OFF, IOS_GREEN, pos))
    rows = []
    for y in range(h):
        py = y + 0.5
        row = []
        for x in range(w):
            px = x + 0.5
            edge = math.hypot(px - min(w - r, max(r, px)), py - r) - r
            c = over(back, track, clamp(0.5 - edge))
            shadow = math.hypot(px - cx, py - r - dpi.SCALE) - knob
            c = over(c, (0, 0, 0), 0.28 * clamp((1.5 * dpi.SCALE - shadow) / (3 * dpi.SCALE)) * clamp(0.5 - edge))
            c = over(c, (255, 255, 255), clamp(0.5 - (math.hypot(px - cx, py - r) - knob)))
            row.append("#%02X%02X%02X" % tuple(round(v) for v in c))
        rows.append("{" + " ".join(row) + "}")
    return " ".join(rows)


class Toggle(tk.Canvas):
    """iOS tarzı aç/kapat anahtarı: açıkken yeşil zemin, beyaz düğme kayarak geçer."""
    W, H, FRAMES = S(42), S(24), 6

    def __init__(self, parent, command, bg):
        super().__init__(parent, width=self.W, height=self.H, bg=bg, highlightthickness=0, cursor="hand2")
        self.value, self.command, self.back = True, command, bg
        self._frame = self.FRAMES  # düğmenin yeri: 0 sol (kapalı), FRAMES sağ (açık)
        self._item = self.create_image(0, 0, anchor="nw", image=self._image())
        self.bind("<Button-1>", lambda _e: self.command())

    def _image(self):
        """Resimler pencere başına ve renk başına bir kez hazırlanır."""
        cache = self._root().__dict__.setdefault("_toggle_images", {})
        key = (self.back, self._frame)
        if key not in cache:
            f = self._frame / self.FRAMES
            image = tk.PhotoImage(master=self, width=self.W, height=self.H)
            image.put(_toggle_rows(self.W, self.H, f * f * (3 - 2 * f), self.back))
            cache[key] = image
        return cache[key]

    def set(self, value):
        if value != self.value:
            self.value = value
            self._slide()

    def _slide(self):
        target = self.FRAMES if self.value else 0
        if self._frame == target:
            return
        self._frame += 1 if target > self._frame else -1
        try:
            self.itemconfigure(self._item, image=self._image())
            self.after(15, self._slide)
        except tk.TclError:
            pass  # pencere kapandı


class RoundBox(tk.Canvas):
    """Yumuşak köşeli kart. İçerik .inner (Frame) içine konur; yükseklik içeriğe göre ayarlanır
    ya da fixed_height ile sabitlenir (kare kartlar). İçerik köşelerden margin kadar içeridedir,
    böylece köşelerin yuvarlağı görünür."""

    def __init__(self, parent, outer=BG, fill=CARD, radius=12, padx=18, pady=12, margin=5, fixed_height=None,
                 min_height=0):
        fixed_height, min_height = fixed_height and S(fixed_height), S(min_height)
        super().__init__(parent, bg=outer, highlightthickness=0, height=fixed_height or max(20, min_height), width=20)
        self.fill, self.radius, self.margin, self.fixed_height = fill, S(radius), S(margin), fixed_height
        self.min_height = min_height
        self.inner = tk.Frame(self, bg=fill, padx=padx, pady=pady)
        self._win = self.create_window(self.margin, self.margin, window=self.inner, anchor="nw")
        self._fit_queued = None
        self._drawn = None
        self._items = None
        self.inner.bind("<Configure>", lambda _e: self._queue_fit())
        self.bind("<Configure>", lambda _e: self._queue_fit())

    def _queue_fit(self):
        """Art arda gelen boyut olayları tek çizimde toplanır."""
        if not self._fit_queued:
            self._fit_queued = self.after_idle(self._fit)

    def _fit(self):
        self._fit_queued = None
        m = self.margin
        w = self.winfo_width()
        if w > 2 * m:
            self.itemconfigure(self._win, width=w - 2 * m)
        if self.fixed_height:
            h = self.fixed_height
            self.itemconfigure(self._win, height=h - 2 * m)
        else:
            h = max(self.inner.winfo_reqheight() + 2 * m, self.min_height)
            if int(float(self["height"])) != h:
                self.configure(height=h)
            h = max(h, self.winfo_height())  # ızgarada yan kartla aynı boya uzatılmış olabilir
        w, h = max(w, 2) - 1, h - 1
        if (w, h) == self._drawn:
            return
        self._drawn = (w, h)
        r = min(self.radius, w // 2, h // 2)
        if self._items is None or r != self._drawn_radius:
            self.delete("bg")
            _round_rect(self, 0, 0, w, h, r, fill=self.fill, outline=self.fill, tags="bg")
            self.tag_lower("bg")
            self._items, self._drawn_radius = self.find_withtag("bg"), r
            return
        # Köşe resimleri aynı: yalnız yerleri değişir (silip yeniden oluşturmak büyütmeyi yavaşlatıyordu).
        body1, body2, *corners = self._items
        self.coords(body1, r, 0, w - r, h)
        self.coords(body2, 0, r, w, h - r)
        for item, (x, y) in zip(corners, ((0, 0), (0, h - r), (w - r, 0), (w - r, h - r))):
            self.coords(item, x, y)


class Swatch(tk.Canvas):
    """Renk dairesi: seçiliyken dışında halka ve içinde onay işareti. Sağ tık: renk listeden silinir."""
    SIZE = S(44)
    DOT = S(32)

    def __init__(self, parent, color, command, bg, remove=None):
        super().__init__(parent, width=self.SIZE, height=self.SIZE, bg=bg, highlightthickness=0, cursor="hand2")
        self.color, self.selected = color, False
        self.bind("<Button-1>", lambda _e: command(self.color))
        if remove:
            self.bind("<Button-3>", lambda _e: remove(self.color))
        self._draw()

    def set_selected(self, selected):
        if selected != self.selected:
            self.selected = selected
            self._draw()

    def _draw(self):
        self.delete("all")
        c = self.SIZE // 2
        if self.color is None:  # "+" özel renk
            self.create_image(c, c, image=_circle(self, self.DOT, fill=CONTROL_HOVER))
            self.create_text(c, c - S(1), text="+", fill=TEXT, font=(FONT, 13))
            return
        if self.selected:
            self.create_image(c, c, image=_circle(self, self.SIZE - S(2), ring=TEXT, ring_width=2 * dpi.SCALE))
        self.create_image(c, c, image=_circle(self, self.DOT, fill="#" + self.color))
        if self.selected:
            r, g, b = fx.parse_hex(self.color)
            dark = 0.299 * r + 0.587 * g + 0.114 * b > 150  # açık renkte koyu işaret
            self.create_text(c, c, text="✓", fill="#101010" if dark else "#FFFFFF", font=(BOLD, 11))


class ColorPicker(tk.Frame):
    """Uygulamanın içinde açılan renk seçici: doygunluk/parlaklık alanı, renk tonu şeridi, HEX kutusu."""
    STEP = 2  # alan yarı çözünürlükte hesaplanır, gözle fark edilmez

    def __init__(self, parent, on_pick, on_cancel, bg=CARD, ok_text="Ekle", color=None, font=(FONT, 10)):
        super().__init__(parent, bg=bg)
        self.on_pick = on_pick
        self.W, self.H, self.BAR = S(240), S(130), S(14)
        self.hue, self.sat, self.val = 0.6, 0.75, 1.0
        if color:
            self.hue, self.sat, self.val = colorsys.rgb_to_hsv(*[c / 255 for c in fx.parse_hex(color)])
        self.area = tk.Canvas(self, width=self.W, height=self.H, bg=bg, highlightthickness=0, cursor="crosshair")
        self.area.grid(row=0, column=0, rowspan=2)
        self.bar = tk.Canvas(self, width=self.W, height=self.BAR, bg=bg, highlightthickness=0, cursor="hand2")
        self.bar.grid(row=2, column=0, pady=(10, 0))
        side = tk.Frame(self, bg=bg)
        side.grid(row=0, column=1, rowspan=3, sticky="n", padx=(18, 0))
        self.swatch = tk.Canvas(side, width=S(52), height=S(52), bg=bg, highlightthickness=0)
        self.swatch.pack(anchor="w")
        self.hex_var = tk.StringVar()
        entry = tk.Entry(side, textvariable=self.hex_var, width=9, bg=CONTROL, fg=TEXT, insertbackground=TEXT,
                         relief="flat", font=font, highlightthickness=1, highlightbackground=CONTROL_EDGE,
                         highlightcolor=ACCENT)
        entry.pack(anchor="w", pady=(10, 10), ipady=5)
        entry.bind("<KeyRelease>", lambda _e: self._typed())
        Pill(side, ok_text, lambda: self.on_pick(self.color()), bg, style="accent", font=font).pack(anchor="w", pady=(0, 6))
        Pill(side, "Vazgeç", on_cancel, bg, font=font).pack(anchor="w")
        rainbow = tk.PhotoImage(master=self, width=self.W, height=1)
        rainbow.put("{" + " ".join(_hex([round(c * 255) for c in colorsys.hsv_to_rgb(x / self.W, 1, 1)])
                                   for x in range(self.W)) + "}")
        self._bar_image = rainbow.zoom(1, self.BAR)
        for canvas, command in ((self.area, self._area_click), (self.bar, self._bar_click)):
            canvas.bind("<Button-1>", command)
            canvas.bind("<B1-Motion>", command)
        self._draw_area()
        self._update()

    def color(self):
        return "%02X%02X%02X" % tuple(round(c * 255) for c in colorsys.hsv_to_rgb(self.hue, self.sat, self.val))

    def _draw_area(self):
        w, h = self.W // self.STEP, self.H // self.STEP
        base = colorsys.hsv_to_rgb(self.hue, 1, 1)
        rows = []
        for y in range(h):
            v = 255 * (1 - y / (h - 1))
            row = []
            for x in range(w):
                s = x / (w - 1)
                row.append("#%02X%02X%02X" % tuple(round(v * (1 - s + s * c)) for c in base))
            rows.append("{" + " ".join(row) + "}")
        image = tk.PhotoImage(master=self, width=w, height=h)
        image.put(" ".join(rows))
        self._area_image = image.zoom(self.STEP)

    def _update(self, keep_text=False):
        for canvas in (self.area, self.bar, self.swatch):
            canvas.delete("all")
        self.area.create_image(0, 0, anchor="nw", image=self._area_image)
        x, y = self.sat * self.W, (1 - self.val) * self.H
        self.area.create_image(x, y, image=_circle(self, S(16), ring="#000000", ring_width=3 * dpi.SCALE))
        self.area.create_image(x, y, image=_circle(self, S(14), ring="#FFFFFF", ring_width=2 * dpi.SCALE))
        self.bar.create_image(0, 0, anchor="nw", image=self._bar_image)
        self.bar.create_image(min(self.W - S(7), max(S(7), self.hue * self.W)), self.BAR // 2,
                              image=_circle(self, self.BAR, fill="#FFFFFF", ring="#303030", ring_width=dpi.SCALE))
        c = S(26)
        self.swatch.create_image(c, c, image=_circle(self, S(52), fill="#" + self.color()))
        if not keep_text:
            self.hex_var.set("#" + self.color())

    def _area_click(self, e):
        self.sat = _clamp(e.x / self.W)
        self.val = 1 - _clamp(e.y / self.H)
        self._update()

    def _bar_click(self, e):
        self.hue = _clamp(e.x / self.W) * 0.9999
        self._draw_area()
        self._update()

    def _typed(self):
        try:
            rgb = fx.parse_hex(self.hex_var.get())
        except ValueError:
            return
        self.hue, self.sat, self.val = colorsys.rgb_to_hsv(*[c / 255 for c in rgb])
        self._draw_area()
        self._update(keep_text=True)


class Slider(tk.Canvas):
    """Dolu kısmı vurgu renginde kaydırıcı (get/set ile tk.Scale gibi kullanılır)."""

    def __init__(self, parent, lo, hi, step, command, bg, length=220):
        super().__init__(parent, width=S(length), height=S(22), bg=bg, highlightthickness=0, cursor="hand2")
        self.lo, self.hi, self.step, self.command = lo, hi, step, command
        self.value = lo
        self.bind("<Button-1>", self._drag)
        self.bind("<B1-Motion>", self._drag)
        self.bind("<Configure>", lambda _e: self._draw())

    def get(self):
        return self.value

    def set(self, value):
        self.value = min(self.hi, max(self.lo, value))
        self._draw()

    def _drag(self, event):
        w = self.winfo_width() - S(20)
        f = min(1.0, max(0.0, (event.x - S(10)) / max(1, w)))
        value = round((self.lo + f * (self.hi - self.lo)) / self.step) * self.step
        value = round(min(self.hi, max(self.lo, value)), 3)
        if value != self.value:
            self.value = value
            self._draw()
            self.command()

    def _draw(self):
        self.delete("all")
        w = self.winfo_width()
        if w <= 1:
            w = int(self["width"])
        x = S(10) + (w - S(20)) * (self.value - self.lo) / (self.hi - self.lo)
        _round_rect(self, S(10), S(9), w - S(10), S(13), S(2), fill=MUTED, outline="")
        _round_rect(self, S(10), S(9), max(x, S(12)), S(13), S(2), fill=ACCENT, outline="")
        self.create_image(x, S(11), image=_circle(self, S(18), fill=KNOB, ring=CONTROL_EDGE, ring_width=dpi.SCALE))
        self.create_image(x, S(11), image=_circle(self, S(10), fill=ACCENT))


class ScrollPage(tk.Frame):
    """Sayfa içeriği; pencereye sığmazsa fare tekerleğiyle kayar."""

    def __init__(self, parent):
        super().__init__(parent, bg=BG)
        self.canvas = tk.Canvas(self, bg=BG, highlightthickness=0)
        self.inner = tk.Frame(self.canvas, bg=BG)
        self._win = self.canvas.create_window(0, 0, window=self.inner, anchor="nw")
        self.canvas.pack(fill="both", expand=True)
        self.inner.bind("<Configure>", lambda _e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(self._win, width=e.width))

    def wheel(self, event):
        if self.inner.winfo_height() > self.canvas.winfo_height():
            self.canvas.yview_scroll(int(-event.delta / 120), "units")


class NavItem(tk.Frame):
    def __init__(self, parent, icon, text, command):
        super().__init__(parent, bg=BG, cursor="hand2")
        self.bar = tk.Canvas(self, width=S(4), height=S(18), bg=BG, highlightthickness=0)
        self.bar.pack(side="left", padx=(6, 8))
        self.icon = tk.Label(self, text=icon, bg=BG, fg=TEXT, font=(FONT, 12))
        if icon in icons.NAMES:
            self.image = tk.PhotoImage(master=self, data=icons.png_data(icon, S(20)), format="png")
            self.icon.configure(image=self.image, text="")
        self.icon.pack(side="left", padx=(0, 10))
        self.label = tk.Label(self, text=text, bg=BG, fg=TEXT, font=(FONT, 10), anchor="w")
        self.label.pack(side="left", fill="x", expand=True, pady=8)
        self.selected = False
        for w in (self, self.bar, self.icon, self.label):
            w.bind("<Button-1>", lambda _e: command())
            w.bind("<Enter>", lambda _e: self._paint(True))
            w.bind("<Leave>", lambda _e: self._paint(False))

    def set_selected(self, selected):
        self.selected = selected
        self._paint(False)

    def _paint(self, hover):
        bg = NAV_SELECTED if self.selected or hover else BG
        for w in (self, self.bar, self.icon, self.label):
            w.configure(bg=bg)
        self.bar.delete("all")
        if self.selected:
            _round_rect(self.bar, 0, 0, S(4), S(18), S(2), fill=ACCENT, outline="")


class Rail(tk.Canvas):
    """Sol kenardaki yüzen simge rayı: en üstte logo (Pano), altında sayfalar, en altta cihaz paneli düğmesi.
    Adlar imleç simgenin üstüne gelince yanında çıkar."""
    GLYPHS = {"dash": "\uE80F", "settings": "\uE713", "log": "\uE8A5", "panel": "\uE772"}

    def __init__(self, parent, on_page, on_panel):
        super().__init__(parent, width=S(56), height=S(200), bg=BG, highlightthickness=0)
        fonts = tkfont.families(parent)
        self.icon_font = "Segoe Fluent Icons" if "Segoe Fluent Icons" in fonts else "Segoe MDL2 Assets"
        self.on_page, self.on_panel = on_page, on_panel
        self.pages, self.current, self.panel_open = [], None, False
        self._hits, self._images, self._hover, self._tip = [], {}, None, None
        self.bind("<Configure>", lambda _e: self._draw())
        self.bind("<Button-1>", self._click)
        self.bind("<Motion>", self._motion)
        self.bind("<Leave>", lambda _e: self._set_hover(None))

    def set_pages(self, pages):
        """pages: [(anahtar, simge, ad)]"""
        self.pages = pages
        self._draw()

    def select(self, key):
        self.current = key
        self._draw()

    def set_panel(self, is_open):
        self.panel_open = is_open
        self._draw()

    def _icon(self, name, size, color=None):
        """color verilirse logonun zeminsiz, tek renkli hali."""
        if (name, size, color) not in self._images:
            data = icons.mono_png_data(name, size, _rgb(color)) if color else icons.png_data(name, size)
            self._images[name, size, color] = tk.PhotoImage(master=self, data=data, format="png")
        return self._images[name, size, color]

    def _draw(self):
        self.delete("all")
        w, h = int(self["width"]), self.winfo_height()
        if h < S(120):
            return
        # Ekran boyunda bir kez üretilir, tuval kırpar: pencere boyutu değişince yeniden üretilmez.
        self.create_image(0, 0, anchor="nw",
                          image=_gradient(self, "rail", w, self.winfo_screenheight(), (RAIL, RAIL_END), vertical=True))
        _mask_corners(self, 0, 0, w, h, w // 2, BG)
        self._hits = []
        cx = w // 2
        # En üstteki logo Pano'nun (ana sayfa) düğmesidir: ayrı bir ev simgesi yok. Seçiliyken çevresinde halka.
        home = next((p for p in self.pages if p[0] == "dash"), None)
        if home:
            d, cy = S(44), S(34)
            if self.current == "dash":
                self.create_image(cx, cy, image=_circle(self, d, ring=RAIL_ON, ring_width=S(2)))
            elif self._hover == "dash":
                self.create_image(cx, cy, image=_circle(self, d, fill=RAIL_HOVER))
            self._hits.append((cy - d // 2, cy + d // 2, "dash", home[2], lambda: self.on_page("dash")))
        self.create_image(cx, S(34), image=self._icon("lightcore", S(30)))
        y = S(88)
        for key, icon, text in self.pages:
            if key == "dash":
                continue
            self._item(key, icon, text, cx, y, key == self.current, lambda k=key: self.on_page(k))
            y += S(46)
        self._item("panel", "panel", "Cihazlar ve durum", cx, h - S(36), self.panel_open, self.on_panel)

    def _item(self, key, icon, text, cx, cy, on, command):
        d = S(40)
        if on or self._hover == key:
            self.create_image(cx, cy, image=_circle(self, d, fill=RAIL_ON if on else RAIL_HOVER))
        if icon in icons.NAMES:
            self.create_image(cx, cy, image=self._icon(icon, S(26), RAIL_ON_TEXT if on else RAIL_TEXT))
        else:
            self.create_text(cx, cy, text=self.GLYPHS.get(key, icon), font=(self.icon_font, 13),
                             fill=RAIL_ON_TEXT if on else RAIL_TEXT)
        self._hits.append((cy - d // 2, cy + d // 2, key, text, command))

    def _at(self, y):
        return next((hit for hit in self._hits if hit[0] <= y <= hit[1]), None)

    def _click(self, e):
        hit = self._at(e.y)
        if hit:
            self._set_hover(None)
            hit[4]()

    def _motion(self, e):
        hit = self._at(e.y)
        self._set_hover(hit[2] if hit else None, hit)

    def _set_hover(self, key, hit=None):
        if key == self._hover:
            return
        self._hover = key
        self.configure(cursor="hand2" if key else "")
        self._draw()
        if self._tip:
            self._tip.destroy()
            self._tip = None
        if hit:
            tip = self._tip = tk.Toplevel(self)
            tip.overrideredirect(True)
            tip.attributes("-topmost", True)
            tk.Label(tip, text=hit[3], bg=RAIL, fg=RAIL_INK, font=(FONT, 9), padx=8, pady=4).pack()
            tip.geometry(f"+{self.winfo_rootx() + int(self['width']) + S(6)}"
                         f"+{self.winfo_rooty() + (hit[0] + hit[1]) // 2 - S(13)}")


class DeviceRow(tk.Frame):
    """Sol listedeki cihaz: canlı renk noktası, ad, alt yazı, aç/kapat anahtarı."""

    def __init__(self, parent, target, on_toggle):
        super().__init__(parent, bg=BG, padx=10, pady=6)
        self.target = target
        self.dot = tk.Canvas(self, width=S(12), height=S(12), bg=BG, highlightthickness=0)
        self.dot.pack(side="left", padx=(4, 10))
        self._dot_item = self.dot.create_rectangle(0, 0, S(12), S(12), fill=BG, outline="")
        self.dot.create_image(0, 0, anchor="nw", image=_circle(self, S(12), back=BG))
        self._dot_ring = self.dot.create_image(0, 0, anchor="nw", image=_circle(self, S(12), ring=MUTED,
                                                                                ring_width=dpi.SCALE))
        self._dot_rgb = None
        # Anahtar yazılardan önce yerleşir: uzun cihaz adı onu satır dışına itmesin.
        self.toggle = Toggle(self, lambda: on_toggle(self.target), BG)
        self.toggle.pack(side="right", padx=(8, 0))
        texts = tk.Frame(self, bg=BG, width=1)
        texts.pack(side="left", fill="x", expand=True)
        self.title = tk.Label(texts, text=target["title"], bg=BG, fg=TEXT, font=(FONT, 10), anchor="w")
        self.title.pack(fill="x")
        self.sub = tk.Label(texts, bg=BG, fg=MUTED, font=(FONT, 8), anchor="w")
        self.sub.pack(fill="x")
        self.problem = None
        self.update_view()

    def update_view(self):
        on = self.target["on"]
        self.toggle.set(on)
        self.problem = None
        self.sub.configure(text=self.target["subtitle"], fg=MUTED)
        if not on:
            self.set_color(None)

    def set_color(self, rgb):
        if rgb is not None and not self.target["on"]:
            rgb = None
        if rgb == self._dot_rgb:
            return  # değişmeyen renk yeniden çizilmez
        self._dot_rgb = rgb
        self.dot.itemconfigure(self._dot_ring, state="normal" if rgb is None else "hidden")
        self.dot.itemconfigure(self._dot_item, fill=BG if rgb is None else _hex(rgb))

    def set_problem(self, label, text):
        """Hata/takılma alt yazıda kalır; bir sonraki ayar değişikliğinde temizlenir."""
        self.problem = label
        short = text if len(text) <= 46 else text[:43] + "..."
        self.sub.configure(text=f"{label}: {short}", fg=AMBER)


class Runner:
    """Efekt motorunu arka planda çalıştırır, arayüzü hiç bekletmeden.

    start() eski işe dur der ve hemen döner. Yeni iş kendi iş parçacığında önce
    eski işin TAMAMEN bitmesini bekler; böylece iki iş aynı OpenRGB bağlantısına
    asla aynı anda yazmaz."""

    def __init__(self, report):
        self.report = report
        self.thread, self.stop = None, threading.Event()

    def start(self, job, always=False):
        """always: araya daha yeni bir iş girse de çalışır (bağlantı kapatma gibi atlanmaması gereken iş)."""
        previous = self.thread
        self.stop.set()
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._run, args=(job, self.stop, previous, always), daemon=True)
        self.thread.start()

    def _run(self, job, stop, previous, always=False):
        if previous:
            previous.join()
        if stop.is_set() and not always:
            return  # bu arada daha yeni bir ayar geldi
        try:
            job(stop)
        except Exception as e:  # arayüzde göster, motor durur
            self.report(f"Hata: {e}")

    def halt(self, timeout=None):
        """Dur der; timeout verilirse en fazla o kadar bekler. Arayüzden çağrılmaz."""
        self.stop.set()
        if self.thread:
            self.thread.join(timeout)


class WakeQueue(queue.Queue):
    """Arka plandan gelen olaylar arayüzü anında uyandırır; böylece kuyruğu sık sık yoklamak gerekmez.
    Uyandırma ayrı iş parçacığındadır: Tk meşgulken işi olan iş parçacıkları beklemez."""

    def __init__(self):
        super().__init__()
        self.root = None
        self._signal = threading.Event()
        threading.Thread(target=self._waker, daemon=True).start()

    def put(self, item, *args, **kw):
        super().put(item, *args, **kw)
        self._signal.set()

    def _waker(self):
        while True:
            self._signal.wait()
            self._signal.clear()
            try:
                if self.root is not None:
                    self.root.event_generate("<<Wake>>", when="tail")
            except (RuntimeError, tk.TclError):
                pass  # pencere kapanıyor ya da döngü başlamadı: olay kuyrukta, sıradaki turda işlenir


CONFIG_VERSION = 2


def may_recover_rgb(last, now, game_mode, owns_openrgb):
    """Düşen cihaz için OpenRGB yeniden başlatılsın mı? Oyunda ve kendi başlatmadığımız OpenRGB'de hayır."""
    return owns_openrgb and not game_mode and (last is None or now - last >= RGB_RECOVERY_SECONDS)


def migrate_config(config):
    """Ayar dosyasının sürümü: ileride biçim değişirse eski dosyalar buradan dönüştürülür."""
    ble = config.get("ble")
    if isinstance(ble, dict) and ble.get("address"):  # sürüm 1: tek şerit
        key = f"ble:{ble['address']}"
        config["ble"] = {"strips": [{k: ble[k] for k in ("address", "name", "gains", "gamma") if k in ble}]}
        config["disabled"] = [key if k == "ble" else k for k in config.get("disabled", [])]
        for name in ("device_colors", ):
            if "ble" in config.get(name, {}):
                config[name][key] = config[name].pop("ble")
    config["version"] = CONFIG_VERSION
    return config


def scrub_report(text):
    """Gönderilebilir rapor: kullanıcı adı, bilgisayar adı, ev klasörü, ağ adresleri ve uzun anahtarlar çıkar."""
    for value, mark in ((os.path.expanduser("~"), "<ev>"), (os.environ.get("USERNAME"), "<kullanici>"),
                        (os.environ.get("COMPUTERNAME"), "<bilgisayar>")):
        if value:
            text = re.sub(re.escape(value), mark, text, flags=re.I)
    text = re.sub(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", "<ip>", text)
    text = re.sub(r"\b[0-9A-Fa-f]{2}(?:[:-][0-9A-Fa-f]{2}){5}\b", "<adres>", text)  # Bluetooth / ağ kartı adresi
    return re.sub(r"\b[A-Za-z0-9_-]{32,}\b", "<anahtar>", text)


def make_backup(config, state, palette):
    """Kullanıcının kendi ayarları. Hue anahtarı köprünün şifresi gibidir, yedeğe girmez."""
    shots = {k: v for k, v in config.get("shots", {}).items() if k != "folder"}
    fans = {k: v for k, v in config.get("fans", {}).items() if k != "enabled"}
    return {"type": "lightcore-backup", "version": 1, "lights": {**{k: state[k] for k in LIGHT_KEYS}, "palette": palette},
            "custom_presets": config.get("custom_presets", {}), "disabled": config.get("disabled", []),
            "fans": fans, "shots": shots}


def check_backup(data):
    """Bozuk ya da yanlış dosyayı uygulamadan önce reddeder."""
    if not isinstance(data, dict) or data.get("type") != "lightcore-backup":
        raise ValueError("Bu dosya LightCore yedeği değil.")
    lights = data.get("lights")
    if not isinstance(lights, dict) or not all(k in lights for k in LIGHT_KEYS):
        raise ValueError("Yedekteki ışık ayarları eksik.")
    for preset in data.get("custom_presets", {}).values():
        if not isinstance(preset, dict) or not all(k in preset for k in LIGHT_KEYS):
            raise ValueError("Yedekteki kişisel ayarlardan biri bozuk.")
        for c in preset["selected"]:
            fx.parse_hex(c)
    for c in (*lights["selected"], *lights.get("palette", ())):
        fx.parse_hex(c)
    return data


# ---------------------------------------------------------------- uygulama
class App:
    def __init__(self, root, args):
        self.root, self.args = root, args
        self.events = WakeQueue()
        self.runner = Runner(lambda text: self.events.put(("status", text, RED)))
        self.client = self.proc = self.fans = None
        self.fans_error = None
        self.device_clients = {}  # "bus" ya da controller_id -> ayrı OpenRGB bağlantısı
        self.stats = {}  # motorun yazma istatistikleri (engine.apply stats)
        self.job_info = None  # (efektin başladığı an, efekt hareketli mi)
        self._stalled = set()
        self._preview_rgb = None
        self.health_lines = []
        self.log_path = os.path.join(program_dir(), LOG_NAME)
        self.config_path = os.path.join(config_dir(), CONFIG_NAME)
        self.config = migrate_config(load_config(self.config_path))
        if os.path.isfile(self.config_path + ".bozuk") and not os.path.isfile(self.config_path):
            self.log(f"ayar dosyası okunamadı, {CONFIG_NAME}.bozuk olarak saklandı; ayarlar baştan başlıyor")
        lang.install(self.config.get("language") or lang.default_language())
        self._last_health = self._last_fill = self._last_usage = 0.0
        self.usage = ProcessUsage()
        self._draining = threading.Event()  # yalnızca iş sürerken bildirim gelir
        self._saved_text = None
        self.game_mode = False
        self._rgb_recovered_at = None
        self._game_closed_openrgb = self._game_started_fans = self._game_kept_lights = False
        self.log("--- LightCore açıldı ---")
        self.devices, self.openrgb_targets, self.hue_targets, self.targets = [], [], [], []
        self.rows = []
        self.hue, self.hue_found = None, []
        self.ble_strips, self.ble_targets, self.ble_found = {}, [], []
        self.ble_box = self.ble_desc = None
        self._rgb_idle = threading.Event()  # kapatma işi sürerken temiz: yeni OpenRGB onu bekler
        self._rgb_idle.set()
        self.palette = list(PALETTE)
        self.state = dict(selected=[], mode="static", brightness=70, speed=5, hold=2.0, fade=1.5, off=False)
        self.preset = None
        self._pending = None
        self._last = None  # (çalışan Fade efekti, başlangıç zamanı)
        self._syncing = False
        self.page = None
        self._live = None  # canlı renk zamanlayıcısı

        root.title("LightCore")
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        w, h = min(S(1180), sw), min(S(780), sh - S(80))
        root.geometry(f"{w}x{h}+{(sw - w) // 2}+{(sh - h) // 3}")
        root.configure(bg=BG)
        self.window = Window(root, (min(S(1060), sw), min(S(680), sh - S(80))), self._window_state, self._window_visible)
        self._tick = None
        root.iconphoto(True, tk.PhotoImage(master=root, data=icons.png_data("lightcore", 48), format="png"))
        self._build()
        if not self._restore_lights():
            self.apply_preset("Oyun", restart=False)
        self._apply_nav()
        self.show_page(next(k for k, _i, _t, f in PAGES if f is None or self.feature(f)))
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.bind_all("<MouseWheel>", self._wheel)
        root.bind("<<Wake>>", lambda _e: self._drain_events())
        self.events.root = root
        self.tray = Tray(lambda kind: self.events.put(("tray", kind)), menu=self._tray_menu)
        if getattr(args, "minimized", False):
            self.window.hide(fade=False)
        self._tick = root.after(POLL_STILL_MS, self._poll)
        threading.Thread(target=self._drain_loop, daemon=True).start()
        if self.feature("rgb"):
            self._start_rgb()
        else:
            self._status("LightRGB kapalı.")
        if self.feature("fan") and self.config.get("fans", {}).get("enabled"):
            root.after(500, self.fan_page.start)  # geçen sefer açıktı
        self.settings_page.run_checks()
        self._game_watcher_update()
        if not self.config.get("setup_seen") and not getattr(args, "minimized", False):
            self.config["setup_seen"] = True  # ilk açılışta kurulum kontrolü görülsün
            self.show_page("settings")
        try:  # FanControl yedeği pencereye sürüklenip bırakılabilsin
            from .ui_fans import enable_file_drop
            enable_file_drop(root, lambda paths: self.events.put(("drop", paths)))
        except Exception as e:  # sürükle-bırak olmazsa "Dosya seç" yine çalışır
            self.log(f"sürükle-bırak açılamadı: {e}")
        gc.collect()
        gc.freeze()  # arayüz nesneleri kalıcı: çöp toplayıcı onları her seferinde taramasın

    def _window_buttons(self):
        """Sağ üstte kendi küçült / büyüt / kapat düğmeleri."""
        fonts = tkfont.families(self.root)
        icons = "Segoe Fluent Icons" if "Segoe Fluent Icons" in fonts else "Segoe MDL2 Assets"
        bar = tk.Frame(self.root, bg=BG)
        bar.place(relx=1, y=0, anchor="ne")

        def button(glyph, command, hover=CONTROL_HOVER):
            label = tk.Label(bar, text=glyph, bg=BG, fg=TEXT, font=(icons, 9), padx=18, pady=9, cursor="hand2")
            label.pack(side="left")
            label.bind("<Button-1>", lambda _e: command())
            label.bind("<Enter>", lambda _e: label.configure(bg=hover, fg="#FFFFFF" if hover == "#C42B1C" else TEXT))
            label.bind("<Leave>", lambda _e: label.configure(bg=BG, fg=TEXT))
            return label

        button("\uE921", self.window.hide)
        self.max_button = button("\uE922", self.window.toggle_maximize)
        button("\uE8BB", self.close, hover="#C42B1C")
        bar.lift()

    # ---- özellikler
    def feature(self, key):
        return self.config.get("features", {}).get(key, True)

    def set_feature(self, key, on):
        if not on and sum(self.feature(f) for f in ("rgb", "fan", "snap")) == 1:
            self._status("En az bir özellik açık kalmalı.", AMBER)
            return
        self.config.setdefault("features", {})[key] = on
        self._save_config()
        self.log(f"özellik {key}: {'açık' if on else 'kapalı'}")
        if key == "rgb":
            self._start_rgb() if on else self._stop_rgb()
        elif key == "fan":
            if on and self.config.get("fans", {}).get("enabled"):
                self.fan_page.start()
            elif not on:
                self.fan_page.stop()
        else:
            self.shots_page.apply_hotkeys()
        self._apply_nav()
        if not self._page_allowed(self.page):
            self.show_page("settings")

    def _page_allowed(self, key):
        feature = next(f for k, _i, _t, f in PAGES if k == key)
        return feature is None or self.feature(feature)

    def _apply_nav(self):
        for key, item in self.nav.items():
            item.pack_forget()
        for key, _icon, _text, _feature in PAGES:
            if self._page_allowed(key):
                self.nav[key].pack(fill="x", padx=8, pady=1)
        self.rail.set_pages([(k, icon, text) for k, icon, text, _f in PAGES if self._page_allowed(k)])
        rgb = self.feature("rgb")
        self.devices_title.pack_forget()
        self.device_scroll.pack_forget()
        if rgb:
            self.devices_title.pack(fill="x", padx=22, pady=(18, 4))
            if self.devices_open:
                self.device_scroll.pack(fill="both", expand=True, padx=8)
        if rgb:
            self.rescan_button.pack(anchor="w")
        else:
            self.rescan_button.pack_forget()

    def toggle_side(self):
        """Cihaz listesi, durum ve başlangıç düğmelerinin olduğu paneli açar ya da kapatır."""
        self.side_open = not self.side_open
        self.config["side_open"] = self.side_open
        self._save_config()
        if self.side_open:
            self.side.pack(side="left", fill="y", after=self.rail)
        else:
            self.side.pack_forget()
        self.rail.set_panel(self.side_open)

    def toggle_devices(self):
        self.devices_open = not self.devices_open
        self.config["devices_open"] = self.devices_open
        self.devices_button.set_text("−" if self.devices_open else "+")
        self._save_config()
        self._apply_nav()

    # ---- kapatılan özelliğin bileşenleri: yalnızca programın kendi klasöründekiler silinir
    def component_paths(self, key):
        names = {"rgb": ("openrgb",), "fan": ("fanhost", "fan"), "snap": ()}[key]
        base = program_dir()
        return [os.path.join(base, n) for n in names if os.path.isdir(os.path.join(base, n))]

    def delete_components(self, key, settings=False):
        """Geliştirme klasöründe (.git) hiçbir şey silinmez. Dönüş: silinen klasörler."""
        if os.path.exists(os.path.join(program_dir(), ".git")):
            raise RuntimeError("Bu klasör bir git deposu: bileşenler silinmedi.")
        removed = []
        for path in self.component_paths(key):
            shutil.rmtree(path)
            removed.append(path)
        if settings:
            for name in {"rgb": ("hue", "ble", "lights", "custom_presets", "device_colors"), "fan": ("fans",), "snap": ("shots",)}[key]:
                self.config.pop(name, None)
            self._save_config()
        self.log(f"bileşenler silindi ({key}): {removed}")
        return removed

    def _start_rgb(self):
        threading.Thread(target=self._connect, daemon=True).start()
        if self.config.get("hue", {}).get("username"):
            self._hue_connect(self.config["hue"]["ip"], self.config["hue"]["username"])
        self._ble_setup()

    def _stop_rgb(self, black=False):
        """OpenRGB'yi kapatır, HID'i, Hue'yu ve Bluetooth şeridi bırakır. Bağlantılar motor sırasında kapatılır: çakışmaz."""
        client, fans, proc, hue, strips = self.client, self.fans, self.proc, self.hue, list(self.ble_strips.values())
        targets = [t for t in self.targets if t["on"]] if black else []
        self.client = self.fans = self.proc = self.hue = None
        self.ble_strips = {}
        self.job_info = None
        self._rgb_idle.clear()

        def job(_stop):
            try:
                for t in targets:
                    try:
                        if t["kind"] == "openrgb" and client:
                            apply(client, [t["device"]], fx.Static((0, 0, 0)), once=True)
                        elif t["kind"] == "fans" and fans:
                            fans.apply_many({})
                        elif t["kind"] == "hue" and hue:
                            hue.set_color(t["hue_id"], (0, 0, 0))
                        elif t["kind"] == "ble":
                            t["strip"].write((0, 0, 0), timeout=3.0)
                    except Exception as e:  # biri olmazsa diğerleri yine sönsün
                        self.log(f"{t['title']} söndürülemedi: {e}")
                self._close_device_clients()
                for thing in (fans, client, hue, *strips):
                    try:
                        if thing:
                            thing.close()
                    except Exception:
                        pass
                time.sleep(0.3)  # yavaş cihazın kuyruğu boşalsın
                stop_openrgb(proc)
            finally:
                self._rgb_idle.set()

        self.runner.start(job, always=True)

    def _restart_openrgb(self):
        """Yalnızca OpenRGB'yi kapatıp yeniden başlatır (düşen cihazı yeniden bulsun); Hue ve Bluetooth
        şeride dokunmaz. Yenisi, eskisi tamamen kapandıktan sonra başlar (_connect _rgb_idle'ı bekler)."""
        client, fans, proc = self.client, self.fans, self.proc
        self.client = self.fans = self.proc = None
        self._rgb_idle.clear()

        def job(_stop):
            try:
                self._close_device_clients()
                for thing in (fans, client):
                    try:
                        if thing:
                            thing.close()
                    except Exception:
                        pass
                time.sleep(0.3)
                stop_openrgb(proc)
            finally:
                self._rgb_idle.set()

        self.runner.start(job, always=True)
        threading.Thread(target=self._connect, daemon=True).start()

    # ---- oyun modu
    def toggle_game(self):
        self.set_game(not self.game_mode)

    def _game_cfg(self, key, default):
        return self.settings_page.cfg().get(key, default)

    def set_game(self, on):
        if on == self.game_mode or (on and not self._game_cfg("enabled", True)):
            return
        self.game_mode = on
        self.tray.set_game(on)
        self.log("oyun modu " + ("açıldı" if on else "kapandı"))
        self.settings_page.game_changed(on)
        self.shots_page.apply_hotkeys()
        rgb = self.feature("rgb")
        if on:
            keep = self._game_cfg("keep_lights", False)
            if not keep:
                self._last = None
                self.config.setdefault("lights", {})["last_rgb"] = [0, 0, 0]
            self.window.hide()
            self._game_kept_lights = keep
            self._game_closed_openrgb = rgb and not keep and self._game_cfg("close_openrgb", True)
            if keep:
                pass  # ışıklar olduğu gibi sürer
            elif self._game_closed_openrgb:
                self._stop_rgb(black=True)
            elif rgb:
                self._schedule(0)
            if self.feature("fan") and not self.fan_page.host:
                self._game_started_fans = True
                self.fan_page.start()
        else:
            if self._game_closed_openrgb and rgb:
                self._start_rgb()
            elif rgb and not self._game_kept_lights:
                self._schedule(0)
            self._game_closed_openrgb = False
            if self._game_started_fans:
                self._game_started_fans = False
                self.fan_page.stop()

    def game_settings_changed(self):
        if self.game_mode and not self._game_cfg("enabled", True):
            self.set_game(False)
        self._game_watcher_update()

    def _game_watcher_update(self):
        from .game import GameWatcher
        want = self._game_cfg("enabled", True) and self._game_cfg("auto", True)
        if want and not self.game_watcher:
            self.game_watcher = GameWatcher(lambda active: self.events.put(("game", active)),
                                            self.settings_page.excluded)
        elif not want and self.game_watcher:
            self.game_watcher.stop()
            self.game_watcher = None

    def _tray_menu(self):
        """Tepsi menüsü iş parçacığından okunur; yalnızca yazıları hazırlar."""
        items = [(lang.T("Aç"), "show"), None]
        if self.feature("rgb"):
            items.append((lang.T("Işıkları aç" if self.state["off"] else "Işıkları kapat"), "lights"))
            items.append((lang.T("Hazır ayar"), [(lang.T(name), f"preset:{name}") for name, _label in self._preset_options()]))
        if self._game_cfg("enabled", True):
            items.append((lang.T("Oyun modunu kapat" if self.game_mode else "Oyun modunu aç"), "game"))
        items += [None, (lang.T("Çıkış"), "exit")]
        return items

    def _window_visible(self, visible):
        """Tepside: zamanlayıcı yok; efekt hareketli değilse süreç verimlilik modunda."""
        set_efficiency(current_process(), not visible and not (self.job_info and self.job_info[1]))
        if visible:
            self._poll()
        elif self._tick:
            self.root.after_cancel(self._tick)
            self._tick = None

    def _window_state(self, maximized):
        self.max_button.configure(text="\uE923" if maximized else "\uE922")

    # ---- ayar dosyası ve kayıt
    def _save_config(self):
        text = json.dumps(self.config, ensure_ascii=False, indent=2)
        if text == self._saved_text:
            return  # değişiklik yok: diske yazılmaz
        try:
            os.makedirs(os.path.dirname(self.config_path), exist_ok=True)
            tmp = self.config_path + ".tmp"  # yarıda kesilen yazma ayar dosyasını bozmasın: önce yaz, sonra değiştir
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(text)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.config_path)
            self._saved_text = text
        except OSError as e:
            self._status(f"Ayarlar kaydedilemedi: {e}", RED)

    _log_lock = threading.Lock()

    def log(self, text):
        """Tanı kaydı: lightcore_log.txt (sorun olursa bu dosya gönderilir)."""
        line = time.strftime("%H:%M:%S ") + text + "\n"
        with self._log_lock:
            try:
                if os.path.exists(self.log_path) and os.path.getsize(self.log_path) > LOG_MAX_BYTES:
                    os.replace(self.log_path, self.log_path + ".old")
                with open(self.log_path, "a", encoding="utf-8") as f:
                    f.write(line)
            except OSError:
                pass  # kayıt yazılamazsa program yine çalışsın

    def _drain_loop(self):
        """OpenRGB her LED güncellemesini bütün bağlantılara bildirir. Boşta duran bağlantılar bunları
        okumazsa ~20 sn'de OpenRGB kilitlenir ve cihazlar renk almaz. Bildirim ancak motor yazarken
        oluşur: motor çalışmıyorken bu iş parçacığı tamamen uyur. İstek süren bağlantıya dokunmaz."""
        while True:
            self._draining.wait()
            clients = list({id(c): c for c in (self.client, *self.device_clients.values()) if c and c.sock}.values())
            try:
                ready = select.select([c.sock for c in clients], [], [], 1)[0] if clients else ()
            except (OSError, ValueError):  # bu arada kapanan bağlantı
                ready = ()
            for c in clients:
                if c.sock in ready:
                    try:
                        c.drain()
                    except (OSError, ProtocolError):
                        pass  # o bağlantıyı kullanan iş hatayı kendisi görür
            time.sleep(0.5)  # bildirimler birikebilir, tampon saniyelerce yeter

    # ---- iskelet
    def _build(self):
        # Solda yüzen simge rayı; cihaz listesi ve durum, raydaki düğmeyle açılıp kapanan panelde.
        self.rail = Rail(self.root, self.show_page, self.toggle_side)
        self.rail.pack(side="left", fill="y", padx=(10, 2), pady=10)
        self.side_open = self.config.get("side_open", False)
        self.rail.set_panel(self.side_open)
        side = tk.Frame(self.root, bg=BG, width=300)
        if self.side_open:
            side.pack(side="left", fill="y")
        side.pack_propagate(False)

        brand = tk.Frame(side, bg=BG, padx=18, pady=18)
        brand.pack(fill="x")
        logo = tk.Canvas(brand, width=S(30), height=S(30), bg=BG, highlightthickness=0)
        logo.pack(side="left", padx=(0, 12))
        self._logo = tk.PhotoImage(master=logo, data=icons.png_data("lightcore", S(30)), format="png")
        logo.create_image(0, 0, anchor="nw", image=self._logo)
        names = tk.Frame(brand, bg=BG)
        names.pack(side="left", fill="x", expand=True)
        brand_name = tk.Label(names, text="LightCore", bg=BG, fg=TEXT, font=(BOLD, 12), anchor="w")
        brand_name.pack(fill="x")
        self.subtitle = tk.Label(names, text="OpenRGB başlatılıyor...", bg=BG, fg=MUTED, font=(FONT, 8),
                                 anchor="w", justify="left", wraplength=220)
        self.subtitle.pack(fill="x")

        self.nav = {}
        self.nav_box = tk.Frame(side, bg=BG)  # yazılı menü: yerini simge rayı aldı, panelde gösterilmez
        self.game_watcher = None
        for key, icon, text, _feature in PAGES:
            self.nav[key] = NavItem(self.nav_box, icon, text, lambda k=key: self.show_page(k))
        self.devices_title = tk.Frame(side, bg=BG)
        tk.Label(self.devices_title, text="Cihazlar", bg=BG, fg=MUTED, font=(BOLD, 9), anchor="w").pack(side="left")
        self.devices_open = self.config.get("devices_open", True)
        self.devices_button = Pill(self.devices_title, "−" if self.devices_open else "+", self.toggle_devices, BG, height=22,
                                   padx=8, font=(FONT, 9))
        self.devices_button.pack(side="left", padx=(8, 0))
        bottom = tk.Frame(side, bg=BG, padx=18, pady=16)
        bottom.pack(side="bottom", fill="x")
        self.status = tk.Label(bottom, bg=BG, fg=MUTED, font=(FONT, 8), anchor="w", justify="left",
                               wraplength=260)
        self.status.pack(fill="x", pady=(0, 10))
        self.usage_label = tk.Label(bottom, bg=BG, fg=MUTED, font=(FONT, 8), anchor="w", justify="left")
        self.usage_label.pack(fill="x", pady=(0, 10))
        self.autostart_button = Pill(bottom, "⏻  Bilgisayar açılınca başlasın", self.toggle_autostart, BG,
                                     style="chip", width=264)
        self.autostart_button.pack(anchor="w", pady=(0, 6))
        self.rescan_button = Pill(bottom, "⟳  Cihazları yeniden tara", self.rescan, BG, width=264)
        self.side = side
        self.device_scroll = ScrollPage(side)  # çok cihaz olunca fare tekerleğiyle kayar
        self.device_list = self.device_scroll.inner
        self.placeholder = tk.Label(self.device_list, text="Cihazlar taranıyor, bu yarım dakikaya\nkadar sürebilir...",
                                    bg=BG, fg=MUTED, font=(FONT, 9), justify="left")
        self.placeholder.pack(anchor="w", padx=14, pady=6)

        self.content = tk.Frame(self.root, bg=BG)
        self.content.pack(side="left", fill="both", expand=True)
        # Panel kapalıyken de görünsün diye durum yazısı sayfanın altında da durur.
        self.foot = tk.Label(self.content, bg=BG, fg=MUTED, font=(FONT, 9), anchor="w", justify="left", wraplength=820)
        self.foot.pack(side="bottom", fill="x", padx=(8, 40), pady=(4, 10))
        # Kaymayan üst şerit: sayfa başlığı.
        header = tk.Frame(self.content, bg=BG)
        header.pack(fill="x", padx=(8, 40), pady=(22, 6))
        self.page_title = tk.Label(header, bg=BG, fg=TEXT, font=(BOLD, 22), anchor="w")
        self.page_title.pack(side="left")
        self.window.draggable(brand, logo, names, brand_name, self.subtitle, header, self.page_title)
        self._window_buttons()
        from .ui_fans import FansPage  # ui_fans bu modülün denetimlerini kullanır
        self.fan_page = FansPage(self)
        from .ui_shots import ShotsPage
        self.shots_page = ShotsPage(self)
        from .ui_settings import SettingsPage
        self.settings_page = SettingsPage(self)
        from .ui_dash import DashPage  # diğer sayfaların verisini okur: onlardan sonra kurulur
        self.dash_page = DashPage(self)
        self.pages = {"dash": self.dash_page.page,
                      "lights": self._build_lights(), "fans": self.fan_page.page, "shots": self.shots_page.page,
                      "settings": self.settings_page.page, "log": self._build_log()}
        threading.Thread(target=self._check_autostart, daemon=True).start()

    def _check_autostart(self):
        from . import autostart
        try:
            on, current = autostart.status(program_dir())
        except Exception as e:  # schtasks yoksa ya da çalışmazsa kutucuk kapalı görünür
            on, current = False, False
            self.log(f"başlangıç görevi okunamadı: {e}")
        self.events.put(("autostart", on, current))

    def toggle_autostart(self):
        from . import autostart
        from .fans import is_admin
        want = not self.autostart_button.active
        self.autostart_button.set_active(want)

        def work():
            try:
                admin = is_admin()
                if want:
                    autostart.enable(program_dir(), elevated=admin)
                    note = ("Bilgisayar açılınca küçültülmüş olarak başlayacak, son ayarları uygulayacak." if admin else
                            "Bilgisayar açılınca başlayacak, ama program yönetici olarak açık olmadığı için görev "
                            "yetkisiz kuruldu: fan kontrolü için programı bir kez yönetici olarak açıp kutucuğu "
                            "kapatıp yeniden işaretle.")
                else:
                    autostart.disable()
                    note = "Başlangıçta açılmayacak."
                self.events.put(("autostart_done", want, note, GREEN if (admin or not want) else AMBER))
            except Exception as e:
                self.events.put(("autostart_done", not want, f"Başlangıç ayarı değiştirilemedi: {e}", RED))

        threading.Thread(target=work, daemon=True).start()

    def _page(self, title):
        """Kayan sayfa. Başlığı kaymaz: üst şeritte (page_title) gösterilir."""
        page = ScrollPage(self.content)
        page.title = title
        tk.Frame(page.inner, bg=BG, height=4).pack(fill="x")
        return page

    def _section(self, page, title):
        tk.Label(page.inner, text=title, bg=BG, fg=TEXT, font=(BOLD, 10), anchor="w").pack(
            fill="x", padx=(8, 40), pady=(18, 6))
        box = tk.Frame(page.inner, bg=BG)
        box.pack(fill="x", padx=(8, 40))
        return box

    def _row(self, box, title, desc="", below=False):
        """Ayarlar satırı (yumuşak köşeli kart): solda başlık+açıklama, sağda (ya da altta) denetimler."""
        row = RoundBox(box)
        row.pack(fill="x", pady=(0, 6))
        left = tk.Frame(row.inner, bg=CARD)
        left.pack(side="top" if below else "left", fill="x", expand=not below)
        tk.Label(left, text=title, bg=CARD, fg=TEXT, font=(FONT, 10), anchor="w").pack(fill="x")
        desc_label = tk.Label(left, text=desc, bg=CARD, fg=MUTED, font=(FONT, 9), anchor="w",
                              justify="left", wraplength=520)
        if desc:
            desc_label.pack(fill="x")
        right = tk.Frame(row.inner, bg=CARD)
        right.pack(side="top" if below else "right", anchor="w", pady=(10, 0) if below else 0)
        return row, right, desc_label

    def _value_slider(self, right, lo, hi, step, command, fmt):
        value = tk.Label(right, bg=CARD, fg=TEXT, font=(FONT, 10), width=6, anchor="e")
        slider = Slider(right, lo, hi, step, command, CARD)
        slider.pack(side="left")
        value.pack(side="left", padx=(8, 0))
        slider.fmt, slider.value_label = fmt, value
        return slider

    # ---- Işıklar sayfası
    def _build_lights(self):
        page = self._page("Işıklar")
        self.preview_hint = tk.Label(page.inner, bg=BG, fg=MUTED, font=(FONT, 9), anchor="w")
        self.preview_hint.pack(fill="x", padx=(8, 40))
        self.preview = tk.Canvas(page.inner, height=S(8), bg=BG, highlightthickness=0)
        self.preview.pack(fill="x", padx=(8, 40), pady=(10, 0))
        self.preview.bind("<Configure>", lambda _e: self._draw_preview())

        box = self._section(page, "Efekt")
        from .ui_fans import Dropdown, labeled  # ui_fans bu modülün denetimlerini kullanır
        _, right, self.mode_desc = self._row(box, "Hazır ayar ve efekt", "", below=True)
        line = tk.Frame(right, bg=CARD)
        line.pack(anchor="w")

        def lists(key):
            def build(f):
                if key == "preset":
                    self.preset_list = Dropdown(f, self._preset_options(), None, self.apply_preset, width=280,
                                                placeholder="Seçili değil")
                    return self.preset_list
                self.mode_list = Dropdown(f, MODES, "cycle", self.set_mode, width=280, placeholder="Kapalı")
                return self.mode_list
            return build

        labeled(line, "Hazır ayar", lists("preset")).pack(side="left", padx=(0, 16))
        labeled(line, "Efekt", lists("mode")).pack(side="left", padx=(0, 16))
        # Tek tuşla kapat / aç: kapatınca seçimler durur, yeniden basınca aynı efekt geri gelir.
        off_box = tk.Frame(line, bg=CARD)
        off_box.pack(side="left", anchor="s")
        self.off_button = Pill(off_box, "Işıkları kapat", self.toggle_off, CARD, style="chip", width=150)
        self.off_button.pack()
        _, right, _ = self._row(
            box, "Kendi ayarlarım", "O anki renk ve efekti adıyla kaydet; Hazır ayar listesine eklenir. Yedek, kendi "
                                    "ayarlarını, fan eğrilerini ve kısayolları tek dosyada taşır.", below=True)
        line = tk.Frame(right, bg=CARD)
        line.pack(anchor="w")
        self.preset_name = tk.Entry(line, width=18, bg=CONTROL, fg=TEXT, insertbackground=TEXT, relief="flat",
                                    font=(FONT, 10), highlightthickness=1, highlightbackground=CONTROL_EDGE,
                                    highlightcolor=ACCENT)
        self.preset_name.pack(side="left", ipady=5, padx=(0, 6))
        self.preset_name.bind("<Return>", lambda _e: self.save_custom_preset())
        Pill(line, "Kaydet", self.save_custom_preset, CARD, style="accent").pack(side="left", padx=(0, 6))
        Pill(line, "Seçili ayarı sil", self.delete_selected_preset, CARD).pack(side="left", padx=(0, 16))
        Pill(line, "Yedeği dışa aktar", self.export_backup, CARD).pack(side="left", padx=(0, 6))
        Pill(line, "Yedeği içe aktar", self.import_backup, CARD).pack(side="left")

        box = self._section(page, "Renkler")
        row, right, self.colors_desc = self._row(box, "Seçili renkler", "", below=True)
        self.swatch_row = right
        self.picker_box = tk.Frame(row.inner, bg=CARD)
        self.picker = None
        self._build_swatches()

        box = self._section(page, "Ayarlar")
        _, right, _ = self._row(box, "Parlaklık", "Bütün açık cihazlar")
        self.brightness = self._value_slider(right, 0, 100, 1, self._brightness_changed, "%{:g}")
        _, right, _ = self._row(box, "Efekt hızı", "Nefes, Gökkuşağı, Pastel ve Nabız için")
        self.speed = self._value_slider(right, 1, 10, 1, self._speed_changed, "{:g}")
        self.hold_row, right, _ = self._row(box, "Her rengin süresi", "Renk geçişinde bir renk ne kadar sabit kalsın")
        self.hold = self._value_slider(right, 0, 30, 0.5, self._times_changed, "{:g} sn")
        self.fade_row, right, _ = self._row(box, "Geçiş süresi", "Sıradaki renge kaç saniyede geçilsin")
        self.fade = self._value_slider(right, 0.1, 10, 0.1, self._times_changed, "{:g} sn")
        self.device_colors_box = self._section(page, "Cihaza özel renk")
        self.color_picker_box = tk.Frame(self.device_colors_box, bg=BG)
        self._build_hue(page)
        self._build_ble(page)
        return page

    # ---- cihaza özel renk: efekti izlemez, kendi sabit rengini alır
    def _device_colors(self):
        return self.config.setdefault("device_colors", {})

    def _fill_device_colors(self):
        for w in self.device_colors_box.winfo_children():
            if w is not self.color_picker_box:
                w.destroy()
        self.color_picker_box.pack_forget()
        for t in self.targets:
            color = self._device_colors().get(t["key"])
            _, right, _ = self._row(self.device_colors_box, t["title"], "Efekti izliyor" if not color else f"Kendi rengi: #{color}")
            if color:
                Swatch(right, color, lambda _c, k=t: self.pick_device_color(k), CARD).pack(side="left", padx=(0, 6))
                Pill(right, "Efekte döndür", lambda k=t["key"]: self.set_device_color(k, None), CARD).pack(side="left")
            else:
                Pill(right, "Renk seç", lambda k=t: self.pick_device_color(k), CARD).pack(side="left")

    def pick_device_color(self, target):
        for w in self.color_picker_box.winfo_children():
            w.destroy()
        panel = RoundBox(self.color_picker_box)
        panel.pack(fill="x", pady=(0, 6))
        tk.Label(panel.inner, text=f"{target['title']} için renk", bg=CARD, fg=TEXT, font=(FONT, 10)).pack(anchor="w", pady=(0, 8))
        ColorPicker(panel.inner, lambda c: self.set_device_color(target["key"], c), self._fill_device_colors,
                    ok_text="Seç", color=self._device_colors().get(target["key"])).pack(anchor="w")
        self.color_picker_box.pack(fill="x")

    def set_device_color(self, key, color):
        if color:
            self._device_colors()[key] = color
        else:
            self._device_colors().pop(key, None)
        self._save_config()
        self._fill_device_colors()
        self._schedule()

    def _target_color(self, target):
        """Cihazın kendi rengi (parlaklıkla) ya da None."""
        color = self._device_colors().get(target["key"])
        if not color or self.state["off"]:
            return None
        return fx.scale(fx.parse_hex(color), self.state["brightness"] / 100)

    def _custom_presets(self):
        return self.config.setdefault("custom_presets", {})

    def _preset_options(self):
        return [(n, n) for n in PRESETS] + [(n, f"{n} (benim)") for n in self._custom_presets()]

    def _build_custom_presets(self):
        self.preset_list.set_options(self._preset_options())

    def delete_selected_preset(self):
        if self.preset in self._custom_presets():
            self.delete_custom_preset(self.preset)
        else:
            self._status("Silmek için listeden kendi kaydettiğin bir ayarı seç.", AMBER)

    def save_custom_preset(self):
        name = self.preset_name.get().strip()
        if not name:
            self._status("Kaydetmek için bir ad yaz.", AMBER)
        elif name in PRESETS:
            self._status(f"'{name}' hazır ayarlarda var, başka bir ad yaz.", AMBER)
        else:
            self._custom_presets()[name] = json.loads(json.dumps({k: self.state[k] for k in LIGHT_KEYS}))
            self.preset_name.delete(0, "end")
            self.preset = name
            self._build_custom_presets()
            self._sync_widgets()
            self._remember_lights()
            self._status(f"'{name}' kaydedildi.", GREEN)

    def delete_custom_preset(self, name):
        self._custom_presets().pop(name, None)
        if self.preset == name:
            self.preset = None
        self._build_custom_presets()
        self._sync_widgets()
        self._remember_lights()

    def export_backup(self):
        from tkinter import filedialog
        path = filedialog.asksaveasfilename(parent=self.root, title="Yedeği kaydet", defaultextension=".json",
                                            initialfile="lightcore_yedek.json", filetypes=[("JSON", "*.json")])
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(make_backup(self.config, self.state, self.palette), f, ensure_ascii=False, indent=2)
            self._status(f"Yedek kaydedildi: {path}", GREEN)
        except OSError as e:
            self._status(f"Yedek kaydedilemedi: {e}", RED)

    def import_backup(self):
        from tkinter import filedialog
        path = filedialog.askopenfilename(parent=self.root, title="Yedeği seç", filetypes=[("JSON", "*.json")])
        if path:
            self.load_backup(path)

    def load_backup(self, path):
        try:
            with open(path, encoding="utf-8-sig") as f:
                data = check_backup(json.load(f))
        except (OSError, ValueError, TypeError, AttributeError) as e:
            self._status(f"Yedek yüklenemedi: {e}", RED)
            return
        lights = data["lights"]
        self.state.update({k: lights[k] for k in LIGHT_KEYS})
        self.palette = list(lights.get("palette") or PALETTE)[-MAX_COLORS:]
        self.preset = None
        self._custom_presets().update(data.get("custom_presets", {}))
        self.config["disabled"] = data.get("disabled", self.config.get("disabled", []))
        self.config.setdefault("fans", {}).update(data.get("fans", {}))
        self.config.setdefault("shots", {}).update(data.get("shots", {}))
        self._build_swatches()
        self._build_custom_presets()
        self.fan_page._refresh()
        self.shots_page.refresh()
        self._sync_widgets()
        self._schedule()
        self._status("Yedek yüklendi.", GREEN)
        self.log(f"yedek yüklendi: {path}")

    def _build_swatches(self):
        for w in self.swatch_row.winfo_children():
            w.destroy()
        self.swatches = [Swatch(self.swatch_row, c, self.toggle_color, CARD, self.remove_color) for c in self.palette]
        for s in self.swatches:
            s.pack(side="left", padx=(0, 2))
        if len(self.palette) < MAX_COLORS:
            Swatch(self.swatch_row, None, lambda _c: self.add_color(), CARD).pack(side="left")

    # ---- Işıklar sayfasının Philips Hue bölümü
    def _build_hue(self, page):
        head = tk.Frame(page.inner, bg=BG)
        head.pack(fill="x", padx=(8, 40), pady=(18, 6))
        tk.Label(head, text="Philips Hue", bg=BG, fg=TEXT, font=(BOLD, 10), anchor="w").pack(side="left")
        self.hue_open = self.config.get("hue_open", bool(self.config.get("hue", {}).get("username")))
        self.hue_button = Pill(head, "−" if self.hue_open else "+", self.toggle_hue, BG, height=24, padx=10)
        self.hue_button.pack(side="left", padx=(10, 0))
        self.hue_body = tk.Frame(page.inner, bg=BG)
        if self.hue_open:
            self.hue_body.pack(fill="x", padx=(8, 40))
        box = tk.Frame(self.hue_body, bg=BG)
        box.pack(fill="x")
        _, right, self.hue_desc = self._row(box, "Köprü", "Bağlı değil")
        Pill(right, "Köprüyü bul", self.hue_find, CARD).pack(side="left")
        _, right, self.pair_desc = self._row(
            box, "Eşleştir", "Köprünün üstündeki büyük yuvarlak düğmeye bas, sonra 30 saniye içinde Eşleştir'e tıkla.")
        Pill(right, "Eşleştir", self.hue_pair, CARD, style="accent").pack(side="left")
        _, right, _ = self._row(box, "IP adresi", "Köprü bulunamazsa IP adresini yaz (Hue uygulaması > Ayarlar > "
                                                  "Hue Bridge'ler > i)")
        self.hue_ip = tk.Entry(right, width=16, bg=CONTROL, fg=TEXT, insertbackground=TEXT, relief="flat",
                               font=(FONT, 10), highlightthickness=1, highlightbackground=CONTROL_EDGE,
                               highlightcolor=ACCENT)
        self.hue_ip.pack(side="left", ipady=5, padx=(0, 8))
        self.hue_ip.insert(0, self.config.get("hue", {}).get("ip", ""))
        Pill(right, "Kullan", self.hue_use_ip, CARD).pack(side="left")
        self.hue_lights_box = tk.Frame(self.hue_body, bg=BG)
        self.hue_lights_box.pack(fill="x")
        self._fill_hue_lights()

    def toggle_hue(self):
        self.hue_open = not self.hue_open
        if self.hue_open:
            self.hue_body.pack(fill="x", padx=(8, 40))
        else:
            self.hue_body.pack_forget()
        self.hue_button.set_text("−" if self.hue_open else "+")
        self.config["hue_open"] = self.hue_open
        self._save_config()

    def _fill_hue_lights(self):
        for w in self.hue_lights_box.winfo_children():
            w.destroy()
        if not self.hue_targets:
            self._row(self.hue_lights_box, "Lamba yok", "Köprü eşleşince renkli lambalar burada ve soldaki "
                                                         "cihaz listesinde görünür.")
        for t in self.hue_targets:
            self._row(self.hue_lights_box, t["title"], t["subtitle"] + " · soldaki listeden açıp kapatabilirsin")

    # ---- Işıklar sayfasının Bluetooth şerit bölümü
    def _build_ble(self, page):
        if not blestrip.available():
            return
        self.ble_box = self._section(page, "Bluetooth LED şerit")
        self._fill_ble()

    def _ble_saved(self):
        return (self.config.get("ble") or {}).get("strips", [])

    def _fill_ble(self, status=None, color=MUTED):
        """Bölümü yeniden kurar: arama satırı, bulunan ama eklenmemiş şeritler, kayıtlı şeritler."""
        if self.ble_box is None:
            return
        from .ui_fans import Stepper, labeled
        for w in self.ble_box.winfo_children():
            w.destroy()
        _, right, self.ble_desc = self._row(
            self.ble_box, "Şerit ekle", status or "Telefondaki Lotus Lantern uygulamasını kapat, sonra Şerit bul'a bas. "
                                                  "Kayıtlı ve bağlı şeritler aramada görünmez.")
        self.ble_desc.configure(fg=color)
        Pill(right, "Şerit bul", self.ble_find, CARD).pack(side="left")
        for s in self.ble_found:
            _, right, _ = self._row(self.ble_box, s["name"], f"Bulundu · sinyal {s['rssi']} dBm (0'a yakın olan daha yakındır)")
            Pill(right, "Ekle", lambda s=s: self.ble_add(s), CARD, style="accent").pack(side="left")
        for i, s in enumerate(self._ble_saved()):
            title = "LED Şerit" if i == 0 else f"LED Şerit {i + 1}"
            _, right, _ = self._row(self.ble_box, f"{title} · {s.get('name') or 'ELK'}",
                                    "Renk düzeltme: sarı yeşile kaçıyorsa yeşili azalt; karışık renkler beyazımsı ya da "
                                    "pembe görünüyorsa eğriyi artır.", below=True)
            gains = s.get("gains", blestrip.DEFAULT_GAINS)
            for c, name in enumerate(("Kırmızı %", "Yeşil %", "Mavi %")):
                labeled(right, name, lambda f, c=c, a=s["address"], g=gains: Stepper(
                    f, round(g[c] * 100), 0, 100, 5, lambda v: self.set_ble_gain(a, c, v))).pack(side="left", padx=(0, 14))
            labeled(right, "Eğri", lambda f, a=s["address"], g=s.get("gamma", blestrip.DEFAULT_GAMMA): Stepper(
                f, g, 1.0, 3.5, 0.1, lambda v: self.set_ble_gamma(a, v))).pack(side="left")
            Pill(right, "Unut", lambda a=s["address"]: self.ble_forget(a), CARD).pack(side="left", padx=(18, 0), anchor="s")

    def _ble_status(self, text, color=MUTED):
        if self.ble_desc is not None:
            self.ble_desc.configure(text=text, fg=color)

    def _ble_setup(self, note=None):
        """Kayıtlı şeritlerin hedeflerini hazırlar; bağlantı ilk renkte kurulur."""
        saved = self._ble_saved() if blestrip.available() else []
        keep = {}
        for s in saved:
            keep[s["address"]] = self.ble_strips.get(s["address"]) or BleStrip(
                s["address"], s.get("name", ""), s.get("gains", blestrip.DEFAULT_GAINS),
                s.get("gamma", blestrip.DEFAULT_GAMMA), on_change=self._ble_changed)
        for address, strip in self.ble_strips.items():
            if address not in keep:
                threading.Thread(target=strip.close, daemon=True).start()
        self.ble_strips = keep
        self.ble_targets = [dict(key=f"ble:{s['address']}", stats=f"ble:{s['address']}",
                                 title="LED Şerit" if i == 0 else f"LED Şerit {i + 1}",
                                 subtitle=f"Bluetooth · {s.get('name') or 'ELK'}", device=None, kind="ble",
                                 strip=keep[s["address"]]) for i, s in enumerate(saved)]
        self.events.put(("ble_ready", note))

    def _ble_changed(self, strip, connected):
        """Arka plan iş parçacığından: şeride ulaşılamıyor / yeniden ulaşıldı."""
        self.log(f"Bluetooth şerit {strip.name}: " + ("yeniden bağlandı" if connected
                                                       else "ulaşılamıyor, arka planda yeniden denenecek"))
        self.events.put(("ble_state", strip.address, connected))

    def ble_find(self):
        self._ble_status("Aranıyor... (10 saniye kadar)", MUTED)

        def work():
            try:
                strips = blestrip.scan()
            except BleError as e:
                self.events.put(("ble_status", str(e), RED))
                return
            self.events.put(("ble_found", strips))

        threading.Thread(target=work, daemon=True).start()

    def _ble_found(self, strips):
        known = {s["address"] for s in self._ble_saved()}
        new = [s for s in strips if s["address"] not in known]
        if len(new) == 1:
            self.ble_add(new[0])
            return
        self.ble_found = new
        if new:  # bu şeritler yaygın ve şifresizdir: yakındaki başkasının şeridi de çıkabilir
            self._fill_ble(f"{len(new)} şerit bulundu. Seninkini ekle; yanlış olanı Unut ile kaldırabilirsin.", TEXT)
        else:
            self._fill_ble("Yeni şerit bulunamadı. Şerit açık mı, telefon uygulaması kapalı mı, bilgisayarın "
                           "Bluetooth'u açık mı?", AMBER)

    def _ble_refresh(self, note):
        if self.feature("rgb"):
            self._ble_setup(note)
        else:
            self._fill_ble(*note)

    def ble_add(self, found):
        strips = self.config.setdefault("ble", {}).setdefault("strips", [])
        strips.append({"address": found["address"], "name": found["name"],
                       "gains": list(blestrip.DEFAULT_GAINS), "gamma": blestrip.DEFAULT_GAMMA})
        self.ble_found = [s for s in self.ble_found if s["address"] != found["address"]]
        self._save_config()
        self.log(f"Bluetooth şerit eklendi: {found['name']} ({found['rssi']} dBm)")
        self._ble_refresh((f"Eklendi: {found['name']}", GREEN))

    def ble_forget(self, address):
        cfg = self.config.setdefault("ble", {})
        cfg["strips"] = [s for s in self._ble_saved() if s["address"] != address]
        self._save_config()
        self._ble_refresh((None, MUTED))

    def _ble_entry(self, address):
        return next((s for s in self._ble_saved() if s["address"] == address), None)

    def set_ble_gain(self, address, index, percent):
        entry = self._ble_entry(address)
        if entry is None:
            return
        gains = list(entry.get("gains", blestrip.DEFAULT_GAINS))
        gains[index] = percent / 100
        entry["gains"] = gains
        self._save_config()
        if address in self.ble_strips:
            self.ble_strips[address].gains = tuple(gains)
        self._schedule()

    def set_ble_gamma(self, address, value):
        entry = self._ble_entry(address)
        if entry is None:
            return
        entry["gamma"] = value
        self._save_config()
        if address in self.ble_strips:
            self.ble_strips[address].gamma = value
        self._schedule()

    # ---- Log sayfası
    def _build_log(self):
        page = self._page("Log")
        box = self._section(page, "Kayıt dosyası")
        _, right, _ = self._row(box, LOG_NAME, self.log_path)
        Pill(right, "Klasörü aç", lambda: self._open_folder(), CARD).pack(side="left", padx=(0, 6))
        Pill(right, "Hata raporu kopyala", self._copy_log, CARD).pack(side="left", padx=(0, 6))
        Pill(right, "Uyumluluk raporu kopyala", self._copy_compat, CARD).pack(side="left")
        box = self._section(page, "Canlı durum ve son kayıtlar")
        self.log_text = tk.Text(box, height=26, bg=CARD, fg=TEXT, font=("Consolas", 9), relief="flat",
                                padx=14, pady=12, highlightthickness=0, wrap="none")
        self.log_text.pack(fill="x")
        return page

    def _open_folder(self):
        try:
            os.startfile(program_dir())
        except (AttributeError, OSError) as e:
            self._status(f"Klasör açılamadı: {e}", RED)

    def compatibility_report(self):
        """Hangi donanım çalıştı: kullanıcı isterse projeye gönderir. Kişisel bilgi içermez."""
        lines = [f"LightCore {__version__}", f"Windows {platform.version()}", "Devices:"]
        lines += [f"- {t['subtitle']}" for t in self.targets if t["kind"] != "hue"] or ["- (none)"]
        controls = self.fan_page.hello.get("controls", [])
        lines += ["Fan controls:"] + ([f"- {c['hardware']}: {c['name']}" for c in controls] or ["- (fan control off)"])
        return "\n".join(lines)

    def _copy_compat(self):
        self.root.clipboard_clear()
        self.root.clipboard_append(scrub_report(self.compatibility_report()))
        self._status("Rapor panoya alındı: kullanıcı adı, adresler ve anahtarlar çıkarıldı. Göndermek senin elinde.", GREEN)

    def _copy_log(self):
        self.root.clipboard_clear()
        self.root.clipboard_append(scrub_report(self.log_text.get("1.0", "end")))
        self._status("Rapor panoya alındı: kullanıcı adı, adresler ve anahtarlar çıkarıldı. Göndermek senin elinde.", GREEN)

    def _fill_log(self):
        lines = [f"LightCore {__version__}"]
        if self.client:
            lines.append(f"OpenRGB SDK protokolü: {self.client.proto}")
        lines.append("Kasa fanları: " + (f"doğrudan HID · {self.fans.detection_info}" if self.fans
                                          else f"OpenRGB üzerinden (HID yok: {self.fans_error})"))
        lines.append("Philips Hue: " + (f"{self.hue.ip}, {len(self.hue_targets)} lamba" if self.hue else "bağlı değil"))
        if blestrip.available():
            lines.append("Bluetooth şerit: " + (", ".join(s.name for s in self.ble_strips.values()) or "kayıtlı değil"))
        lines += ["", "Canlı durum:"] + (self.health_lines or ["(efekt başlamadı)"])
        try:
            with open(self.log_path, "rb") as f:  # dosyanın tamamı değil, yalnızca sonu okunur
                f.seek(max(0, os.path.getsize(self.log_path) - 16384))
                tail = f.read().decode("utf-8", "replace").splitlines()[-120:]
        except OSError:
            tail = []
        lines += ["", "Son kayıtlar:"] + [line.rstrip("\n") for line in tail]
        y = self.log_text.yview()
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.insert("1.0", "\n".join(lines))
        self.log_text.configure(state="disabled")
        self.log_text.yview_moveto(y[0])

    # ---- sayfa geçişi
    def show_page(self, key):
        if self.page:
            self.pages[self.page].pack_forget()
        self.page = key
        self.pages[key].pack(fill="both", expand=True)
        self.page_title.configure(text=self.pages[key].title)
        for k, item in self.nav.items():
            item.set_selected(k == key)
        self.rail.select(key)
        if key == "log":
            self._fill_log()
        elif key == "dash":
            self.dash_page.refresh(force=True)

    def _wheel(self, event):
        """Tekerlek, imlecin altındaki bölümü kaydırır: sol cihaz listesi ya da sayfa.
        (Yol karşılaştırması tam yapılır: ".!frame2" de ".!frame" ile başlar.)"""
        widget = self.root.winfo_containing(event.x_root, event.y_root)
        side = str(self.side)
        if widget is not None and (str(widget) == side or str(widget).startswith(side + ".")):
            self.device_scroll.wheel(event)
        else:
            self.pages[self.page].wheel(event)

    # ---- durum değişiklikleri
    def apply_preset(self, name, restart=True):
        self.state.update(PRESETS.get(name) or self._custom_presets()[name])
        missing = [c for c in self.state["selected"] if c not in self.palette]
        if missing:
            self.palette = (self.palette + missing)[-MAX_COLORS:]
            self._build_swatches()
        self.preset = name
        self._sync_widgets()
        if restart:
            self._schedule()

    def toggle_off(self):
        self.state["off"] = not self.state["off"]
        self.preset = None
        self._sync_widgets()
        self._schedule()

    def _manual(self):
        self.preset = None
        self.state["off"] = False

    def toggle_color(self, color):
        sel = self.state["selected"]
        if color in sel:
            sel.remove(color)
        else:
            sel.append(color)
        self._manual()
        self._sync_widgets()
        self._schedule()

    def add_color(self):
        if self.picker:
            self._close_picker()
            return
        self.picker = ColorPicker(self.picker_box, self._color_picked, self._close_picker)
        self.picker.pack(anchor="w")
        self.picker_box.pack(anchor="w", pady=(12, 0))

    def _close_picker(self):
        self.picker.destroy()
        self.picker = None
        self.picker_box.pack_forget()

    def _color_picked(self, color):
        self._close_picker()
        if color not in self.palette:
            self.palette = (self.palette + [color])[-MAX_COLORS:]
            self._build_swatches()
        if color not in self.state["selected"]:
            self.toggle_color(color)
        else:
            self._sync_widgets()

    def remove_color(self, color):
        if len(self.palette) <= 1:
            return
        self.palette.remove(color)
        if color in self.state["selected"]:
            self.state["selected"].remove(color)
            self._schedule()
        self._build_swatches()
        self._sync_widgets()

    def set_mode(self, mode):
        self.state["mode"] = mode
        self._manual()
        self._sync_widgets()
        self._schedule()

    def _brightness_changed(self):
        if self._syncing:
            return
        self.state["brightness"] = int(self.brightness.get())
        self._manual()
        self._sync_widgets()
        self._schedule()

    def _speed_changed(self):
        if self._syncing:
            return
        self.state["speed"] = int(self.speed.get())
        self.preset = None
        self._sync_widgets()
        self._schedule()

    def _times_changed(self):
        if self._syncing:
            return
        self.state["hold"] = float(self.hold.get())
        self.state["fade"] = float(self.fade.get())
        self.preset = None
        self._sync_widgets()
        self._schedule()

    def toggle_target(self, target):
        target["on"] = not target["on"]
        self.log(f"{target['title']} {'açıldı' if target['on'] else 'kapatıldı'}")
        self._sync_widgets()
        self._schedule()

    def _sync_widgets(self):
        st = self.state
        self._syncing = True
        for slider, key in ((self.brightness, "brightness"), (self.speed, "speed"),
                            (self.hold, "hold"), (self.fade, "fade")):
            slider.set(st[key])
            slider.value_label.configure(text=slider.fmt.format(st[key]))
        self._syncing = False
        self.preset_list.set_current(self.preset)
        for s in self.swatches:
            s.set_selected(s.color in st["selected"] and not st["off"])
        self.mode_list.set_current(None if st["off"] else st["mode"])
        self.off_button.set_active(st["off"])
        self.off_button.set_text("Işıkları aç" if st["off"] else "Işıkları kapat", keep_width=True)
        n = len(st["selected"])
        cycle = st["mode"] == "cycle"
        if cycle and n < 2:
            self.colors_desc.configure(text="Renk geçişi için en az 2 renk seç", fg=AMBER)
        else:
            self.colors_desc.configure(text=f"Efektler seçtiğin renkleri kullanır ({n} seçili). + ile kendi "
                                            "rengini ekle, sağ tıkla listeden sil.", fg=MUTED)
        self.mode_desc.configure(text=MODE_NOTES[st["mode"]])
        for row in (self.hold_row, self.fade_row):
            if cycle and not row.winfo_ismapped():
                row.pack(fill="x", pady=(0, 6))
            elif not cycle and row.winfo_ismapped():
                row.pack_forget()
        self.preview_samples, footer = self._preview()
        self._draw_preview()
        on = sum(t["on"] for t in self.targets)
        self.preview_hint.configure(text=f"{footer} · {on} cihaza uygulanıyor" if self.targets else footer)
        for row in self.rows:
            row.update_view()
        self._update_subtitle()

    def _draw_preview(self):
        """Şerit efektin o anki rengini gösterir (canlı): pencere açıkken _animate_dots günceller."""
        c = self.preview
        if not getattr(self, "_preview_item", None):
            self._preview_item = c.create_rectangle(0, 0, 0, S(8), outline="", fill=DIM)
        c.coords(self._preview_item, 0, 0, max(1, c.winfo_width()), S(8))

    def effect(self):
        st = self.state
        if st["off"]:
            return fx.Static((0, 0, 0))
        colors = [fx.parse_hex(c) for c in st["selected"]] or [fx.parse_hex(self.palette[0])]
        seconds = 24.0 / max(1, st["speed"])
        mode = st["mode"]
        if mode == "cycle" and len(colors) >= 2:
            return fx.HoldCycle(colors, st["hold"], st["fade"])
        if mode == "rainbow":
            return fx.Rainbow(seconds)
        if mode == "pastel":
            return fx.Rainbow(seconds, saturation=0.55)
        if mode == "breathe":
            return fx.Breathe(colors[0], max(1.5, seconds / 3), colors)
        if mode == "pulse":
            return fx.Pulse(colors[0], 2.1 - 0.15 * st["speed"])
        if mode == "candle":
            return fx.Candle(colors[0])
        if mode == "flow":
            return fx.Flow(colors * 2 if len(colors) == 1 else colors, seconds)
        if mode == "neon":
            return fx.Neon(colors[0])
        if mode == "twinkle":
            return fx.Twinkle(colors[0])
        if mode == "disco":
            return fx.Disco(colors if len(st["selected"]) >= 2 else None, 3.0 / max(1, st["speed"]))
        if mode == "alarm":
            pair = colors[:2] if len(colors) >= 2 else ((255, 0, 20), (0, 40, 255))
            return fx.Alarm(pair[0], pair[1], 6.0 / max(1, st["speed"]))
        if mode == "fire":
            return fx.Fire()
        if mode == "lava":
            return fx.Lava(seconds)
        if mode == "aurora":
            return fx.Aurora(seconds)
        if mode == "ocean":
            return fx.Ocean(seconds)
        if mode == "storm":
            return fx.Storm()
        return fx.Static(colors[0])

    def _preview(self):
        st = self.state
        if st["off"]:
            return [], "Kapalı"
        eff = self.effect()
        b = st["brightness"] / 100
        if eff.is_static:
            samples = [eff.at(0)]
        else:
            period = getattr(eff, "seconds", 4.0)
            span = period / 2 if isinstance(eff, fx.Breathe) else period
            samples = [eff.at(span * i / 80) for i in range(80)]
        samples = [fx.scale(c, max(b, 0.25)) for c in samples]
        return samples, f"{dict(MODES)[st['mode']]} · %{st['brightness']}"

    def _update_subtitle(self):
        if not self.targets:
            return
        on = sum(t["on"] for t in self.targets)
        fan = "fanlar HID ile" if self.fans else "fanlar OpenRGB ile"
        self.subtitle.configure(text=f"{len(self.targets)} cihaz · {on} açık · {fan}")

    def _status(self, text, color=MUTED):
        self.status.configure(text=text, fg=color)
        self.foot.configure(text=text, fg=color)

    # ---- OpenRGB cihazları
    def _connect(self):
        post = self.events.put
        self._rgb_idle.wait(30)  # eski OpenRGB kapanmadan yenisi başlarsa bağlantı noktası çakışır
        try:
            if self.args.openrgb:
                post(("status", "OpenRGB başlatılıyor...", MUTED))
                self.proc = start_openrgb(self.args.openrgb, self.args.port)
            client = self._open_client()
            post(("status", "Cihazlar taranıyor...", MUTED))
            if self.proc:
                devices = client.wait_for_devices(
                    progress=lambda n: post(("status", f"Cihazlar taranıyor... {n} cihaz bulundu", MUTED)))
            else:
                devices = client.list_devices()
            fans, fans_error = None, None
            if self.args.board:
                try:
                    from .msi_hid import MsiJRainbow
                    fans = MsiJRainbow()
                except Exception as e:
                    fans_error = str(e)
                if fans is None and self.args.argb_leds > 0 and resize_fan_zones(client, devices, self.args.argb_leds):
                    devices = client.list_devices()
            self.log(f"OpenRGB SDK protokolü {client.proto}; fanlar: "
                     + (fans.detection_info if fans else f"OpenRGB (HID yok: {fans_error})"))
            for d in devices:
                self.log(f"cihaz {d.index}: {d.name} [{d.vendor}] tür {d.kind}, {d.led_count} LED, "
                         f"modlar: {', '.join(m.name for m in d.modes)}")
            post(("ready", client, devices, fans, fans_error))
        except Exception as e:
            post(("status", f"Bağlanılamadı: {e}", RED))

    def _recover_rgb(self, names):
        """Bağlantıyı yenilemek yetmedi: OpenRGB'nin cihazı yeniden bulması için onu baştan başlatır."""
        now = time.monotonic()
        title = ", ".join(names) or "Cihaz"
        if may_recover_rgb(self._rgb_recovered_at, now, self.game_mode, bool(self.proc)):
            self._rgb_recovered_at = now
            self.log(f"{title} düştü, OpenRGB yeniden başlatılıyor")
            self._status(f"{title} yanıt vermiyor, OpenRGB yeniden başlatılıyor...", AMBER)
            self._restart_openrgb()
        else:
            self._status(f"{title} cihazına ulaşılamıyor. Kablosunu kontrol et ya da programı yeniden aç.", RED)

    def _open_client(self):
        deadline = time.monotonic() + (45 if self.proc else 5)
        while True:
            client = OpenRGBClient(self.args.host, self.args.port)
            try:
                client.connect(timeout=3)
                return client
            except OSError:
                client.close()
                if time.monotonic() > deadline:
                    raise RuntimeError("OpenRGB'ye bağlanılamadı")
                time.sleep(0.5)

    def _is_board(self, d):
        return bool(jrgb_zones(d)) or any(z.name.strip().casefold().startswith(("jrainbow", "jargb"))
                                          for z in d.zones)

    def _set_devices(self, devices):
        self.devices = devices
        targets = []
        for d in devices:
            board = self._is_board(d)
            if board and not self.args.board:
                continue
            if board and self.fans:
                continue  # anakartın fanları aşağıda HID ile sürülür
            if d.led_count <= 0 and not board:
                continue
            title = "Kasa Fanları" if board else KIND_TITLES.get(d.kind, d.name)
            sub = f"{d.name} (OpenRGB)" if board else d.name
            targets.append(dict(key=f"dev:{d.name}:{d.index}", stats=d.controller_id, title=title,
                                subtitle=sub, device=d, kind="openrgb"))
        if self.fans:
            targets.append(dict(key="fans", stats="fans", title="Kasa Fanları",
                                subtitle="JRAINBOW 1-2 · doğrudan HID", device=None, kind="fans"))
        self.openrgb_targets = targets
        if self.fans_error and self.args.board:
            self._status("Fanlar için doğrudan HID açılamadı, OpenRGB kullanılıyor (Log).", AMBER)
        else:
            self._status(f"{len(targets)} cihaz hazır.", GREEN)
        self._rebuild_targets()

    def _rebuild_targets(self):
        previous = {t["key"]: t["on"] for t in self.targets}
        disabled = set(self.config.get("disabled", []))
        self.targets = self.openrgb_targets + self.hue_targets + self.ble_targets
        for t in self.targets:
            t["on"] = previous.get(t["key"], t["key"] not in disabled)
        for row in self.rows:
            row.destroy()
        self.rows = []
        if self.targets:
            self.placeholder.pack_forget()
        for t in self.targets:
            row = DeviceRow(self.device_list, t, self.toggle_target)
            row.pack(fill="x", pady=1)
            self.rows.append(row)
            if t["kind"] == "ble" and t["strip"].failing:
                row.set_problem("Bağlı değil", "yeniden denenecek")
        if self.page == "log":
            self._fill_log()
        self._fill_hue_lights()
        self._fill_device_colors()
        self._sync_widgets()
        self._schedule(0)

    def rescan(self):
        if not self.client:
            return
        self._status("Cihazlar yeniden taranıyor...", MUTED)
        client = self.client

        def work(_stop):
            # Motor işi gibi sıraya girer: çalışan efekt bitmeden bağlantıya dokunmaz.
            self._close_device_clients()  # cihaz kimlikleri değişebilir
            try:
                self.events.put(("rescanned", client.list_devices()))
            except Exception as e:
                self.events.put(("status", f"Tarama başarısız: {e}", RED))

        self.runner.start(work)
        if self.hue:
            self._hue_connect(self.hue.ip, self.hue.username)

    # ---- Philips Hue
    def hue_find(self):
        self._hue_status("Köprü aranıyor...", MUTED)

        def work():
            try:
                bridges = discover(progress=lambda text: self.events.put(("hue_status", text, MUTED)))
            except Exception as e:
                self.events.put(("hue_status", f"Arama başarısız: {e}", RED))
                return
            self.events.put(("hue_found", bridges))

        threading.Thread(target=work, daemon=True).start()

    def hue_use_ip(self):
        ip = self.hue_ip.get().strip()
        if not ip:
            return
        self.hue_found = [{"ip": ip, "id": "", "name": "Hue Bridge"}]
        saved = self.config.get("hue", {})
        if saved.get("ip") == ip and saved.get("username"):
            self._hue_connect(ip, saved["username"])
        else:
            self._hue_status(f"{ip} kullanılacak. Şimdi köprünün düğmesine basıp Eşleştir'e tıkla.", TEXT)

    def hue_pair(self):
        ip = (self.hue_found[0]["ip"] if self.hue_found else "") or self.hue_ip.get().strip()
        if not ip:
            self._hue_status("Önce 'Köprüyü bul' ya da IP adresi yaz.", AMBER)
            return
        self._hue_status(f"{ip} ile eşleşiliyor...", MUTED)

        def work():
            bridge = HueBridge(ip)
            try:
                username = bridge.pair()
            except HueLinkButtonNotPressed:
                self.events.put(("hue_status", "Köprüdeki yuvarlak düğmeye basılmamış. Bas ve 30 saniye içinde "
                                               "tekrar Eşleştir'e tıkla.", AMBER))
                return
            except HueError as e:
                self.events.put(("hue_status", str(e), RED))
                return
            finally:
                bridge.close()
            self.events.put(("hue_paired", ip, username))

        threading.Thread(target=work, daemon=True).start()

    def _hue_connect(self, ip, username):
        self._hue_status(f"{ip} köprüsüne bağlanılıyor...", MUTED)

        def work():
            bridge = HueBridge(ip, username)
            try:
                lights = bridge.lights()
            except HueError as e:
                self.events.put(("hue_status", f"Köprüye bağlanılamadı: {e}", RED))
                return
            finally:
                bridge.close()
            self.events.put(("hue_ready", bridge, lights))

        threading.Thread(target=work, daemon=True).start()

    def _hue_status(self, text, color):
        self.hue_desc.configure(text=text, fg=color)

    def _hue_ready(self, bridge, lights):
        self.hue = bridge
        self.log(f"Hue köprüsü {bridge.ip}: {len(lights)} renkli lamba "
                 f"({', '.join(l['name'] for l in lights)})")
        self._hue_status(f"Bağlı: {bridge.ip} · {len(lights)} renkli lamba", GREEN)
        self.hue_ip.delete(0, "end")
        self.hue_ip.insert(0, bridge.ip)
        self.hue_targets = [dict(key=f"hue:{l['id']}", stats="hue", title=l["name"],
                                 subtitle=f"Philips Hue · {l['product']}", device=None, kind="hue",
                                 hue_id=l["id"]) for l in lights]
        self._rebuild_targets()

    # ---- olaylar
    def _poll(self):
        """Yalnızca pencere açıkken çalışır."""
        if self._tick:
            self.root.after_cancel(self._tick)
            self._tick = None
        if self.window.hidden:
            return
        now = time.monotonic()
        if now - self._last_health >= 1:
            self._last_health = now
            self._health()
        self._animate_dots(now)
        if self.page == "dash":
            self.dash_page.tick(now)
        if now - self._last_usage >= 2:
            self._last_usage = now
            self._show_usage()
        self._drain_events()
        moving = self._moving(now)
        if moving and not self._live:
            self._live = self.root.after(LIVE_MS, self._live_tick)
        self._tick = self.root.after(POLL_ANIMATED_MS if moving else POLL_STILL_MS, self._poll)

    def _moving(self, now):
        return bool((self.job_info and self.job_info[1]) or (self._last and now - self._last[1] < 1))

    def _live_tick(self):
        """Canlı renk göstergeleri efekt hareketliyken akıcı yenilenir (yalnızca birkaç öğenin rengi değişir).
        Pencere gizliyken ya da renk sabitken çalışmaz; _poll gerekince yeniden başlatır."""
        self._live = None
        if self.window.hidden:
            return
        now = time.monotonic()
        self._animate_dots(now)
        if self.page == "dash":
            self.dash_page.animate(now)
        if self._moving(now):
            self._live = self.root.after(LIVE_MS, self._live_tick)

    def _drain_events(self):
        try:
            while True:
                event = self.events.get_nowait()
                kind = event[0]
                if kind == "status":
                    self._status(event[1], event[2])
                    if not self.targets:
                        self.subtitle.configure(text=event[1])
                elif kind == "ready":
                    _, self.client, devices, self.fans, self.fans_error = event
                    self._set_devices(devices)
                elif kind == "rescanned":
                    self._set_devices(event[1])
                elif kind == "device_error":
                    _, keys, text = event
                    names = []
                    for row in self.rows:
                        if row.target["stats"] in keys:
                            row.set_problem("Hata", text)
                            names.append(row.target["title"])
                    self._status(f"{', '.join(names) or 'Cihaz'}: {text}", AMBER)
                    if DROPPED_TEXT in text and not any(isinstance(k, str) for k in keys):
                        self._recover_rgb(names)
                elif kind == "hue_status":
                    self._hue_status(event[1], event[2])
                elif kind == "hue_found":
                    self.hue_found = event[1]
                    if self.hue_found:
                        ip = self.hue_found[0]["ip"]
                        self.hue_ip.delete(0, "end")
                        self.hue_ip.insert(0, ip)
                        saved = self.config.get("hue", {})
                        if saved.get("ip") == ip and saved.get("username"):
                            self._hue_connect(ip, saved["username"])
                        else:
                            self._hue_status(f"Köprü bulundu: {ip}. Düğmesine basıp Eşleştir'e tıkla.", TEXT)
                    else:
                        self._hue_status("Ağda Hue Bridge bulunamadı. Lambalar köprüsüz (Bluetooth) "
                                         "kullanılıyorsa bilgisayar onlara ulaşamaz.", AMBER)
                elif kind == "hue_paired":
                    _, ip, username = event
                    self.config["hue"] = {"ip": ip, "username": username}
                    self._save_config()
                    self.log(f"Hue köprüsü {ip} ile eşleşildi")
                    self._hue_connect(ip, username)
                elif kind == "hue_ready":
                    self._hue_ready(event[1], event[2])
                elif kind == "ble_ready":
                    self._fill_ble(*(event[1] or (None, MUTED)))
                    self._rebuild_targets()
                elif kind == "ble_state":
                    for row in self.rows:
                        if row.target["stats"] == f"ble:{event[1]}":
                            if event[2]:
                                row.update_view()
                            else:
                                row.set_problem("Bağlı değil", "yeniden denenecek")
                elif kind == "ble_status":
                    self._ble_status(event[1], event[2])
                elif kind == "ble_found":
                    self._ble_found(event[1])
                elif kind == "fan_started":
                    self.fan_page.started(event[1])
                elif kind == "fan":
                    self.fan_page.on_message(event[1])
                elif kind == "drop":
                    self.fan_page.on_drop(event[1])
                elif kind == "tray":
                    if event[1] == "show":
                        self.window.show()
                    elif event[1] == "exit":
                        self.close()
                        return
                    elif event[1] == "game":
                        self.toggle_game()
                    elif event[1] == "lights":
                        self.toggle_off()
                    elif event[1].startswith("preset:"):
                        self.apply_preset(event[1][7:])
                    else:  # uykudan dönüş: bazı cihazlar uykuda rengini kaybeder
                        self.log("uykudan dönüldü: renkler yeniden yazılıyor")
                        for strip in self.ble_strips.values():
                            strip.nudge()
                        self._schedule(3000)
                elif kind == "game":
                    self.set_game(event[1])
                elif kind == "checks":
                    self.settings_page.show_checks(event[1])
                elif kind == "pawnio":
                    self.settings_page.pawnio_done(event[1], event[2])
                elif kind == "shot":
                    self.shots_page.taken(event[1], event[2])
                elif kind == "shot_link":
                    self.shots_page.linked(event[1])
                elif kind == "shot_note":
                    self.shots_page.note(*event[1:])
                elif kind == "shot_keys":
                    self.shots_page.keys_result(event[1])
                elif kind == "shot_error":
                    self.log(f"ekran görüntüsü alınamadı: {event[1]}")
                    self._status(f"Ekran görüntüsü alınamadı: {event[1]}", RED)
                elif kind == "autostart":
                    _, on, current = event
                    self.autostart_button.set_active(on)
                    if on and not current:  # görev eski bir sürüm klasörünü başlatıyor: buraya çevir
                        from .fans import is_admin
                        if is_admin():
                            self.autostart_button.set_active(False)
                            self.toggle_autostart()
                elif kind == "autostart_done":
                    _, on, note, color = event
                    self.autostart_button.set_active(on)
                    self._status(note, color)
                    self.log("başlangıç: " + note)
        except queue.Empty:
            pass

    def _show_usage(self):
        """Programın, OpenRGB'nin ve fan yardımcısının toplam kullanımı (Görev Yöneticisi ölçeğinde)."""
        host = self.fan_page.host
        cpu, mb = self.usage.sample({"openrgb": self.proc, "fan": getattr(host, "proc", None)})
        parts = self.usage.parts
        detail = f"Program %{parts.get('self', 0):.1f}"
        if "openrgb" in parts:
            detail += f" · OpenRGB %{parts['openrgb']:.1f}"
        if "fan" in parts:
            detail += f" · Fan %{parts['fan']:.1f}"
        self.usage_label.configure(text=f"Toplam: %{cpu:.1f} işlemci · {mb:.0f} MB\n{detail}")
        self.dash_page.set_usage(f"%{cpu:.1f} işlemci · {mb:.0f} MB", detail)

    def _animate_dots(self, now):
        """Sol listedeki noktalar çalışan efektin o anki rengini gösterir."""
        if not self._last:
            return
        effect, began = self._last
        rgb = effect.value(now - began)
        shown = rgb if max(rgb) > 0 else (60, 60, 60)
        for row in self.rows:
            row.set_color((self._target_color(row.target) or shown) if row.target["on"] else None)
        if self.page == "lights" and rgb != self._preview_rgb:
            self._preview_rgb = rgb
            self.preview.itemconfigure(self._preview_item, fill=_hex(shown))

    def _health(self):
        """Pencere açıkken saniyede bir: hareketli efektte yazılamayan cihazı listede
        'Takıldı' diye işaretle; Log sayfası açıksa canlı durumu göster. Sabit renkte ve
        başka sayfadayken hiçbir iş yapılmaz."""
        if not self.job_info:
            return
        started, animated, stall_after = self.job_info
        on_log = self.page == "log"
        if not animated and not on_log:
            return
        now = time.monotonic()
        lines = []
        for row in self.rows:
            t = row.target
            if not t["on"]:
                continue
            count, last, slowest = self.stats.get(t["stats"], (0, started, 0.0))
            age = now - max(last, started)
            lines.append(f"{t['title']} ({t['subtitle']}): {count} yazma, son yazma {age:.1f} sn önce, "
                         f"en yavaş yazma {slowest * 1000:.0f} ms")
            if animated and age > stall_after:
                row.set_problem("Takıldı", f"{age:.0f} sn'dir renk yazılamıyor")
                if t["key"] not in self._stalled:  # kayda yalnızca bir kez geçer
                    self._stalled.add(t["key"])
                    self.log(f"takıldı: {lines[-1]}")
            elif row.problem == "Takıldı":
                self._stalled.discard(t["key"])
                row.update_view()  # yeniden yazılmaya başladı
        self.health_lines = lines
        if on_log and now - self._last_fill >= 2:
            self._last_fill = now
            self._fill_log()

    # ---- motor
    def _schedule(self, delay=150):
        if self._pending:
            self.root.after_cancel(self._pending)
        self._pending = self.root.after(delay, self._restart)

    def _restart(self):
        self._pending = None
        self._remember_lights()
        if not self.client and not self.hue and not self.ble_strips:
            return
        if self.game_mode and not self._game_kept_lights:
            self._game_dark()
            return
        on = [t for t in self.targets if t["on"]]
        off = [t for t in self.targets if not t["on"]]
        on_devs = [t["device"] for t in on if t["kind"] == "openrgb"]
        off_devs = [t["device"] for t in off if t["kind"] == "openrgb"]
        fans_on = any(t["kind"] == "fans" for t in on)
        fans_off = any(t["kind"] == "fans" for t in off)
        hue_own = {t["hue_id"]: self._target_color(t) for t in on if t["kind"] == "hue" and self._target_color(t)}
        hue_on = [t["hue_id"] for t in on if t["kind"] == "hue" and t["hue_id"] not in hue_own]
        overrides = {t["stats"]: self._target_color(t) for t in on
                     if t["kind"] not in ("hue", "ble") and self._target_color(t)}
        hue_off = [t["hue_id"] for t in off if t["kind"] == "hue"]
        live = set(self.ble_strips.values())
        # (şerit, sabit renk): kapalıysa siyah, kendi rengi varsa o; None ise efekti izler
        ble_plan = [(t["strip"], self._target_color(t) if t["on"] else (0, 0, 0))
                    for t in self.targets if t["kind"] == "ble" and t["strip"] in live]
        fans, client, hue = self.fans, self.client, self.hue
        # Yeni efekte, ışıkların o anki renginden yumuşakça geçilir.
        now = time.monotonic()
        start_rgb = (self._last[0].value(now - self._last[1]) if self._last
                     else tuple(self.config.get("lights", {}).get("last_rgb", (0, 0, 0))))
        effect = fx.Fade(start_rgb, self.effect(), self.state["brightness"] / 100)
        self._last = (effect, now)
        self.config["disabled"] = [t["key"] for t in off]
        self._save_config()
        self.log(f"efekt: {self._preview()[1]} ({type(effect.effect).__name__}), "
                 f"açık: {', '.join(t['title'] for t in on)}")
        report = lambda text: self.events.put(("status", f"Hata: {text}", RED))
        animated = not effect.effect.is_static
        # Renk geçişinin bekleme süresinde hiçbir şey yazılmaz: takılma sayılmasın.
        stall_after = STALL_SECONDS + getattr(effect.effect, "hold", 0.0)

        def device_error(group, text):
            keys = [group] if isinstance(group, str) else [d.controller_id for d in group]
            self.log(f"HATA {', '.join(map(str, keys))}: {text}")
            self.events.put(("device_error", keys, text))

        def job(stop):
            self.stats.clear()
            self.job_info = (time.monotonic(), animated, stall_after)
            if off_devs and client:
                apply(client, off_devs, fx.Static((0, 0, 0)), once=True, stop=stop)
            if fans and fans_off:
                fans.apply_many({})
            sinks, bridges = [], []
            if hue:
                bridge = HueBridge(hue.ip, hue.username)  # bu işe ait kendi bağlantısı
                bridges.append(bridge)
                try:
                    for light_id in hue_off:
                        bridge.set_color(light_id, (0, 0, 0))
                    for light_id, own in hue_own.items():
                        bridge.set_color(light_id, own, 0.4)
                except HueError as e:
                    device_error("hue", str(e))
                if hue_on:
                    sinks.append(HueSink(bridge, hue_on))
            sinks += [BleSink(strip, fixed) for strip, fixed in ble_plan]
            clients = self._device_clients(on_devs, report) if client else {}
            self._draining.set()
            try:
                apply_parallel(clients, on_devs if client else [], effect, fps=FPS, stop=stop,
                               fans=fans if fans_on else None, on_error=device_error,
                               stats=self.stats, sinks=sinks, overrides=overrides)
            finally:
                self._draining.clear()
                for bridge in bridges:  # Bluetooth şeridin bağlantısı açık kalır, sonraki iş onu kullanır
                    bridge.close()

        self.runner.start(job)

    def _game_dark(self):
        """Oyun modu, OpenRGB açık kalıyor: yalnızca ışıklar söner."""
        client, fans, hue = self.client, self.fans, self.hue
        on = [t for t in self.targets if t["on"]]

        def job(stop):
            for t in on:
                if t["kind"] == "ble":
                    try:
                        t["strip"].write((0, 0, 0), timeout=3.0)
                    except BleError as e:
                        self.log(f"{t['title']} söndürülemedi: {e}")
                    continue
                if t["kind"] == "openrgb" and client:
                    apply(client, [t["device"]], fx.Static((0, 0, 0)), once=True, stop=stop)
                elif t["kind"] == "fans" and fans:
                    fans.apply_many({})
                elif t["kind"] == "hue" and hue:
                    hue.set_color(t["hue_id"], (0, 0, 0))

        self.job_info = None
        self.runner.start(job)

    def _device_clients(self, devices, report):
        """Her cihaza kendi OpenRGB bağlantısı (motor iş parçacığında açılır ve yeniden başlatmalar
        arasında saklanır). Anakart ve RAM ana bağlantıyı kullanır: OpenRGB her güncellemeyi bütün
        bağlantılara bildirdiği için her fazla bağlantı ona iş demektir."""
        clients = {}
        for d in devices:
            # Anakart ve RAM aynı yavaş SMBus hattını paylaşır: tek bağlantı, tek sıra.
            key = "bus" if d.kind in BUS_DEVICE_KINDS else d.controller_id
            c = self.device_clients.get(key)
            if c is None and key == "bus" and self.client and self.client.sock:
                c = self.client
                c.sock.settimeout(3.0)  # takılan cihaz hata verip yeniden denensin
                self.device_clients[key] = c
            if c is None:
                c = OpenRGBClient(self.args.host, self.args.port, name=f"lightcore-{key}")
                try:
                    # Efekt bağlantısında 3 sn'den uzun onay beklenmez: takılan cihaz
                    # hata verip yeniden denenir, efekt değişimi de çabuk olur.
                    c.connect(timeout=3, command_timeout=3.0)
                    self.device_clients[key] = c
                    self.log(f"bağlantı açıldı: {key} (SDK protokolü {c.proto}"
                             + (", onaylı" if c.proto >= 6 else ", onaysız: her komuttan sonra eşitleme") + ")")
                except OSError as e:
                    report(f"{d.name} için ayrı bağlantı açılamadı ({e})")
                    c = self.client
            clients[d.controller_id] = c
        return clients

    def _close_device_clients(self):
        for c in self.device_clients.values():
            if c is not self.client:
                c.close()
        self.device_clients = {}

    # ---- son ayarlar: açılışta geri gelir, kapanışta donanımda kalır
    def _restore_lights(self):
        saved = self.config.get("lights")
        if not isinstance(saved, dict) or not all(k in saved for k in LIGHT_KEYS):
            return False
        try:
            for c in (*saved["selected"], *saved.get("palette", ())):
                fx.parse_hex(c)
        except (AttributeError, TypeError, ValueError):
            return False
        self.state.update({k: saved[k] for k in LIGHT_KEYS})
        self.palette = list(saved.get("palette") or PALETTE)[-MAX_COLORS:]
        self.preset = saved.get("preset") if saved.get("preset") in (*PRESETS, *self._custom_presets()) else None
        self._build_swatches()
        self._sync_widgets()
        return True

    def _remember_lights(self):
        keep = self.config.get("lights", {})
        self.config["lights"] = {**{k: self.state[k] for k in LIGHT_KEYS}, "palette": self.palette,
                                 "preset": self.preset, "last_rgb": keep.get("last_rgb", [0, 0, 0]),
                                 "saved": keep.get("saved", {})}
        self._save_config()

    def final_color(self):
        """Program kapanınca donanımda kalacak renk: seçili ilk renk (parlaklığıyla). Donanım
        kendi başına efekt oynatamaz; seçilen rengi olmayan efektte o anki renk kalır."""
        st = self.state
        if st["off"]:
            return (0, 0, 0)
        if not st["selected"] and self._last:
            return self._last[0].value(time.monotonic() - self._last[1])
        if st["mode"] in ("rainbow", "pastel", "fire", "lava", "aurora", "ocean", "storm") and self._last:
            return self._last[0].value(time.monotonic() - self._last[1])
        color = st["selected"][0] if st["selected"] else self.palette[0]
        return fx.scale(fx.parse_hex(color), st["brightness"] / 100)

    def _hold_last_colors(self):
        """Her açık cihaza son rengi yazar ve yazılımsız tutmasını sağlar. Cihazın kendi
        belleğine yazma ömrü sınırlıdır: renk son kayıttan farklıysa yapılır."""
        general = self.final_color()
        lights = self.config.setdefault("lights", {})
        lights["last_rgb"] = list(general)
        saved = lights.setdefault("saved", {})
        on = [t for t in self.targets if t["on"]]
        for t in on:
            rgb = self._target_color(t) or general
            color = _hex(rgb)
            try:
                changed = saved.get(t["key"]) != color
                if t["kind"] == "openrgb" and self.client:
                    d = t["device"]
                    apply(self.client, [d], fx.Static(rgb), once=True, skip_board=False)
                    if hold_color(self.client, d, rgb, save=changed):
                        saved[t["key"]] = color
                        self.log(f"kapanış: {t['title']} {color} cihaz belleğine yazıldı")
                elif t["kind"] == "fans" and self.fans:
                    self.fans.hold(rgb, save=changed)
                    saved[t["key"]] = color
                    self.log(f"kapanış: {t['title']} {color} anakartta sabitlendi")
                elif t["kind"] == "hue" and self.hue:
                    self.hue.set_color(t["hue_id"], rgb, 0.4)
                elif t["kind"] == "ble":
                    t["strip"].write(rgb, timeout=3.0)  # şerit yoksa kapanış beklemesin
            except Exception as e:  # biri olmazsa diğerleri yine yazılsın
                self.log(f"kapanış: {t['title']} son rengi alamadı: {e}")
        self._save_config()
        time.sleep(0.3)  # OpenRGB kapatılmadan yavaş cihazın kuyruğu boşalsın

    def close(self):
        self.events.root = None
        if self._tick:
            self.root.after_cancel(self._tick)
        self.root.withdraw()  # kullanıcı beklemesin; kalan iş birkaç saniye sürebilir
        self.shots_page.stop()
        self.tray.stop()
        if self.game_watcher:
            self.game_watcher.stop()
        self.fan_page.shutdown()  # fanları BIOS'a geri ver (FanHost bunu çıkarken yapar)
        self.runner.halt(timeout=2)  # takılı bir cihaz pencereyi kapatmayı engellemesin
        self._remember_lights()
        if self._last and not self.game_mode:  # efekt hiç başlamadıysa donanıma dokunulmaz; oyun modunda ışıklar kapalı kalır
            worker = threading.Thread(target=self._hold_last_colors, daemon=True)
            worker.start()
            worker.join(HOLD_SECONDS)
        self._close_device_clients()
        for thing in (self.fans, self.client, self.hue, *self.ble_strips.values()):
            try:
                if thing:
                    thing.close()
            except Exception:
                pass
        stop_openrgb(self.proc)
        self.root.destroy()


def run(args):
    if already_running():
        return 0
    root = tk.Tk()
    App(root, args)
    root.mainloop()
    return 0
