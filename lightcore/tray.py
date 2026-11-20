"""Simge tepsisi (saatin yanı) ve tek kopya koruması. Yalnızca Windows API'si.

Kendi gizli penceresi ve iş parçacığı vardır; GetMessage'da uyur, tıklanmadıkça işlemci kullanmaz.
Windows uyku bildirimi de buraya gelir: uykudan dönüşü anlamak için zamanlayıcı gerekmez.
"""
import ctypes
import threading
from ctypes import wintypes

from . import icons

CLASS_NAME = "LightCoreTray"
MUTEX_NAME = "LightCoreTekKopya"
WM_APP, WM_CLOSE, WM_DESTROY, WM_POWERBROADCAST = 0x8000, 0x0010, 0x0002, 0x0218
WM_TRAY, WM_SHOW, WM_GAME = WM_APP + 1, WM_APP + 2, WM_APP + 3
NIM_MODIFY = 1
GAME_CORE = (255, 90, 40)  # oyun modunda simgenin ortası turuncu olur
WM_LBUTTONUP, WM_RBUTTONUP = 0x0202, 0x0205
NIM_ADD, NIM_DELETE, NIF_MESSAGE, NIF_ICON, NIF_TIP = 0, 2, 1, 2, 4
PBT_RESUMES = (0x7, 0x12)
TPM_RIGHTBUTTON, TPM_RETURNCMD = 0x2, 0x100
ERROR_ALREADY_EXISTS = 183

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
user32 = ctypes.WinDLL("user32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)


class _WndClass(ctypes.Structure):
    _fields_ = [("style", wintypes.UINT), ("proc", WNDPROC), ("extra1", ctypes.c_int), ("extra2", ctypes.c_int),
                ("instance", wintypes.HINSTANCE), ("icon", wintypes.HICON), ("cursor", wintypes.HANDLE),
                ("brush", wintypes.HBRUSH), ("menu", wintypes.LPCWSTR), ("name", wintypes.LPCWSTR)]


class _NotifyIcon(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("hwnd", wintypes.HWND), ("id", wintypes.UINT), ("flags", wintypes.UINT),
                ("message", wintypes.UINT), ("icon", wintypes.HICON), ("tip", wintypes.WCHAR * 128),
                ("state", wintypes.DWORD), ("state_mask", wintypes.DWORD), ("info", wintypes.WCHAR * 256),
                ("version", wintypes.UINT), ("info_title", wintypes.WCHAR * 64), ("info_flags", wintypes.DWORD),
                ("guid", ctypes.c_byte * 16), ("balloon_icon", wintypes.HICON)]


user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.DefWindowProcW.restype = LRESULT
user32.RegisterClassW.argtypes = [ctypes.POINTER(_WndClass)]
user32.RegisterClassW.restype = wintypes.ATOM
user32.UnregisterClassW.argtypes = [wintypes.LPCWSTR, wintypes.HINSTANCE]
user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD] + [ctypes.c_int] * 4 \
    + [wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, ctypes.c_void_p]
user32.CreateWindowExW.restype = wintypes.HWND
user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
user32.FindWindowW.restype = wintypes.HWND
user32.CreateIcon.argtypes = [wintypes.HINSTANCE, ctypes.c_int, ctypes.c_int, wintypes.BYTE, wintypes.BYTE,
                              ctypes.c_char_p, ctypes.c_char_p]
user32.CreateIcon.restype = wintypes.HICON
user32.DestroyIcon.argtypes = [wintypes.HICON]
user32.CreatePopupMenu.restype = wintypes.HMENU
user32.AppendMenuW.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_size_t, wintypes.LPCWSTR]
user32.TrackPopupMenu.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                  wintypes.HWND, ctypes.c_void_p]
user32.DestroyMenu.argtypes = [wintypes.HMENU]
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.DestroyWindow.argtypes = [wintypes.HWND]
user32.RegisterWindowMessageW.argtypes = [wintypes.LPCWSTR]
user32.RegisterWindowMessageW.restype = wintypes.UINT
shell32.Shell_NotifyIconW.argtypes = [wintypes.DWORD, ctypes.POINTER(_NotifyIcon)]
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
kernel32.GetModuleHandleW.restype = wintypes.HMODULE
kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
kernel32.CreateMutexW.restype = wintypes.HANDLE


def _icon_handle(instance, core=icons.CORE):
    data = bytearray(icons.rgba("lightcore", 32, core))
    data[0::4], data[2::4] = data[2::4], data[0::4]  # RGBA -> BGRA
    return user32.CreateIcon(instance, 32, 32, 1, 32, bytes(128), bytes(data))


class Tray:
    """on_event("show" | "exit" | "resumed" | menü anahtarı) tepsi iş parçacığından çağrılır: kuyruğa koymalıdır.
    menu(): sağ tık menüsü [(yazı, anahtar), (yazı, [(yazı, anahtar), ...]) ya da None (ayraç)]."""

    def __init__(self, on_event, tip="LightCore", menu=None):
        self.on_event, self.tip = on_event, tip
        self.menu_items = menu or (lambda: [("Aç", "show"), None, ("Çıkış", "exit")])
        self.hwnd = None
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self._ready.wait(2)

    def stop(self):
        if self.hwnd:
            user32.PostMessageW(self.hwnd, WM_CLOSE, 0, 0)
            self._thread.join(1)

    def set_game(self, on):
        if self.hwnd:
            user32.PostMessageW(self.hwnd, WM_GAME, 1 if on else 0, 0)

    def _data(self):
        data = _NotifyIcon(size=ctypes.sizeof(_NotifyIcon), hwnd=self.hwnd, id=1, message=WM_TRAY,
                           flags=NIF_MESSAGE | NIF_ICON | NIF_TIP, icon=self.icon)
        data.tip = self.tip
        return data

    def _menu(self):
        keys = []

        def fill(menu, items):
            for item in items:
                if item is None:
                    user32.AppendMenuW(menu, 0x800, 0, None)  # ayraç
                elif isinstance(item[1], list):
                    sub_menu = user32.CreatePopupMenu()
                    fill(sub_menu, item[1])
                    user32.AppendMenuW(menu, 0x10, sub_menu, item[0])  # alt menü
                else:
                    keys.append(item[1])
                    user32.AppendMenuW(menu, 0, len(keys), item[0])

        menu = user32.CreatePopupMenu()
        fill(menu, self.menu_items())
        point = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(point))
        user32.SetForegroundWindow(self.hwnd)  # dışarı tıklanınca menü kapansın diye Windows bunu ister
        choice = user32.TrackPopupMenu(menu, TPM_RIGHTBUTTON | TPM_RETURNCMD, point.x, point.y, 0, self.hwnd, None)
        user32.DestroyMenu(menu)
        user32.PostMessageW(self.hwnd, 0, 0, 0)
        if choice:
            self.on_event(keys[choice - 1])

    def _proc(self, hwnd, message, wparam, lparam):
        if message == WM_TRAY:
            if lparam == WM_LBUTTONUP:
                self.on_event("show")
            elif lparam == WM_RBUTTONUP:
                self._menu()
            return 0
        if message == WM_GAME:
            old, self.icon = self.icon, _icon_handle(kernel32.GetModuleHandleW(None), GAME_CORE if wparam else icons.CORE)
            self.tip = "LightCore: oyun modu" if wparam else "LightCore"
            shell32.Shell_NotifyIconW(NIM_MODIFY, self._data())
            user32.DestroyIcon(old)
            return 0
        if message == WM_SHOW:
            self.on_event("show")
            return 0
        if message == WM_POWERBROADCAST:
            if wparam in PBT_RESUMES:
                self.on_event("resumed")
            return 1
        if message == self._taskbar_created:  # Gezgin yeniden başladı: simge yeniden eklenmeli
            shell32.Shell_NotifyIconW(NIM_ADD, self._data())
            return 0
        if message == WM_CLOSE:
            user32.DestroyWindow(hwnd)
            return 0
        if message == WM_DESTROY:
            shell32.Shell_NotifyIconW(NIM_DELETE, self._data())
            user32.PostQuitMessage(0)
            return 0
        return user32.DefWindowProcW(hwnd, message, wparam, lparam)

    def _run(self):
        instance = kernel32.GetModuleHandleW(None)
        self._callback = WNDPROC(self._proc)  # çöp toplayıcı silmesin
        user32.RegisterClassW(ctypes.byref(_WndClass(proc=self._callback, instance=instance, name=CLASS_NAME)))
        self._taskbar_created = user32.RegisterWindowMessageW("TaskbarCreated")
        self.icon = _icon_handle(instance)
        self.hwnd = user32.CreateWindowExW(0, CLASS_NAME, "LightCore", 0, 0, 0, 0, 0, None, None, instance, None)
        shell32.Shell_NotifyIconW(NIM_ADD, self._data())
        self._ready.set()
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.DispatchMessageW(ctypes.byref(msg))
        user32.DestroyIcon(self.icon)
        user32.UnregisterClassW(CLASS_NAME, instance)


def already_running():
    """Program zaten açıksa onu öne getirir ve True döner. Tanıtıcı bilerek kapatılmaz: süreç bitene kadar
    yaşar. İki kopya aynı anda cihazlara ve fanlara yazarsa birbirini bozar."""
    kernel32.CreateMutexW(None, False, MUTEX_NAME)
    if ctypes.get_last_error() != ERROR_ALREADY_EXISTS:
        return False
    hwnd = user32.FindWindowW(CLASS_NAME, None)
    if hwnd:
        user32.PostMessageW(hwnd, WM_SHOW, 0, 0)
    return True
