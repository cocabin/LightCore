"""Programın ve alt süreçlerinin (OpenRGB, FanHost) toplam işlemci ve bellek kullanımı."""
import ctypes
import os
import time
from ctypes import wintypes


class _Memory(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD), ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t), ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t), ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t), ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t), ("PrivateUsage", ctypes.c_size_t)]


def set_efficiency(handle, on):
    """Windows'un verimlilik modu (Görev Yöneticisi'ndeki yaprak): süreç düşük güçte çalışır."""
    class State(ctypes.Structure):
        _fields_ = [("version", wintypes.ULONG), ("mask", wintypes.ULONG), ("state", wintypes.ULONG)]
    state = State(1, 1, 1 if on else 0)
    try:
        api = ctypes.windll.kernel32.SetProcessInformation
        api.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        api(handle, 4, ctypes.byref(state), ctypes.sizeof(state))
    except (AttributeError, OSError):
        pass  # eski Windows


def current_process():
    kernel = ctypes.windll.kernel32
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    return kernel.GetCurrentProcess()


class ProcessUsage:
    def __init__(self):
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.GetCurrentProcess.restype = wintypes.HANDLE
        self.kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
        self.kernel.K32GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(_Memory), wintypes.DWORD]
        self.last = {}  # ad -> toplam işlemci süresi
        self.last_time = time.monotonic()
        self.parts = {}  # ad -> son aralıktaki işlemci %

    def _read(self, handle):
        """(işlemci saniyesi, özel bellek baytı); okunamazsa None."""
        created, ended, kernel, user = (wintypes.FILETIME() for _ in range(4))
        if not self.kernel.GetProcessTimes(handle, created, ended, kernel, user):
            return None
        seconds = sum((t.dwHighDateTime << 32 | t.dwLowDateTime) / 1e7 for t in (kernel, user))
        memory = _Memory(cb=ctypes.sizeof(_Memory))
        self.kernel.K32GetProcessMemoryInfo(handle, memory, memory.cb)
        return seconds, memory.PrivateUsage

    def sample(self, children):
        """children: {ad: subprocess.Popen ya da None}. Dönüş: (son aralıktaki işlemci %, MB).
        Yüzde, Görev Yöneticisi gibi bütün çekirdeklerin toplamına göredir."""
        now = time.monotonic()
        elapsed = max(now - self.last_time, 0.001)
        self.last_time = now
        handles = {"self": self.kernel.GetCurrentProcess()}
        handles.update({k: int(p._handle) for k, p in children.items() if p is not None and p.poll() is None})
        used, memory, current, self.parts = 0.0, 0, {}, {}
        share = 100 / elapsed / (os.cpu_count() or 1)
        for name, handle in handles.items():
            reading = self._read(handle)
            if reading:
                current[name] = reading[0]
                delta = max(0.0, reading[0] - self.last.get(name, reading[0]))
                used += delta
                self.parts[name] = delta * share
                memory += reading[1]
        self.last = current
        return used * share, memory / (1024 * 1024)
