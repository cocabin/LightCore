"""Pano sayfası: programın o anki durumu tek bakışta, kartlar halinde.

Düzen: ortada kart ızgarası (üstte koyu yeşil hazır ayar kartı, altında cihazlar / işlemci / fanlar,
sonra ekran kartı ile oyun modu + hazır ayarlar), sağda dar sütun (fan eğrisi, ekran görüntüleri, olaylar).

Kendi ayarı yoktur: veriyi App ile FansPage'den okur, düğmeleri var olan işlevleri çağırır (hazır ayar,
oyun modu, ekran görüntüsü). Pencere açıkken ve bu sayfa görünürken App._poll her turda tick() çağırır;
sayılar saniyede bir, kayıtlar 5 saniyede bir yenilenir. Değişmeyen bölüm yeniden çizilmez.
"""
import math
import os
import time
import tkinter as tk
from tkinter import font as tkfont

from . import fans as fanctl
from . import icons, lang
from .dpi import S
from .ui import (ACCENT, AMBER, BG, BOLD, CARD, CONTROL, CONTROL_EDGE, CONTROL_HOVER, DIM, FONT, MODES, MUTED,
                 ON_PEACH, PEACH, PRESETS, RAIL, RAIL_END, RAIL_INK, RAIL_ON, RAIL_TEXT, RED, TEXT, Pill, RoundBox,
                 Toggle, _circle, _clamp, _gradient, _hex, _image, _mask_corners, _rgb, _round_rect)

MAX_DEVICES, MAX_FANS, MAX_PRESETS, MAX_EVENTS, MAX_SHOTS = 6, 5, 6, 5, 4
FAN_FLOOR = 25  # FanHost'un alt sınırı (%): sütunlarda kesikli çizgi
SIDE_W = 300  # sağ sütunun genişliği
# Windows'la gelen yazı tipleri; ilk bulunan kullanılır.
ITALIC_FONTS = ("Sitka Display", "Sitka Heading", "Constantia", "Georgia")  # üst karttaki vurgu sözcüğü
DISPLAY_FONTS = ("Segoe UI Variable Display", FONT)  # üst karttaki cümle
NUMBER_FONTS = ("Segoe UI Variable Display Semib", BOLD)  # büyük sayılar
# OpenRGB cihaz türü -> simge
DEVICE_ICONS = {0: "board", 1: "ram", 2: "gpu", 3: "fan", 4: "strip", 5: "keyboard", 6: "mouse", 7: "mouse",
                8: "headset", 11: "bulb", 12: "speaker", 15: "case"}


def _mix(a, b, f):
    return _hex([round(u + (v - u) * f) for u, v in zip(_rgb(a), _rgb(b))])


def _ramp(stops, f):
    """Renk duraklarında f (0-1) konumundaki renk."""
    pos = _clamp(f) * (len(stops) - 1)
    i = min(int(pos), len(stops) - 2)
    return _mix(stops[i], stops[i + 1], pos - i)


TRACK = _mix(CARD, TEXT, 0.08)  # boş sütun, çentik ve kutu zemini: kartın üstünde seçilsin
HEAT = (ACCENT, _mix(ACCENT, PEACH, 0.6), PEACH, RED)  # sıcaklık çubuğu: serinden sınıra


def device_kind(target):
    """Cihaz dairesindeki simge: hedefin türüne göre."""
    kind = target.get("kind")
    if kind == "fans" or target.get("title") == "Kasa Fanları":
        return "fan"
    if kind == "hue":
        return "bulb"
    if kind == "ble":
        return "strip"
    return DEVICE_ICONS.get(getattr(target.get("device"), "kind", None), "generic")


def _device_image(widget, kind, size, color):
    root = widget._root()
    cache = root.__dict__.setdefault("_device_icons", {})
    if (kind, size, color) not in cache:
        cache[kind, size, color] = tk.PhotoImage(master=root, data=icons.device_png_data(kind, size, _rgb(color)), format="png")
    return cache[kind, size, color]


_FONTS = {}


def _font(font):
    """Ölçüm için yazı tipi nesnesi; her çizimde yeniden oluşturulmaz."""
    if font not in _FONTS:
        _FONTS[font] = tkfont.Font(font=font)
    return _FONTS[font]


def _fit_text(text, font, width):
    """Genişliğe sığmayan yazı sonuna … konarak kısaltılır."""
    f = _font(font)
    if f.measure(text) <= width:
        return text
    while text and f.measure(text + "…") > width:
        text = text[:-1]
    return text + "…"


def curve_points(curve):
    """Eğrinin çizilebilir (sıcaklık, %) noktaları; çizilemeyen türde (eşitle, karışım) None."""
    kind = curve.get("type", "graph")
    if kind == "graph":
        pts = curve.get("points") or []
    elif kind == "linear":
        pts = [(curve.get("min_temp", 40), curve.get("min_value", 30)),
               (curve.get("max_temp", 80), curve.get("max_value", 100))]
    elif kind == "flat":
        pts = [(30, curve.get("value", 50)), (100, curve.get("value", 50))]
    elif kind == "trigger":
        load = curve.get("load_temp", 70)
        pts = [(30, curve.get("idle_value", 30)), (load, curve.get("idle_value", 30)),
               (load, curve.get("load_value", 100)), (100, curve.get("load_value", 100))]
    else:
        return None
    return [(float(t), float(p)) for t, p in pts] or None


def tail_events(path, count=MAX_EVENTS):
    """Kayıt dosyasının sonundaki olaylar, yeniden eskiye: [(saat:dakika, metin)]."""
    try:
        with open(path, "rb") as f:
            start = max(0, os.path.getsize(path) - 4096)
            f.seek(start)
            lines = f.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return []
    if start:
        lines = lines[1:]  # ortasından başlayan ilk satır
    out = []
    for line in reversed(lines):
        stamp, _, text = line.partition(" ")
        if len(stamp) == 8 and stamp[2] == ":" and text and not text.startswith("---"):
            out.append((stamp[:5], text if len(text) <= 90 else text[:87] + "..."))
            if len(out) == count:
                break
    return out


def _ring_mask(widget, d, width, back, inner):
    """Halka biçiminde delik: dışı zemin rengi, içi düz daire; aradaki halkadan alttaki renk görünür."""
    def pixel(x, y):
        r = d / 2
        dist = math.hypot(x + 0.5 - r, y + 0.5 - r)
        outside = 1 - _clamp(r - dist + 0.5)
        if outside > 0:
            return (*_rgb(back), round(255 * outside))
        return (*_rgb(inner), round(255 * _clamp(r - width - dist + 0.5)))

    return _image(widget, ("ringmask", d, width, back, inner), d, d, pixel)


class Orb(tk.Canvas):
    """Renkli daire ya da (ring verilirse) renkli halkalı rozet. Altta düz renkli kare, üstte delikli maske
    durur: renk değişince yalnızca karenin rengi değişir, her renk için resim üretilmez.
    Renk None ise daire boş halka, rozetin halkası soluk olur."""

    def __init__(self, parent, diameter, bg, ring=0, icon="generic"):
        d = S(diameter)
        super().__init__(parent, width=d, height=d, bg=bg, highlightthickness=0)
        self.back, self.ring, self._rgb = bg, ring, None
        self._fill = self.create_rectangle(0, 0, d, d, fill=DIM if ring else bg, outline="")
        if ring:
            self.create_image(0, 0, anchor="nw", image=_ring_mask(self, d, S(ring), bg, CONTROL))
            self.create_image(d // 2, d // 2, image=_device_image(self, icon, S(24), TEXT))
        else:
            self.create_image(0, 0, anchor="nw", image=_circle(self, d, back=bg))
            self._hollow = self.create_image(0, 0, anchor="nw", image=_circle(self, d, ring=MUTED, ring_width=S(2)))

    def set_color(self, rgb):
        rgb = tuple(int(v) for v in rgb) if rgb is not None else None
        if rgb == self._rgb:
            return
        self._rgb = rgb
        if self.ring:
            self.itemconfigure(self._fill, fill=DIM if rgb is None else _hex(rgb))
        else:
            self.itemconfigure(self._hollow, state="normal" if rgb is None else "hidden")
            self.itemconfigure(self._fill, fill=self.back if rgb is None else _hex(rgb))


class Hero(tk.Canvas):
    """Üst kart: koyu yeşilden etkin hazır ayarın renklerine geçen zemin; üstünde cümle, renk noktaları,
    efektin o anki rengini gösteren daire ve tek vurgu düğmesi. Hepsi tuvale çizilir: renk geçişinin
    üstüne düz zeminli pencere öğesi konmaz."""
    H, TINT, BUTTON = 150, 620, "Sıradaki ayara geç  →"

    def __init__(self, parent, command, display, italic):
        super().__init__(parent, height=S(self.H), width=S(200), bg=BG, highlightthickness=0)
        self.command, self.display, self.italic = command, display, italic
        self.words, self.sub, self.colors = ("", "", ""), "", ()
        self._live, self._oval, self._hover, self._button = None, None, False, (0, 0, 0, 0)
        self.bind("<Configure>", lambda _e: self._draw())
        self.bind("<Motion>", lambda e: self._set_hover(self._on_button(e)))
        self.bind("<Leave>", lambda _e: self._set_hover(False))
        self.bind("<Button-1>", lambda e: self._on_button(e) and self.command())

    def set(self, words, sub, colors):
        if (words, sub, colors) != (self.words, self.sub, self.colors):
            self.words, self.sub, self.colors = words, sub, colors
            self._draw()

    def set_live(self, rgb):
        rgb = tuple(int(v) for v in rgb) if rgb is not None else None
        if rgb != self._live:
            self._live = rgb
            self._paint_live()

    def _paint_live(self):
        if self._oval:
            self.itemconfigure(self._oval, fill=_hex(self._live) if self._live else "")

    def _on_button(self, e):
        x1, y1, x2, y2 = self._button
        return x1 <= e.x <= x2 and y1 <= e.y <= y2

    def _set_hover(self, hover):
        if hover != self._hover:
            self._hover = hover
            self.configure(cursor="hand2" if hover else "")
            self._draw()

    def _draw(self):
        self.delete("all")
        w, h = self.winfo_width(), S(self.H)
        if w < S(200):
            return
        # Zemin düz koyu yeşil; renk geçişi sağ kenara yaslı sabit genişlikte bir resim. Resim yalnızca renkler
        # değişince üretilir, pencere boyutu değişince yalnızca yeri değişir.
        tints = [_mix(RAIL, "#" + c, 0.62) for c in self.colors[:3]] or [RAIL_END]
        self.create_rectangle(0, 0, w, h, fill=RAIL, outline="")
        self.create_image(w, 0, anchor="ne", image=_gradient(self, "hero", S(self.TINT), h, [RAIL] + tints))
        _mask_corners(self, 0, 0, w, h, S(24), BG)
        x = S(30)
        for i, word in enumerate(self.words):
            if word:  # iki yazı tipi aynı satır çizgisine oturur: alt kenar = satır çizgisi + harf kuyruğu payı
                font = self.italic if i == 1 else self.display
                item = self.create_text(x, S(72) + _font(font).metrics("descent"), text=word, anchor="sw",
                                        font=font, fill=RAIL_ON if i == 1 else RAIL_INK)
                x = self.bbox(item)[2] + S(9)
        self.create_text(S(30), S(102), text=self.sub, anchor="w", font=(FONT, 10), fill=RAIL_TEXT)
        # Sağ üst: efektin o anki rengi (düz daire, kenarı yumuşak halkayla örtülü) ve seçili renkler.
        d, small = S(46), S(24)
        cx, cy = w - S(30) - d // 2, S(50)
        self._oval = self.create_oval(cx - d // 2 + 2, cy - d // 2 + 2, cx + d // 2 - 2, cy + d // 2 - 2, outline="", fill="")
        self.create_image(cx, cy, image=_circle(self, d, ring=RAIL_INK, ring_width=S(3)))
        self._paint_live()
        x = cx - d // 2 - S(12) - small // 2
        for color in reversed(self.colors):
            self.create_image(x, cy, image=_circle(self, small, fill="#" + color, ring=RAIL_INK, ring_width=S(2)))
            x -= small + S(4)
        # Sağ alt: vurgu düğmesi.
        text, font = lang.T(self.BUTTON), (BOLD, 10)
        x2, y2 = w - S(30), h - S(22)
        x1, y1 = x2 - _font(font).measure(text) - S(36), y2 - S(36)
        self._button = (x1, y1, x2, y2)
        _round_rect(self, x1, y1, x2, y2, S(18), fill=_mix(PEACH, "#FFFFFF", 0.2) if self._hover else PEACH)
        self.create_text((x1 + x2) // 2, (y1 + y2) // 2, text=text, font=font, fill=ON_PEACH)


class PresetChip(tk.Canvas):
    """Hazır ayar düğmesi: renk noktaları ve ad; etkin olanın kenarı vurgu rengindedir."""

    def __init__(self, parent, name, colors, command):
        super().__init__(parent, width=S(60), height=S(40), bg=CARD, highlightthickness=0, cursor="hand2")
        self.name, self.colors, self.active, self.hover = name, colors[:3], False, False
        self.bind("<Button-1>", lambda _e: command())
        self.bind("<Enter>", lambda _e: self._set(hover=True))
        self.bind("<Leave>", lambda _e: self._set(hover=False))
        self.bind("<Configure>", lambda _e: self._draw())

    def set_active(self, active):
        if active != self.active:
            self._set(active=active)

    def _set(self, **changes):
        self.__dict__.update(changes)
        self._draw()

    def _draw(self):
        self.delete("all")
        w, h = self.winfo_width(), int(self["height"])
        if w < S(40):
            return
        fill = CONTROL_HOVER if self.hover else CONTROL
        _round_rect(self, 1, 1, w - 1, h - 1, h // 2, fill=fill, outline=ACCENT if self.active else fill)
        d, x = S(18), S(12)
        for color in self.colors:
            self.create_image(x, h // 2, anchor="w", image=_circle(self, d, fill="#" + color, ring=fill, ring_width=S(2)))
            x += d - S(6)
        x += S(12)
        self.create_text(x, h // 2, anchor="w", fill=TEXT, font=(FONT, 10),
                         text=_fit_text(self.name, (FONT, 10), max(S(20), w - x - S(12))))


class DashPage:
    def __init__(self, app):
        self.app = app
        self.page = app._page("Pano")
        self._sig = {}  # bölüm -> son çizilen durum
        self._last_refresh = self._last_slow = 0.0
        self._cells = []  # (hedef, Orb)
        self._chips = {}
        self._redraw_queued = None
        self._build()

    # ---- iskelet
    def _card(self, parent, title):
        # margin: içerik çerçevesinin köşesi yuvarlağın dışına taşmasın (en az yarıçapın ~0,3 katı)
        box = RoundBox(parent, radius=18, padx=16, pady=13, margin=6)
        head = tk.Frame(box.inner, bg=CARD)
        head.pack(fill="x", pady=(0, 8))
        tk.Label(head, text=title, bg=CARD, fg=TEXT, font=(BOLD, 11), anchor="w").pack(side="left")
        return box, head

    def _chart(self, parent, height):
        canvas = tk.Canvas(parent, height=S(height), width=S(40), bg=CARD, highlightthickness=0)
        canvas.bind("<Configure>", lambda _e: self._queue_redraw())
        return canvas

    def _build(self):
        body = tk.Frame(self.page.inner, bg=BG)
        body.pack(fill="x", padx=(4, 36), pady=(0, 16))
        body.grid_columnconfigure(0, weight=1)
        body.grid_columnconfigure(1, minsize=S(SIDE_W))
        center = tk.Frame(body, bg=BG)
        center.grid(row=0, column=0, sticky="nsew")
        right = tk.Frame(body, bg=BG)
        right.grid(row=0, column=1, sticky="nsew", padx=(4, 0))
        self._body, self._right, self._stacked = body, right, False
        body.bind("<Configure>", lambda e: self._layout(e.width))
        for i in range(12):
            center.grid_columnconfigure(i, weight=1, uniform="pano")
        cell = dict(padx=4, pady=4, sticky="nsew")

        families = set(tkfont.families(self.page))
        pick = lambda names: next((n for n in names if n in families), names[-1])
        number = (pick(NUMBER_FONTS), 26)

        # Üst kart: etkin hazır ayar, renkleri ve efektin o anki rengi.
        self.hero = Hero(center, self.next_preset, (pick(DISPLAY_FONTS), 19), (pick(ITALIC_FONTS), 25, "italic"))
        self.hero.grid(row=0, column=0, columnspan=12, **cell)

        box, head = self._card(center, "Cihazlar")
        box.grid(row=1, column=0, columnspan=5, **cell)
        self.dev_count = tk.Label(head, bg=CARD, fg=MUTED, font=(FONT, 9))
        self.dev_count.pack(side="right")
        self.dev_box = tk.Frame(box.inner, bg=CARD)
        self.dev_box.pack(fill="x")
        for i in range(3):
            self.dev_box.grid_columnconfigure(i, weight=1, uniform="cihaz")

        box, _ = self._card(center, "İşlemci")
        box.grid(row=1, column=5, columnspan=4, **cell)
        self.cpu_value = tk.Label(box.inner, text="—", bg=CARD, fg=TEXT, font=number, anchor="w")
        self.cpu_value.pack(fill="x")
        self.cpu_sub = tk.Label(box.inner, bg=CARD, fg=MUTED, font=(FONT, 9), anchor="w", justify="left", wraplength=180)
        self.cpu_sub.pack(fill="x")
        self.cpu_chart = self._chart(box.inner, 76)
        self.cpu_chart.pack(fill="x", pady=(8, 0))

        box, _ = self._card(center, "Fanlar")
        box.grid(row=1, column=9, columnspan=3, **cell)
        self.fan_chart = self._chart(box.inner, 176)
        self.fan_chart.pack(fill="x")
        self.fan_note = tk.Label(box.inner, bg=CARD, fg=MUTED, font=(FONT, 8), anchor="w", justify="left", wraplength=125)
        self.fan_note.pack(fill="x", pady=(6, 0))

        box, _ = self._card(center, "Ekran kartı")
        box.grid(row=2, column=0, columnspan=6, **cell)
        self.gpu_value = tk.Label(box.inner, text="—", bg=CARD, fg=TEXT, font=number, anchor="w")
        self.gpu_value.pack(fill="x")
        self.gpu_sub = tk.Label(box.inner, bg=CARD, fg=MUTED, font=(FONT, 9), anchor="w", justify="left", wraplength=280)
        self.gpu_sub.pack(fill="x")
        self.gpu_ticks = self._chart(box.inner, 18)
        self.gpu_ticks.pack(fill="x", pady=(10, 4))
        self.gpu_note = tk.Label(box.inner, bg=CARD, fg=MUTED, font=(FONT, 9), anchor="w")
        self.gpu_note.pack(fill="x")
        tiles = tk.Frame(box.inner, bg=CARD)
        tiles.pack(fill="x", pady=(12, 0))
        self.tile_values = []
        for i, caption in enumerate(("fanı program sürüyor", "fan BIOS'ta")):
            tiles.grid_columnconfigure(i, weight=1, uniform="kutu")
            tile = RoundBox(tiles, outer=CARD, fill=TRACK, radius=14, padx=10, pady=8)
            tile.grid(row=0, column=i, sticky="nsew", padx=(0, 4) if i == 0 else (4, 0))
            value = tk.Label(tile.inner, text="—", bg=TRACK, fg=TEXT, font=(number[0], 18))
            value.pack()
            tk.Label(tile.inner, text=caption, bg=TRACK, fg=MUTED, font=(FONT, 9)).pack()
            self.tile_values.append(value)

        column = tk.Frame(center, bg=BG)
        column.grid(row=2, column=6, columnspan=6, padx=4, pady=4, sticky="nsew")
        game = RoundBox(column, fill=RAIL, radius=28, padx=14, pady=4, margin=10)
        game.pack(fill="x")
        self.game_toggle = Toggle(game.inner, self.app.toggle_game, RAIL)
        self.game_toggle.value = False  # anahtar kapalı çizilerek başlasın (kayma canlandırması olmadan)
        self.game_toggle._frame = 0
        self.game_toggle.itemconfigure(self.game_toggle._item, image=self.game_toggle._image())
        self.game_toggle.pack(side="right")
        texts = tk.Frame(game.inner, bg=RAIL)
        texts.pack(side="left", fill="x", expand=True)
        tk.Label(texts, text="Oyun modu", bg=RAIL, fg=RAIL_INK, font=(BOLD, 10), anchor="w").pack(fill="x")
        self.game_sub = tk.Label(texts, bg=RAIL, fg=RAIL_TEXT, font=(FONT, 9), anchor="w")
        self.game_sub.pack(fill="x")
        box, head = self._card(column, "Hazır ayarlar")
        box.pack(fill="both", expand=True, pady=(8, 0))
        Pill(head, "Tümü", lambda: self.app.show_page("lights"), CARD, height=24, padx=8, font=(FONT, 9)).pack(side="right")
        chips = tk.Frame(box.inner, bg=CARD)
        chips.pack(fill="x")
        for i, name in enumerate(list(PRESETS)[:MAX_PRESETS]):
            chips.grid_columnconfigure(i % 2, weight=1, uniform="ayar")
            chip = PresetChip(chips, name, PRESETS[name]["selected"], lambda n=name: self.pick_preset(n))
            chip.grid(row=i // 2, column=i % 2, sticky="ew", padx=(0, 3) if i % 2 == 0 else (3, 0), pady=3)
            self._chips[name] = chip

        # Sağ sütun. En üstte programın kendi yükü: LightCore, OpenRGB ve fan yardımcısının toplamı
        # (Görev Yöneticisi ölçeğinde); kaydırmadan görünsün diye ilk sırada.
        box = RoundBox(right, radius=18, padx=16, pady=8, margin=6)
        box.pack(fill="x", pady=4)
        line = tk.Frame(box.inner, bg=CARD)
        line.pack(fill="x")
        tk.Label(line, text="Program kullanımı", bg=CARD, fg=MUTED, font=(FONT, 9), anchor="w").pack(side="left")
        self.usage_value = tk.Label(line, text="—", bg=CARD, fg=TEXT, font=(BOLD, 10), anchor="e")
        self.usage_value.pack(side="right")
        self.usage_detail = tk.Label(box.inner, bg=CARD, fg=MUTED, font=(FONT, 8), anchor="e")
        self.usage_detail.pack(fill="x")

        box, head = self._card(right, "Fan eğrisi")
        box.pack(fill="x", pady=4)
        self.curve_name = tk.Label(head, bg=CARD, fg=MUTED, font=(FONT, 9))
        self.curve_name.pack(side="right")
        self.curve_chart = self._chart(box.inner, 150)
        self.curve_chart.pack(fill="x")

        box, _ = self._card(right, "Ekran görüntüleri")
        box.pack(fill="x", pady=4)
        self.shot_box = tk.Frame(box.inner, bg=CARD)
        self.shot_box.pack(fill="x")

        box, head = self._card(right, "Olaylar")
        box.pack(fill="both", expand=True, pady=4)
        Pill(head, "Tümü", lambda: self.app.show_page("log"), CARD, height=24, padx=8, font=(FONT, 9)).pack(side="right")
        self.event_box = tk.Frame(box.inner, bg=CARD)
        self.event_box.pack(fill="x")

    def _layout(self, width):
        """Dar pencerede (ya da cihaz paneli açıkken) sağ sütun kartların altına iner."""
        stacked = width < S(880)
        if stacked == self._stacked:
            return
        self._stacked = stacked
        self._body.grid_columnconfigure(1, minsize=0 if stacked else S(SIDE_W))
        if stacked:
            self._right.grid(row=1, column=0, sticky="nsew", padx=0)
        else:
            self._right.grid(row=0, column=1, sticky="nsew", padx=(4, 0))

    # ---- yenileme
    def _changed(self, key, sig):
        if self._sig.get(key) == sig:
            return False
        self._sig[key] = sig
        return True

    def _queue_redraw(self):
        """Pencere boyutu değişince çizimler yeni genişliğe göre bir kez yenilenir."""
        if not self._redraw_queued:
            self._redraw_queued = self.page.after_idle(self._redraw)

    def _redraw(self):
        self._redraw_queued = None
        self._refresh_fans_data()

    def tick(self, now):
        self._animate(now)
        if now - self._last_refresh >= 1:
            self.refresh()

    def animate(self, now):
        """Yalnızca canlı renkler (App._live_tick saniyede 25 kez çağırır)."""
        self._animate(now)

    def set_usage(self, total, detail):
        """App._show_usage 2 saniyede bir verir: toplam işlemci ve bellek, altında parçaları."""
        if self._changed("usage", (total, detail)):
            self.usage_value.configure(text=total)
            self.usage_detail.configure(text=detail)

    def refresh(self, force=False):
        now = time.monotonic()
        self._last_refresh = now
        self._refresh_hero()
        self._refresh_devices()
        self._refresh_fans_data()
        self._refresh_game()
        if force or now - self._last_slow >= 5:
            self._last_slow = now
            self._refresh_shots()
            self._refresh_events()
        self._animate(now)

    def _animate(self, now):
        """Daireler çalışan efektin o anki rengini gösterir (cihaz panelindeki noktalar gibi)."""
        app = self.app
        rgb = None
        if app._last:
            effect, began = app._last
            rgb = effect.value(now - began)
        lit = rgb is not None and max(rgb) > 0
        self.hero.set_live(rgb if lit else None)
        for target, orb in self._cells:
            orb.set_color((app._target_color(target) or (rgb if lit else None)) if target["on"] else None)

    # ---- üst kart ve hazır ayarlar
    def _preset_names(self):
        return list(PRESETS) + list(self.app._custom_presets())

    def next_preset(self):
        names = self._preset_names()
        i = names.index(self.app.preset) if self.app.preset in names else -1
        self.pick_preset(names[(i + 1) % len(names)])

    def pick_preset(self, name):
        self.app.apply_preset(name)
        self.refresh()

    def _refresh_hero(self):
        app, st = self.app, self.app.state
        on = sum(1 for t in app.targets if t["on"])
        if st["off"]:
            words, sub, colors = ("Işıklar", "kapalı", ""), "Bir hazır ayar seçince yeniden yanar", ()
        else:
            words = ("Işıklar şu an", app.preset, "ayarında") if app.preset else ("Işıklar", "özel", "bir ayarda")
            sub = f"{dict(MODES).get(st['mode'], st['mode'])} · parlaklık %{st['brightness']}"
            sub += f" · {on} cihaz yanıyor" if app.targets else ""
            colors = tuple(st["selected"][:5])
        if app.game_mode:
            sub += " · oyun modu açık"
        self.hero.set(words, sub, colors)
        for name, chip in self._chips.items():
            chip.set_active(name == app.preset and not st["off"])

    def _refresh_devices(self):
        app = self.app
        targets = app.targets
        self.dev_count.configure(text=f"{sum(1 for t in targets if t['on'])} / {len(targets)}" if targets else "")
        shown = targets[:MAX_DEVICES]
        if not self._changed("devices", (tuple((t["key"], t["title"], t["subtitle"]) for t in shown), len(targets),
                                         app.feature("rgb"))):
            return
        for w in self.dev_box.winfo_children():
            w.destroy()
        self._cells = []
        if not shown:
            text = "Cihazlar taranıyor..." if app.feature("rgb") else "LightRGB kapalı."
            tk.Label(self.dev_box, text=text, bg=CARD, fg=MUTED, font=(FONT, 9), anchor="w").grid(
                row=0, column=0, columnspan=3, sticky="w")
            return
        for i, target in enumerate(shown):
            cell = tk.Frame(self.dev_box, bg=CARD)
            cell.grid(row=i // 3, column=i % 3, sticky="n", pady=6)
            orb = Orb(cell, 48, CARD, ring=3, icon=device_kind(target))
            orb.pack()
            tk.Label(cell, text=_fit_text(target["title"], (FONT, 9), S(84)), bg=CARD, fg=TEXT, font=(FONT, 9)).pack(pady=(4, 0))
            tk.Label(cell, text=_fit_text(target["subtitle"], (FONT, 8), S(84)), bg=CARD, fg=MUTED, font=(FONT, 8)).pack()
            self._cells.append((target, orb))
        if len(targets) > len(shown):
            tk.Label(self.dev_box, text=f"+{len(targets) - len(shown)} cihaz daha, cihaz panelinde", bg=CARD, fg=MUTED,
                     font=(FONT, 8), anchor="w").grid(row=2, column=0, columnspan=3, sticky="w", pady=(4, 0))

    # ---- sıcaklık ve fanlar
    def _fan_state(self):
        fp = self.app.fan_page
        return fp.state if fp.host and fp.planner and fp.state else None

    def _fan_hint(self):
        if not self.app.feature("fan"):
            return "LightFan kapalı."
        return "Fan kontrolü kapalı: sıcaklık ve fan verisi okunmuyor. LightFan sayfasından aç."

    def _refresh_fans_data(self):
        state = self._fan_state()
        self._refresh_cpu(state)
        self._refresh_gpu(state)
        self._draw_fans(state)
        self._draw_curve(state)

    def _refresh_cpu(self, state):
        fp = self.app.fan_page
        cpu = fp.current_temp("cpu") if state else None
        limit = fp.hello.get("limits", {}).get("cpu", 90)
        values = [s[0] for s in fp.history if s[0] is not None] if state else []
        if cpu is None:
            self.cpu_value.configure(text="—", fg=TEXT)
            self.cpu_sub.configure(text=self._fan_hint() if not state else "İşlemci sıcaklığı okunamıyor.")
        else:
            self.cpu_value.configure(text=f"{cpu:.0f}°C", fg=AMBER if cpu >= limit - 5 else TEXT)
            peak = f"Son 5 dakikada en yüksek {max(values):.0f}°C · " if values else ""
            self.cpu_sub.configure(text=f"{peak}koruma sınırı {limit:g}°C")
        c = self.cpu_chart
        w, h = c.winfo_width(), c.winfo_height()
        if w < S(60) or not self._changed("cpu_chart", (tuple(round(v) for v in values), w, h)):
            return
        c.delete("all")
        if len(values) < 2:
            c.create_text(w // 2, h // 2, text="Veri bekleniyor" if state else "", fill=MUTED, font=(FONT, 9))
            return
        lo = min(40, math.floor(min(values) / 5) * 5)
        hi = max(70, math.ceil(max(values) / 5) * 5)
        left, top, bottom, right = S(30), S(6), h - S(6), w - S(6)
        x = lambda i: left + i / (len(values) - 1) * (right - left)
        y = lambda v: bottom - (v - lo) / (hi - lo) * (bottom - top)
        for v in (lo, (lo + hi) / 2, hi):
            c.create_line(left, y(v), right, y(v), fill=CONTROL_EDGE)
            c.create_text(left - S(6), y(v), text=f"{v:.0f}°", fill=MUTED, font=(FONT, 8), anchor="e")
        pts = [(x(i), y(v)) for i, v in enumerate(values)]
        c.create_polygon(left, bottom, *[n for p in pts for n in p], right, bottom, fill=_mix(CARD, ACCENT, 0.16), outline="")
        c.create_line(*[n for p in pts for n in p], fill=ACCENT, width=S(2))
        c.create_image(*pts[-1], image=_circle(c, S(10), fill=ACCENT, ring=CARD, ring_width=S(2)))

    def _refresh_gpu(self, state):
        fp = self.app.fan_page
        gpu = fp.current_temp("gpu") if state else None
        limit = fp.hello.get("limits", {}).get("gpu", 85)
        hot = None
        if state:
            temps = fp.hello.get("temps", [])
            hardware = next((t["hardware"] for t in temps if t["id"] == fp.planner.sources.get("gpu")), None)
            spot = next((t for t in temps if t["name"] == "GPU Hot Spot" and t["hardware"] == hardware), None)
            hot = state["temps"].get(spot["id"]) if spot else None
        if gpu is None:
            self.gpu_value.configure(text="—", fg=TEXT)
            self.gpu_sub.configure(text=self._fan_hint() if not state else "Ekran kartı sıcaklığı okunamıyor.")
            self.gpu_note.configure(text="")
        else:
            self.gpu_value.configure(text=f"{gpu:.0f}°C", fg=AMBER if gpu >= limit - 5 else TEXT)
            self.gpu_sub.configure(text=f"Sıcak nokta {hot:.0f}°C" if isinstance(hot, (int, float)) else "")
            self.gpu_note.configure(text=f"Koruma sınırı {limit:g}°C · {max(0, limit - gpu):.0f}°C pay var")
        driven = bios = None
        if state:
            soft = lambda c: state["controls"].get(c["id"], {}).get("software")
            case = lambda c: not c["kind"].startswith("Gpu")
            driven = sum(1 for c in fp.hello.get("controls", []) if case(c) and soft(c))
            bios = sum(1 for card in fp.cards.values() if case(card.control) and not soft(card.control))
        for label, value in zip(self.tile_values, (driven, bios)):
            label.configure(text="—" if value is None else str(value))
        c = self.gpu_ticks
        w, h = c.winfo_width(), c.winfo_height()
        count = 32
        filled = 0 if gpu is None else min(count, round(gpu / limit * count))
        if w < S(60) or not self._changed("gpu_ticks", (filled, gpu is not None and gpu >= limit - 5, w, h)):
            return
        c.delete("all")
        step = w / count
        for i in range(count):  # dolu çentikler serinden sınıra doğru renk değiştirir
            c.create_rectangle(round(i * step), 0, round((i + 1) * step) - S(2), h, outline="",
                               fill=_ramp(HEAT, i / (count - 1)) if i < filled else TRACK)

    def _draw_fans(self, state):
        fp = self.app.fan_page
        fans = []
        if state:
            for cid, card in fp.cards.items():
                if card.control["kind"].startswith("Gpu"):
                    continue
                ctrl = state["controls"].get(cid, {})
                fid = fp._fan_by_control(card.control)
                value, rpm = ctrl.get("value"), state["fans"].get(fid) if fid else None
                fans.append((card.title, round(value) if isinstance(value, (int, float)) else None,
                             round(rpm) if isinstance(rpm, (int, float)) else None, bool(ctrl.get("software"))))
        fans = fans[:MAX_FANS]
        self.fan_note.configure(text=f"Gri sütun: BIOS sürüyor. Kesikli çizgi: alt sınır %{FAN_FLOOR}." if fans else "")
        c = self.fan_chart
        w, h = c.winfo_width(), c.winfo_height()
        if w < S(60) or not self._changed("fans", (tuple(fans), bool(state), w, h)):
            return
        c.delete("all")
        if not fans:
            text = "Fan bulunamadı." if state else self._fan_hint()
            c.create_text(0, 0, text=text, fill=MUTED, font=(FONT, 9), anchor="nw", width=w)
            return
        slot = w / len(fans)
        bar = max(S(12), min(S(32), round(slot) - S(10)))
        top, bottom = S(4), h - S(52)
        for i, (title, value, rpm, software) in enumerate(fans):
            cx = round(slot * (i + 0.5))
            x0 = cx - bar // 2
            _round_rect(c, x0, top, x0 + bar, bottom, bar // 2, fill=TRACK)
            if value:
                fill_top = max(top, bottom - round((bottom - top) * min(100, value) / 100))
                low, high = (ACCENT, _mix(ACCENT, RAIL_ON, 0.8)) if software else (MUTED, _mix(MUTED, CARD, 0.5))
                if bottom - fill_top > bar + 2:  # aşağıdan yukarı açılan renk geçişi; köşeleri maskeyle yuvarlanır
                    c.create_image(x0, fill_top, anchor="nw",
                                   image=_gradient(c, ("fan", i), bar, bottom - fill_top, (high, low), vertical=True))
                    _mask_corners(c, x0, fill_top, x0 + bar, bottom, bar // 2, CARD, top=TRACK)
                else:
                    _round_rect(c, x0, fill_top, x0 + bar, bottom, bar // 2, fill=low)
            floor = bottom - round((bottom - top) * FAN_FLOOR / 100)
            c.create_line(x0, floor, x0 + bar, floor, fill=CARD, dash=(3, 2))
            c.create_text(cx, bottom + S(12), text=f"%{value}" if value is not None else "—", fill=TEXT, font=(BOLD, 9))
            c.create_text(cx, bottom + S(27), text=_fit_text(title, (FONT, 8), round(slot) - S(4)), fill=MUTED, font=(FONT, 8))
            c.create_text(cx, bottom + S(41), text=f"{rpm}" if rpm is not None else "—", fill=MUTED, font=(FONT, 8))

    # ---- fan eğrisi
    def _hottest(self):
        fp = self.app.fan_page
        temps = [t for t in (fp.current_temp("cpu"), fp.current_temp("gpu")) if t is not None]
        return max(temps) if temps else None

    def _active_curve(self, state):
        """Gösterilecek eğri: oyun modundaysa oyun eğrisi, yoksa eğri kullanan ilk fanınki.
        (ad, noktalar, kaynağın o anki sıcaklığı) ya da None."""
        if not state:
            return None
        fp = self.app.fan_page
        game = fanctl.game_curve_of(fp.cfg().get("game_curve"))["points"]
        if self.app.game_mode:
            return "Oyun eğrisi", game, self._hottest()
        curves = {c["name"]: c for c in fp.curves() if c.get("name")}
        for cid, card in fp.cards.items():
            channel = fp.channels().get(cid, {})
            mode = channel.get("mode", "auto")
            if mode == "game":
                return f"{card.title} · Oyun eğrisi", game, self._hottest()
            if mode == "curve":
                name = channel.get("curve", "Dengeli")
                curve = fp.planner.resolve_curve(name, curves, channel)
                pts = curve_points(curve) if curve else None
                if pts:
                    source = curve.get("source", "cpu")
                    return f"{card.title} · {name}", pts, fp.current_temp(source if isinstance(source, str) else "cpu")
        return None

    def _draw_curve(self, state):
        c = self.curve_chart
        w, h = c.winfo_width(), c.winfo_height()
        info = self._active_curve(state)
        temp = info[2] if info else None
        self.curve_name.configure(text=_fit_text(info[0], (FONT, 9), S(150)) if info else "")
        sig = (info[0], tuple(info[1]), round(temp) if temp is not None else None) if info else bool(state)
        if w < S(60) or not self._changed("curve", (sig, w, h)):
            return
        c.delete("all")
        if not info:
            text = "Eğri kullanan fan yok. LightFan sayfasından bir fana eğri seç." if state else self._fan_hint()
            c.create_text(0, 0, text=text, fill=MUTED, font=(FONT, 9), anchor="nw", width=w)
            return
        _name, pts, _temp = info
        left, right, top, bottom, t0, t1 = S(36), w - S(10), S(8), h - S(22), 30, 100
        x = lambda t: left + (min(t1, max(t0, t)) - t0) / (t1 - t0) * (right - left)
        y = lambda p: bottom - min(100, max(0, p)) / 100 * (bottom - top)
        for p in (25, 50, 75, 100):
            c.create_line(left, y(p), right, y(p), fill=CONTROL_EDGE)
            c.create_text(left - S(6), y(p), text=f"%{p}", fill=MUTED, font=(FONT, 8), anchor="e")
        for t in (40, 60, 80, 100):
            c.create_text(x(t), h - S(8), text=f"{t}°", fill=MUTED, font=(FONT, 8), anchor="e" if t == t1 else "center")
        line = [(x(t0), y(pts[0][1]))] + [(x(t), y(p)) for t, p in pts] + [(x(t1), y(pts[-1][1]))]
        flat = [n for p in line for n in p]
        c.create_polygon(x(t0), bottom, *flat, x(t1), bottom, fill=_mix(CARD, ACCENT, 0.16), outline="")
        c.create_line(*flat, fill=ACCENT, width=S(2))
        if temp is None:
            return
        try:
            value = fanctl.curve_value(pts, temp)
        except ZeroDivisionError:  # tetik eğrisinin dik basamağı
            return
        px, py = x(temp), y(value)
        c.create_image(px, py, image=_circle(c, S(12), fill=PEACH, ring=CARD, ring_width=S(2)))
        after = px < (left + right) / 2  # eğri sağa doğru yükselir: etiket solda üstte, sağda altta boş alana düşer
        c.create_text(px + (S(10) if after else -S(10)), py + S(13) if after and py < bottom - S(22) else py - S(13),
                      text=f"{temp:.0f}°C → %{value:.0f}", fill=TEXT, font=(BOLD, 9), anchor="w" if after else "e")

    # ---- oyun modu, ekran görüntüleri, olaylar
    def _refresh_game(self):
        app = self.app
        if not app._game_cfg("enabled", True):
            text = "Ayarlar'dan kapatılmış"
        elif app.game_mode:
            text = "Açık: ışıklar ve fanlar oyun düzeninde"
        else:
            text = "Bekliyor · açınca pencere simge tepsisine iner"
        if self._changed("game", (text, app.game_mode)):
            self.game_sub.configure(text=text)
            self.game_toggle.set(app.game_mode)

    def _refresh_shots(self):
        app, shots = self.app, self.app.shots_page
        on = app.feature("snap")
        history = shots.history[:MAX_SHOTS] if on else []
        if not self._changed("shots", (on, tuple((stamp, id(shot)) for stamp, shot in history))):
            return
        for w in self.shot_box.winfo_children():
            w.destroy()
        if not on:
            tk.Label(self.shot_box, text="LightSnap kapalı.", bg=CARD, fg=MUTED, font=(FONT, 9), anchor="w").pack(fill="x")
            return
        if not history:
            tk.Label(self.shot_box, text="Henüz görüntü yok ya da geçmiş kapalı.", bg=CARD, fg=MUTED, font=(FONT, 9),
                     anchor="w", justify="left", wraplength=SIDE_W - 60).pack(fill="x")
        for stamp, shot in history:
            row = tk.Label(self.shot_box, text=f"{stamp[:5]} · {shot.w} × {shot.h}", bg=CARD, fg=TEXT, font=(FONT, 9),
                           anchor="w", cursor="hand2")
            row.pack(fill="x", pady=1)
            row.bind("<Button-1>", lambda _e, s=shot: shots._copy_old(s))
        if history:
            tk.Label(self.shot_box, text="Panoya almak için tıkla", bg=CARD, fg=MUTED, font=(FONT, 8), anchor="w").pack(fill="x")
        Pill(self.shot_box, "Bölge al", lambda: shots.take_from_button("region"), CARD, height=28, padx=10,
             font=(FONT, 9)).pack(anchor="w", pady=(10, 0))

    def _refresh_events(self):
        events = tail_events(self.app.log_path)
        if not self._changed("events", tuple(events)):
            return
        for w in self.event_box.winfo_children():
            w.destroy()
        if not events:
            tk.Label(self.event_box, text="Henüz kayıt yok.", bg=CARD, fg=MUTED, font=(FONT, 9), anchor="w").pack(fill="x")
        for stamp, text in events:
            row = RoundBox(self.event_box, outer=CARD, fill=CONTROL, radius=12, padx=7, pady=3, margin=4)
            row.pack(fill="x", pady=2)
            tk.Label(row.inner, text=stamp, bg=CONTROL, fg=MUTED, font=(FONT, 8)).pack(side="right", anchor="n", padx=(6, 0), pady=(1, 0))
            tk.Label(row.inner, text=text, bg=CONTROL, fg=TEXT, font=(FONT, 9), anchor="w", justify="left",
                     wraplength=SIDE_W - 110).pack(side="left", fill="x", expand=True)
