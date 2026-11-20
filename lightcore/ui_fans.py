"""Fanlar sayfası: fan kontrolü, sıcaklıklar, FanControl tarzı eğriler, FanControl'den geçiş.

Ayarlar App.config["fans"] içinde: {"enabled", "show_all", "curves": [eğri], "channels":
{kontrol_id: kanal}}. Eğri ve kanal biçimi için fans.FanPlanner.desired'a bakın.

Görünüm: fanlar, eğriler ve sıcaklıklar yumuşak köşeli kare kartlar halinde, pencere
genişliğine göre yan yana dizilir (CardGrid). Açılır liste, sayı kutusu ve onay kutusu
Windows'un eski (XP görünümlü) denetimleri yerine burada çizilir.
"""
import collections
import ctypes
import json
import os
import threading
import time
import tkinter as tk
from ctypes import wintypes
from tkinter import filedialog, messagebox

from .dpi import S
from . import fancontrol_import as fcimp
from . import fans as fanctl
from . import lang
from .ui import (ACCENT, AMBER, BG, BOLD, CARD, CONTROL, CONTROL_EDGE, CONTROL_HOVER, FONT, GREEN, MUTED,
                 RED, TEXT, Pill, RoundBox, Toggle, _round_rect, config_dir, program_dir)

FAN_MODES = (("auto", "Otomatik"), ("game", "Oyun"), ("curve", "Eğri"))
BUILTIN_SOURCES = (("cpu", "İşlemci"), ("gpu", "Ekran kartı"))
FANCONTROL_CONFIGS = os.path.join(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
                                  "FanControl", "Configurations")
CARD_W, FAN_H, CURVE_H, TEMP_W, TEMP_H = 290, 290, 290, 200, 96
FULL_SPEED_WARNING = 120  # saniye
BACKUP_SECONDS, BACKUP_COUNT = 60, 5
RESTART_LIMIT, RESTART_WINDOW = 3, 120  # FanHost bu sürede bu kadar çökerse yeniden denenmez
DBG_TERMINATE_PROCESS = 0x40010004  # Windows kapanışta süreci bu kodla sonlandırır


def find_fan_lib():
    """LibreHardwareMonitor: programın kendi 'fan' klasörü (pakette gelir). FanControl gerekmez."""
    return fanctl.find_lhm([os.path.join(program_dir(), "fan"), program_dir()])


# ---------------------------------------------------------------- denetimler
class Dropdown(tk.Canvas):
    """Koyu, yumuşak köşeli açılır liste. options: [(anahtar, etiket)]; seçilince command(anahtar)."""

    def __init__(self, parent, options, current, command, bg=CARD, width=230, height=32, placeholder=None):
        super().__init__(parent, width=S(width), height=S(height), bg=bg, highlightthickness=0, cursor="hand2")
        self.options, self.command, self.current = list(options), command, current
        self.placeholder = placeholder  # seçim yokken görünen yazı
        self.hover = False
        self.bind("<Button-1>", lambda _e: self.open())
        self.bind("<Enter>", lambda _e: self._set_hover(True))
        self.bind("<Leave>", lambda _e: self._set_hover(False))
        self._draw()

    def label(self):
        fallback = self.placeholder if self.placeholder is not None else (self.options[0][1] if self.options else "")
        return lang.T(next((l for k, l in self.options if k == self.current), fallback))

    def set_current(self, current):
        if current != self.current:
            self.current = current
            self._draw()

    def set_options(self, options):
        self.options = list(options)
        self._draw()

    def _set_hover(self, hover):
        self.hover = hover
        self._draw()

    def _draw(self):
        self.delete("all")
        w, h = int(self["width"]), int(self["height"])
        _round_rect(self, 1, 1, w - 1, h - 1, S(7), fill=CONTROL_HOVER if self.hover else CONTROL, outline=CONTROL_EDGE)
        text = self.label()
        while len(text) > 3 and self.tk.call("font", "measure", (FONT, 10), text) > w - S(40):
            text = text[:-2]
        if text != self.label():
            text = text.rstrip() + "…"
        self.create_text(S(12), h // 2, text=text, fill=TEXT, font=(FONT, 10), anchor="w")
        self.create_line(w - S(22), h // 2 - S(2), w - S(17), h // 2 + S(3), w - S(12), h // 2 - S(2), fill=MUTED,
                         width=S(2))

    def open(self):
        top = tk.Toplevel(self)
        top.overrideredirect(True)
        top.configure(bg=CONTROL_EDGE)
        # Liste bir tuvalin içinde: ekrana sığmayan uzun liste tekerlekle kendi içinde kayar.
        view = tk.Canvas(top, bg=CONTROL, highlightthickness=0)
        view.pack(padx=1, pady=1)
        frame = tk.Frame(view, bg=CONTROL, padx=4, pady=4)
        view.create_window(0, 0, window=frame, anchor="nw")
        width = int(self["width"]) / S(1)
        chosen = None
        for key, label in self.options:
            item = tk.Label(frame, text=lang.T(label), bg=CONTROL, fg=TEXT, font=(FONT, 10), anchor="w", padx=10, pady=6,
                            width=max(10, int(width // 8)), cursor="hand2")
            if key == self.current:
                item.configure(fg=ACCENT)
                chosen = item
            item.pack(fill="x")
            item.bind("<Enter>", lambda _e, w=item: w.configure(bg=CONTROL_HOVER))
            item.bind("<Leave>", lambda _e, w=item: w.configure(bg=CONTROL))
            item.bind("<ButtonRelease-1>", lambda _e, k=key: self._pick(top, k))
        top.update_idletasks()
        full_w, full_h = frame.winfo_reqwidth(), frame.winfo_reqheight()
        x, below_y = self.winfo_rootx(), self.winfo_rooty() + int(self["height"]) + 2
        below = self.winfo_screenheight() - below_y - S(56)  # görev çubuğu payı
        above = self.winfo_rooty() - 2 - S(8)
        up = full_h > below and above > below  # aşağıya sığmıyorsa ve yukarıda daha çok yer varsa yukarı açılır
        height = max(S(60), min(full_h, above if up else below))
        view.configure(width=full_w, height=height, scrollregion=(0, 0, full_w, full_h), yscrollincrement=S(32))
        if chosen is not None and full_h > height:  # seçili satır görünür olsun
            view.yview_moveto(max(0.0, (chosen.winfo_y() - height / 2) / full_h))
        top.geometry(f"+{x}+{self.winfo_rooty() - height - 4 if up else below_y}")

        def wheel(e):
            """Liste açıkken tekerlek sayfayı değil listeyi kaydırır (sayfa kayarsa liste havada kalıyordu)."""
            if full_h > height:
                view.yview_scroll(int(-e.delta / 120), "units")
            return "break"

        top.bind("<MouseWheel>", wheel)
        top.bind("<Escape>", lambda _e: top.destroy())
        top.bind("<FocusOut>", lambda _e: top.after(80, lambda: top.winfo_exists() and top.destroy()))

        def outside(e):  # pencere dışına tıklanınca kapan
            if not (0 <= e.x_root - top.winfo_rootx() <= top.winfo_width()
                    and 0 <= e.y_root - top.winfo_rooty() <= top.winfo_height()):
                top.destroy()

        top.bind("<Button-1>", outside, add="+")
        top.focus_force()
        top.grab_set()

    def _pick(self, top, key):
        top.destroy()
        self.current = key
        self._draw()
        self.command(key)


class Stepper(tk.Frame):
    """− değer + sayı denetimi (Windows'un eski ok düğmeli kutusu yerine)."""

    def __init__(self, parent, value, lo, hi, step, command, bg=CARD, width=5):
        super().__init__(parent, bg=bg)
        self.lo, self.hi, self.step, self.command = lo, hi, step, command
        self.var = tk.StringVar(value=f"{value:g}")
        Pill(self, "−", lambda: self._add(-step), bg, height=30, padx=11).pack(side="left")
        entry = tk.Entry(self, textvariable=self.var, width=width, justify="center", bg=CONTROL, fg=TEXT,
                         insertbackground=TEXT, relief="flat", font=(FONT, 10), highlightthickness=1,
                         highlightbackground=CONTROL_EDGE, highlightcolor=ACCENT)
        entry.pack(side="left", padx=4, ipady=5)
        Pill(self, "+", lambda: self._add(step), bg, height=30, padx=10).pack(side="left")
        entry.bind("<Return>", lambda _e: self._commit())
        entry.bind("<FocusOut>", lambda _e: self._commit())

    def _value(self):
        try:
            return float(self.var.get().replace(",", "."))
        except ValueError:
            return None

    def _add(self, delta):
        v = self._value()
        self._set((self.lo if v is None else v) + delta)

    def _commit(self):
        v = self._value()
        if v is not None:
            self._set(v)

    def _set(self, v):
        v = round(min(self.hi, max(self.lo, v)), 2)
        self.var.set(f"{v:g}")
        self.command(v)


def labeled(parent, text, widget_factory, bg=CARD):
    frame = tk.Frame(parent, bg=bg)
    tk.Label(frame, text=text, bg=bg, fg=MUTED, font=(FONT, 9)).pack(anchor="w")
    widget_factory(frame).pack(anchor="w", pady=(3, 0))
    return frame


def toggle_row(parent, text, value, command, bg=CARD):
    """iOS anahtarı + yazı (onay kutusu yerine)."""
    row = tk.Frame(parent, bg=bg)
    t = Toggle(row, lambda: (t.set(not t.value), command(t.value)), bg)
    t.set(value)
    t.pack(side="left")
    tk.Label(row, text=text, bg=bg, fg=TEXT, font=(FONT, 10)).pack(side="left", padx=(10, 0))
    return row


class CardGrid(tk.Frame):
    """Kartları pencere genişliğine göre yan yana dizer. Düzenleme paneli (detail), ait olduğu
    kartın satırının hemen altında bütün genişlikte açılır."""

    def __init__(self, parent, min_width=CARD_W):
        super().__init__(parent, bg=BG)
        self.min_width, self.cards, self._cols, self._set_cols = S(min_width), [], 0, 0
        self.detail = self.detail_card = None
        self.bind("<Configure>", lambda _e: self._layout())

    def add(self, card):
        self.cards.append(card)
        self._layout(force=True)
        return card

    def clear(self):
        self.close_detail()
        for c in self.cards:
            c.destroy()
        self.cards, self._cols = [], 0

    def show_detail(self, widget, card):
        self.close_detail()
        self.detail, self.detail_card = widget, card
        self._layout(force=True)

    def close_detail(self):
        if self.detail:
            self.detail.destroy()
        self.detail = self.detail_card = None

    def _layout(self, force=False):
        cols = max(1, self.winfo_width() // self.min_width) if self.winfo_width() > 1 else 3
        if cols == self._cols and not force:
            return
        self._cols = cols
        for c in range(max(cols, self._set_cols)):  # kaç sütun olursa olsun hepsi eşit genişlikte
            self.columnconfigure(c, weight=1 if c < cols else 0, uniform="card" if c < cols else "")
        self._set_cols = cols
        for i, card in enumerate(self.cards):
            card.grid(row=2 * (i // cols), column=i % cols, sticky="nsew", padx=(0, 8), pady=(0, 8))
        if self.detail:
            row = 2 * (self.cards.index(self.detail_card) // cols) + 1
            self.detail.grid(row=row, column=0, columnspan=cols, sticky="ew", padx=(0, 8), pady=(0, 8))


def curve_preview(canvas, curve, w, h):
    """Kartın içindeki küçük eğri çizimi (grafik/doğrusal/sabit/tetik)."""
    canvas.delete("all")
    kind = curve.get("type", "graph")
    t0, t1 = 20, 100
    if kind == "graph":
        pts = curve.get("points") or []
    elif kind == "linear":
        pts = [(curve.get("min_temp", 40), curve.get("min_value", 30)), (curve.get("max_temp", 80),
                                                                         curve.get("max_value", 100))]
    elif kind == "flat":
        pts = [(t0, curve.get("value", 50)), (t1, curve.get("value", 50))]
    elif kind == "trigger":
        it, lt = curve.get("idle_temp", 50), curve.get("load_temp", 70)
        iv, lv = curve.get("idle_value", 30), curve.get("load_value", 100)
        pts = [(t0, iv), (lt, iv), (lt, lv), (t1, lv)]
        _ = it
    else:
        return False
    if not pts:
        return False
    pad = S(4)
    xy = lambda t, p: (pad + (min(t1, max(t0, t)) - t0) / (t1 - t0) * (w - 2 * pad), h - pad - p / 100 * (h - 2 * pad))
    for p in (25, 50, 75):
        _, y = xy(t0, p)
        canvas.create_line(pad, y, w - pad, y, fill=CONTROL_EDGE)
    line = [xy(t0, pts[0][1])] + [xy(t, p) for t, p in pts] + [xy(t1, pts[-1][1])]
    canvas.create_line(*[c for p in line for c in p], fill=ACCENT, width=S(2))
    return True


# ---------------------------------------------------------------- dosya sürükle-bırak (Windows)
def enable_file_drop(root, on_files):
    """Pencereye bırakılan dosyaları on_files(yollar) ile verir. Program yönetici olarak
    çalışırken Explorer'dan bırakmaya Windows'un izin vermesi için mesaj süzgeci açılır.

    DİKKAT: on_files pencere yordamının içinden çağrılır; orada tkinter'a dokunmak Python'u
    çökertir (Tcl olay döngüsünün içindeyiz). on_files sadece bir kuyruğa koymalıdır."""
    try:
        user32, shell32 = ctypes.windll.user32, ctypes.windll.shell32
    except AttributeError:
        return False
    root.update_idletasks()
    hwnd = user32.GetParent(root.winfo_id()) or root.winfo_id()
    for msg in (0x0233, 0x004A, 0x0049):  # WM_DROPFILES, WM_COPYDATA, WM_COPYGLOBALDATA
        try:
            user32.ChangeWindowMessageFilterEx(wintypes.HWND(hwnd), msg, 1, None)
        except (AttributeError, OSError):
            pass
    shell32.DragAcceptFiles(wintypes.HWND(hwnd), True)
    LRESULT = ctypes.c_ssize_t
    WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
    user32.CallWindowProcW.argtypes = [ctypes.c_void_p, wintypes.HWND, wintypes.UINT, wintypes.WPARAM,
                                       wintypes.LPARAM]
    user32.CallWindowProcW.restype = LRESULT
    set_proc = user32.SetWindowLongPtrW
    set_proc.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
    set_proc.restype = ctypes.c_void_p
    shell32.DragQueryFileW.argtypes = [ctypes.c_void_p, wintypes.UINT, ctypes.c_wchar_p, wintypes.UINT]
    shell32.DragFinish.argtypes = [ctypes.c_void_p]

    def proc(h, msg, wparam, lparam):
        if msg == 0x0233:
            paths = []
            for i in range(shell32.DragQueryFileW(wparam, 0xFFFFFFFF, None, 0)):
                n = shell32.DragQueryFileW(wparam, i, None, 0) + 1
                buf = ctypes.create_unicode_buffer(n)
                shell32.DragQueryFileW(wparam, i, buf, n)
                paths.append(buf.value)
            shell32.DragFinish(wparam)
            on_files(paths)
            return 0
        return user32.CallWindowProcW(old, h, msg, wparam, lparam)

    callback = WNDPROC(proc)
    old = set_proc(wintypes.HWND(hwnd), -4, ctypes.cast(callback, ctypes.c_void_p))  # GWLP_WNDPROC
    root._file_drop_proc = callback  # çöp toplayıcı silmesin
    return True


# ---------------------------------------------------------------- eğri grafiği
class GraphEditor(tk.Canvas):
    """FanControl'deki gibi grafik eğri: noktaları sürükle; çift tıkla nokta ekle;
    sağ tıkla sil. Dikey çizgi: kaynağın o anki sıcaklığı."""
    T_MIN, T_MAX = 20, 110
    PAD_L, PAD_R, PAD_T, PAD_B = S(44), S(16), S(28), S(30)  # üstte %100 noktasının etiketine yer

    def __init__(self, parent, points, current_temp=None, width=640, height=220, on_change=lambda: None):
        super().__init__(parent, width=S(width), height=S(height), bg=CARD, highlightthickness=0, cursor="crosshair")
        self.points = [list(p) for p in sorted(points)] or [[40, 30], [80, 100]]
        self.on_change = on_change
        self.current_temp = current_temp
        self.drag = None
        self.bind("<Button-1>", self._press)
        self.bind("<B1-Motion>", self._move)
        self.bind("<ButtonRelease-1>", self._release)
        self.bind("<Double-Button-1>", self._add)
        self.bind("<Button-3>", self._remove)
        self.bind("<Configure>", lambda _e: self.draw())

    def _size(self):
        return max(100, self.winfo_width()), max(100, self.winfo_height())

    def _xy(self, t, p):
        w, h = self._size()
        x = self.PAD_L + (t - self.T_MIN) / (self.T_MAX - self.T_MIN) * (w - self.PAD_L - self.PAD_R)
        y = h - self.PAD_B - p / 100 * (h - self.PAD_T - self.PAD_B)
        return x, y

    def _tp(self, x, y):
        w, h = self._size()
        t = self.T_MIN + (x - self.PAD_L) / (w - self.PAD_L - self.PAD_R) * (self.T_MAX - self.T_MIN)
        p = (h - self.PAD_B - y) / (h - self.PAD_T - self.PAD_B) * 100
        return round(min(self.T_MAX, max(self.T_MIN, t))), round(min(100, max(0, p)))

    def draw(self):
        self.delete("all")
        w, h = self._size()
        for t in range(self.T_MIN, self.T_MAX + 1, 10):
            x, _ = self._xy(t, 0)
            self.create_line(x, self.PAD_T, x, h - self.PAD_B, fill=CONTROL_EDGE)
            self.create_text(x, h - self.PAD_B + S(14), text=f"{t}°", fill=MUTED, font=(FONT, 8))
        for p in range(0, 101, 20):
            _, y = self._xy(self.T_MIN, p)
            self.create_line(self.PAD_L, y, w - self.PAD_R, y, fill=CONTROL_EDGE)
            self.create_text(self.PAD_L - S(8), y, text=f"%{p}", fill=MUTED, font=(FONT, 8), anchor="e")
        if self.current_temp:
            x, _ = self._xy(min(self.T_MAX, max(self.T_MIN, self.current_temp)), 0)
            self.create_line(x, self.PAD_T, x, h - self.PAD_B, fill=AMBER, dash=(4, 3))
            self.create_text(x + S(4), self.PAD_T + S(6), text=f"şu an {self.current_temp:.0f}°C", fill=AMBER,
                             font=(FONT, 8), anchor="w")
        pts = [self._xy(*p) for p in self.points]
        line = [(self.PAD_L, pts[0][1])] + pts + [(w - self.PAD_R, pts[-1][1])]
        self.create_line(*[c for xy in line for c in xy], fill=ACCENT, width=S(2))
        for (x, y), (t, p) in zip(pts, self.points):
            self.create_oval(x - S(6), y - S(6), x + S(6), y + S(6), fill=ACCENT, outline=TEXT)
            self.create_text(x, y - S(14), text=f"{t:g}° %{p:g}", fill=TEXT, font=(FONT, 8))

    def _nearest(self, x, y):
        best, dist = None, S(14)
        for i, p in enumerate(self.points):
            px, py = self._xy(*p)
            d = ((px - x) ** 2 + (py - y) ** 2) ** 0.5
            if d < dist:
                best, dist = i, d
        return best

    def _press(self, e):
        self.drag = self._nearest(e.x, e.y)

    def _release(self, _e):
        if self.drag is not None:
            self.drag = None
            self.on_change()

    def _move(self, e):
        if self.drag is None:
            return
        t, p = self._tp(e.x, e.y)
        i = self.drag
        lo = self.points[i - 1][0] + 1 if i > 0 else self.T_MIN
        hi = self.points[i + 1][0] - 1 if i < len(self.points) - 1 else self.T_MAX
        self.points[i] = [min(hi, max(lo, t)), p]
        self.draw()

    def _add(self, e):
        if self._nearest(e.x, e.y) is not None:
            return
        t, p = self._tp(e.x, e.y)
        if any(q[0] == t for q in self.points):
            return
        self.points = sorted(self.points + [[t, p]])
        self.draw()
        self.on_change()

    def _remove(self, e):
        i = self._nearest(e.x, e.y)
        if i is not None and len(self.points) > 2:
            del self.points[i]
            self.draw()
            self.on_change()


# ---------------------------------------------------------------- pencereler
class Dialog(tk.Toplevel):
    """Koyu pencere: düğmeler en altta sabit, içerik .body içinde."""

    def __init__(self, page, title, size):
        super().__init__(page.app.root, bg=BG)
        self.page = page
        self.title(title)
        self.geometry("%dx%d" % S(size))
        self.minsize(*S((560, 380)))
        self.transient(page.app.root)
        self.grab_set()
        self.buttons = tk.Frame(self, bg=BG, padx=24, pady=14)
        self.buttons.pack(side="bottom", fill="x")
        self.body = tk.Frame(self, bg=BG, padx=24, pady=18)
        self.body.pack(fill="both", expand=True)


class CurvePanel(RoundBox):
    """Eğrinin kartının hemen altında açılan düzenleme bölümü. Her değişiklik anında geçerli
    olur ve kaydedilir; ayrı pencere ya da Kaydet düğmesi yoktur."""

    def __init__(self, page, grid, curve):
        super().__init__(grid, padx=18, pady=16)
        kind = curve.get("type", "graph")
        self.page, self.curve = page, curve
        inner = self.inner
        head = tk.Frame(inner, bg=CARD)
        head.pack(fill="x")
        tk.Label(head, text=f"{curve.get('name')} düzenleniyor", bg=CARD, fg=TEXT, font=(BOLD, 12)).pack(side="left")
        Pill(head, "Kapat", page.close_editor, CARD, height=28).pack(side="right")
        self.error = tk.Label(head, bg=CARD, fg=RED, font=(FONT, 9))
        self.error.pack(side="right", padx=(0, 10))
        tk.Label(inner, text={
            "graph": "Noktayı sürükle, boş yere çift tıklayıp nokta ekle, sağ tıkla sil. Değişiklik anında geçerli olur.",
            "linear": "En düşük sıcaklıkta en düşük hız, en yüksekte en yüksek hız; arası düz çizgi.",
            "flat": "Her zaman aynı hız.",
            "sync": "İzlenecek fanı ve farkı üstteki karttan değiştirebilirsin.",
            "mix": "Birden çok eğriyi birleştirir (en yüksek, en düşük, ortalama ya da toplam).",
            "trigger": "Yük sıcaklığına ulaşınca yük hızına geçer, boşta sıcaklığına inince boşta hızına döner.",
        }.get(kind, ""), bg=CARD, fg=MUTED, font=(FONT, 9), justify="left").pack(anchor="w", pady=(2, 12))
        top = tk.Frame(inner, bg=CARD)
        top.pack(fill="x")
        self.name_var = tk.StringVar(value=curve.get("name", ""))

        def name_entry(f):
            entry = tk.Entry(f, textvariable=self.name_var, width=24, bg=CONTROL, fg=TEXT, insertbackground=TEXT,
                             relief="flat", font=(FONT, 10), highlightthickness=1, highlightbackground=CONTROL_EDGE,
                             highlightcolor=ACCENT)
            entry.bind("<Return>", lambda _e: self._rename())
            entry.bind("<FocusOut>", lambda _e: self._rename())
            return entry

        labeled(top, "Ad", name_entry).pack(side="left", padx=(0, 16))
        if kind in ("graph", "linear", "trigger"):
            labeled(top, "Sıcaklık kaynağı",
                    lambda f: Dropdown(f, page.temp_options(), curve.get("source", "cpu"), self._set_source,
                                       width=300)).pack(side="left")
        self.graph = None
        fields = tk.Frame(inner, bg=CARD)
        fields.pack(fill="both", expand=True, pady=(14, 0))
        if kind != "sync":
            getattr(self, f"_build_{kind}", self._build_flat)(fields)
        if kind in ("graph", "linear", "trigger"):
            self._build_hysteresis(inner)

    def _changed(self):
        self.page.curve_changed(self.curve)

    def _rename(self):
        name = self.name_var.get().strip()
        old = self.curve.get("name")
        if name == old:
            return
        if not name:
            self.error.configure(text="Eğriye bir ad ver.")
        elif name in self.page.curve_names():
            self.error.configure(text=f"'{name}' adında bir eğri zaten var.")
        else:
            self.curve["name"] = name
            self.page.renamed(self.curve, old)

    def _set(self, key, value):
        self.curve[key] = value
        self._changed()

    def _set_source(self, key):
        self.curve["source"] = key
        if self.graph:
            self.graph.current_temp = self.page.current_temp(key)
            self.graph.draw()
        self._changed()

    def _graph_changed(self):
        self.curve["points"] = [list(p) for p in self.graph.points]
        self._changed()

    def _num(self, parent, label, key, default, lo, hi, step=1):
        self.curve.setdefault(key, default)
        return labeled(parent, label, lambda f: Stepper(f, self.curve[key], lo, hi, step,
                                                        lambda v, k=key: self._set(k, v)))

    def _build_graph(self, f):
        self.graph = GraphEditor(f, self.curve.get("points") or fanctl.CURVES["Dengeli"],
                                 self.page.current_temp(self.curve.get("source", "cpu")),
                                 on_change=self._graph_changed)
        self.graph.pack(fill="both", expand=True)

    def _build_linear(self, f):
        for label, key, default, lo, hi in (("En düşük sıcaklık °C", "min_temp", 40, 0, 120),
                                            ("En yüksek sıcaklık °C", "max_temp", 80, 0, 120),
                                            ("En düşük hız %", "min_value", 30, 0, 100),
                                            ("En yüksek hız %", "max_value", 100, 0, 100)):
            self._num(f, label, key, default, lo, hi).pack(side="left", padx=(0, 16), anchor="n")

    def _build_flat(self, f):
        self._num(f, "Hız %", "value", 50, 0, 100).pack(anchor="w")

    def _build_mix(self, f):
        self.curve.setdefault("curves", [])
        self.curve.setdefault("function", "max")
        labeled(f, "Birleştirme", lambda p: Dropdown(p, list(fanctl.MIX_FUNCTIONS.items()), self.curve["function"],
                                                     lambda k: self._set("function", k), width=180)).pack(anchor="w")
        tk.Label(f, text="Birleştirilecek eğriler", bg=CARD, fg=MUTED, font=(FONT, 9)).pack(anchor="w", pady=(14, 4))
        for name in self.page.curve_names(exclude=self.curve.get("name")):
            def toggled(on, n=name):
                if on and n not in self.curve["curves"]:
                    self.curve["curves"].append(n)
                elif not on and n in self.curve["curves"]:
                    self.curve["curves"].remove(n)
                self._changed()

            toggle_row(f, name, name in self.curve["curves"], toggled).pack(anchor="w", pady=3)

    def _build_trigger(self, f):
        for label, key, default, lo, hi in (("Boşta sıcaklığı °C", "idle_temp", 50, 0, 120),
                                            ("Yük sıcaklığı °C", "load_temp", 70, 0, 120),
                                            ("Boşta hızı %", "idle_value", 30, 0, 100),
                                            ("Yük hızı %", "load_value", 100, 0, 100)):
            self._num(f, label, key, default, lo, hi).pack(side="left", padx=(0, 16), anchor="n")

    def _build_hysteresis(self, inner):
        h = self.curve.get("hysteresis") or {}
        self.curve["hysteresis"] = h

        def set_h(key, value):
            h[key] = value
            self._changed()

        tk.Label(inner, text="Histerezis (fanın her küçük sıcaklık oynamasında hız değiştirmemesi için)",
                 bg=CARD, fg=TEXT, font=(FONT, 10)).pack(anchor="w", pady=(16, 6))
        row = tk.Frame(inner, bg=CARD)
        row.pack(fill="x")
        for label, key, default, hi in (("Artış eşiği °C", "up", 2, 30), ("Azalış eşiği °C", "down", 2, 30),
                                        ("Artış bekleme sn", "response_up", 1, 120),
                                        ("Azalış bekleme sn", "response_down", 5, 120)):
            h.setdefault(key, default)
            labeled(row, label, lambda p, k=key, hi=hi: Stepper(p, h[k], 0, hi, 1, lambda v, k=k: set_h(k, v),
                                                                width=4)).pack(side="left", padx=(0, 14))
        toggle_row(inner, "Eğrinin uçlarında beklemeden geç", h.setdefault("ignore_at_limits", True),
                   lambda on: set_h("ignore_at_limits", on)).pack(anchor="w", pady=(12, 0))


class GameCurvePanel(RoundBox):
    """Oyun modu eğrisinin düzenleyicisi. Her değişiklik anında geçerli olur ve kaydedilir; ilk değişiklikte
    varsayılan eğrinin kopyası kullanıcının eğrisi olur."""

    _build_hysteresis = CurvePanel._build_hysteresis

    def __init__(self, page, parent):
        super().__init__(parent, padx=18, pady=16)
        self.page = page
        curve = fanctl.game_curve_of(page.cfg().get("game_curve"))
        self.curve = {"points": [[t, p] for t, p in curve["points"]], "hysteresis": curve["hysteresis"]}
        inner = self.inner
        tk.Label(inner, text="Noktayı sürükle, boş yere çift tıklayıp nokta ekle, sağ tıkla sil. Değişiklik anında "
                             "geçerli olur. Kesikli çizgi: işlemci ve ekran kartından sıcak olanı.",
                 bg=CARD, fg=MUTED, font=(FONT, 9), justify="left").pack(anchor="w", pady=(0, 12))
        self.graph = GraphEditor(inner, self.curve["points"], page.hottest_temp(), on_change=self._graph_changed)
        self.graph.pack(fill="both", expand=True)
        self._build_hysteresis(inner)

    def _graph_changed(self):
        self.curve["points"] = [list(p) for p in self.graph.points]
        self._changed()

    def _changed(self):
        self.page.cfg()["game_curve"] = self.curve
        self.page.save()
        self.page.game_curve_changed()


class FanDetails(RoundBox):
    """Bir fanın FanControl tarzı ayrıntıları: kartın altında açılır, değişiklik anında geçerli."""

    def __init__(self, page, grid, fan_card):
        super().__init__(grid, padx=18, pady=16)
        s = fan_card.settings
        head = tk.Frame(self.inner, bg=CARD)
        head.pack(fill="x")
        tk.Label(head, text=f"{fan_card.title}: ayrıntılar", bg=CARD, fg=TEXT, font=(BOLD, 12)).pack(side="left")
        Pill(head, "Kapat", page.fan_grid.close_detail, CARD, height=28).pack(side="right")
        tk.Label(self.inner, text="Fanın hız değişim sınırları ve en düşük hızı. Adım 100: sınırsız, hemen değişir.", bg=CARD,
                 fg=MUTED, font=(FONT, 9)).pack(anchor="w", pady=(2, 12))
        row = tk.Frame(self.inner, bg=CARD)
        row.pack(fill="x")
        for label, key, default, lo, hi in (("Fark (ofset) %", "offset", 0, -100, 100),
                                            ("Hızlanma %/sn", "step_up", 100, 1, 100),
                                            ("Yavaşlama %/sn", "step_down", fanctl.RAMP_DOWN_PER_SECOND, 1, 100),
                                            ("En az %", "minimum", 0, 0, 100),
                                            ("Durma %", "stop", 0, 0, 100)):
            labeled(row, label, lambda p, k=key, d=default, lo=lo, hi=hi:
                    Stepper(p, s.get(k, d), lo, hi, 1, lambda v, k=k: fan_card.set_value(k, v), width=4)).pack(
                side="left", padx=(0, 10))
        tk.Label(self.inner, text=f"Güvenlik: program bu fanı %{fan_card.control['min']:g}'in altına indirmez.",
                 bg=CARD, fg=MUTED, font=(FONT, 9)).pack(anchor="w", pady=(12, 0))
        c = s.get("calibration")
        if c:
            table = "   ".join(f"%{p:g}: {r:.0f}" if isinstance(r, (int, float)) else f"%{p:g}: —" for p, r in c["points"])
            tk.Label(self.inner, text="Kalibrasyon (RPM):  " + table, bg=CARD, fg=MUTED, font=(FONT, 9), anchor="w",
                     justify="left", wraplength=760).pack(anchor="w", pady=(6, 0))


class ImportDialog(Dialog):
    def __init__(self, page, result, source_name):
        super().__init__(page, "FanControl'den aktar", (760, 640))
        Pill(self.buttons, "Uygula", lambda: (page.apply_import(result, source_name), self.destroy()), BG,
             style="accent").pack(side="right")
        Pill(self.buttons, "Vazgeç", self.destroy, BG).pack(side="right", padx=(0, 8))
        tk.Label(self.buttons, text="Aynı adlı eğriler ve listedeki fanların ayarları değiştirilir.", bg=BG, fg=MUTED,
                 font=(FONT, 9)).pack(side="left")
        tk.Label(self.body, text="İçe aktarılacaklar", bg=BG, fg=TEXT, font=(BOLD, 16)).pack(anchor="w")
        tk.Label(self.body, text=source_name, bg=BG, fg=MUTED, font=(FONT, 9)).pack(anchor="w", pady=(0, 10))
        card = RoundBox(self.body, padx=6, pady=6)
        card.pack(fill="both", expand=True)
        text = tk.Text(card.inner, bg=CARD, fg=TEXT, font=(FONT, 10), relief="flat", padx=10, pady=8,
                       highlightthickness=0, wrap="word", height=22)
        text.pack(fill="both", expand=True)
        text.tag_configure("h", font=(BOLD, 10))
        text.tag_configure("warn", foreground=AMBER)
        text.tag_configure("muted", foreground=MUTED)
        for note in result.notes:
            text.insert("end", note + "\n")
        if result.warnings:
            text.insert("end", "\nDikkat\n", "h")
            for w in result.warnings:
                text.insert("end", "• " + w + "\n", "warn")
        text.insert("end", "\nFanlar\n", "h")
        names = {c["id"]: c["name"] for c in page.hello.get("controls", [])}
        for cid, ch in sorted(result.channels.items(), key=lambda kv: kv[1].get("mode") == "auto"):
            what = {"curve": f"eğri: {ch.get('curve')}", "fixed": f"sabit %{ch.get('value', 0):g}"}.get(
                ch.get("mode"), "BIOS (program sürmez)")
            text.insert("end", f"• {ch.get('name') or names.get(cid, cid)}")
            text.insert("end", f"  ({names.get(cid, cid)})  →  {what}\n", "muted")
        text.insert("end", "\nEğriler\n", "h")
        for c in result.curves:
            text.insert("end", f"• {c['name']}: {page.describe_curve(c)}\n")
        text.configure(state="disabled")


# ---------------------------------------------------------------- kartlar
class FanCard(RoundBox):
    def __init__(self, page, grid, control):
        super().__init__(grid, min_height=FAN_H, padx=16, pady=14)
        self.page, self.control, self.cid = page, control, control["id"]
        nick = self.settings.get("name")
        self.title = nick if nick else control["name"]
        inner = self.inner
        tk.Label(inner, text=self.title, bg=CARD, fg=TEXT, font=(BOLD, 12), anchor="w", justify="left",
                 wraplength=CARD_W - 40).pack(fill="x")
        tk.Label(inner, text=control["name"] if nick and nick != control["name"] else control["hardware"],
                 bg=CARD, fg=MUTED, font=(FONT, 8), anchor="w").pack(fill="x")
        big = tk.Frame(inner, bg=CARD)
        big.pack(fill="x", pady=(8, 0))
        self.rpm = tk.Label(big, text="—", bg=CARD, fg=TEXT, font=(BOLD, 20))
        self.rpm.pack(side="left")
        tk.Label(big, text=" RPM", bg=CARD, fg=MUTED, font=(FONT, 9)).pack(side="left", anchor="s", pady=(0, 5))
        self.state = tk.Label(inner, text="", bg=CARD, fg=MUTED, font=(FONT, 9), anchor="w")
        self.state.pack(fill="x")
        modes = tk.Frame(inner, bg=CARD)
        modes.pack(anchor="w", pady=(10, 0))
        self.mode_buttons = {}
        for key, label in FAN_MODES:
            b = Pill(modes, label, lambda k=key: self.set_mode(k), CARD, style="chip", height=28, padx=10,
                     font=(FONT, 9))
            b.pack(side="left", padx=(0, 4))
            self.mode_buttons[key] = b
        self.sub = tk.Frame(inner, bg=CARD)
        self.sub.pack(fill="x", pady=(10, 0))
        tools = tk.Frame(inner, bg=CARD)
        tools.pack(fill="x", pady=(8, 0))
        self.calibrate_button = Pill(tools, "Kalibre et", lambda: page.toggle_calibration(self), CARD, height=26, padx=8,
                                     font=(FONT, 9))
        self.calibrate_button.pack(anchor="w")
        self.calibrate_info = tk.Label(tools, bg=CARD, fg=MUTED, font=(FONT, 8), anchor="w", justify="left",
                                       wraplength=CARD_W - 50)
        self.calibrate_info.pack(fill="x", pady=(4, 0))
        self.show_calibration()
        self.build()

    @property
    def settings(self):
        return self.page.channels().setdefault(self.cid, {"mode": "auto"})

    def set_mode(self, mode):
        s = self.settings
        s["mode"] = mode
        s.setdefault("value", 50)
        s.setdefault("curve", "Dengeli")
        s.setdefault("source", "gpu" if self.control["kind"].startswith("Gpu") else "cpu")
        self.page.app.log(f"fan {self.control['name']}: mod {mode}")
        self.page.save()
        self.build()

    def set_value(self, key, value, rebuild=False):
        self.settings[key] = value
        self.page.save()
        if rebuild:
            self.build()

    def build(self):
        for w in self.sub.winfo_children():
            w.destroy()
        s = self.settings
        mode = s.get("mode", "auto")
        for key, b in self.mode_buttons.items():
            b.set_active(key == mode)
        if mode == "auto":
            tk.Label(self.sub, text="Donanımın kendi (BIOS) eğrisiyle çalışır.", bg=CARD, fg=MUTED,
                     font=(FONT, 9), justify="left", wraplength=CARD_W - 50).pack(anchor="w")
        elif mode == "game":
            tk.Label(self.sub, text="Oyun eğrisini her zaman izler: sessiz başlar, ısındıkça hızlanır.", bg=CARD, fg=MUTED,
                     font=(FONT, 9), justify="left", wraplength=CARD_W - 50).pack(anchor="w")
        else:
            Dropdown(self.sub, self.page.curve_options(), s.get("curve", "Dengeli"),
                     lambda k: self.set_value("curve", k, rebuild=True), width=230).pack(anchor="w")
            line = tk.Frame(self.sub, bg=CARD)
            line.pack(anchor="w", pady=(8, 0))
            if s.get("curve") in fanctl.CURVES and s.get("curve") not in self.page.curve_names():
                for key, label in BUILTIN_SOURCES:
                    b = Pill(line, label, lambda k=key: self.set_value("source", k, rebuild=True), CARD, style="chip",
                             height=26, padx=8, font=(FONT, 9))
                    b.set_active(s.get("source", "cpu") == key)
                    b.pack(side="left", padx=(0, 4))
            Pill(line, "Ayrıntılar…", self._toggle_details, CARD, height=26, padx=8,
                 font=(FONT, 9)).pack(side="left")

    def show_calibration(self, running=None):
        """running: o an denenen yüzde; yoksa son kalibrasyonun özeti."""
        self.calibrate_button.set_text("Kalibrasyonu durdur" if running is not None else "Kalibre et")
        c = self.settings.get("calibration")
        if running is not None:
            text = f"Kalibre ediliyor: %{running} deneniyor (yaklaşık 1 dakika)"
        elif c:
            text = f"Kalibre edildi: {c['min_rpm']:.0f}–{c['max_rpm']:.0f} RPM · en az dönen hız %{c['min_spin']:g}"
        else:
            text = ""
        self.calibrate_info.configure(text=text)

    def _toggle_details(self):
        grid = self.page.fan_grid
        if grid.detail_card is self:
            grid.close_detail()
        else:
            grid.show_detail(FanDetails(self.page, grid, self), self)

    def update_state(self, rpm, value, software):
        self.rpm.configure(text=f"{rpm:.0f}" if rpm is not None else "—")
        parts = [f"%{value:.0f}" if value is not None else "", "program kontrolünde" if software else "BIOS kontrolünde"]
        self.state.configure(text=" · ".join(p for p in parts if p))


class CurveCard(RoundBox):
    def __init__(self, page, grid, curve):
        super().__init__(grid, min_height=CURVE_H, padx=16, pady=14)
        self.page, self.curve = page, curve
        inner = self.inner
        tk.Label(inner, text=curve["name"], bg=CARD, fg=TEXT, font=(BOLD, 12), anchor="w").pack(fill="x")
        self.kind = curve.get("type", "graph")
        self.sub_label = tk.Label(inner, bg=CARD, fg=MUTED, font=(FONT, 8), anchor="w")
        self.sub_label.pack(fill="x")
        self.preview = self.summary = None
        if self.kind == "sync":
            self._build_sync(inner)
        elif self.kind == "mix":
            self.summary = tk.Label(inner, bg=CARD, fg=TEXT, font=(FONT, 10), anchor="w", justify="left",
                                    wraplength=CARD_W - 60)
            self.summary.pack(fill="x", pady=(8, 0))
        else:
            self.preview = tk.Canvas(inner, width=S(CARD_W - 50), height=S(82), bg=CARD, highlightthickness=0)
            self.preview.pack(anchor="w", pady=(8, 0))
        names = {c["id"]: c["name"] for c in page.hello.get("controls", [])}
        users = [ch.get("name") or names.get(cid, cid) for cid, ch in page.channels().items()
                 if ch.get("mode") == "curve" and ch.get("curve") == curve["name"]]
        tk.Label(inner, text=("Kullanan: " + ", ".join(users)) if users else "Kullanan yok", bg=CARD, fg=MUTED,
                 font=(FONT, 9), anchor="w", wraplength=CARD_W - 50, justify="left").pack(fill="x", pady=(6, 0))
        buttons = tk.Frame(inner, bg=CARD)
        buttons.pack(side="bottom", anchor="w", pady=(8, 0))
        Pill(buttons, "Düzenle", lambda: page.toggle_editor(curve), CARD, height=28).pack(side="left", padx=(0, 6))
        Pill(buttons, "Sil", lambda: page.delete_curve(curve), CARD, height=28).pack(side="left")
        self.refresh()

    def _build_sync(self, inner):
        """Eşitle: izlenecek fan (listeden) ve fark doğrudan kartta değişir."""
        tk.Label(inner, text="İzlenecek fan", bg=CARD, fg=MUTED, font=(FONT, 9), anchor="w").pack(fill="x", pady=(8, 3))
        options = self.page.follow_options(self.curve.get("control"))
        if options:
            Dropdown(inner, options, self.curve.get("control"), self._follow, width=CARD_W - 50).pack(anchor="w")
        else:
            tk.Label(inner, text="Fan kontrolü açılınca seçilebilir.", bg=CARD, fg=MUTED, font=(FONT, 9),
                     anchor="w").pack(fill="x")
        labeled(inner, "Fark (ofset) %", lambda f: Stepper(f, self.curve.get("offset", 0), -100, 100, 1,
                                                           self._set_offset)).pack(anchor="w", pady=(8, 0))

    def _follow(self, key):
        self.curve["control"] = key
        self.page.curve_changed(self.curve)

    def _set_offset(self, value):
        self.curve["offset"] = value
        self.page.curve_changed(self.curve)

    def refresh(self):
        src = dict(self.page.temp_options()).get(self.curve.get("source"), "") \
            if self.kind in ("graph", "linear", "trigger") else ""
        self.sub_label.configure(text=fanctl.CURVE_TYPES.get(self.kind, self.kind) + (f" · {src}" if src else ""))
        if self.preview:
            curve_preview(self.preview, self.curve, S(CARD_W - 50), S(82))
        if self.summary:
            self.summary.configure(text=self.page.describe_curve(self.curve).split(" · ", 1)[-1])


class NewCurveCard(RoundBox):
    def __init__(self, page, grid):
        super().__init__(grid, min_height=CURVE_H, padx=16, pady=14)
        tk.Label(self.inner, text="＋ Yeni eğri", bg=CARD, fg=TEXT, font=(BOLD, 12), anchor="w").pack(fill="x")
        tk.Label(self.inner, text="Oluşturmak istediğin eğri türünü seç.", bg=CARD, fg=MUTED, font=(FONT, 9),
                 anchor="w").pack(fill="x", pady=(0, 10))
        kinds = list(fanctl.CURVE_TYPES.items())
        for i in range(0, len(kinds), 2):
            line = tk.Frame(self.inner, bg=CARD)
            line.pack(anchor="w", pady=(0, 6))
            for kind, label in kinds[i:i + 2]:
                Pill(line, label, lambda k=kind: page.new_curve(k), CARD, height=30, padx=12).pack(
                    side="left", padx=(0, 6))


class TempTile(RoundBox):
    def __init__(self, grid, label):
        super().__init__(grid, fixed_height=TEMP_H, padx=14, pady=10)
        tk.Label(self.inner, text=label, bg=CARD, fg=MUTED, font=(FONT, 9), anchor="w").pack(fill="x")
        self.value = tk.Label(self.inner, text="—", bg=CARD, fg=TEXT, font=(BOLD, 18), anchor="w")
        self.value.pack(fill="x")


# ---------------------------------------------------------------- sayfa
class FansPage:
    def __init__(self, app):
        self.app = app
        self.host = self.planner = None
        self.hello = {}
        self.state = None
        self.cards = {}
        self.temp_tiles = {}
        self.pending_import = None
        self.calibrations = {}  # kontrol_id -> fans.Calibration
        self._full_since = None
        self.full_warning = False
        self.history = collections.deque(maxlen=100)  # (işlemci, ekran kartı, en hızlı fan): 3 sn'de bir
        self._backup_text, self._backup_time = None, -1e9
        self._restarts = []
        self.page = app._page("Fanlar")
        self._build()

    # ---- ayarlar
    def cfg(self):
        return self.app.config.setdefault("fans", {})

    def channels(self):
        return self.cfg().setdefault("channels", {})

    def curves(self):
        return self.cfg().setdefault("curves", [])

    def curve_names(self, exclude=None):
        return [c["name"] for c in self.curves() if c.get("name") and c["name"] != exclude]

    def curve_options(self):
        own = [(n, n) for n in self.curve_names()]
        builtin = [(n, f"{n} (hazır)") for n in fanctl.CURVES if n not in self.curve_names()]
        return own + builtin

    def control_options(self):
        out = []
        for c in self.hello.get("controls", []):
            nick = self.channels().get(c["id"], {}).get("name")
            out.append((c["id"], f"{nick} · {c['name']}" if nick and nick != c["name"] else c["name"]))
        return out

    def temp_options(self):
        opts = [("cpu", "İşlemci (Tctl)"), ("gpu", "Ekran kartı")]
        for t in self.hello.get("temps", []):
            hw = t["hardware"].replace("NVIDIA GeForce ", "").replace("AMD Ryzen 7 ", "")
            opts.append((t["id"], f"{hw} · {t['name']}"))
        return opts

    def current_temp(self, source):
        if not self.state or not self.planner:
            return None
        return self.planner.source_temp(source, self.state["temps"])

    def save(self):
        self._auto_backup()
        self.app._save_config()

    # ---- iskelet
    def _build(self):
        app, inner = self.app, self.page.inner
        tk.Label(inner, text="Kasa, işlemci ve ekran kartı fanlarını sıcaklığa göre ya da sabit hızda sürer. "
                             "Güvenlik: hiçbir fan en az hızın altına inmez; işlemci 90°C'yi ya da ekran kartı 85°C'yi "
                             "geçerse bütün fanlar %100'e çıkar; program kapanınca ya da donarsa fanlar anakartın "
                             "(BIOS) kontrolüne geri döner.",
                 bg=BG, fg=MUTED, font=(FONT, 9), anchor="w", justify="left", wraplength=700).pack(
            fill="x", padx=(8, 40))
        box = app._section(self.page, "Durum")
        _, right, self.status = app._row(box, "Fan kontrolü", "Kapalı: fanları anakart (BIOS) sürüyor")
        self.toggle = Toggle(right, self.toggle_running, CARD)
        self.toggle.set(False)
        self.toggle.pack()

        self.temps_box = app._section(self.page, "Sıcaklıklar")
        self.temp_grid = CardGrid(self.temps_box, min_width=TEMP_W)
        self.temp_grid.pack(fill="x")
        self.temps_hint = tk.Label(self.temps_box, text="Fan kontrolü açılınca görünür.", bg=BG, fg=MUTED,
                                   font=(FONT, 9), anchor="w")
        self.temps_hint.pack(fill="x")

        chart_box = app._section(self.page, "Son 5 dakika")
        tk.Label(chart_box, text="Turuncu: işlemci °C · Turkuaz: ekran kartı °C · Mavi: en hızlı fan %", bg=BG, fg=MUTED,
                 font=(FONT, 9), anchor="w").pack(fill="x", pady=(0, 4))
        self.chart = tk.Canvas(chart_box, height=S(110), bg=CARD, highlightthickness=0)
        self.chart.pack(fill="x")
        self.fans_box = app._section(self.page, "Fanlar")
        self.fan_grid = CardGrid(self.fans_box)
        self.fan_grid.pack(fill="x")
        self.gpu_hint = tk.Label(self.fans_box, bg=BG, fg=MUTED, font=(FONT, 9), anchor="w", cursor="hand2")
        self.gpu_hint.bind("<Button-1>", lambda _e: self._toggle_show_gpu())
        self.fans_hint = tk.Label(self.fans_box, text="Fan kontrolü açılınca görünür.", bg=BG, fg=MUTED,
                                  font=(FONT, 9), anchor="w")
        self.fans_hint.pack(fill="x")

        self.curves_box = app._section(self.page, "Eğriler")
        self.curve_grid = CardGrid(self.curves_box, min_width=350)  # Eşitle düğmeleri iki sütun sığsın
        self.curve_grid.pack(fill="x")

        self.game_box = app._section(self.page, "Oyun modu eğrisi")
        _, right, self.game_desc = app._row(self.game_box, "Oyun eğrisi", "")
        self.game_desc.pack(fill="x")
        self.game_edit = Pill(right, "Düzenle", self.toggle_game_editor, CARD)
        self.game_edit.pack(side="left", padx=(0, 6))
        self.game_reset = Pill(right, "Varsayılan", self.reset_game_curve, CARD)
        self.game_panel = None
        self.game_curve_changed()

        box = app._section(self.page, "FanControl'den geçiş ve yedek")
        _, right, self.import_desc = app._row(
            box, "Yedeği içe aktar", "Bu programın yedeğini ve FanControl yedeğini (.json) bu pencerenin üstüne "
                                     "sürükleyip bırak ya da dosya seç. Eğriler, fan adları ve ayarlar aktarılır; önce "
                                     "ne aktarılacağı gösterilir.")
        Pill(right, "Dosya seç…", self.pick_file, CARD).pack(side="left")
        _, right, _ = app._row(box, "Bu programın yedeği", "Eğrileri ve fan ayarlarını dosyaya kaydet; bu dosya da "
                                                           "aynı 'Dosya seç' ile geri yüklenir.")
        Pill(right, "Dışa aktar…", self.export_file, CARD).pack(side="left")
        _, right, _ = app._row(box, "Önceki ayarlara dön", "Fan ayarı değişince önceki hali otomatik saklanır, son beşi durur.")
        self.backup_list = Dropdown(right, [("", "Henüz yedek yok")], None, self.restore_backup, width=240, placeholder="Yedek seç")
        self.backup_list.pack()
        self._fill_backups()
        self._build_curves()

    # ---- eğri listesi
    def describe_curve(self, c):
        kind = c.get("type", "graph")
        label = fanctl.CURVE_TYPES.get(kind, kind)
        src = dict(self.temp_options()).get(c.get("source"), c.get("source", ""))
        if kind == "graph":
            pts = "  ".join(f"{t:g}°→%{v:g}" for t, v in c.get("points", []))
            return f"{label} · {src} · {pts}"
        if kind == "linear":
            return (f"{label} · {src} · {c.get('min_temp', 40):g}°C %{c.get('min_value', 30):g} → "
                    f"{c.get('max_temp', 80):g}°C %{c.get('max_value', 100):g}")
        if kind == "flat":
            return f"{label} · %{c.get('value', 50):g}"
        if kind == "sync":
            target = dict(self.control_options()).get(c.get("control"), c.get("control"))
            return f"{label} · {target} {c.get('offset', 0):+g}%"
        if kind == "mix":
            return f"{label} · {fanctl.MIX_FUNCTIONS.get(c.get('function'), c.get('function'))}: " \
                   f"{', '.join(c.get('curves', []))}"
        if kind == "trigger":
            return (f"{label} · {src} · {c.get('load_temp', 70):g}°C'de %{c.get('load_value', 100):g}, "
                    f"{c.get('idle_temp', 50):g}°C'de %{c.get('idle_value', 30):g}")
        return label

    def follow_options(self, current=None):
        """Eşitle eğrisinin izleyebileceği fanlar: takılı görünenler ve seçili olan."""
        shown = set(self.cards) | {current}
        return [(k, label) for k, label in self.control_options() if k in shown]

    def _build_curves(self):
        card = self.curve_grid.detail_card
        editing = card.curve["name"] if card else None
        self.curve_grid.clear()
        for c in self.curves():
            self.curve_grid.add(CurveCard(self, self.curve_grid, c))
        self.curve_grid.add(NewCurveCard(self, self.curve_grid))
        if editing:
            self.open_editor(editing)

    def _curve_card(self, name):
        return next((c for c in self.curve_grid.cards if isinstance(c, CurveCard) and c.curve["name"] == name), None)

    def open_editor(self, curve):
        name = curve if isinstance(curve, str) else curve["name"]
        card = self._curve_card(name)
        if card:
            self.curve_grid.show_detail(CurvePanel(self, self.curve_grid, card.curve), card)

    def close_editor(self):
        self.curve_grid.close_detail()

    def toggle_editor(self, curve):
        card = self.curve_grid.detail_card
        if card and card.curve["name"] == curve["name"]:
            self.close_editor()
        else:
            self.open_editor(curve)

    def curve_changed(self, curve):
        """Düzenleme anında geçerli olur: planlayıcı ayarı her turda buradan okur."""
        self.save()
        card = self._curve_card(curve["name"])
        if card:
            card.refresh()

    def renamed(self, curve, old_name):
        self.close_editor()
        self.save_curve(curve, old_name)
        self.open_editor(curve)

    def new_curve(self, kind):
        base = {"graph": {"points": [list(p) for p in fanctl.CURVES["Dengeli"]], "source": "cpu"},
                "linear": {"source": "cpu"}, "trigger": {"source": "cpu"}}.get(kind, {})
        n, name = 1, fanctl.CURVE_TYPES[kind]
        while name in self.curve_names():
            n += 1
            name = f"{fanctl.CURVE_TYPES[kind]} {n}"
        curve = dict(base, type=kind, name=name)
        if kind == "sync":
            options = self.follow_options()
            curve.update(control=options[0][0] if options else None, offset=0)
        self.close_editor()
        self.save_curve(curve, None)
        self.open_editor(curve)

    def save_curve(self, curve, original_name):
        curves = self.curves()
        idx = next((i for i, c in enumerate(curves) if c["name"] == original_name), None) if original_name else None
        if idx is None:
            curves.append(curve)
        else:
            curves[idx] = curve
        if original_name and original_name != curve["name"]:  # yeniden adlandırma: kullananları güncelle
            for ch in self.channels().values():
                if ch.get("curve") == original_name:
                    ch["curve"] = curve["name"]
            for c in curves:
                if c.get("type") == "mix":
                    c["curves"] = [curve["name"] if n == original_name else n for n in c.get("curves", [])]
        self.app.log(f"eğri kaydedildi: {curve['name']} ({self.describe_curve(curve)})")
        self.save()
        self._refresh()

    def delete_curve(self, curve):
        users = [ch.get("name") or cid for cid, ch in self.channels().items() if ch.get("curve") == curve["name"]]
        msg = f"'{curve['name']}' eğrisi silinsin mi?"
        if users:
            msg += f"\n\nBu eğriyi kullanan fanlar BIOS'a döner: {', '.join(users)}"
        if not messagebox.askyesno("Eğriyi sil", msg, parent=self.app.root):
            return
        self.close_editor()
        self.cfg()["curves"] = [c for c in self.curves() if c["name"] != curve["name"]]
        for ch in self.channels().values():
            if ch.get("curve") == curve["name"]:
                ch["mode"] = "auto"
        self.app.log(f"eğri silindi: {curve['name']}")
        self.save()
        self._refresh()

    def _refresh(self):
        self._build_curves()
        if self.state:
            self._build_cards(self.state)
        if self.game_panel:  # yedekten ya da içe aktarmadan gelen eğriyi göstersin
            self.toggle_game_editor()
            self.toggle_game_editor()
        self.game_curve_changed()

    # ---- oyun modu eğrisi
    def hottest_temp(self):
        temps = [t for t in (self.current_temp("cpu"), self.current_temp("gpu")) if t is not None]
        return max(temps) if temps else None

    def game_curve_changed(self):
        custom = bool(self.cfg().get("game_curve"))
        points = fanctl.game_curve_of(self.cfg().get("game_curve"))["points"]
        self.game_desc.configure(text="Oyun modu açıkken eğriyle çalışan fanlar bu eğriye geçer: işlemci fanı "
                                      "işlemciye, diğerleri işlemci ve ekran kartından sıcak olana bakar.\n"
                                      + ("Kendi eğrin · " if custom else "Varsayılan · ")
                                      + "  ".join(f"{t:g}°→%{p:g}" for t, p in points))
        if custom:
            self.game_reset.pack(side="left", padx=(0, 6))
        else:
            self.game_reset.pack_forget()

    def toggle_game_editor(self):
        if self.game_panel:
            self.game_panel.destroy()
            self.game_panel = None
            self.game_edit.set_text("Düzenle")
        else:
            self.game_panel = GameCurvePanel(self, self.game_box)
            self.game_panel.pack(fill="x", pady=(0, 6))
            self.game_edit.set_text("Kapat")

    def reset_game_curve(self):
        self.cfg().pop("game_curve", None)
        self.app.log("oyun eğrisi varsayılana döndü")
        self.save()
        if self.game_panel:
            self.toggle_game_editor()
            self.toggle_game_editor()
        self.game_curve_changed()

    # ---- içe / dışa aktarma
    def pick_file(self):
        initial = FANCONTROL_CONFIGS if os.path.isdir(FANCONTROL_CONFIGS) else os.path.expanduser("~")
        path = filedialog.askopenfilename(parent=self.app.root, title="FanControl yedeği ya da LightCore yedeği",
                                          initialdir=initial, filetypes=[("JSON", "*.json"), ("Tüm dosyalar", "*.*")])
        if path:
            self.import_file(path)

    def on_drop(self, paths):
        jsons = [p for p in paths if p.lower().endswith(".json")]
        if not jsons:
            self.app._status("Bırakılan dosya .json değil; FanControl yedeği .json olmalı.", AMBER)
            return
        self.app.show_page("fans")
        self.import_file(jsons[0])

    def import_file(self, path):
        try:
            with open(path, encoding="utf-8-sig") as f:
                data = json.load(f)
        except (OSError, ValueError) as e:
            self.import_desc.configure(text=f"Dosya okunamadı: {e}", fg=RED)
            return
        if not self.hello:
            self.pending_import = (data, path)
            self.import_desc.configure(text=f"{os.path.basename(path)} hazır: fanlar eşleştirilebilsin diye fan "
                                            "kontrolünü açınca aktarma ekranı gelecek.", fg=AMBER)
            return
        try:
            result = fcimp.import_config(data, self.hello)
        except ValueError as e:
            self.import_desc.configure(text=str(e), fg=RED)
            return
        ImportDialog(self, result, os.path.basename(path))

    def apply_import(self, result, source_name):
        names = {c["name"] for c in result.curves}
        self.cfg()["curves"] = [c for c in self.curves() if c["name"] not in names] + result.curves
        self.channels().update(result.channels)
        if result.game_curve:
            self.cfg()["game_curve"] = result.game_curve
        self._migrate_fixed()
        self.app.log(f"içe aktarıldı: {source_name} — {'; '.join(result.notes)}"
                     + (f" | uyarılar: {'; '.join(result.warnings)}" if result.warnings else ""))
        self.import_desc.configure(text=f"{source_name} aktarıldı: {len(result.curves)} eğri, "
                                        f"{len(result.channels)} fan.", fg=GREEN)
        self.save()
        self._refresh()

    def export_file(self):
        path = filedialog.asksaveasfilename(parent=self.app.root, title="Fan ayarlarını kaydet",
                                            defaultextension=".json", initialfile="lightcore_fanlar.json",
                                            filetypes=[("JSON", "*.json")])
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(fcimp.export_config(self.cfg()), f, ensure_ascii=False, indent=2)
        except OSError as e:
            self.import_desc.configure(text=f"Kaydedilemedi: {e}", fg=RED)
            return
        self.import_desc.configure(text=f"Kaydedildi: {path}", fg=GREEN)

    # ---- FanHost
    def toggle_running(self):
        if self.host:
            self.stop(user=True)
        else:
            self.start()

    def _status(self, text, color=MUTED):
        self.status.configure(text=text, fg=color)

    def start(self):
        if not fanctl.is_admin():
            self._status("Fan kontrolü için programı yönetici olarak aç (sağ tık > Yönetici olarak çalıştır).", AMBER)
            return
        if fanctl.fancontrol_running():
            self._status("FanControl açık. İki program aynı anda fan sürerse birbirini bozar: önce FanControl'ü "
                         "kapat (sistem tepsisinden Exit).", AMBER)
            return
        if not fanctl.pawnio_installed():
            self._status("Fan kontrolü için PawnIO sürücüsü gerekli: Ayarlar > Kurulum kontrolü'ndeki 'PawnIO'yu kur' "
                         "düğmesiyle kur, sonra fan kontrolünü yeniden aç.", AMBER)
            self.app.log("PawnIO sürücüsü kurulu değil, fan kontrolü başlatılmadı")
            return
        lib = find_fan_lib()
        if not lib:
            self._status("LibreHardwareMonitor bulunamadı: programın fan klasörü eksik.", RED)
            return
        self.toggle.set(True)
        self._status("Başlatılıyor...", MUTED)
        self.cfg()["enabled"] = True
        self.save()
        events = self.app.events

        def work():
            try:
                exe = fanctl.build_host(os.path.join(program_dir(), "fanhost"), lib)
                host = fanctl.FanHostProcess(exe, lib, lambda m: events.put(("fan", m)))
            except Exception as e:
                events.put(("fan", {"type": "exit", "code": -1, "stderr": str(e)}))
                return
            events.put(("fan_started", host))

        threading.Thread(target=work, daemon=True).start()

    def started(self, host):
        self.host = host
        self.app.log("fan kontrolü başlatıldı")

    def stop(self, user=False):
        host, self.host = self.host, None
        self.toggle.set(False)
        if user:
            self.cfg()["enabled"] = False
            self.save()
        self._status("Kapalı: fanları anakart (BIOS) sürüyor")
        if host:
            self.app.log("fan kontrolü kapatıldı, fanlar BIOS'a verildi")
            threading.Thread(target=host.stop, daemon=True).start()

    def shutdown(self):
        if self.host:
            self.host.stop()
            self.app.log("program kapandı, fanlar BIOS'a verildi")

    def _report_missing_sources(self):
        """Okunamayan sensör kayda bir kez yazılır; sayfada düzelene kadar uyarı kalır (_update_view)."""
        new = self.planner.missing_sources - self._reported_sources
        if new:
            self._reported_sources |= new
            self.app.log(f"eğri sensörü okunamadı: {', '.join(sorted(new))}; yerine işlemci ya da ekran kartı kullanılıyor")

    def on_message(self, m):
        kind = m.get("type")
        if kind == "hello":
            self._reported_sources = set()
            self.hello = m
            self.planner = fanctl.FanPlanner(m)
            self._migrate_fixed()
            self.app.log("fan kontrolü: " + ", ".join(c["name"] for c in m["controls"]) + " · sıcaklık kaynakları "
                         + str(self.planner.sources))
        elif kind == "state":
            if not self.planner or not self.host:
                return
            self.state = m
            if not self.cards:
                self._build_cards(m)
                self._build_curves()
                if self.pending_import:
                    data, path = self.pending_import
                    self.pending_import = None
                    try:
                        ImportDialog(self, fcimp.import_config(data, self.hello), os.path.basename(path))
                    except ValueError as e:
                        self.import_desc.configure(text=str(e), fg=RED)
            values = self.planner.desired(self.cfg(), m["temps"], game=self.app.game_mode,
                                         current={k: c.get("value") for k, c in m["controls"].items()})
            self._report_missing_sources()
            if self.app.game_mode:
                self.calibrations.clear()
            self._calibrate(m, values)
            self._remember_point(m, values)
            self._watch_full_speed(values)
            self.host.set_values(values)  # aynı zamanda "arayüz yaşıyor" sinyali
            if self.app.page == "fans" and not self.app.window.hidden:
                self._update_view(m)  # görünmeyen sayfa çizilmez
        elif kind == "emergency":
            text = (f"ACİL DURUM: sıcaklık sınırı aşıldı (işlemci {m.get('cpu')}°C, ekran kartı {m.get('gpu')}°C) — "
                    "program sürdüğü bütün fanları %100'e aldı." if m.get("active") else "Sıcaklık normale döndü.")
            self.app.log(text)
            self._status(text, RED if m.get("active") else GREEN)
        elif kind == "error":
            self.app.log("FanHost hatası: " + m.get("message", ""))
            self.app._status("Fan: " + m.get("message", "")[:200], RED)
        elif kind == "exit":
            was_running = self.host is not None
            self.host = None
            self.toggle.set(False)
            if m.get("code") == DBG_TERMINATE_PROCESS and not m.get("stderr"):
                # Windows kapanırken / oturum kapanırken sonlandırıldı: çökme değil, yeniden başlatılmaz
                self.app.log("FanHost Windows tarafından kapatıldı (kapanış ya da oturum kapatma)")
                self._status("Fan kontrolü Windows tarafından kapatıldı; fanları anakart sürüyor.", AMBER)
            elif m.get("code") not in (0, None) or (was_running and m.get("stderr")):
                self.app.log(f"FanHost beklenmedik kapandı (kod {m.get('code')}): {m.get('stderr')}")
                self._recover(was_running, m)

    def _recover(self, was_running, m):
        """FanHost zorla kapanırsa fanlar son hızında takılı kalır ve sıcaklık koruması da durur: hemen
        yeniden başlatılır. Fan ancak yeni kopya çalışırken sıcaklığa tepki verir."""
        now = time.monotonic()
        self._restarts = [t for t in self._restarts if now - t < RESTART_WINDOW]
        if was_running and len(self._restarts) < RESTART_LIMIT:
            self._restarts.append(now)
            self.app.log("fan kontrolü yeniden başlatılıyor")
            self.start()
            self._status("Fan kontrolü beklenmedik kapandı, yeniden başlatıldı. Programı kapatmadan önce bilgisayarı "
                         "yeniden başlat: fanlar BIOS'a ancak o zaman döner.", AMBER)
        else:
            self._status(f"FAN KONTROLÜ DURDU: fanlar son hızında takılı kalmış olabilir, bilgisayarı yeniden başlat. "
                         f"{m.get('stderr', '')[:200]}", RED)

    # ---- canlı grafik: son 5 dakika
    def _remember_point(self, state, values):
        temps = [self.planner.source_temp(s, state["temps"]) for s in ("cpu", "gpu")]
        speeds = [c.get("value") for c in state["controls"].values() if isinstance(c.get("value"), (int, float))]
        self.history.append((temps[0], temps[1], max(speeds) if speeds else None))
        if self.app.page == "fans" and not self.app.window.hidden:
            self._draw_history()

    def _draw_history(self):
        c = self.chart
        c.delete("all")
        w, h = max(c.winfo_width(), 2), max(c.winfo_height(), 2)
        for level in (25, 50, 75):
            y = h - level / 100 * h
            c.create_line(0, y, w, y, fill=CONTROL_EDGE)
            c.create_text(4, y - 2, text=f"{level}", fill=MUTED, anchor="sw", font=(FONT, 8))
        for index, color in ((0, "#FB923C"), (1, "#2ED6C8"), (2, ACCENT)):
            points = []
            for i, sample in enumerate(self.history):
                if sample[index] is not None:
                    points += [w - (len(self.history) - 1 - i) * w / (self.history.maxlen - 1), h - min(100, sample[index]) / 100 * h]
            if len(points) >= 4:
                c.create_line(*points, fill=color, width=S(2), smooth=False)

    # ---- otomatik yedek: fan ayarı değişince önceki hali saklanır, son beşi durur
    def _backup_dir(self):
        return os.path.join(config_dir(), "fan_yedekleri")

    def _auto_backup(self):
        now = time.monotonic()
        text = json.dumps({k: self.cfg().get(k) for k in ("curves", "channels", "game_curve")}, ensure_ascii=False,
                          sort_keys=True)
        if self._backup_text is None:
            self._backup_text = text
            return
        if text == self._backup_text or now - self._backup_time < BACKUP_SECONDS:
            return
        previous, self._backup_text, self._backup_time = self._backup_text, text, now
        try:
            folder = self._backup_dir()
            os.makedirs(folder, exist_ok=True)
            with open(os.path.join(folder, time.strftime("%Y-%m-%d %H-%M-%S.json")), "w", encoding="utf-8") as f:
                f.write(previous)
            for old in sorted(os.listdir(folder))[:-BACKUP_COUNT]:
                os.remove(os.path.join(folder, old))
        except OSError as e:
            self.app.log(f"fan yedeği yazılamadı: {e}")
        self._fill_backups()

    def _fill_backups(self):
        try:
            files = sorted(os.listdir(self._backup_dir()), reverse=True)
        except OSError:
            files = []
        self.backup_list.set_options([(f, f[:11] + f[11:-5].replace("-", ":")) for f in files] or [("", "Henüz yedek yok")])
        self.backup_list.set_current(None)

    def restore_backup(self, name):
        if not name:
            return
        try:
            with open(os.path.join(self._backup_dir(), name), encoding="utf-8") as f:
                data = json.load(f)
            self.cfg().update(curves=data.get("curves") or [], channels=data.get("channels") or {})
            if data.get("game_curve"):
                self.cfg()["game_curve"] = data["game_curve"]
            else:  # yedekte yok: o zaman varsayılan eğri kullanılıyordu
                self.cfg().pop("game_curve", None)
        except (OSError, ValueError) as e:
            self.import_desc.configure(text=f"Yedek okunamadı: {e}", fg=RED)
            return
        self.app.log(f"fan ayarları yedekten döndü: {name}")
        self.save()
        self._refresh()

    def _migrate_fixed(self):
        """Eski ya da içe aktarılan "Sabit" fanlar: hızları değişmesin diye Sabit türü eğriye çevrilir."""
        for ch in self.channels().values():
            if ch.get("mode") != "fixed":
                continue
            value = ch.get("value", 50)
            name = f"Sabit %{value:g}"
            if name not in self.curve_names():
                self.curves().append({"type": "flat", "name": name, "value": value})
            ch.update(mode="curve", curve=name)

    # ---- oyun modu ve uyarı
    def _watch_full_speed(self, values):
        """Normal kullanımda fan uzun süre tam hızdaysa söyler: soğutma ya da eğri bakılmalı."""
        full = not self.app.game_mode and any(isinstance(v, (int, float)) and v >= 99 for v in values.values())
        now = time.monotonic()
        if not full:
            self._full_since = None
            self.full_warning = False
        elif self._full_since is None:
            self._full_since = now
        elif now - self._full_since > FULL_SPEED_WARNING and not self.full_warning:
            self.full_warning = True
            self.app.log("uyarı: fan iki dakikadır tam hızda")

    # ---- kalibrasyon
    def toggle_calibration(self, card):
        if card.cid in self.calibrations:
            del self.calibrations[card.cid]
            card.show_calibration()
            self.app.log(f"kalibrasyon durduruldu: {card.title}")
            return
        self.calibrations[card.cid] = fanctl.Calibration(card.control["min"], time.monotonic())
        card.show_calibration(100)
        self.app.log(f"kalibrasyon başladı: {card.title}")

    def _calibrate(self, state, values):
        """Kalibre edilen fanın hızı planlayıcının yerine buradan gelir. Güvenlik kuralları FanHost'ta
        olduğu için aşırı sıcakta fan yine %100 olur; o zaman ölçüm geçersizdir ve durdurulur."""
        now = time.monotonic()
        for cid, cal in list(self.calibrations.items()):
            card = self.cards.get(cid)
            if state.get("emergency") or card is None:
                del self.calibrations[cid]
                if card:
                    card.show_calibration()
                    self._status("Sıcaklık sınırı aşıldı, kalibrasyon durduruldu.", AMBER)
                continue
            fid = self._fan_by_control(card.control)
            percent = cal.tick(now, state["fans"].get(fid) if fid else None)
            if percent is not None:
                values[cid] = percent
                card.show_calibration(percent)
                continue
            del self.calibrations[cid]
            result = cal.result()
            if result:
                card.settings["calibration"] = result
                if result["min_spin"] > card.settings.get("minimum", 0):
                    card.settings["minimum"] = result["min_spin"]
                self.save()
                self.app.log(f"kalibrasyon bitti: {card.title}: {result}")
                card.calibrate_info.configure(text="")
            else:
                self.app.log(f"kalibrasyon: {card.title} devri okunamadı")
            card.show_calibration()
            if not result:
                card.calibrate_info.configure(text="Bu kanalda devir okunamadı, kalibrasyon yapılamadı.", fg=AMBER)

    # ---- fan kartları ve sıcaklıklar
    def _fan_by_control(self, control):
        for f in self.hello.get("fans", []):
            if f["hardware"] == control["hardware"] and f["name"] == control["name"]:
                return f["id"]
        return None

    def _build_cards(self, state):
        self.fan_grid.clear()
        self.cards = {}
        self.fans_hint.pack_forget()
        self.gpu_hint.pack_forget()
        show_all = self.cfg().get("show_all", False)
        show_gpu = self.cfg().get("show_gpu", False)
        hidden = hidden_gpu = 0
        for c in self.hello["controls"]:
            if c["kind"].startswith("Gpu"):
                if show_gpu or self.channels().get(c["id"], {}).get("mode", "auto") != "auto":
                    self.cards[c["id"]] = self.fan_grid.add(FanCard(self, self.fan_grid, c))
                else:
                    hidden_gpu += 1
                continue
            fid = self._fan_by_control(c)
            rpm = state["fans"].get(fid) if fid else None
            connected = (rpm or 0) > 0 \
                or self.channels().get(c["id"], {}).get("mode", "auto") != "auto"
            if not connected and not show_all:
                hidden += 1
                continue
            self.cards[c["id"]] = self.fan_grid.add(FanCard(self, self.fan_grid, c))
        if hidden_gpu or show_gpu:
            self.gpu_hint.configure(text="Ekran kartı fanlarını gizlemek için tıkla" if show_gpu
                                    else "Ekran kartı fanlarını görmek için tıkla")
            self.gpu_hint.pack(fill="x", pady=(2, 0))
        if hidden or show_all:
            self.fans_hint.configure(text=(f"{hidden} kanalda devir okunmuyor (fan takılı değil) — " if hidden else "")
                                     + ("göstermek için tıkla" if not show_all else "gizlemek için tıkla"),
                                     cursor="hand2", fg=MUTED)
            self.fans_hint.bind("<Button-1>", lambda _e: self._toggle_show_all())
            self.fans_hint.pack(fill="x", pady=(2, 0))
        self.temp_grid.clear()
        self.temps_hint.pack_forget()
        self.temp_tiles = {}
        for label, sid in self._temp_choices():
            self.temp_tiles[sid] = self.temp_grid.add(TempTile(self.temp_grid, label)).value

    def _toggle_show_gpu(self):
        self.cfg()["show_gpu"] = not self.cfg().get("show_gpu", False)
        self.save()
        if self.state:
            self._build_cards(self.state)
            self._build_curves()
            self._update_view(self.state)

    def _toggle_show_all(self):
        self.cfg()["show_all"] = not self.cfg().get("show_all", False)
        self.save()
        if self.state:
            self._build_cards(self.state)

    def _temp_choices(self):
        temps = self.hello.get("temps", [])
        chosen = [("İşlemci (Tctl)", self.planner.sources.get("cpu"))]
        ccd = next((t for t in temps if t["kind"] == "Cpu" and t["name"].startswith("CCD")), None)
        if ccd:
            chosen.append((f"İşlemci {ccd['name'].split(' ')[0]} (Tdie)", ccd["id"]))
        chosen.append(("Ekran kartı", self.planner.sources.get("gpu")))
        gpu_hw = next((t["hardware"] for t in temps if t["id"] == self.planner.sources.get("gpu")), None)
        for label, name, hw in (("GPU sıcak nokta", "GPU Hot Spot", gpu_hw), ("Anakart", "System", None),
                                ("VRM", "VRM MOS", None), ("Chipset", "PCH", None)):
            t = next((t for t in temps if t["name"] == name and (hw is None or t["hardware"] == hw)), None)
            if t:
                chosen.append((label, t["id"]))
        return [(label, sid) for label, sid in chosen if sid]

    def _update_view(self, state):
        limits = self.hello.get("limits", {})
        for sid, label in self.temp_tiles.items():
            v = state["temps"].get(sid)
            hot = isinstance(v, (int, float)) and v >= min(limits.get("gpu", 85), limits.get("cpu", 90)) - 5
            label.configure(text=f"{v:.0f}°C" if isinstance(v, (int, float)) else "—", fg=AMBER if hot else TEXT)
        for cid, card in self.cards.items():
            fid = self._fan_by_control(card.control)
            ctrl = state["controls"].get(cid, {})
            card.update_state(state["fans"].get(fid) if fid else None, ctrl.get("value"), ctrl.get("software"))
        if not state.get("emergency"):
            controls = self.hello.get("controls", [])
            soft = lambda c: state["controls"].get(c["id"], {}).get("software")
            gpus = [c for c in controls if c["kind"].startswith("Gpu")]
            driven = sum(1 for c in controls if soft(c) and not c["kind"].startswith("Gpu"))
            parts = [f"{driven} fan program tarafından kontrol ediliyor"]
            bios = sum(1 for cid, card in self.cards.items() if not card.control["kind"].startswith("Gpu")
                       and not soft(card.control))
            if bios:
                parts.append(f"{bios} fan BIOS kontrolünde")
            if gpus:
                parts.append("ekran kartı " + ("program tarafından kontrol ediliyor" if any(soft(c) for c in gpus)
                                               else "BIOS kontrolünde"))
            if self.full_warning:
                parts.append("bir fan 2 dakikadır tam hızda, eğriye ya da soğutmaya bak")
            missing = self.planner.missing_sources
            if missing:
                parts.append(f"eğri sensörü okunamadı ({len(missing)}), yerine işlemci ya da ekran kartı kullanılıyor: eğrinin kaynağını seç")
            self._status("Açık · " + ", ".join(parts)
                         + (" · arayüz sinyali gecikti, fanlar BIOS'ta" if state.get("stale") else ""),
                         AMBER if state.get("stale") or missing else GREEN)
