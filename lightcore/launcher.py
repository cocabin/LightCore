import os
import subprocess
import time


def default_fallbacks(path):
    """Verilen yol yoksa bakılacak yerler: zip iç içe açıldıysa bir üst klasördeki
    'openrgb' ve OpenRGB'nin kurulu olabileceği klasörler."""
    parent = os.path.dirname(os.path.dirname(os.path.abspath(path)))
    places = [os.path.join(parent, "openrgb")]
    for var in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
        base = os.environ.get(var)
        if base:
            places.append(os.path.join(base, "Programs", "OpenRGB") if var == "LOCALAPPDATA"
                          else os.path.join(base, "OpenRGB"))
    return places


def _search(path):
    if os.path.isfile(path):
        return path
    if os.path.isdir(path):
        for root, _, files in os.walk(path):
            for f in files:
                if f.lower() == "openrgb.exe":
                    return os.path.join(root, f)
    return None


def find_openrgb(path, fallbacks=None):
    """Dosya ya da klasör verilebilir; klasörse içinde (alt klasörler dahil) OpenRGB.exe arar.
    Bulamazsa fallbacks klasörlerine bakar."""
    tried = [path] + list(default_fallbacks(path) if fallbacks is None else fallbacks)
    for place in tried:
        found = _search(place)
        if found:
            return found
    raise FileNotFoundError(
        "OpenRGB.exe bulunamadi. Bakilan yerler:\n  " + "\n  ".join(tried) +
        "\nOpenRGB 'Windows portable' zip'ini bu programin yanindaki 'openrgb' klasorune ac.")


def start_openrgb(exe, port=6742):
    """OpenRGB'yi pencere açmadan sadece arka plan motoru olarak başlatır.
    Port yoklanmaz (boş bağlanıp kapatmak OpenRGB'de uyarı üretir); hazır olup
    olmadığını gerçek bağlantı denemesi belirler."""
    exe = find_openrgb(exe)
    print(f"Bulunan OpenRGB: {exe}")
    flags = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW
    try:
        proc = subprocess.Popen([exe, "--server", "--noautoconnect", "--server-port", str(port)],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                creationflags=flags)
    except OSError as e:
        raise RuntimeError(f"OpenRGB baslatilamadi: {e}")
    time.sleep(0.5)
    if proc.poll() is not None:
        raise RuntimeError("OpenRGB baslatilamadi (hemen kapandi)")
    return proc


def stop_openrgb(proc):
    if not proc:
        return
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
