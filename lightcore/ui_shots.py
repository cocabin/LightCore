"""Ekran Görüntüsü sayfası, seçim/yazı ekranı ve bildirim kutusu.

Kısayol basılınca görüntü kısayol iş parçacığında hemen alınır, sonra arayüze bildirilir.
Yükleme yalnızca kullanıcı isteyince (ya da otomatik yüklemeyi kendisi açtıysa) yapılır.
"""
import math
import os
import subprocess
import threading
import time
import tkinter as tk
from tkinter import filedialog

from . import dpi
from . import screenshot as ss
from .dpi import S
from .ui import (ACCENT, AMBER, BG, BOLD, CARD, CONTROL, CONTROL_EDGE, FONT, GREEN, MUTED, RED, TEXT, ColorPicker, Pill,
                 Toggle, _circle, _image, _round_rect)
from .ui_fans import Dropdown

KINDS = (("region", "Bölge", "Sürükleyerek bir alan seç"), ("screen", "Tam ekran", "İmlecin olduğu ekran"),
         ("all", "Bütün ekranlar", "Bütün monitörler tek görüntüde"))
DIM = bytes(round(i * 0.45) for i in range(256))
TOAST_SECONDS = 8
HISTORY = 5
DEFAULT_FOLDER = os.path.join(os.path.expanduser("~"), "Pictures", "LightCore")
KEYS_NOTE = "Düzenle'ye bas, sonra istediğin tuşa ya da tuş birleşimine bas (PrtScn dahil). Esc: vazgeç."


def _font(points, family=FONT):
    """Seçim ekranının yazıları imlecin olduğu ekranın ölçeğine göre: 1080p ekranda büyük görünmesin."""
    return (family, -max(8, round(points * 4 / 3 * dpi.SCALE)))


def geometry(x, y, w, h):
    return f"{w}x{h}{x:+d}{y:+d}"


def _selection_corner(widget, r, right, bottom):
    """Seçimin yumuşak köşesi: dışı karartır (zemindeki karartmanın aynısı), üstünde yuvarlak çizgi."""
    edge, line = S(2), [int(ACCENT[i:i + 2], 16) for i in (1, 3, 5)]

    def pixel(x, y):
        px = r - 1 - x if right else x
        py = r - 1 - y if bottom else y
        d = math.hypot(r - px - 0.5, r - py - 0.5)
        outside = min(1.0, max(0.0, d - r + 0.5))
        ring = min(1.0, max(0.0, 0.5 - abs(d - r + edge / 2) + edge / 2))
        alpha = ring + (1 - ring) * outside * 0.55
        return (*[round(c * ring / alpha) if alpha else 0 for c in line], round(255 * alpha))

    return _image(widget, ("selection", r, right, bottom), r, r, pixel)


class Editor(tk.Toplevel):
    """Dondurulmuş ekran: alan seç, yazı/ok/kalem ekle, kopyala/kaydet/yükle. Esc ya da sağ tık vazgeçer."""

    def __init__(self, page, shot, whole=False):
        super().__init__(page.app.root, bg="#000000", cursor="arrow")
        self.page, self.shot = page, shot
        self._system_scale = dpi.SCALE
        dpi.SCALE = ss.monitor_scale()  # ekran kapanana kadar bu ekranın ölçeği geçerli
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.geometry(geometry(shot.x, shot.y, shot.w, shot.h))
        self.bright = tk.PhotoImage(master=self, data=shot.ppm(), format="ppm")
        dim = tk.PhotoImage(master=self, data=shot.ppm(DIM), format="ppm")
        self.canvas = tk.Canvas(self, width=shot.w, height=shot.h, highlightthickness=0, bg="#000000", cursor="arrow")
        self.canvas.pack()
        self.canvas.create_image(0, 0, anchor="nw", image=dim)
        self._dim = dim
        self.crop_image = tk.PhotoImage(master=self)
        self.crop_item = self.canvas.create_image(0, 0, anchor="nw", image=self.crop_image)
        self.edges = [self.canvas.create_rectangle(0, 0, 0, 0, fill=ACCENT, outline="", state="hidden") for _ in range(4)]
        self.corners = [self.canvas.create_image(0, 0, anchor="nw", state="hidden") for _ in range(4)]
        self.size = self.canvas.create_text(0, 0, fill="#FFFFFF", anchor="sw", font=_font(9), state="hidden")
        self.hint = self.canvas.create_text(*self._hint_position(), fill="#FFFFFF", font=_font(12, BOLD), justify="center",
                                            text="Alan seçmek için sürükle · Esc: vazgeç")
        self.selection = None
        self.anchor_xy = None
        self.tool = None  # None: seçim, "text", "arrow", "pen", "blur"
        self.color = page.cfg().get("draw_color", "#FF3B30")
        self.point_size = page.cfg().get("draw_size", 12)
        self.annotations = []  # draw_annotations biçimi
        self.patches = []  # her çizimin yumuşatılmış resmi ve canvas öğesi
        self.entry = None
        self.stroke = None
        self.toolbar = self.picker = None
        self.canvas.bind("<Button-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._drag)
        self.canvas.bind("<ButtonRelease-1>", self._release)
        self.canvas.bind("<Double-Button-1>", self._double)
        self.canvas.bind("<Button-3>", lambda _e: self.cancel())
        self.bind("<Escape>", self._escape)
        self.bind("<Return>", lambda _e: self._finish("copy") if self.selection and not self.entry else None)
        self.bind("<Control-z>", lambda _e: self._undo())
        self.focus_force()
        if whole:
            self.selection = (0, 0, shot.w, shot.h)
            self._show_selection()
            self._show_toolbar()

    def _hint_position(self):
        x, y, w, h = ss.monitor_at_cursor()
        return x - self.shot.x + w // 2, y - self.shot.y + S(60)

    def _px(self):
        """Punto -> ekran pikseli: 12, ekranda normal yazı boyutudur."""
        return max(4, round(self.point_size * 4 / 3 * dpi.SCALE))

    def _line_width(self):
        return max(2, round(self._px() / 6))

    def _inside(self, e):
        x1, y1, x2, y2 = self.selection
        return max(x1, min(x2, e.x)), max(y1, min(y2, e.y))

    # ---- fare
    def _press(self, e):
        self.focus_force()
        if self.picker:
            self._close_picker()
            return
        if self.entry:
            self._commit_text()
            return
        if self.tool and self.selection:
            x, y = self._inside(e)
            if self.tool == "text":
                self._begin_text(x, y)
            else:
                self.stroke = [(x, y)]
                options = dict(fill=self.color, width=self._line_width(), capstyle="round", joinstyle="round")
                if self.tool == "arrow":
                    w = self._line_width()
                    options.update(arrow="last", arrowshape=(w * 4.5, w * 4.5, w * 2.2))
                if self.tool == "blur":
                    self.stroke_item = self.canvas.create_rectangle(x, y, x, y, outline="#FFFFFF", dash=(4, 3))
                else:
                    self.stroke_item = self.canvas.create_line(x, y, x, y, **options)
            return
        self.anchor_xy = (e.x, e.y)
        self._remove_toolbar()
        self.canvas.itemconfigure(self.hint, state="hidden")

    def _drag(self, e):
        if self.stroke:
            x, y = self._inside(e)
            if self.tool in ("arrow", "blur"):
                self.stroke = [self.stroke[0], (x, y)]
                self.canvas.coords(self.stroke_item, *self.stroke[0], x, y)
            elif abs(x - self.stroke[-1][0]) + abs(y - self.stroke[-1][1]) >= 2:
                self.stroke.append((x, y))
                self.canvas.coords(self.stroke_item, *[c for q in self.stroke for c in q])
            return
        if not self.anchor_xy:
            return
        x0, y0 = self.anchor_xy
        x1, y1 = max(0, min(self.shot.w, e.x)), max(0, min(self.shot.h, e.y))
        self.selection = (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
        self._show_selection()

    def _release(self, _e):
        if self.stroke:
            stroke, self.stroke = self.stroke, None
            self.canvas.delete(self.stroke_item)
            if self.tool == "blur":
                if len(stroke) == 2:
                    (x1, y1), (x2, y2) = stroke
                    if abs(x2 - x1) > 3 and abs(y2 - y1) > 3:
                        self._add(("blur", min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)))
            elif self.tool == "arrow":
                if len(stroke) == 2 and stroke[0] != stroke[1]:
                    self._add(("arrow", *stroke[0], *stroke[1], self.color, self._line_width()))
            else:
                self._add(("pen", stroke, self.color, self._line_width()))
            return
        if not self.anchor_xy:
            return
        self.anchor_xy = None
        if self.selection and self.selection[2] - self.selection[0] > 2 and self.selection[3] - self.selection[1] > 2:
            self._show_toolbar()
        else:
            self.selection = None
            for item in (*self.edges, *self.corners, self.size):
                self.canvas.itemconfigure(item, state="hidden")
            self.crop_image.blank()

    def _double(self, _e):
        if self.selection and not self.tool:
            self._finish("copy")

    def _show_selection(self):
        x1, y1, x2, y2 = self.selection
        w, h = x2 - x1, y2 - y1
        if w > 0 and h > 0:  # seçili alan aydınlık: yalnızca bu parça kopyalanır, tam ekran yeniden çizilmez
            self.crop_image.configure(width=w, height=h)
            self.tk.call(str(self.crop_image), "copy", str(self.bright), "-from", x1, y1, x2, y2, "-to", 0, 0)
        self.canvas.coords(self.crop_item, x1, y1)
        r = min(S(12), w // 2, h // 2)
        e = S(2)
        for item, box in zip(self.edges, ((x1 + r, y1, x2 - r, y1 + e), (x1 + r, y2 - e, x2 - r, y2),
                                          (x1, y1 + r, x1 + e, y2 - r), (x2 - e, y1 + r, x2, y2 - r))):
            self.canvas.coords(item, *box)
            self.canvas.itemconfigure(item, state="normal")
        for item, right, bottom in zip(self.corners, (False, True, False, True), (False, False, True, True)):
            if r >= 2:
                self.canvas.coords(item, x2 - r if right else x1, y2 - r if bottom else y1)
                self.canvas.itemconfigure(item, state="normal", image=_selection_corner(self, r, right, bottom))
            else:
                self.canvas.itemconfigure(item, state="hidden")
        self.canvas.coords(self.size, x1, max(S(18), y1 - S(4)))
        self.canvas.itemconfigure(self.size, state="normal", text=f"{w} × {h}")

    # ---- araç çubuğu
    def _remove_toolbar(self):
        self._close_picker()
        if self.toolbar:
            self.canvas.delete("toolbar")
            self.toolbar.destroy()
            self.toolbar = None

    def _show_toolbar(self):
        self._remove_toolbar()
        bar = tk.Frame(self.canvas, bg=CARD, cursor="arrow")
        self.toolbar = bar
        small = dict(height=30, padx=10, font=_font(9))
        self.tool_buttons = {}

        def tool(key, label):
            b = Pill(bar, label, lambda: self._set_tool(key), CARD, style="chip", **small)
            b.pack(side="left", padx=(0, 4))
            self.tool_buttons[key] = b

        tool("text", "Yazı")
        self.size_var = tk.StringVar(value=str(self.point_size))
        size = tk.Entry(bar, textvariable=self.size_var, width=3, justify="center", bg=CONTROL, fg=TEXT,
                        insertbackground=TEXT, relief="flat", font=_font(9), highlightthickness=1,
                        highlightbackground=CONTROL_EDGE, highlightcolor=ACCENT)
        size.pack(side="left", padx=(0, 8), ipady=4)
        size.bind("<KeyRelease>", lambda _e: self._size_typed())
        size.bind("<Return>", lambda _e: (self.focus_force(), "break")[1])
        size.bind("<Button-1>", lambda _e: size.focus_force())
        tool("arrow", "Ok")
        tool("pen", "Kalem")
        tool("blur", "Kareleme")
        self.color_button = tk.Canvas(bar, width=S(28), height=S(28), bg=CARD, highlightthickness=0, cursor="hand2")
        self.color_button.pack(side="left", padx=(4, 12))
        self.color_button.bind("<Button-1>", lambda _e: self._toggle_picker())
        self._draw_color()
        Pill(bar, "Kopyala", lambda: self._finish("copy"), CARD, style="accent", **small).pack(side="left", padx=(0, 4))
        Pill(bar, "Kaydet", lambda: self._finish("save"), CARD, **small).pack(side="left", padx=(0, 4))
        Pill(bar, "Yükle", lambda: self._finish("upload"), CARD, **small).pack(side="left", padx=(0, 4))
        Pill(bar, "Vazgeç", self.cancel, CARD, **small).pack(side="left")
        bar.update_idletasks()
        w, h = bar.winfo_reqwidth(), bar.winfo_reqheight()
        x1, y1, x2, y2 = self.selection
        mx, my, mw, mh = ss.monitor_at_cursor()
        mx, my = mx - self.shot.x, my - self.shot.y
        pad = S(10)
        x = max(mx + 2 * pad, min(x2 - w, mx + mw - w - 2 * pad))
        y = y2 + 2 * pad if y2 + 3 * pad + h < my + mh else max(my + 2 * pad, min(y2, my + mh) - h - 2 * pad)
        _round_rect(self.canvas, x - pad, y - pad, x + w + pad, y + h + pad, S(10), fill=CARD, tags="toolbar")
        self.canvas.create_window(x, y, anchor="nw", window=bar, tags="toolbar")
        self.bar_box = (x - pad, y - pad, x + w + pad, y + h + pad)
        self._set_tool(self.tool, keep=True)

    def _draw_color(self):
        c = S(14)
        self.color_button.delete("all")
        self.color_button.create_image(c, c, image=_circle(self, S(26), fill=self.color, ring="#FFFFFF",
                                                           ring_width=2 * dpi.SCALE))

    def _set_tool(self, key, keep=False):
        if self.entry:
            self._commit_text()
        self.tool = key if keep or key != self.tool else None
        for name, button in self.tool_buttons.items():
            button.set_active(name == self.tool)
        self.canvas.configure(cursor="xterm" if self.tool == "text" else "arrow")

    def _size_typed(self):
        try:
            value = int(self.size_var.get())
        except ValueError:
            return
        if 6 <= value <= 200:
            self.point_size = value
            self.page.cfg()["draw_size"] = value
            if self.entry:
                self.entry.configure(font=(FONT, -self._px(), "bold"))

    # ---- renk
    def _toggle_picker(self):
        if self.picker:
            self._close_picker()
            return
        self.picker = ColorPicker(self.canvas, self._color_picked, self._close_picker, ok_text="Seç", color=self.color,
                                  font=_font(10))
        self.picker.update_idletasks()
        w, h = self.picker.winfo_reqwidth(), self.picker.winfo_reqheight()
        x1, y1, x2, y2 = self.bar_box
        pad = S(14)
        y = y1 - h - 3 * pad if y1 - h - 3 * pad > 0 else y2 + 2 * pad
        x = max(pad, min(x1 + pad, self.shot.w - w - pad))
        _round_rect(self.canvas, x - pad, y - pad, x + w + pad, y + h + pad, S(10), fill=CARD, tags="picker")
        self.canvas.create_window(x, y, anchor="nw", window=self.picker, tags="picker")

    def _close_picker(self):
        if self.picker:
            self.canvas.delete("picker")
            self.picker.destroy()
            self.picker = None

    def _color_picked(self, color):
        self.color = "#" + color
        self.page.cfg()["draw_color"] = self.color
        self._close_picker()
        self._draw_color()
        if self.entry:
            self.entry.configure(fg=self.color, insertbackground=self.color)
            self.entry.focus_force()

    # ---- yazı
    def _begin_text(self, x, y):
        self.entry = tk.Entry(self.canvas, bg="#202020", fg=self.color, insertbackground=self.color, relief="flat",
                              highlightthickness=1, highlightbackground=ACCENT, highlightcolor=ACCENT,
                              font=(FONT, -self._px(), "bold"), width=24)
        self.entry_xy = (x, y)
        self.entry_item = self.canvas.create_window(x, y, anchor="nw", window=self.entry)
        self.entry.bind("<Return>", lambda _e: (self._commit_text(), "break")[1])
        self.entry.bind("<Escape>", self._drop_text)
        self.entry.focus_force()

    def _drop_text(self, _e=None):
        self.canvas.delete(self.entry_item)
        self.entry.destroy()
        self.entry = None
        self.focus_force()
        return "break"

    def _commit_text(self):
        text = self.entry.get().strip()
        x, y = self.entry_xy
        self._drop_text()
        if text:
            self._add(("text", x, y, text, self.color, self._px()))

    # ---- çizimler: ekranda da son görüntüdeki kadar yumuşak görünsün diye resim olarak konur
    def _bounds(self, a):
        if a[0] == "text":
            item = self.canvas.create_text(a[1], a[2], text=a[3], anchor="nw", font=(FONT, -a[5], "bold"))
            x1, y1, x2, y2 = self.canvas.bbox(item)
            self.canvas.delete(item)
            pad = S(4)
        elif a[0] == "arrow":
            x1, x2 = sorted((a[1], a[3]))
            y1, y2 = sorted((a[2], a[4]))
            pad = a[6] * 6
        elif a[0] == "blur":
            x1, y1, x2, y2 = a[1:]
            pad = 0
        else:
            xs, ys = [q[0] for q in a[1]], [q[1] for q in a[1]]
            x1, y1, x2, y2 = min(xs), min(ys), max(xs), max(ys)
            pad = a[3] * 2
        sx1, sy1, sx2, sy2 = self.selection
        return max(sx1, x1 - pad), max(sy1, y1 - pad), min(sx2, x2 + pad), min(sy2, y2 + pad)

    def _add(self, annotation):
        self.annotations.append(annotation)
        x1, y1, x2, y2 = self._bounds(annotation)
        if x2 <= x1 or y2 <= y1:
            self.annotations.pop()
            return
        part = self.shot.crop(x1, y1, x2, y2)
        drawn = ss.draw_annotations(part, self.annotations, -x1, -y1)
        image = tk.PhotoImage(master=self, data=drawn.ppm(), format="ppm")
        self.patches.append((image, self.canvas.create_image(x1, y1, anchor="nw", image=image)))
        self.canvas.tag_raise("toolbar")
        self.canvas.tag_raise("picker")

    def _undo(self):
        if self.annotations and not self.entry:
            self.annotations.pop()
            self.canvas.delete(self.patches.pop()[1])

    def _escape(self, _e):
        if self.picker:
            self._close_picker()
        elif not self.entry:
            self.cancel()

    def cancel(self):
        self.page.editor = None
        self.page.app._save_config()
        self._close()

    def _close(self):
        self.destroy()
        dpi.SCALE = self._system_scale
        self.bright = self._dim = self.crop_image = self.shot = self.patches = None  # büyük resimler bellekte kalmasın
        self.page.back_to_window()

    def _finish(self, action):
        if self.entry:
            self._commit_text()
        if not self.selection:
            return
        x1, y1, x2, y2 = self.selection
        final = ss.draw_annotations(self.shot, self.annotations).crop(x1, y1, x2, y2)
        self.page.editor = None
        self.page.app._save_config()
        self._close()
        self.page.finished(final, action)


class Toast(tk.Toplevel):
    """Sağ altta kısa bildirim: panoya alındı, Yazı ekle / Kaydet / Yükle. Ekran görüntüsüne girmez."""

    def __init__(self, page):
        super().__init__(page.app.root, bg=CONTROL_EDGE)
        self.page = page
        system_scale, dpi.SCALE = dpi.SCALE, ss.monitor_scale()  # bildirim imlecin olduğu ekranda açılır
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        body = tk.Frame(self, bg=CARD, padx=14, pady=12)
        body.pack(padx=1, pady=1)
        self.title_label = tk.Label(body, bg=CARD, fg=TEXT, font=_font(10, BOLD), anchor="w", justify="left", wraplength=420)
        self.title_label.pack(fill="x")
        self.detail = tk.Label(body, bg=CARD, fg=MUTED, font=_font(9), anchor="w", justify="left", wraplength=420)
        self.detail.pack(fill="x", pady=(2, 8))
        self.buttons = tk.Frame(body, bg=CARD)
        self.buttons.pack(anchor="w")
        Pill(self.buttons, "Yazı ekle", page.annotate_last, CARD, height=28, padx=9, font=_font(9)).pack(
            side="left", padx=(0, 4))
        Pill(self.buttons, "Kaydet", page.save_last, CARD, height=28, padx=9, font=_font(9)).pack(side="left", padx=(0, 4))
        Pill(self.buttons, "Yükle", page.upload_last, CARD, height=28, padx=9, font=_font(9)).pack(side="left", padx=(0, 4))
        self.reveal = Pill(self.buttons, "Klasörde göster", lambda: page.open_folder(self.saved), CARD, height=28,
                           padx=9, font=_font(9))
        self.close_button = Pill(self.buttons, "Kapat", self.close, CARD, height=28, padx=9, font=_font(9))
        self.close_button.pack(side="left")
        self.saved = None
        self._timer = None
        self.update_idletasks()
        dpi.SCALE = system_scale
        ss.hide_from_capture(int(self.wm_frame(), 16))

    def show(self, title, detail="", color=TEXT, keep=False, saved=None):
        self.title_label.configure(text=title, fg=color)
        self.detail.configure(text=detail)
        self.saved = saved
        if saved:  # kaydedilen dosyaya giden düğme
            self.reveal.pack(side="left", padx=(0, 4), before=self.close_button)
        else:
            self.reveal.pack_forget()
        self.update_idletasks()
        x, y, w, h = ss.monitor_at_cursor(work=True)
        self.geometry(f"{self.winfo_reqwidth()}x{self.winfo_reqheight()}"
                      f"{x + w - self.winfo_reqwidth() - S(16):+d}{y + h - self.winfo_reqheight() - S(16):+d}")
        if self._timer:
            self.after_cancel(self._timer)
        self._timer = None if keep else self.after(TOAST_SECONDS * 1000, self.close)

    def close(self):
        self.page.toast = None
        self.page.last = None  # görüntü bellekte boşuna kalmasın
        self.destroy()


class ShotsPage:
    def __init__(self, app):
        self.app = app
        self.editor = self.toast = self.last = None
        self.history = []  # (saat, görüntü)
        self.reopen = False
        self.page = app._page("Ekran Görüntüsü")
        self.hotkey_labels = {}
        self.edit_buttons = {}
        self.listening = None
        self._build()
        self.hotkeys = ss.Hotkeys(self._pressed, lambda failed: app.events.put(("shot_keys", failed)))
        self.apply_hotkeys()

    def cfg(self):
        return self.app.config.setdefault("shots", {})

    def bindings(self):
        keys = {k: list(v) for k, v in ss.DEFAULT_HOTKEYS.items()}
        keys.update(self.cfg().get("hotkeys", {}))
        return keys

    def apply_hotkeys(self):
        self.hotkeys.set(self.bindings() if self.app.feature("snap") and not getattr(self.app, "game_mode", False) else {})
        for name, label in self.hotkey_labels.items():
            label.configure(text=ss.key_name(*self.bindings()[name]))

    def refresh(self):
        """Yedekten yüklenince."""
        self.apply_hotkeys()
        self.provider.current = ss.provider_key(self.cfg().get("provider"))
        self.provider._draw()
        self.custom_url.set(self.cfg().get("custom", {}).get("url", ""))
        self.custom_field.set(self.cfg().get("custom", {}).get("field", "file"))
        self.auto_upload.set(self.cfg().get("auto_upload", False))
        self.auto_save.set(self.cfg().get("auto_save", False))
        self.folder_label.configure(text=self.folder())
        self._show_reset()
        self._show_custom()

    # ---- sayfa
    def _build(self):
        app = self.app
        box = app._section(self.page, "Görüntü al")
        _, right, _ = app._row(box, "Şimdi al", "Görüntü panoya alınır; istediğin yere Ctrl+V ile yapıştırırsın.")
        for kind, label, _desc in KINDS:
            Pill(right, label, lambda k=kind: self.take_from_button(k), CARD).pack(side="left", padx=(0, 6))
        box = app._section(self.page, "Kısayollar")
        for kind, label, desc in KINDS:
            _, right, _ = app._row(box, label, desc)
            key = tk.Label(right, bg=CONTROL, fg=TEXT, font=(FONT, 10), padx=12, pady=5, width=16)
            key.pack(side="left", padx=(0, 6))
            self.hotkey_labels[kind] = key
            b = Pill(right, "Düzenle", lambda k=kind: self.listen(k), CARD, width=100)
            b.pack(side="left")
            self.edit_buttons[kind] = b
        self.keys_note = tk.Label(box, bg=BG, fg=MUTED, font=(FONT, 9), anchor="w", justify="left", wraplength=700,
                                  text=KEYS_NOTE)
        self.keys_note.pack(fill="x")
        box = app._section(self.page, "Link ile paylaşım")
        options = [(k, p["label"]) for k, p in ss.PROVIDERS.items()] + [("custom", "Özel sunucu")]
        _, right, _ = app._row(box, "Sağlayıcı", "Yükleme yalnızca Yükle'ye basınca yapılır. Linki alan herkes görüntüyü "
                                                 "görebilir; şifre ya da özel mesaj içeren görüntüleri yükleme.", below=True)
        self.provider = Dropdown(right, options, ss.provider_key(self.cfg().get("provider")), self._set_provider, width=360)
        self.provider.pack()
        self.custom_row, right, _ = app._row(box, "Özel sunucu", "Dosyayı form ile alıp yanıtta bağlantı döndüren adres "
                                                                 "ve dosya alanının adı.")
        self.custom_url = tk.StringVar(value=self.cfg().get("custom", {}).get("url", ""))
        self.custom_field = tk.StringVar(value=self.cfg().get("custom", {}).get("field", "file"))
        for var, width in ((self.custom_url, 34), (self.custom_field, 12)):
            entry = tk.Entry(right, textvariable=var, width=width, bg=CONTROL, fg=TEXT, insertbackground=TEXT,
                             relief="flat", font=(FONT, 10), highlightthickness=1, highlightbackground=CONTROL_EDGE,
                             highlightcolor=ACCENT)
            entry.pack(side="left", padx=(0, 6), ipady=5)
            entry.bind("<FocusOut>", lambda _e: self._save_custom())
        self._show_custom()
        _, right, _ = app._row(box, "Otomatik yükle", "Her görüntüyü sormadan yükler ve linki panoya koyar. "
                                                      "Görüntü panoda değil, internette olur; dikkatli aç.")
        self.auto_upload = self._toggle(right, "auto_upload")
        box = app._section(self.page, "Geçmiş")
        _, right, _ = app._row(box, "Son görüntüleri tut", f"Son {HISTORY} görüntü bellekte durur, yanlışlıkla panoyu "
                                                           "değiştirince geri alırsın. Program kapanınca silinir, açıkken "
                                                           "bellek kullanır.")
        self.keep_history = self._toggle(right, "history")
        self.history_box = tk.Frame(box, bg=BG)
        self.history_box.pack(fill="x")
        box = app._section(self.page, "Kayıt")
        _, right, self.folder_label = app._row(box, "Kayıt klasörü", self.folder())
        Pill(right, "Klasörü aç", self.open_folder, CARD).pack(side="left", padx=(0, 6))
        Pill(right, "Değiştir", self.choose_folder, CARD).pack(side="left", padx=(0, 6))
        self.reset_button = Pill(right, "Varsayılan", self.reset_folder, CARD)
        self._show_reset()
        _, right, _ = app._row(box, "Otomatik kaydet", "Her görüntü panoya alınırken bu klasöre de kaydedilir.")
        self.auto_save = self._toggle(right, "auto_save")

    def _toggle(self, parent, key):
        toggle = Toggle(parent, lambda: self._flip(toggle, key), CARD)
        toggle.set(self.cfg().get(key, False))
        toggle.pack(side="left")
        return toggle

    def _flip(self, toggle, key):
        toggle.set(not toggle.value)
        self.cfg()[key] = toggle.value
        self.app._save_config()

    def _set_provider(self, key):
        self.cfg()["provider"] = key
        self.app._save_config()
        self._show_custom()

    def _show_custom(self):
        if self.cfg().get("provider") == "custom":
            self.custom_row.pack(fill="x", pady=(0, 6), after=self.provider.master.master.master)
        else:
            self.custom_row.pack_forget()

    def _save_custom(self):
        self.cfg()["custom"] = {"url": self.custom_url.get().strip(), "field": self.custom_field.get().strip() or "file"}
        self.app._save_config()

    def folder(self):
        return self.cfg().get("folder") or DEFAULT_FOLDER

    def open_folder(self, path=None):
        """Klasörü Gezgin'de açar; dosya verilirse onu seçili gösterir."""
        try:
            if path and os.path.exists(path):
                subprocess.Popen(f'explorer /select,"{os.path.normpath(path)}"')
            else:
                os.makedirs(self.folder(), exist_ok=True)
                os.startfile(self.folder())
        except OSError as e:
            self.app._status(f"Klasör açılamadı: {e}", RED)

    def choose_folder(self):
        chosen = filedialog.askdirectory(parent=self.app.root, title="Ekran görüntülerinin kaydedileceği klasör",
                                         initialdir=self.folder() if os.path.isdir(self.folder()) else None)
        if chosen:
            self._set_folder(os.path.normpath(chosen))

    def reset_folder(self):
        self._set_folder(None)

    def _set_folder(self, path):
        if path and os.path.normcase(path) != os.path.normcase(DEFAULT_FOLDER):
            self.cfg()["folder"] = path
        else:
            self.cfg().pop("folder", None)
        self.app._save_config()
        self.folder_label.configure(text=self.folder())
        self._show_reset()

    def _show_reset(self):
        if self.cfg().get("folder"):
            self.reset_button.pack(side="left", padx=(0, 6))
        else:
            self.reset_button.pack_forget()

    # ---- kısayol atama
    def listen(self, kind):
        was = self.listening
        self._end_listen()
        if was == kind:
            return
        self.listening = kind
        self.hotkeys.set({})  # atanacak tuşu kısayollar yutmasın
        self.hotkey_labels[kind].configure(text="Tuşlara bas…", fg=ACCENT)
        self.edit_buttons[kind].set_text("Vazgeç")
        root = self.app.root
        self._listen_ids = (root.bind("<KeyPress>", self._key, add="+"), root.bind("<KeyRelease>", self._key_up, add="+"))
        root.focus_force()

    def _end_listen(self):
        if self.listening:
            root = self.app.root
            root.unbind("<KeyPress>", self._listen_ids[0])
            root.unbind("<KeyRelease>", self._listen_ids[1])
            self.hotkey_labels[self.listening].configure(fg=TEXT)
            self.edit_buttons[self.listening].set_text("Düzenle")
            self.listening = None
            self.apply_hotkeys()

    @staticmethod
    def _mods(state):
        return (ss.MOD_CONTROL if state & 0x4 else 0) | (ss.MOD_SHIFT if state & 0x1 else 0) \
            | (ss.MOD_ALT if state & 0x20000 else 0)

    def _key_up(self, e):
        """Windows PrtScn'nin yalnızca bırakılışını programlara bildirir."""
        if self.listening and (e.keysym == "Print" or e.keycode == ss.VK_SNAPSHOT):
            self._assign(self.listening, self._mods(e.state), ss.VK_SNAPSHOT)

    def _key(self, e):
        kind = self.listening
        if e.keysym == "Escape":
            self._end_listen()
            return
        if e.keysym.split("_")[0] in ("Control", "Shift", "Alt", "Win", "Meta", "Caps"):
            return  # asıl tuşu bekle
        mods = self._mods(e.state)
        is_function = 0x70 <= e.keycode <= 0x87 or e.keycode == ss.VK_SNAPSHOT
        if not mods and not is_function:
            self.keys_note.configure(text="En az bir Ctrl, Alt ya da Shift ile birlikte bas (F tuşları ve PrtScn tek "
                                          "başına olur).", fg=AMBER)
            return
        self._assign(kind, mods, e.keycode)

    def _assign(self, kind, mods, vk):
        self._end_listen()
        taken = [k for k, v in self.bindings().items() if k != kind and v == [mods, vk]]
        if taken:
            self.keys_note.configure(text=f"{ss.key_name(mods, vk)} başka bir görüntü türünde kullanılıyor.", fg=AMBER)
            return
        self.cfg().setdefault("hotkeys", {})[kind] = [mods, vk]
        self.app._save_config()
        self.keys_note.configure(text=KEYS_NOTE, fg=MUTED)
        self.apply_hotkeys()

    def keys_result(self, failed):
        if failed:
            names = ", ".join(ss.key_name(*self.bindings()[k]) for k in failed)
            self.keys_note.configure(text=f"{names} başka bir program tarafından kullanılıyor, başka kısayol seç. PrtScn "
                                          "ise Windows'ta Ayarlar > Erişilebilirlik > Klavye > \"Ekran Alıntısı Aracı\" "
                                          "seçeneğini kapat.", fg=AMBER)

    # ---- alma
    def _pressed(self, kind):
        """Kısayol iş parçacığında: görüntü hemen alınır, arayüzün uyanmasını beklemez."""
        if self.editor is not None:
            return
        try:
            self.app.events.put(("shot", kind, ss.capture_kind(kind)))
        except Exception as e:
            self.app.events.put(("shot_error", str(e)))

    def take_from_button(self, kind):
        """Programın kendi penceresi görüntüye girmesin: kısa süre gizlenir, sonra geri gelir."""
        self.reopen = True
        self.app.window.hide()
        self.app.root.after(150, lambda: self._pressed(kind))

    def back_to_window(self):
        if self.reopen:
            self.reopen = False
            self.app.window.show()

    def taken(self, kind, shot):
        if self.toast:
            self.toast.close()
        if kind == "region":
            self.editor = Editor(self, shot)
        else:
            self.finished(shot, "copy")
            self.back_to_window()

    def annotate_last(self):
        shot = self.last
        if shot and not self.editor:
            if self.toast:
                self.toast.close()
            self.editor = Editor(self, shot, whole=True)

    def _remember(self, shot):
        if not self.cfg().get("history"):
            self.history.clear()
        else:
            self.history = ([(time.strftime("%H:%M:%S"), shot)] + self.history)[:HISTORY]
        for w in self.history_box.winfo_children():
            w.destroy()
        for stamp, old in self.history:
            Pill(self.history_box, f"{stamp} · {old.w} × {old.h}", lambda s=old: self._copy_old(s), BG).pack(
                side="left", padx=(0, 6), pady=(0, 6))

    def _copy_old(self, shot):
        self.last = shot
        ss.copy_to_clipboard(shot)
        self._toast("Panoya alındı", f"{shot.w} × {shot.h}")

    def finished(self, shot, action):
        self.last = shot
        self._remember(shot)
        try:
            ss.copy_to_clipboard(shot)
            title = f"Panoya alındı: {shot.w} × {shot.h}"
            detail, color = "Ctrl+V ile yapıştırabilirsin.", TEXT
        except ss.ScreenshotError as e:
            title, detail, color = "Panoya alınamadı", str(e), RED
        self._toast(title, detail, color)
        self.app.log(f"ekran görüntüsü: {shot.w}x{shot.h}")
        if action == "save" or self.cfg().get("auto_save"):
            self.save_last()
        if action == "upload" or self.cfg().get("auto_upload"):
            self.upload_last()

    def _toast(self, title, detail="", color=TEXT, keep=False, saved=None):
        if not self.toast:
            self.toast = Toast(self)
        self.toast.show(title, detail, color, keep, saved)

    def save_last(self):
        if not self.last:
            return
        shot = self.last
        folder = self.folder()
        path = os.path.join(folder, time.strftime("Ekran %Y-%m-%d %H-%M-%S.png"))

        def work():
            try:
                os.makedirs(folder, exist_ok=True)
                with open(path, "wb") as f:
                    f.write(shot.png())
                self.app.events.put(("shot_note", "Kaydedildi", path, GREEN, path))
            except OSError as e:
                self.app.events.put(("shot_note", "Kaydedilemedi", str(e), RED))

        threading.Thread(target=work, daemon=True).start()

    def upload_last(self):
        if not self.last:
            return
        shot = self.last
        provider, custom = ss.provider_key(self.cfg().get("provider")), self.cfg().get("custom")
        self._toast("Yükleniyor…", "", TEXT, keep=True)

        def work():
            try:
                link = ss.upload(shot.png(), provider, custom)
                self.app.events.put(("shot_link", link))
            except ss.ScreenshotError as e:
                self.app.events.put(("shot_note", "Yüklenemedi", str(e), RED))

        threading.Thread(target=work, daemon=True).start()

    def linked(self, link):
        root = self.app.root
        root.clipboard_clear()
        root.clipboard_append(link)
        root.update()  # Tk panosu sahipliğini hemen alsın
        self.app.log("ekran görüntüsü yüklendi")
        self._toast("Link panoya alındı", link, GREEN)

    def note(self, title, detail, color, saved=None):
        self._toast(title, detail, color, saved=saved)

    def stop(self):
        self.hotkeys.stop()
