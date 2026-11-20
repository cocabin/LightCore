"""Otomatik oyun modu algılama.

Windows "bu uygulama oyun" diyen hazır bir işaret vermediği için iki koşula bakılır: ön plandaki
pencere ekranı kaplıyor ve ekran kartı gerçekten çalışıyor. Pencere değişimini Windows bildirir;
tam ekran pencere yokken ekran kartı hiç sorgulanmaz ve iş parçacıkları uyur.
"""
import ctypes
import os
import threading
import time
from ctypes import wintypes

ENTER_LOAD, ENTER_SECONDS = 50.0, 10.0  # bu kadar yük, bu kadar süre: oyun
EXIT_LOAD, EXIT_SECONDS = 15.0, 30.0  # Alt+Tab'da hemen kapanmasın: OpenRGB'nin açılışı pahalı
TICK_SECONDS = 3.0
RECHECK_SECONDS = 30.0  # oyun önce pencereli açılıp sonra tam ekrana geçebilir
GPU_COUNTER = r"\GPU Engine(*engtype_3D)\Utilization Percentage"
EVENT_SYSTEM_FOREGROUND = 0x0003
SHELL_CLASSES = ("Progman", "WorkerW", "Shell_TrayWnd")

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
user32.MonitorFromWindow.restype = wintypes.HMONITOR
user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.c_void_p]
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                ctypes.POINTER(wintypes.DWORD)]
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
_WINEVENT = ctypes.WINFUNCTYPE(None, wintypes.HANDLE, wintypes.DWORD, wintypes.HWND, wintypes.LONG, wintypes.LONG,
                               wintypes.DWORD, wintypes.DWORD)
user32.SetWinEventHook.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.HMODULE, _WINEVENT, wintypes.DWORD,
                                   wintypes.DWORD, wintypes.DWORD]
user32.SetWinEventHook.restype = wintypes.HANDLE
user32.UnhookWinEvent.argtypes = [wintypes.HANDLE]
user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]


class _MonitorInfo(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT),
                ("dwFlags", wintypes.DWORD)]


class GameDetector:
    """Karar mantığı (Windows'a bağlı değil). update her turda çağrılır, oyun modunun durumunu döndürür."""

    def __init__(self):
        self.active = False
        self.since = None  # koşulun ilk sağlandığı ya da bozulduğu an

    def update(self, now, fullscreen, load):
        if self.active:
            playing = fullscreen and load >= EXIT_LOAD
        else:
            playing = fullscreen and load >= ENTER_LOAD
        if playing == self.active:
            self.since = None
            return self.active
        if self.since is None:
            self.since = now
        if now - self.since >= (EXIT_SECONDS if self.active else ENTER_SECONDS):
            self.active, self.since = playing, None
        return self.active


def foreground_fullscreen(excluded=()):
    """Ön plandaki pencere bir ekranı tamamen kaplıyorsa True. Masaüstü, bu program ve dışlananlar sayılmaz."""
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return False
    name = ctypes.create_unicode_buffer(64)
    user32.GetClassNameW(hwnd, name, 64)
    if name.value in SHELL_CLASSES:
        return False
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if pid.value == os.getpid():
        return False
    if excluded and process_name(pid.value) in excluded:
        return False
    rect, info = wintypes.RECT(), _MonitorInfo(cbSize=ctypes.sizeof(_MonitorInfo))
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    user32.GetMonitorInfoW(user32.MonitorFromWindow(hwnd, 2), ctypes.byref(info))
    m = info.rcMonitor
    return rect.left <= m.left and rect.top <= m.top and rect.right >= m.right and rect.bottom >= m.bottom


def process_name(pid):
    handle = kernel32.OpenProcess(0x1000, False, pid)  # yalnızca ad okuma izni
    if not handle:
        return ""
    try:
        size = wintypes.DWORD(1024)
        path = ctypes.create_unicode_buffer(size.value)
        if not kernel32.QueryFullProcessImageNameW(handle, 0, path, ctypes.byref(size)):
            return ""
        return os.path.basename(path.value).casefold()
    finally:
        kernel32.CloseHandle(handle)


class _CounterItem(ctypes.Structure):
    _fields_ = [("name", wintypes.LPWSTR), ("status", wintypes.DWORD), ("value", ctypes.c_double)]


class GpuLoad:
    """Windows'un kendi başarım sayacı: bütün ekran kartlarının 3B motoru (Görev Yöneticisi'ndeki değer)."""

    def __init__(self):
        self.pdh = ctypes.WinDLL("pdh")
        self.query, self.counter = wintypes.HANDLE(), wintypes.HANDLE()
        if self.pdh.PdhOpenQueryW(None, None, ctypes.byref(self.query)):
            raise OSError("başarım sayacı açılamadı")
        self.pdh.PdhAddEnglishCounterW.argtypes = [wintypes.HANDLE, wintypes.LPCWSTR, ctypes.c_size_t,
                                                   ctypes.POINTER(wintypes.HANDLE)]
        if self.pdh.PdhAddEnglishCounterW(self.query, GPU_COUNTER, 0, ctypes.byref(self.counter)):
            raise OSError("ekran kartı sayacı eklenemedi")
        self.pdh.PdhCollectQueryData(self.query)

    def read(self):
        self.pdh.PdhCollectQueryData(self.query)
        size, count = wintypes.DWORD(0), wintypes.DWORD(0)
        self.pdh.PdhGetFormattedCounterArrayW(self.counter, 0x200, ctypes.byref(size), ctypes.byref(count), None)
        if not size.value:
            return 0.0
        buffer = ctypes.create_string_buffer(size.value)
        if self.pdh.PdhGetFormattedCounterArrayW(self.counter, 0x200, ctypes.byref(size), ctypes.byref(count), buffer):
            return 0.0
        items = ctypes.cast(buffer, ctypes.POINTER(_CounterItem))
        return min(100.0, sum(items[i].value for i in range(count.value)))

    def close(self):
        self.pdh.PdhCloseQuery(self.query)


class GameWatcher:
    """on_change(True/False) algılama iş parçacığından çağrılır: kuyruğa koymalıdır."""

    def __init__(self, on_change, excluded=lambda: ()):
        self.on_change, self.excluded = on_change, excluded
        self.detector = GameDetector()
        self._wake = threading.Event()
        self._quit = False
        self._hook_thread_id = None
        threading.Thread(target=self._hook_loop, daemon=True).start()
        threading.Thread(target=self._worker, daemon=True).start()
        self._wake.set()  # açılışta zaten oyundaysa görülsün

    def stop(self):
        self._quit = True
        self._wake.set()
        if self._hook_thread_id:
            user32.PostThreadMessageW(self._hook_thread_id, 0x0012, 0, 0)

    def _hook_loop(self):
        self._hook_thread_id = kernel32.GetCurrentThreadId()
        self._callback = _WINEVENT(lambda *_a: self._wake.set())  # çöp toplayıcı silmesin
        hook = user32.SetWinEventHook(EVENT_SYSTEM_FOREGROUND, EVENT_SYSTEM_FOREGROUND, None, self._callback, 0, 0, 0)
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            pass
        if hook:
            user32.UnhookWinEvent(hook)

    def _worker(self):
        while not self._quit:
            self._wake.wait()  # pencere değişene kadar hiç çalışmaz
            self._wake.clear()
            self._watch()

    def _watch(self):
        """Tam ekran pencere ya da oyun modu sürdükçe yoklar, biter bitmez uykuya döner."""
        gpu = None
        deadline = time.monotonic() + RECHECK_SECONDS
        try:
            while not self._quit:
                now = time.monotonic()
                if self._wake.is_set():
                    self._wake.clear()
                    deadline = now + RECHECK_SECONDS
                fullscreen = foreground_fullscreen(self.excluded())
                if not fullscreen and not self.detector.active and self.detector.since is None and now > deadline:
                    return
                load = 0.0
                if fullscreen:
                    if gpu is None:
                        gpu = GpuLoad()
                    load = gpu.read()
                before = self.detector.active
                if self.detector.update(now, fullscreen, load) != before:
                    self.on_change(self.detector.active)
                time.sleep(TICK_SECONDS)
        except OSError:
            pass  # sayaç yoksa otomatik algılama çalışmaz; elle açma yine çalışır
        finally:
            if gpu:
                gpu.close()
