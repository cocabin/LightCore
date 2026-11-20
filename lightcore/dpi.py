"""Ekran ölçeği (%125, %150...): program Windows'a ölçeği bildiğini söyler, böylece pencere
bulanık büyütülmez; arayüz ölçüleri de gerçek piksele çevrilir.

Yazı tipleri (punto) Tk tarafından zaten ölçeklenir. Pencere düzenindeki boşluklar ve ölçüler
install() ile merkezî olarak çevrilir, kodda 100% ölçeğe göre yazılır. Canvas'ların ölçüleri ve
içine çizilenler gerçek piksel olduğu için yalnızca oralarda S() kullanılır.
"""
import tkinter as tk


def _detect_scale():
    try:
        import ctypes
        try:
            # Ekran başına: koordinatlar her ekranda gerçek piksel, ekran görüntüsü bulanık olmaz. Arayüz boyutu
            # ana ekrana göre kalır; ölçeği farklı ekranda biraz büyük ya da küçük ama keskin görünür.
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except OSError:
            pass  # zaten ayarlanmış
        return ctypes.windll.user32.GetDpiForSystem() / 96
    except (AttributeError, OSError):
        return 1.0  # Windows değil


SCALE = _detect_scale()


def S(value):
    """100% ölçekteki ölçüyü gerçek piksele çevirir; (sol, sağ) gibi çiftler de olur."""
    if isinstance(value, (tuple, list)):
        return tuple(S(v) for v in value)
    return round(value * SCALE)


_PADDING = ("padx", "pady", "ipadx", "ipady")
_WIDGET_OPTIONS = {"frame": ("width", "height", "padx", "pady"), "label": ("padx", "pady", "wraplength"),
                   "text": ("padx", "pady")}


def _scaled(options, keys):
    return {k: S(v) if k in keys else v for k, v in options.items()}


def install():
    if SCALE == 1.0:
        return
    pack, grid, init = tk.Pack.pack_configure, tk.Grid.grid_configure, tk.BaseWidget.__init__

    def pack_configure(self, cnf={}, **kw):
        pack(self, cnf, **_scaled(kw, _PADDING))

    def grid_configure(self, cnf={}, **kw):
        grid(self, cnf, **_scaled(kw, _PADDING))

    def widget_init(self, master, widget_name, cnf={}, kw={}, extra=()):
        keys = _WIDGET_OPTIONS.get(widget_name)
        init(self, master, widget_name, _scaled(cnf, keys) if keys else cnf, _scaled(kw, keys) if keys else kw, extra)

    tk.Pack.pack_configure = tk.Pack.pack = pack_configure
    tk.Grid.grid_configure = tk.Grid.grid = grid_configure
    tk.BaseWidget.__init__ = widget_init
