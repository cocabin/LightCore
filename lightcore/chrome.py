"""Çerçevesiz pencere: simge tepsisine küçültme, ekranı kaplama, sürükleme ve kenarlardan boyutlandırma.

Tk'nin override-redirect penceresi Windows'ta görev çubuğunda görünmez; aşağıdaki pencere stilleri
bunu açar. Küçült düğmesi pencereyi gizler, simge tepsisinden geri gelir. Sürükleme bilerek Windows'un modal taşıma döngüsüyle yapılmaz: o döngü
sürerken Tk olayları durur ve fan sinyali (FanHost, 10 sn) gecikebilir.
"""
import ctypes
import time
from ctypes import wintypes

from .dpi import S

GWL_STYLE, GWL_EXSTYLE = -16, -20
WS_MINIMIZEBOX, WS_SYSMENU = 0x00020000, 0x00080000
WS_EX_TOOLWINDOW, WS_EX_APPWINDOW, WS_EX_LAYERED = 0x00000080, 0x00040000, 0x00080000
LWA_ALPHA = 2
FADE_STEPS, FADE_STEP_SECONDS = 5, 0.012
DWMWA_WINDOW_CORNER_PREFERENCE, DWMWA_BORDER_COLOR, DWMWCP_ROUND = 33, 34, 2
GRIP = 5


class _MonitorInfo(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT),
                ("dwFlags", wintypes.DWORD)]


class _Blend(ctypes.Structure):
    _fields_ = [("op", wintypes.BYTE), ("flags", wintypes.BYTE), ("alpha", ctypes.c_ubyte), ("format", wintypes.BYTE)]


class _Ghost:
    """Ekranın bir bölgesinin görüntüsünü taşıyan, en üstte duran ve tıklamayı geçiren katmanlı pencere.
    Görüntü ekrandan doğrudan Windows'un belleğine kopyalanır (Python'dan piksel geçmez: 4K ekranda da hızlı);
    alpha() yalnızca saydamlığı değiştirir."""
    WS_POPUP, WS_EX_TOPMOST, WS_EX_TRANSPARENT, WS_EX_NOACTIVATE = 0x80000000, 0x00000008, 0x00000020, 0x08000000
    SRCCOPY, ULW_ALPHA, SW_SHOWNOACTIVATE = 0x00CC0020, 2, 4

    def __init__(self, x, y, w, h):
        user, gdi = ctypes.WinDLL("user32"), ctypes.WinDLL("gdi32")  # kendi kopyaları: imzalar başka modülü etkilemez
        handle, int_ = ctypes.c_void_p, ctypes.c_int
        for function, result, arguments in (
                (user.GetDC, handle, [handle]), (user.ReleaseDC, int_, [handle, handle]),
                (user.CreateWindowExW, handle, [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
                                                int_, int_, int_, int_, handle, handle, handle, handle]),
                (user.ShowWindow, int_, [handle, int_]), (user.DestroyWindow, int_, [handle]),
                (user.UpdateLayeredWindow, int_, [handle, handle, ctypes.POINTER(wintypes.POINT), ctypes.POINTER(wintypes.SIZE),
                                                  handle, ctypes.POINTER(wintypes.POINT), wintypes.DWORD,
                                                  ctypes.POINTER(_Blend), wintypes.DWORD]),
                (gdi.CreateCompatibleDC, handle, [handle]), (gdi.CreateCompatibleBitmap, handle, [handle, int_, int_]),
                (gdi.SelectObject, handle, [handle, handle]), (gdi.DeleteObject, int_, [handle]),
                (gdi.DeleteDC, int_, [handle]),
                (gdi.BitBlt, int_, [handle, int_, int_, int_, int_, handle, int_, int_, wintypes.DWORD])):
            function.restype, function.argtypes = result, arguments
        self.user, self.gdi = user, gdi
        self.position, self.size = wintypes.POINT(x, y), wintypes.SIZE(w, h)
        self.screen = user.GetDC(None)
        self.dc = gdi.CreateCompatibleDC(self.screen)
        self.bitmap = gdi.CreateCompatibleBitmap(self.screen, w, h)
        self.previous = gdi.SelectObject(self.dc, self.bitmap)
        self.hwnd = None
        try:
            if not gdi.BitBlt(self.dc, 0, 0, w, h, self.screen, x, y, self.SRCCOPY):
                raise OSError("ekran görüntüsü alınamadı")
            self.hwnd = user.CreateWindowExW(
                WS_EX_LAYERED | WS_EX_TOOLWINDOW | self.WS_EX_TOPMOST | self.WS_EX_TRANSPARENT | self.WS_EX_NOACTIVATE,
                "Static", None, self.WS_POPUP, x, y, w, h, None, None, None, None)
            if not self.hwnd or not self.alpha(255):
                raise OSError("kopya pencere açılamadı")
            user.ShowWindow(self.hwnd, self.SW_SHOWNOACTIVATE)
        except Exception:
            self.close()
            raise

    def alpha(self, value):
        blend = _Blend(0, 0, value, 0)
        return self.user.UpdateLayeredWindow(self.hwnd, self.screen, ctypes.byref(self.position), ctypes.byref(self.size),
                                             self.dc, ctypes.byref(wintypes.POINT(0, 0)), 0, ctypes.byref(blend), self.ULW_ALPHA)

    def close(self):
        if self.hwnd:
            self.user.DestroyWindow(self.hwnd)
            self.hwnd = None
        if self.dc:
            self.gdi.SelectObject(self.dc, self.previous)
            self.gdi.DeleteObject(self.bitmap)
            self.gdi.DeleteDC(self.dc)
            self.user.ReleaseDC(None, self.screen)
            self.dc = None


class Window:
    def __init__(self, root, min_size, on_state=lambda _maximized: None, on_visible=lambda _visible: None):
        self.root, self.min_size, self.on_state, self.on_visible = root, min_size, on_state, on_visible
        self.maximized = False
        self.hidden = False
        self._restore_geometry = None
        self._drag = None
        root.overrideredirect(True)
        root.update_idletasks()
        self.user32 = ctypes.windll.user32
        self.user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
        self.user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
        self.user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
        self.user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
        self.user32.SetLayeredWindowAttributes.argtypes = [wintypes.HWND, wintypes.DWORD, wintypes.BYTE, wintypes.DWORD]
        self.user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
        self.user32.MonitorFromWindow.restype = wintypes.HMONITOR
        self.user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.POINTER(_MonitorInfo)]
        self.hwnd = int(root.wm_frame(), 16)
        style = self.user32.GetWindowLongPtrW(self.hwnd, GWL_STYLE)
        self.user32.SetWindowLongPtrW(self.hwnd, GWL_STYLE, style | WS_MINIMIZEBOX | WS_SYSMENU)
        ex = self.user32.GetWindowLongPtrW(self.hwnd, GWL_EXSTYLE)
        self.user32.SetWindowLongPtrW(self.hwnd, GWL_EXSTYLE, (ex & ~WS_EX_TOOLWINDOW) | WS_EX_APPWINDOW)
        root.withdraw()  # görev çubuğu yeni stili ancak yeniden gösterilince görür
        root.deiconify()
        self._dwm()
        self._grips()

    def _dwm(self):
        """Windows 11: yuvarlak köşe ve ince kenarlık. Eski Windows'ta sessizce yok sayılır."""
        try:
            dwm = ctypes.windll.dwmapi
            for attribute, value in ((DWMWA_WINDOW_CORNER_PREFERENCE, DWMWCP_ROUND),
                                     (DWMWA_BORDER_COLOR, 0x003A3A3A)):
                data = ctypes.c_int(value)
                dwm.DwmSetWindowAttribute(wintypes.HWND(self.hwnd), attribute, ctypes.byref(data), ctypes.sizeof(data))
        except (AttributeError, OSError):
            pass

    def _grips(self):
        import tkinter as tk
        g = S(GRIP)
        self.grips = []
        for sides, cursor, place in (("w", "sb_h_double_arrow", dict(x=0, y=0, width=g, relheight=1)),
                                     ("e", "sb_h_double_arrow", dict(relx=1, x=-g, y=0, width=g, relheight=1)),
                                     ("n", "sb_v_double_arrow", dict(x=0, y=0, height=g, relwidth=1)),
                                     ("s", "sb_v_double_arrow", dict(x=0, rely=1, y=-g, height=g, relwidth=1)),
                                     ("se", "size_nw_se", dict(relx=1, rely=1, x=-3 * g, y=-3 * g, width=3 * g, height=3 * g))):
            grip = tk.Frame(self.root, bg=self.root["bg"], cursor=cursor)
            grip.bind("<Button-1>", lambda e, s=sides: self._begin_resize(e, s))
            grip.bind("<B1-Motion>", self._resize)
            grip.bind("<ButtonRelease-1>", self._end_resize)
            self.grips.append((grip, place))
        self.show_grips()

    def show_grips(self):
        for grip, place in self.grips:
            if self.maximized:
                grip.place_forget()
            else:
                grip.place(**place)
                grip.lift()

    def _begin_resize(self, e, sides):
        r = self.root
        self._drag = (sides, e.x_root, e.y_root, r.winfo_x(), r.winfo_y(), r.winfo_width(), r.winfo_height())
        self._target = None
        # Sürüklerken yalnızca saydam bir çerçeve hareket eder; yüzlerce denetim her fare adımında yeniden
        # dizilmez (Tk'de adım başına ~100 ms sürüyor ve takılıyor). Bırakınca pencere tek seferde boyutlanır.
        import tkinter as tk
        self._ghost = tk.Toplevel(r, bg="#4CC2FF")
        self._ghost.overrideredirect(True)
        self._ghost.attributes("-alpha", 0.25, "-topmost", True)
        self._ghost.geometry(r.geometry())

    def _resize(self, e):
        if not self._drag:
            return
        sides, x0, y0, x, y, w, h = self._drag
        dx, dy = e.x_root - x0, e.y_root - y0
        if "e" in sides:
            w = max(self.min_size[0], w + dx)
        if "s" in sides:
            h = max(self.min_size[1], h + dy)
        if "w" in sides:
            new = max(self.min_size[0], w - dx)
            x, w = x + w - new, new
        if "n" in sides:
            new = max(self.min_size[1], h - dy)
            y, h = y + h - new, new
        self._target = f"{w}x{h}+{x}+{y}"
        self._ghost.geometry(self._target)

    def _end_resize(self, _e):
        if self._drag:
            self._ghost.destroy()
            self._drag = None
            if self._target:
                self._snap(self._target)

    def _fade(self, start, end):
        """Windows'un kendi pencere geçişinin yerine kısa yumuşak geçiş (çerçevesiz pencerede o yoktur)."""
        # Tk'nin -alpha ayarı görev çubuğu stilini sıfırladığı için doğrudan Windows'tan yapılır.
        ex = self.user32.GetWindowLongPtrW(self.hwnd, GWL_EXSTYLE)
        self.user32.SetWindowLongPtrW(self.hwnd, GWL_EXSTYLE, ex | WS_EX_LAYERED)
        for i in range(1, FADE_STEPS + 1):
            self._alpha(start + (end - start) * i / FADE_STEPS)
            self.root.update_idletasks()
            time.sleep(FADE_STEP_SECONDS)
        if end == 1.0:
            self.user32.SetWindowLongPtrW(self.hwnd, GWL_EXSTYLE, ex & ~WS_EX_LAYERED)

    def _alpha(self, value):
        self.user32.SetLayeredWindowAttributes(self.hwnd, 0, round(255 * value), LWA_ALPHA)

    def _snapshot(self):
        """Pencerenin o anki görüntüsünü taşıyan kopya pencere; alınamazsa None."""
        try:
            r = self.root
            return _Ghost(r.winfo_rootx(), r.winfo_rooty(), r.winfo_width(), r.winfo_height())
        except Exception:  # görüntü alınamazsa eski yöntemle (solarak) boyutlanır
            return None

    def _snap(self, geometry):
        """Yarım çizilmiş hal de boş ekran da görünmesin: pencerenin görüntüsü üstte bir kopyada durur, asıl
        pencere altında görünmeden yeniden dizilir, sonra kopya solarak kalkar (eskiden pencere dizilme
        süresince tamamen kayboluyordu, bu takılma gibi görünüyordu)."""
        ghost = self._snapshot()
        if ghost is None:
            self._fade(1.0, 0.0)
            self.root.geometry(geometry)
            self.root.update()
            self._fade(0.0, 1.0)
            return
        try:
            ex = self.user32.GetWindowLongPtrW(self.hwnd, GWL_EXSTYLE)
            self.user32.SetWindowLongPtrW(self.hwnd, GWL_EXSTYLE, ex | WS_EX_LAYERED)
            self._alpha(0.0)
            try:
                self.root.geometry(geometry)
                self.root.update()
            finally:
                self._alpha(1.0)
                self.user32.SetWindowLongPtrW(self.hwnd, GWL_EXSTYLE, ex & ~WS_EX_LAYERED)
            self.root.update_idletasks()
            for i in range(1, FADE_STEPS + 1):
                ghost.alpha(round(255 * (1.0 - i / FADE_STEPS)))
                time.sleep(FADE_STEP_SECONDS)
        finally:
            ghost.close()

    # ---- sürükleme: başlık gibi davranan alanlara bağlanır
    def draggable(self, *widgets):
        for w in widgets:
            w.bind("<Button-1>", self._begin_move, add="+")
            w.bind("<B1-Motion>", self._move, add="+")
            w.bind("<Double-Button-1>", lambda _e: self.toggle_maximize(), add="+")

    def _begin_move(self, e):
        self._move_from = (e.x_root - self.root.winfo_x(), e.y_root - self.root.winfo_y())

    def _move(self, e):
        if self.maximized:  # ekranı kaplıyorken sürüklenince eski boyuta dön, imleç pencerede kalsın
            self.toggle_maximize()
            self._move_from = (self.root.winfo_width() // 2, S(20))
        self.root.geometry(f"+{e.x_root - self._move_from[0]}+{e.y_root - self._move_from[1]}")

    # ---- düğmeler
    def hide(self, fade=True):
        """Simge tepsisine iner: görev çubuğunda yer kaplamaz, hiçbir şey çizilmez."""
        if self.hidden:
            return
        if fade:
            self._fade(1.0, 0.0)
        self.hidden = True
        self.root.withdraw()
        self.on_visible(False)

    def show(self):
        if not self.hidden:
            self.root.lift()
            self.root.focus_force()
            return
        self.hidden = False
        ex = self.user32.GetWindowLongPtrW(self.hwnd, GWL_EXSTYLE)
        self.user32.SetWindowLongPtrW(self.hwnd, GWL_EXSTYLE, ex | WS_EX_LAYERED)
        self._alpha(0.0)
        self.root.deiconify()
        self.root.update()
        self._fade(0.0, 1.0)
        self.root.lift()
        self.root.focus_force()
        self.on_visible(True)

    def toggle_maximize(self):
        if self.maximized:
            self.maximized = False
            self._snap(self._restore_geometry)
        else:
            self._restore_geometry = self.root.geometry()
            info = _MonitorInfo(cbSize=ctypes.sizeof(_MonitorInfo))
            self.user32.GetMonitorInfoW(self.user32.MonitorFromWindow(self.hwnd, 2), ctypes.byref(info))
            area = info.rcWork  # görev çubuğunun üstüne taşmaz
            self.maximized = True
            self._snap(f"{area.right - area.left}x{area.bottom - area.top}+{area.left}+{area.top}")
        self.show_grips()
        self.on_state(self.maximized)
