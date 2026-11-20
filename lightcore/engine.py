"""Efekti cihazlara dağıtır. Üç yol vardır:

direct : tek tek LED rengi (Direct/Custom modu). RAM, ekran kartı, klavye.
static : yalnızca Static modu olan cihazlar için mod güncellemesi.
jrgb   : MSI anakartın fan/kasa çıkışları: JRGB (12V, adreslenemez) ve JRAINBOW /
         JARGB (5V ARGB fanlar). Bu bölgeler anakartın modunu izler. Bu yüzden
         (SDK protokol 6 gerekir):
         1) bölge modu -1 ("cihaz modunu devral") yapılır,
         2) anakartın Static moduna renk yazılır,
         3) bölgenin LED'lerine de aynı renk yazılır.
         ARGB bölgeleri OpenRGB'de 0 LED ile gelebilir; resize_fan_zones önce
         onlara LED sayısı verir, yoksa bu bölgelere hiç renk yazılamaz.
         Her yazış bir USB isteği olduğu için en sık SLOW_INTERVAL'da yazılır.
"""
import contextlib
import copy
import threading
import time

from .effects import scale
from .openrgb_client import MODE_FLAG_HAS_MODE_SPECIFIC_COLOR, MODE_FLAG_MANUAL_SAVE

SLOW_INTERVAL = 0.1  # anakart modu artık yalnızca bir kez yazıldığı için daha sık olabilir
FAN_HID_INTERVAL = 0.04  # saniyede ~25 kare geçişlerde gözle akıcıdır, fazlası boşa CPU'dur
NEAR = 2  # 0-255'te bu kadarlık fark gözle seçilmez: yavaş geçişlerde boşuna yazılmaz
KIND_INTERVALS = {"jrgb": SLOW_INTERVAL}
# Anakart ve RAM (OpenRGB türleri 0, 1) aynı SMBus hattındadır: tek sırada yazılır.
# RAM yazması ekran kartına göre çok yavaştır (yüzlerce ms olabilir). Ekran kartı bu yüzden kendi sırasındadır; RAM onu yavaşlatmaz.
BUS_DEVICE_KINDS = (0, 1)
# SMBus yazması OpenRGB'de işlemciyi meşgul eder: hat en fazla saniyede ~7 kez yazılır.
# Ekran kartı (I2C) saniyede ~15 kez: ikisi de LED geçişinde gözle akıcıdır.
BUS_INTERVAL = 0.15
GPU_KIND = 2
GPU_INTERVAL = 0.066
MAX_FAILURES = 5
FAN_ZONE_PREFIXES = ("jrgb", "jrainbow", "jargb")
ARGB_ZONE_PREFIXES = ("jrainbow", "jargb")
DEFAULT_ARGB_LEDS = 60


@contextlib.contextmanager
def high_res_timer(active=True):
    """Windows'ta bekleme süreleri varsayılan olarak ~15.6 ms'ye yuvarlanır; kareler
    düzensiz aralıklarla gider ve geçiş takılır gibi görünür. Hareketli efekt sürerken
    1 ms çözünürlük ister, biter bitmez geri verir (sabit renkte hiç istemez)."""
    winmm = None
    if active:
        try:
            import ctypes
            winmm = ctypes.windll.winmm
            winmm.timeBeginPeriod(1)
        except (AttributeError, OSError):
            winmm = None  # Windows değil
    try:
        yield
    finally:
        if winmm:
            winmm.timeEndPeriod(1)


def _mode(modes, name):
    return next((m for m in modes if m.name.strip().casefold() == name), None)


def _named(zone, prefixes):
    return zone.name.strip().casefold().startswith(prefixes)


def jrgb_zones(device):
    """Anakartın fan/kasa bölgeleri (JRGB, JRAINBOW, JARGB); LED'i olanlar."""
    return [z for z in device.zones if _named(z, FAN_ZONE_PREFIXES) and z.led_count > 0]


def empty_argb_zones(device):
    return [z for z in device.zones if _named(z, ARGB_ZONE_PREFIXES) and z.led_count == 0]


def resize_fan_zones(client, devices, leds=DEFAULT_ARGB_LEDS):
    """0 LED'li ARGB bölgelerine LED sayısı verir. Değişiklik olduysa True döner;
    çağıran cihaz listesini yeniden almalıdır."""
    changed = False
    for d in devices:
        for z in empty_argb_zones(d):
            size = max(leds, z.leds_min, 1)
            if z.leds_max:
                size = min(size, z.leds_max)
            client.resize_zone(d.controller_id, z, size)
            print(f"{d.name}: {z.name} bölgesi 0 LED'di, {size} LED olarak ayarlandı.")
            changed = True
    return changed


def _plan(client, devices, skip_jrgb=False):
    """skip_jrgb: anakartın fanları HID ile sürülüyorsa OpenRGB o karta hiç dokunmaz."""
    plan = {}
    for d in devices:
        zones = jrgb_zones(d)
        if skip_jrgb and (zones or empty_argb_zones(d)):
            continue  # anakart: fan/kasa çıkışlarına hiç dokunulmaz
        if zones:
            if client.proto < 6:
                raise RuntimeError(f"{d.name}: fan/kasa bölgeleri için OpenRGB SDK protokol 6 gerekir "
                                   f"(bağlantı protokol {client.proto} kullanıyor).")
            items = [(z, _mode(z.modes, "static")) for z in zones]
            static = _mode(d.modes, "static")
            inherit = [z for z, zs in items if zs is None]
            if inherit and static is None:
                raise RuntimeError(f"{d.name}: cihaz düzeyinde Static modu bulunamadı.")
            for z in inherit:
                client.update_zone_mode(d.controller_id, z, None)  # modu devral
            plan[d.controller_id] = ("jrgb", {"static": static, "items": items, "mode_sent": False})
        elif d.led_count <= 0:
            continue
        elif _mode(d.modes, "direct") or _mode(d.modes, "custom") or not _mode(d.modes, "static"):
            client.set_custom_mode(d.controller_id)
            plan[d.controller_id] = ("direct", None)
        else:
            plan[d.controller_id] = ("static", _mode(d.modes, "static"))
    return plan


def _send(client, d, kind, extra, rgb):
    cid = d.controller_id
    if kind == "direct":
        client.set_leds(cid, [rgb] * d.led_count)
    elif kind == "static":
        client.update_mode(cid, extra, rgb)
    else:
        inherit = []
        for zone, zone_static in extra["items"]:
            if zone_static:
                client.update_zone_mode(cid, zone, zone_static, rgb)
            else:
                inherit.append(zone)
        if inherit:
            # Anakart modunu her karede yeniden yazmak fanları yanıp söndürüyordu:
            # mod yalnızca ilk karede yazılır, sonra sadece bölge LED'leri güncellenir.
            if not extra["mode_sent"]:
                client.update_mode(cid, extra["static"], rgb)
                extra["mode_sent"] = True
            for zone in inherit:
                client.update_zone_leds(cid, zone, [rgb] * zone.led_count)


def _near(a, b):
    return b is not None and all(abs(x - y) <= NEAR for x, y in zip(a, b))


def _count(stats, key, began):
    if stats is not None:
        end = time.monotonic()
        s = stats.setdefault(key, [0, 0.0, 0.0])
        s[0] += 1
        s[1] = end
        s[2] = max(s[2], end - began)


def apply(client, devices, effect, fps=10.0, brightness=1.0, stop=None,
          fans=None, fan_headers=(1, 2), once=False, skip_board=False,
          min_interval=0.0, stats=None, overrides=None):
    """Efekti cihazlara uygular. Renk değişmediyse hiçbir şey göndermez.

    fans: apply_many({başlık: rgb}) olan HID nesnesi (MsiJRainbow). Verilirse
    anakart fanları ona yazılır (saniyede en fazla 1/FAN_HID_INTERVAL kez) ve
    OpenRGB anakarta hiç dokunmaz. once: efekt sabitse renk her yere gidince döner
    (iş parçacığı bitsin, hiç CPU harcanmasın).
    skip_board: MSI anakart (fan/kasa çıkışı) OpenRGB ile hiç sürülmez.
    min_interval: her cihaza en az bu kadar arayla yazılır (yavaş SMBus/I2C cihazları).
    overrides: {controller_id ya da "fans": rgb}: bu cihazlar efekti izlemez, kendi sabit rengini alır.
    stats: verilirse {controller_id ya da "fans": [yazma sayısı, son yazma zamanı,
    en uzun yazma süresi]} güncellenir (arayüzdeki tanı bilgisi için).
    """
    stop = stop or threading.Event()
    plan = _plan(client, devices, skip_jrgb=skip_board or fans is not None)
    active = [d for d in devices if d.controller_id in plan]
    intervals = {d.controller_id: max(min_interval, KIND_INTERVALS.get(plan[d.controller_id][0], 0.0))
                 for d in active}
    overrides = overrides or {}
    sent = {}  # controller_id -> (son yazılan renk, zamanı)
    fan_state = (None, -1e9)
    start = next_frame = time.monotonic()
    while not stop.is_set():
        now = time.monotonic()
        rgb = scale(effect.at(now - start), brightness)
        # Renk durulduysa (sabit ya da bekleme) tam değeri yazılır; geçiş sırasında küçük farklar atlanır.
        settled = effect.is_static or effect.steady_for(now - start) > 0
        for d in active:
            if stop.is_set():
                return  # yavaş cihazlar varken durma isteği bir kareyi beklemesin
            cid = d.controller_id
            kind, extra = plan[cid]
            last_rgb, last_time = sent.get(cid, (None, -1e9))
            color = overrides.get(cid, rgb)
            if color == last_rgb or now - last_time < intervals[cid] or (not settled and _near(color, last_rgb)):
                continue
            sent[cid] = (color, now)
            began = time.monotonic()
            _send(client, d, kind, extra, color)
            _count(stats, cid, began)
        fan_color = overrides.get("fans", rgb)
        if fans is not None and fan_color != fan_state[0] and now - fan_state[1] >= FAN_HID_INTERVAL \
                and (settled or not _near(fan_color, fan_state[0])):
            began = time.monotonic()
            fans.apply_many({h: fan_color for h in fan_headers})
            _count(stats, "fans", began)
            fan_state = (fan_color, now)
        pending = settled and (any(sent.get(d.controller_id, (None,))[0] != overrides.get(d.controller_id, rgb) for d in active)
                               or (fans is not None and fan_state[0] != fan_color))
        if effect.is_static:
            # Hız sınırı yüzünden son renk henüz gitmediyse kısa bekle; gittiyse iş bitti.
            if pending:
                stop.wait(max([FAN_HID_INTERVAL, *intervals.values()]))
            elif once:
                return
            else:
                stop.wait()
        else:
            steady = effect.steady_for(now - start) if not pending else 0.0
            if steady > 0:  # renk bir süre değişmeyecek (Renk geçişinde bekleme): uyu
                stop.wait(steady)
                next_frame = time.monotonic()
                continue
            next_frame += 1.0 / max(1.0, fps)
            delay = next_frame - time.monotonic()
            if delay < 0:
                # Geride kaldık (yavaş cihaz): kaçan kareleri peş peşe yollamak
                # renkte zıplama yapar; saati bugüne çekip sıradaki kareden devam et.
                next_frame, delay = time.monotonic(), 0.0
            stop.wait(delay)


def _drive_sink(sink, effect, brightness, stop, stats):
    """Yavaş hedef (Hue): renk değiştiyse en fazla sink.interval'de bir yazar. Lamba
    aradaki geçişi kendisi yumuşatır (geçiş süresi = interval)."""
    interval = sink.interval
    resend = getattr(sink, "resend", None)  # yanıtsız yazan hedef (Bluetooth şerit): durulan renk bir kez yinelenir
    start = next_write = time.monotonic()
    last_rgb, confirmed = None, True
    while not stop.is_set():
        now = time.monotonic()
        rgb = scale(effect.at(now - start), brightness)
        if rgb != last_rgb and now >= next_write:
            began = time.monotonic()
            sink.write(rgb)
            _count(stats, sink.key, began)
            last_rgb, confirmed = rgb, False
            # Yazmalar eşit aralıkla gider: sıradaki yazma, bu yazmanın ne kadar sürdüğüne göre değil saate göre
            # planlanır (düzensiz aralık geçişte takılma gibi görünür). Belirgin gecikince saat bugüne çekilir.
            next_write = next_write + interval if began - next_write < interval / 2 else began + interval
        now = time.monotonic()
        steady = effect.steady_for(now - start)
        if rgb == last_rgb and (effect.is_static or steady > 0):
            # Geçiş bitince: sık yazılan komutlardan sonuncusu kaçtıysa şerit ara renkte kalmasın.
            if resend and not confirmed and not effect.is_static:
                if stop.wait(interval):
                    return
                resend()
                confirmed = True
                next_write = time.monotonic() + interval
            if effect.is_static:
                return  # son renk gitti; lamba onu kendisi tutar
            stop.wait(max(effect.steady_for(time.monotonic() - start), 0.001))
        else:
            # Yazılacak renk varsa sıradaki yazma anına kadar; renk henüz değişmediyse kısa bir süre (boşa dönmesin).
            stop.wait(max(next_write - now, 0.002 if rgb != last_rgb else interval / 4))


DROPPED_TEXT = "bu cihaz bırakıldı"


def apply_parallel(clients, devices, effect, fps=50.0, brightness=1.0, stop=None,
                   fans=None, fan_headers=(1, 2), on_error=None, stats=None, sinks=None, overrides=None):
    """Her cihazı (ve HID fanları) kendi iş parçacığında sürer; yavaş bir cihaz
    (ör. RAM) diğerlerini bekletmez. clients: {controller_id: OpenRGBClient}.
    Aynı bağlantıyı paylaşan cihazlar tek iş parçacığında birlikte sürülür, çünkü
    bir bağlantıyı aynı anda iki iş parçacığı kullanamaz. stop kurulunca döner.

    Bir grup hata verirse durmaz: hata bildirilir, bağlantı yenilenir ve yeniden
    denenir; MAX_FAILURES kez üst üste hata olursa o grup bırakılır.
    on_error(grup, metin): grup bir cihaz listesi ya da "fans" / bir sink'in key'i.
    sinks: write(rgb), interval ve key olan yavaş hedefler (ör. Philips Hue).
    Efekt sabitse renk her yere gidince bütün iş parçacıkları biter ve bu işlev döner:
    donanım rengi kendisi tutar, program hiç iş yapmaz."""
    stop = stop or threading.Event()
    groups = {}
    for d in devices:
        client = clients[d.controller_id]
        groups.setdefault(id(client), (client, []))[1].append(d)

    def report(group, text):
        if on_error:
            on_error(group, text)

    def guarded(work, group, reconnect=None):
        """work() stop'a kadar çalışır; hata olursa bildirir, yeniden dener."""
        failures = 0
        while not stop.is_set():
            began = time.monotonic()
            try:
                work()
                return
            except Exception as e:  # bir cihazın hatası diğerlerini durdurmasın
                if stop.is_set():
                    return  # durdurulurken gelen hata: yeniden bağlanmaya gerek yok
                failures = 1 if time.monotonic() - began > 10 else failures + 1
                if failures >= MAX_FAILURES:
                    report(group, f"{e} ({MAX_FAILURES} kez üst üste hata, {DROPPED_TEXT})")
                    return
                report(group, f"{e} (yeniden deneniyor)")
                if stop.wait(1.0):
                    return
                if reconnect:
                    try:  # zaman aşımından sonra bağlantıda eski yanıtlar kalabilir
                        reconnect()
                    except OSError:
                        pass

    def run_group(client, group):
        if any(d.kind in BUS_DEVICE_KINDS for d in group):
            interval = BUS_INTERVAL
        else:
            interval = GPU_INTERVAL if any(d.kind == GPU_KIND for d in group) else 0.0

        def reconnect():
            client.close()
            client.connect(timeout=3, command_timeout=3.0)

        guarded(lambda: apply(client, group, copy.copy(effect), fps, brightness, stop, once=True,
                              min_interval=interval, stats=stats, overrides=overrides),
                group, reconnect)

    def run_fans():
        guarded(lambda: apply(None, [], copy.copy(effect), fps, brightness, stop, once=True,
                              fans=fans, fan_headers=fan_headers, stats=stats, overrides=overrides), "fans")

    def run_sink(sink):
        guarded(lambda: _drive_sink(sink, copy.copy(effect), brightness, stop, stats), sink.key)

    threads = [threading.Thread(target=run_group, args=(client, group), daemon=True)
               for client, group in groups.values()]
    if fans is not None:
        threads.append(threading.Thread(target=run_fans, daemon=True))
    for sink in sinks or ():
        threads.append(threading.Thread(target=run_sink, args=(sink,), daemon=True))
    with high_res_timer(not getattr(effect, "settles", effect.is_static)):
        for t in threads:
            t.start()
        for t in threads:
            t.join()


def hold_color(client, device, rgb, save=False):
    """Program kapanırken: cihaz son rengi yazılımsız tutsun. Direct renkleri zaten yazılı;
    Static modu olan cihaz o moda alınır (Direct'te sürekli komut bekleyen cihazlar sönmesin).
    save: cihaz destekliyorsa kendi belleğine de kaydeder, bilgisayar kapanınca da kalsın.
    Dönüş: kaydedildiyse True."""
    static = _mode(device.modes, "static")
    if static is None or not static.flags & MODE_FLAG_HAS_MODE_SPECIFIC_COLOR:
        return False
    client.update_mode(device.controller_id, static, rgb)
    if save and static.flags & MODE_FLAG_MANUAL_SAVE:
        client.save_mode(device.controller_id, static, rgb)
        return True
    return False
