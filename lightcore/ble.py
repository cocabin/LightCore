"""Bluetooth LE RGB şerit: ELK-BLEDOM ailesi denetleyiciler (Lotus Lantern uygulaması).

- Komut: 0xFFF3 özelliğine şifresiz, yanıtsız 9 baytlık yazma. Windows'ta eşleştirme gerekmez.
- Bağlantı bir arka plan asyncio döngüsünde açık tutulur. Şeride ulaşılamazsa ya da bağlantı koparsa
  arka planda, aralığı uzayarak yeniden denenir ve bağlanınca son istenen renk yazılır.
- Denetleyici aynı anda tek bağlantı kabul eder: telefon uygulaması bağlıyken program bağlanamaz.
- bleak isteğe bağlıdır; kurulu değilse bu özellik görünmez. bleak ve asyncio yalnızca şerit
  kullanılınca yüklenir: şeridi olmayanın açılışını ve belleğini etkilemez.
"""
import threading
import time

WRITE_UUID = "0000fff3-0000-1000-8000-00805f9b34fb"
NAME_PREFIXES = ("ELK-BLE",)
# Geçişte saniyede bu kadar renk: 12'de adımlar gözle seçiliyordu. Yazmalar sırayla ve bitmesi beklenerek
# gider, kuyruk birikmez; denetleyici arada komut kaçırırsa durulan renk bir kez yinelenir (BleSink.resend).
COMMANDS_PER_SECOND = 20
CONNECT_TIMEOUT = 8.0  # şerit ~3 sn'de bağlanır; yoksa bu kadar beklenir
RETRY_FIRST, RETRY_MAX = 20.0, 120.0  # yeniden deneme aralığı: her başarısızlıkta ikiye katlanır
POWER_IDLE = 60.0  # bu kadar renk gitmediyse açma komutu yeniden gider (kumandayla kapatılmış olabilir)
DEFAULT_GAINS = (1.0, 0.5, 0.4)  # Lotus Lantern şeridinde yeşil ve mavi LED'ler kırmızıdan belirgin parlak
DEFAULT_GAMMA = 2.6  # ucuz LED'de düşük değerler fazla parlak görünür: karışık renkler beyazlaşır


class BleError(RuntimeError):
    pass


def available():
    import importlib.util
    try:
        return importlib.util.find_spec("bleak") is not None
    except (ImportError, ValueError):
        return False


def color_packet(rgb):
    r, g, b = rgb
    return bytes([0x7E, 0x00, 0x05, 0x03, r, g, b, 0x00, 0xEF])


def power_packet(on):
    return bytes([0x7E, 0x00, 0x04, 0xF0 if on else 0x00, 0x00, 0x01 if on else 0x00, 0xFF, 0x00, 0xEF])


def correct(rgb, gains, gamma=1.0):
    """Renk düzeltme. gamma: kanallar en güçlü kanala göre eğilir; parlaklık (en güçlü kanal) değişmez,
    yalnızca karışımdaki zayıf kanallar kısılır. Ardından kanal başına oran (0-1) uygulanır."""
    top = max(rgb)
    if top and gamma != 1.0:
        rgb = [top * (c / top) ** gamma for c in rgb]
    return tuple(max(0, min(255, round(c * g))) for c, g in zip(rgb, gains))


def scan(timeout=8.0):
    """Yakındaki ELK-BLE* şeritleri, en güçlü sinyal önce: [{"address", "name", "rssi"}].
    Bağlı bir şerit kendini duyurmaz: taramada görünmez."""
    import asyncio
    from bleak import BleakScanner

    async def run():
        found = await BleakScanner.discover(timeout=timeout, return_adv=True)
        strips = []
        for device, adv in found.values():
            name = (adv.local_name or device.name or "").strip()
            if name.upper().startswith(NAME_PREFIXES):
                strips.append({"address": device.address, "name": name, "rssi": adv.rssi})
        return sorted(strips, key=lambda s: -(s["rssi"] if s["rssi"] is not None else -999))

    try:
        return asyncio.run(run())
    except Exception as e:  # Bluetooth kapalı, bağdaştırıcı yok...
        raise BleError(f"Bluetooth taraması başarısız: {e}")


class BleStrip:
    """on_change(strip, connected): şeride ulaşılamayınca / bağlantı kopunca False, yeniden yazılabilince True.
    Arka plan iş parçacıklarından çağrılır."""

    def __init__(self, address, name="", gains=DEFAULT_GAINS, gamma=DEFAULT_GAMMA, timeout=CONNECT_TIMEOUT,
                 on_change=None):
        self.address, self.name, self.gains, self.gamma, self.timeout = address, name, tuple(gains), gamma, timeout
        self.on_change = on_change
        self._loop = self._client = None
        self._lock = threading.Lock()  # aynı anda tek yazma
        self._powered = False  # bu bağlantıda açma komutu gitti mi
        self._last_write = self._failed_at = -1e9
        self._fails = 0  # üst üste başarısız bağlantı sayısı: yeniden deneme aralığını belirler
        self._wanted = None  # son istenen renk: yeniden bağlanınca yazılır
        self._retry = None  # çalışan yeniden deneme döngüsünün iptal olayı
        self._closed = False  # close() sonrası yazılmaz: bırakılan şeride geç kalan bir yazma yeniden bağlanmasın
        self._retry_lock = threading.Lock()

    @property
    def connected(self):
        client = self._client
        return client is not None and client.is_connected

    @property
    def failing(self):
        """Şeride şu an ulaşılamıyor (arka planda yeniden deneniyor)."""
        return self._fails > 0

    def retry_delay(self):
        return min(RETRY_MAX, RETRY_FIRST * 2 ** max(0, self._fails - 1))

    def nudge(self):
        """Ayar değişti ya da uykudan dönüldü: uzamış bekleme en kısa aralığa iner."""
        self._fails = min(self._fails, 1)

    def _packets(self, rgb, now):
        """Yazılacak komutlar. Açma komutu bağlantı başına ve uzun bir aradan sonra bir kez gider (kumandayla
        kapatılmış şerit açılsın); sık gönderilince şerit göz kırpar gibi yanıp sönebiliyor."""
        rgb = correct(rgb, self.gains, self.gamma)
        packets = []
        if any(rgb) and (not self._powered or now - self._last_write >= POWER_IDLE):
            packets.append(power_packet(True))
        packets.append(color_packet(rgb))
        return packets

    def _run(self, coro, timeout):
        import asyncio
        import concurrent.futures
        if self._loop is None:
            self._loop = asyncio.new_event_loop()
            threading.Thread(target=self._loop.run_forever, daemon=True).start()
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        try:
            return future.result(timeout)
        except concurrent.futures.TimeoutError:
            future.cancel()
            raise BleError(f"{self.name or 'Şerit'} yanıt vermedi")
        except BleError:
            raise
        except Exception as e:
            raise BleError(f"{self.name or 'Şerit'} cihazına ulaşılamadı: {e}")

    async def _send(self, packets):
        from bleak import BleakClient
        try:
            if self._client is None or not self._client.is_connected:
                await self._drop()
                self._client = BleakClient(self.address, timeout=self.timeout, disconnected_callback=self._lost)
                await self._client.connect()
            for packet in packets:
                await self._client.write_gatt_char(WRITE_UUID, packet, response=False)
        except Exception:
            await self._drop()
            raise

    async def _drop(self):
        client, self._client = self._client, None  # önce bırakılır: _lost bunu kendi kapatmamız sayar
        self._powered = False
        if client is not None:
            try:
                await client.disconnect()
            except Exception:
                pass

    def _lost(self, client):
        """Bağlantı kendiliğinden koptu (fiş çekildi, uyku, menzil): arka planda yeniden denenir."""
        if client is not self._client:
            return
        self._client, self._powered = None, False
        self._failed(notify=True)

    def _failed(self, notify):
        first = self._fails == 0
        self._fails += 1
        self._failed_at = time.monotonic()
        self._start_retry()
        if (first or notify) and self.on_change:
            self.on_change(self, False)

    def _start_retry(self):
        with self._retry_lock:
            if self._retry is not None or self._wanted is None:
                return
            cancel = self._retry = threading.Event()
        threading.Thread(target=self._retry_loop, args=(cancel,), daemon=True).start()

    def _retry_loop(self, cancel):
        try:
            while not cancel.wait(self.retry_delay()):
                if self.connected or self._closed or self._wanted is None:
                    return
                try:
                    self._write(self._wanted, None, force=True)
                    return
                except BleError:
                    pass
        finally:
            with self._retry_lock:
                if self._retry is cancel:
                    self._retry = None

    def write(self, rgb, timeout=None):
        """timeout: en fazla bu kadar beklenir (kapanışta kısa tutulur). Az önce bağlanamadıysa beklemeden
        BleError verir (ulaşılamayan şerit kimseyi bekletmesin); renk hatırlanır ve bağlanınca yazılır."""
        if self._closed:
            raise BleError(f"{self.name or 'Şerit'} bırakıldı")
        self._wanted = tuple(rgb)
        self._write(self._wanted, timeout, force=False)

    def _write(self, rgb, timeout, force):
        if not self._lock.acquire(timeout=-1 if timeout is None else timeout):
            raise BleError(f"{self.name or 'Şerit'} meşgul")
        try:
            now = time.monotonic()
            if not force and self._fails and not self.connected and now - self._failed_at < self.retry_delay():
                self._start_retry()
                raise BleError(f"{self.name or 'Şerit'} şu an ulaşılamıyor, yeniden denenecek")
            packets = self._packets(rgb, now)
            try:
                self._run(self._send(packets), self.timeout + 5 if timeout is None else timeout)
            except BleError:
                self._failed(notify=False)
                raise
            recovered, self._fails = self._fails > 0, 0
            self._last_write = time.monotonic()
            if len(packets) > 1:
                self._powered = True
        finally:
            self._lock.release()
        if recovered and self.on_change:
            self.on_change(self, True)

    def close(self, timeout=3.0):
        """Bağlantıyı ve yeniden denemeyi kalıcı olarak bırakır. Yazma sürüyorsa en fazla timeout beklenir."""
        self._closed = True
        with self._retry_lock:
            if self._retry is not None:
                self._retry.set()
        self._wanted = None
        if self._loop is None or not self._lock.acquire(timeout=timeout):
            return
        try:
            self._run(self._drop(), timeout)
        except BleError:
            pass
        finally:
            self._lock.release()


class BleSink:
    """Motorun yazdığı yavaş hedef (Hue gibi): renk değiştikçe en fazla interval'de bir. Diğer ışıklarla aynı
    anda kendi iş parçacığında sürülür; ulaşılamayan şerit onları bekletmez. fixed: efekt yerine bu renk
    (cihazın kendi rengi ya da kapalıysa siyah). Ulaşılamayan şerit motor için hata değildir: BleStrip
    arka planda yeniden dener ve durumu on_change ile bildirir."""

    def __init__(self, strip, fixed=None):
        self.strip, self.fixed = strip, fixed
        self.key = f"ble:{strip.address}"
        self.interval = 1.0 / COMMANDS_PER_SECOND
        self._last = None
        strip.nudge()

    def write(self, rgb):
        target = self.fixed if self.fixed is not None else rgb
        if target == self._last:
            return
        try:
            self.strip.write(target)
            self._last = target
        except BleError:
            self._last = None

    def resend(self):
        """Son rengi yeniden yazar: komut yanıtsız gittiği için kaçmış olabilir."""
        if self._last is not None:
            try:
                self.strip.write(self._last)
            except BleError:
                self._last = None
