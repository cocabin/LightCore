"""Fan kontrolü: FanHost.exe ile konuşur ve her fan için istenen yüzdeyi hesaplar.

FanHost.exe (FanHost.cs) LibreHardwareMonitor'ü kullanır; kütüphane FanControl'ün
kurulu olduğu klasörden alınır. İlk kullanımda Windows'un kendi C# derleyicisiyle
(csc.exe, .NET Framework 4) derlenir; hiçbir şey indirilmez.

Güvenlik kuralları (en düşük hız, sıcaklık acil durumu, arayüz susarsa BIOS'a dönüş)
FanHost'un içindedir. Bu modül sadece istenen değerleri hesaplar ve 3 saniyede bir yollar;
bu yollama aynı zamanda "arayüz yaşıyor" sinyalidir.
"""
import json
import os
import subprocess
import threading
import time

from .usage import set_efficiency

LHM_DLL = "LibreHardwareMonitorLib.dll"
PAWNIO_URL = "https://pawnio.eu"
CURVES = {  # (sıcaklık °C, fan %) noktaları
    "Sessiz": [(40, 25), (60, 35), (75, 60), (85, 100)],
    "Dengeli": [(40, 30), (55, 45), (70, 70), (80, 100)],
    "Performans": [(35, 45), (50, 60), (65, 85), (75, 100)],
}
# Oyun eğrisi: sessiz başlar, ısındıkça hava verir; %100 yalnızca gerçekten sıcakken. Eski eğri (72°C'de %60,
# 80°C'de %75) oyunda fanları gereksiz hızlandırıyordu; bu eğri Dengeli/kullanıcı eğrilerinin biraz önünde gider.
GAME_CURVE = [(40, 25), (60, 30), (70, 40), (78, 52), (84, 70), (90, 100)]
# Ryzen'in anlık sıcaklık sıçramaları (birkaç saniyelik +5-8°C) fanı oynatmasın: 4°C ve 10 sn.
GAME_HYSTERESIS = {"up": 4, "down": 3, "response_up": 10, "response_down": 20, "ignore_at_limits": True}
RAMP_DOWN_PER_SECOND = 3.0  # eğride fan yavaşça iner (ani sessizleşme/hızlanma olmasın)


def game_curve_of(custom):
    """Kullanılacak oyun eğrisi: kullanıcınınki geçerliyse o (eksik histerezis değerleri varsayılandan), yoksa
    varsayılan. Dönüş: {"points": [(°C, %)...], "hysteresis": {...}}."""
    points = (custom or {}).get("points") or []
    try:
        points = sorted((float(t), float(p)) for t, p in points)
    except (TypeError, ValueError):
        points = []
    if len(points) < 2:
        points = list(GAME_CURVE)
    return {"points": points, "hysteresis": dict(GAME_HYSTERESIS, **((custom or {}).get("hysteresis") or {}))}


CURVE_TYPES = {"graph": "Grafik", "linear": "Doğrusal", "flat": "Sabit", "sync": "Eşitle",
               "mix": "Karışım", "trigger": "Tetik"}
MIX_FUNCTIONS = {"max": "En yüksek", "min": "En düşük", "avg": "Ortalama", "sum": "Toplam"}
CREATE_NO_WINDOW = 0x08000000


class FanError(RuntimeError):
    pass


def curve_value(points, temp):
    """Noktalar arasında doğrusal; ilk noktanın altında ilk, sonun üstünde son değer."""
    points = sorted(points)
    if temp <= points[0][0]:
        return float(points[0][1])
    for (t1, v1), (t2, v2) in zip(points, points[1:]):
        if temp <= t2:
            return v1 + (v2 - v1) * (temp - t1) / (t2 - t1)
    return float(points[-1][1])


def find_lhm(extra_dirs=()):
    """LibreHardwareMonitorLib.dll'in bulunduğu klasör: önce verilen klasörler (programın kendi 'fan' klasörü),
    sonra kurulu FanControl; yoksa None."""
    dirs = list(extra_dirs)
    for var, sub in (("ProgramFiles(x86)", "FanControl"), ("ProgramFiles", "FanControl"),
                     ("LOCALAPPDATA", os.path.join("Programs", "FanControl"))):
        base = os.environ.get(var)
        if base:
            dirs.append(os.path.join(base, sub))
    for d in dirs:
        if os.path.isfile(os.path.join(d, LHM_DLL)):
            return d
    return None


def fancontrol_running():
    try:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq FanControl.exe", "/NH"], capture_output=True,
                             text=True, errors="replace", timeout=10, creationflags=CREATE_NO_WINDOW).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return "fancontrol.exe" in out.lower()


def pawnio_installed(key=r"SYSTEM\CurrentControlSet\Services\PawnIO"):
    """LibreHardwareMonitor anakart yongasına ve işlemci sıcaklığına PawnIO sürücüsüyle ulaşır;
    sürücü pakette yoktur, ayrıca kurulur. Kurulu sürücü bir servis kaydı bırakır."""
    try:
        import winreg
        winreg.CloseKey(winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key))
        return True
    except OSError:
        return False


def is_admin():
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        return False


def _csc():
    windir = os.environ.get("WINDIR", r"C:\Windows")
    for fw in ("Framework64", "Framework"):
        path = os.path.join(windir, "Microsoft.NET", fw, "v4.0.30319", "csc.exe")
        if os.path.isfile(path):
            return path
    raise FanError("Windows'un C# derleyicisi (.NET Framework 4 csc.exe) bulunamadı.")


def build_host(out_dir, lib_dir):
    """FanHost.exe'yi derler (kaynak değişmediyse yeniden derlemez). Yolunu döndürür."""
    source = os.path.join(os.path.dirname(os.path.abspath(__file__)), "FanHost.cs")
    exe = os.path.join(out_dir, "FanHost.exe")
    bundled = os.path.join(lib_dir, "FanHost.exe")
    if os.path.isfile(bundled):  # hazır pakette önceden derlenmiş gelir
        return bundled
    if os.path.isfile(exe) and os.path.getmtime(exe) >= os.path.getmtime(source):
        return exe
    os.makedirs(out_dir, exist_ok=True)
    cmd = [_csc(), "/nologo", "/target:exe", "/platform:x64", "/optimize", f"/out:{exe}",
           f"/reference:{os.path.join(lib_dir, LHM_DLL)}", "/reference:System.Web.Extensions.dll", source]
    r = subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=120,
                       creationflags=CREATE_NO_WINDOW)
    if r.returncode or not os.path.isfile(exe):
        raise FanError("FanHost derlenemedi:\n" + (r.stdout + r.stderr).strip()[-1500:])
    return exe


class FanHostProcess:
    """FanHost.exe'yi başlatır; gelen her JSON satırı on_message'a verilir (okuyucu iş
    parçacığından). Süreç bitince {"type": "exit", ...} gelir."""

    def __init__(self, exe, lib_dir, on_message):
        self.on_message = on_message
        self.proc = subprocess.Popen([exe, lib_dir], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
                                     bufsize=1, creationflags=CREATE_NO_WINDOW)
        self._lock = threading.Lock()
        set_efficiency(int(self.proc._handle), True)  # 3 sn'de bir kısa iş: düşük güç yeter
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        for line in self.proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                self.on_message(json.loads(line))
            except ValueError:
                self.on_message({"type": "error", "message": f"FanHost'tan anlaşılmayan satır: {line[:200]}"})
        code = self.proc.wait()
        err = self.proc.stderr.read() if self.proc.stderr else ""
        self.on_message({"type": "exit", "code": code, "stderr": err.strip()[-1500:]})

    def send(self, obj):
        with self._lock:
            try:
                self.proc.stdin.write(json.dumps(obj) + "\n")
                self.proc.stdin.flush()
            except (OSError, ValueError):
                pass  # süreç bitti; okuyucu "exit" bildirir

    def set_values(self, values):
        self.send({"cmd": "set", "values": values})

    def stop(self, timeout=5.0):
        """Kibarca durdurur (FanHost fanları BIOS'a geri verir); olmazsa zorla kapatır."""
        self.send({"cmd": "quit"})
        try:
            self.proc.stdin.close()
        except OSError:
            pass
        try:
            self.proc.wait(timeout)
        except subprocess.TimeoutExpired:
            self.proc.kill()


class Calibration:
    """Bir fanı %100'den en az hıza adım adım indirir, her adımda devri ölçer. Güvenlik sınırının
    altı denenmez (FanHost zaten indirmez). tick() her durum mesajında çağrılır."""
    FIRST_WAIT, STEP_WAIT, STEP, SPINNING_RPM = 12.0, 6.0, 10, 50

    def __init__(self, floor, now):
        self.floor = max(1, int(floor))
        self.steps = list(range(100, self.floor, -self.STEP)) + [self.floor]
        self.index, self.since = 0, now
        self.points = []
        self.done = False

    @property
    def percent(self):
        return self.steps[self.index]

    def tick(self, now, rpm):
        """Bu turda fana yazılacak yüzde; bittiyse None."""
        wait = self.FIRST_WAIT if self.index == 0 else self.STEP_WAIT
        if now - self.since >= wait:
            self.points.append([self.percent, rpm])
            self.index, self.since = self.index + 1, now
            if self.index == len(self.steps):
                self.done = True
                return None
        return self.percent

    def result(self):
        """{"points", "max_rpm", "min_rpm", "min_spin"}; devir hiç okunamadıysa None."""
        spinning = [(p, r) for p, r in self.points if isinstance(r, (int, float)) and r > self.SPINNING_RPM]
        if not spinning:
            return None
        return {"points": self.points, "max_rpm": max(r for _, r in spinning),
                "min_rpm": min(r for _, r in spinning), "min_spin": min(p for p, _ in spinning)}


class FanPlanner:
    """Her fan için ayardan (otomatik / sabit / eğri) istenen yüzdeyi hesaplar.
    settings: {kontrol_id: {"mode": "auto"|"fixed"|"curve", "value": 50,
                            "source": "cpu"|"gpu", "curve": "Dengeli"}}"""

    def __init__(self, hello):
        temps = hello.get("temps", [])
        self.sources = {"cpu": self._pick_cpu(temps), "gpu": self._pick_gpu(temps)}
        self.controls = {c["id"]: c for c in hello.get("controls", [])}
        self._last = {}  # kontrol_id -> (değer, zaman): adım sınırı (hızlanma/yavaşlama) için
        self._outputs = {}  # kontrol_id -> son değer: "Eşitle" eğrileri başka fanı izler
        self._curve_state = {}  # eğri adı -> histerezis durumu
        self._trigger = {}  # eğri adı -> yük durumunda mı (Tetik eğrisi)
        self.missing_sources = set()  # okunamadığı için yerine işlemci/ekran kartı kullanılan sensörler

    @staticmethod
    def _pick_cpu(temps):
        cpu = [t for t in temps if t["kind"] == "Cpu"]
        for key in ("Tctl", "Package", "Core"):
            for t in cpu:
                if key in t["name"]:
                    return t["id"]
        return cpu[0]["id"] if cpu else None

    @staticmethod
    def _pick_gpu(temps):
        cores = [t for t in temps if t["kind"].startswith("Gpu") and t["name"] == "GPU Core"]
        # Ayrık ekran kartı öncelikli (işlemcinin içindeki Radeon(TM) Graphics değil).
        for kind in ("GpuNvidia", "GpuAmd", "GpuIntel"):
            for t in cores:
                if t["kind"] == kind and "Radeon(TM) Graphics" not in t["hardware"]:
                    return t["id"]
        return cores[0]["id"] if cores else None

    def source_temp(self, source, temps):
        """source: "cpu" / "gpu" kısayolu ya da bir sıcaklık sensörünün kimliği. Kimliği verilen sensör
        okunamazsa (başka bilgisayardan gelen ayar) aynı donanımın kısayolu kullanılır; o da yoksa None."""
        sid = self.sources.get(source, source)
        value = temps.get(sid) if sid else None
        if isinstance(value, (int, float)) and value > 0:
            return value
        if source in self.sources:
            return None
        self.missing_sources.add(source)
        return self.source_temp("gpu" if "gpu" in source.casefold() else "cpu", temps)

    # ---- eğriler
    def resolve_curve(self, name, curves, channel=None):
        """Kullanıcı eğrisi; yoksa hazır eğri (Sessiz/Dengeli/Performans, kanalın kaynağıyla)."""
        if name in curves:
            return curves[name]
        if name in CURVES:
            return {"type": "graph", "name": name, "points": CURVES[name],
                    "source": (channel or {}).get("source", "cpu")}
        return None

    def _hysteresis(self, name, curve, temp, now):
        """FanControl gibi: sıcaklık, kullanılan değerden en az "up"/"down" kadar ve
        "response_up"/"response_down" saniye boyunca farklı kalırsa yeni değer kullanılır.
        ignore_at_limits: eğrinin uçlarına ulaşınca beklemeden geçer."""
        h = curve.get("hysteresis") or {}
        st = self._curve_state.setdefault(name, {"temp": temp, "dir": None, "since": now})
        used = st["temp"]
        rising = temp > used and temp >= used + h.get("up", 0)
        falling = temp < used and temp <= used - h.get("down", 0)
        if h.get("ignore_at_limits", True) and curve.get("points"):
            temps = [p[0] for p in curve["points"]]
            if (rising and temp >= max(temps)) or (falling and temp <= min(temps)):
                st.update(temp=temp, dir=None)
                return temp
        if rising or falling:
            direction = "up" if rising else "down"
            wait = h.get("response_up" if rising else "response_down", 0)
            if st["dir"] != direction:
                st.update(dir=direction, since=now)
            if now - st["since"] >= wait:
                st.update(temp=temp, dir=None)
        else:
            st["dir"] = None
        return st["temp"]

    def curve_output(self, name, curves, temps, now, channel=None, stack=()):
        """Eğrinin o anki yüzdesi; hesaplanamazsa None. Sıcaklık okunamazsa 100 (güvenli)."""
        curve = self.resolve_curve(name, curves, channel)
        if curve is None or name in stack:  # yok ya da kendi kendine bağlı (döngü)
            return None
        kind = curve.get("type", "graph")
        if kind == "flat":
            return float(curve.get("value", 50))
        if kind == "sync":
            base = self._outputs.get(curve.get("control"))
            return None if base is None else base + curve.get("offset", 0)
        if kind == "mix":
            values = [self.curve_output(n, curves, temps, now, channel, stack + (name,)) for n in curve.get("curves", [])]
            values = [v for v in values if v is not None]
            if not values:
                return None
            fn = curve.get("function", "max")
            return {"min": min(values), "avg": sum(values) / len(values), "sum": sum(values)}.get(fn, max(values))
        temp = self.source_temp(curve.get("source", "cpu"), temps)
        if temp is None:
            return 100.0
        temp = self._hysteresis(name, curve, temp, now)
        if kind == "linear":
            return curve_value([(curve.get("min_temp", 40), curve.get("min_value", 30)),
                                (curve.get("max_temp", 80), curve.get("max_value", 100))], temp)
        if kind == "trigger":
            load = self._trigger.get(name, False)
            if temp >= curve.get("load_temp", 70):
                load = True
            elif temp <= curve.get("idle_temp", 50):
                load = False
            self._trigger[name] = load
            return float(curve.get("load_value" if load else "idle_value", 100 if load else 30))
        return curve_value(curve.get("points") or CURVES["Dengeli"], temp)

    def game_output(self, cid, channel, curves, temps, now, game_curve=None):
        """Oyun eğrisinin yüzdesi. İşlemci fanı işlemciye bakar; kasa fanları işlemci ve ekran kartından
        sıcak olana, çünkü oyunda ısınan çoğunlukla ekran kartıdır. Sıcaklık okunamazsa güvenli taraf: %100.
        game_curve: kullanıcının düzenlediği oyun eğrisi {"points", "hysteresis"}; yoksa GAME_CURVE."""
        name = f"{channel.get('name', '')} {self.controls[cid].get('name', '')}".casefold()
        sources = ("cpu",) if "cpu" in name else ("cpu", "gpu")
        readings = [self.source_temp(s, temps) for s in sources]
        readings = [v for v in readings if v is not None]
        if not readings:
            return 100.0
        curve = game_curve_of(game_curve)
        value = curve_value(curve["points"], self._hysteresis("__oyun_" + "_".join(sources), curve, max(readings), now))
        linked = curves.get(channel.get("curve"), {})  # kendi eğrisindeki fark korunur (ör. Eşitle +15)
        return value + (linked.get("offset", 0) if linked.get("type") == "sync" else 0)

    def desired(self, fan_cfg, temps, now=None, game=False):
        """fan_cfg: {"curves": [eğri, ...], "channels": {kontrol_id: kanal ayarı}}.
        Kanal: mode auto|game|curve, value, curve (ad), offset, step_up/step_down (%/sn),
        minimum, start, stop. Değerler: {kontrol_id: yüzde ya da None (BIOS)}.
        game: oyun modu açık: eğri kullanan fanlar oyun eğrisine geçer, Otomatik olanlar BIOS'ta kalır."""
        now = time.monotonic() if now is None else now
        self.missing_sources = set()
        curves = {c["name"]: c for c in fan_cfg.get("curves", []) if c.get("name")}
        channels = fan_cfg.get("channels", {})
        raw = {}
        for _pass in (0, 1):  # ikinci tur: başka fanı izleyen (Eşitle) eğriler bu turun değerlerini görsün
            for cid in self.controls:
                s = channels.get(cid) or {}
                mode = s.get("mode", "auto")
                if mode == "fixed":  # eski ayar: arayüz bunu Sabit eğriye çevirir
                    raw[cid] = float(s.get("value", 50))
                elif mode == "game" or mode == "curve":
                    if mode == "game" or game:
                        value = self.game_output(cid, s, curves, temps, now, fan_cfg.get("game_curve"))
                    else:
                        value = self.curve_output(s.get("curve", "Dengeli"), curves, temps, now, s)
                    if value is None and _pass:
                        value = 100.0  # eğri hesaplanamadı: güvenli taraf
                    if value is not None:
                        value = value + s.get("offset", 0)
                        if value < s.get("stop", 0):
                            value = 0.0  # FanControl "durma %": altında fan durdurulmak istenir (FanHost en aza çeker)
                        raw[cid] = max(min(100.0, value), float(s.get("minimum", 0)), 0.0)
                else:
                    raw[cid] = None
            if _pass == 0:
                self._outputs.update({k: v for k, v in raw.items() if v is not None})
        values = {}
        for cid in self.controls:
            s = channels.get(cid) or {}
            target = raw.get(cid)
            if target is None or s.get("mode", "auto") not in ("curve", "game"):
                self._last.pop(cid, None)
                values[cid] = None if target is None else round(target, 1)
                continue
            last = self._last.get(cid)
            if last:
                dt = max(0.0, now - last[1])
                up, down = s.get("step_up", 100.0), s.get("step_down", RAMP_DOWN_PER_SECOND)
                if target > last[0]:
                    if up < 100:  # 100 = sınırsız (hemen hızlan)
                        target = min(target, last[0] + up * max(dt, 1.0))
                else:
                    target = max(target, last[0] - down * dt)
            self._last[cid] = (target, now)
            self._outputs[cid] = target
            values[cid] = round(target, 1)
        return values
