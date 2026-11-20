"""Minimal OpenRGB SDK istemcisi (protokol v6). Sadece standart kütüphane.

ÖNEMLİ: OpenRGB, herhangi bir istemcinin yaptığı her LED güncellemesini bütün açık
bağlantılara bildirim (paket 1150, ~155 bayt) olarak yollar. Boşta duran bir bağlantı
bunları okumazsa tampon ~20 saniyede dolar, OpenRGB o bağlantıya yazarken kilitlenir
ve hiçbir cihaz komut almaz. Boşta duran bağlantılarda drain() düzenli çağrılmalıdır.
"""
import socket
import struct
import threading
import time
from dataclasses import dataclass, field

MAGIC = b"ORGB"
HEADER = struct.Struct("<4sIII")
PKT_COUNT, PKT_DATA, PKT_PROTO, PKT_NAME, PKT_ACK = 0, 1, 40, 50, 10
PKT_RESIZE_ZONE = 1000
PKT_UPDATE_LEDS, PKT_UPDATE_ZONE_LEDS = 1050, 1051
PKT_CUSTOM_MODE, PKT_UPDATE_MODE, PKT_SAVE_MODE, PKT_UPDATE_ZONE_MODE = 1100, 1101, 1102, 1103
MODE_FLAG_HAS_MODE_SPECIFIC_COLOR = 1 << 6
MODE_FLAG_MANUAL_SAVE = 1 << 8
CLIENT_PROTO = 6


class ProtocolError(Exception):
    pass


@dataclass
class DeviceMode:
    index: int
    name: str
    raw: bytes
    flags: int
    colors_min: int
    colors_max: int
    color_mode_offset: int
    colors_count_offset: int
    colors_data_offset: int

    def with_color(self, rgb):
        """Create an SDK mode block using a single mode-specific color."""
        count = max(1, self.colors_min)
        if self.colors_max:
            count = min(count, self.colors_max)
        if count > 64:
            raise ProtocolError(f"{self.name} modu beklenmeyen renk sayısı istiyor ({count})")

        block = bytearray(self.raw)
        # OpenRGB MODE_COLORS_MODE_SPECIFIC = 2
        struct.pack_into("<I", block, self.color_mode_offset, 2)
        struct.pack_into("<H", block, self.colors_count_offset, count)
        r, g, b = (min(255, max(0, int(c))) for c in rgb)
        color = bytes((r, g, b, 0))
        block[self.colors_data_offset:] = color * count
        return bytes(block)


@dataclass
class Zone:
    index: int
    name: str
    led_count: int
    modes: list = field(default_factory=list)
    leds_min: int = 0
    leds_max: int = 0


@dataclass
class Device:
    index: int
    name: str
    vendor: str
    kind: int
    led_count: int
    zones: list = field(default_factory=list)
    modes: list = field(default_factory=list)
    controller_id: int = 0
    active_mode: int = 0


def _parse_mode(r, data, proto):
    mode_start = r.o
    mode_name = r.string()
    field_start = r.o
    if proto >= 6:
        fixed = 11  # mode_value was removed in SDK protocol 6
    else:
        fixed = 12 if proto >= 3 else 9
    fields = [r.u32() for _ in range(fixed)]
    flags_index = 0 if proto >= 6 else 1
    if proto >= 3:
        colors_min_index, colors_max_index = ((5, 6) if proto >= 6 else (6, 7))
        color_mode_field = 10 if proto >= 6 else 11
    else:
        colors_min_index, colors_max_index = 4, 5
        color_mode_field = 8
    colors_count_offset = r.o
    n_colors = r.u16()
    colors_data_offset = r.o
    r.skip(4 * n_colors)
    return DeviceMode(
        index=0,
        name=mode_name,
        raw=data[mode_start:r.o],
        flags=fields[flags_index],
        colors_min=fields[colors_min_index],
        colors_max=fields[colors_max_index],
        color_mode_offset=field_start - mode_start + 4 * color_mode_field,
        colors_count_offset=colors_count_offset - mode_start,
        colors_data_offset=colors_data_offset - mode_start,
    )


class _Reader:
    def __init__(self, data):
        self.b, self.o = data, 0

    def _take(self, n):
        if self.o + n > len(self.b):
            raise ProtocolError(f"veri beklenenden kısa (konum {self.o}, istenen {n})")
        chunk = self.b[self.o:self.o + n]
        self.o += n
        return chunk

    def u16(self):
        return struct.unpack("<H", self._take(2))[0]

    def u32(self):
        return struct.unpack("<I", self._take(4))[0]

    def skip(self, n):
        self._take(n)

    def string(self):
        n = self.u16()
        return self._take(n).rstrip(b"\0").decode("utf-8", "replace")


def parse_device(index, data, proto, controller_id=None):
    """Cihaz tanımını çözer. Paketin TAMAMI tüketilmezse hata verir; bu,
    yanlış bir alan düzeni varsayıldığını anlamanın en güvenli yoludur."""
    r = _Reader(data)
    r.u32()  # toplam boyut
    kind = r.u32()
    name = r.string()
    vendor = r.string() if proto >= 1 else ""
    for _ in range(4):
        r.string()  # açıklama, sürüm, seri no, konum
    n_modes = r.u16()
    active_mode = r.u32()
    modes = []
    for _ in range(n_modes):
        mode = _parse_mode(r, data, proto)
        mode.index = len(modes)
        modes.append(mode)
    zones = []
    for zone_index in range(r.u16()):
        zone_name = r.string()
        r.u32()  # zone type
        zone_leds_min = r.u32()
        zone_leds_max = r.u32()
        zone_led_count = r.u32()
        r.skip(r.u16())  # matrix map
        if proto >= 4:
            for _ in range(r.u16()):
                r.string()  # segment name
                r.skip(12)  # type, start index, LED count
                if proto >= 6:
                    r.skip(r.u16())  # segment matrix map
                    r.skip(4)  # segment flags
        if proto >= 5:
            r.skip(4)  # zone flags
        zone_modes = []
        if proto >= 6:
            r.u32()  # active zone mode
            for _ in range(r.u16()):
                mode = _parse_mode(r, data, proto)
                mode.index = len(zone_modes)
                zone_modes.append(mode)
            r.string()  # zone display name
        zones.append(Zone(zone_index, zone_name, zone_led_count, zone_modes,
                          zone_leds_min, zone_leds_max))
    n_leds = r.u16()
    for _ in range(n_leds):
        r.string()
        if proto < 6:
            r.skip(4)  # LED value removed in protocol 6
    r.skip(4 * r.u16())  # OpenRGB'nin tuttuğu mevcut renkler
    if proto >= 5:
        for _ in range(r.u16()):
            r.string()  # LED alternate display name
        r.skip(4)  # controller flags
    if proto >= 6:
        r.string()  # controller display name
        r.skip(r.u32())  # device-specific configuration JSON
    if r.o != len(data):
        raise ProtocolError(f"cihaz paketi tam çözülemedi ({r.o}/{len(data)} bayt)")
    return Device(index, name, vendor, kind, n_leds, zones, modes,
                  index if controller_id is None else controller_id, active_mode)


def _led_bytes(colors):
    """Her karede çağrılır: efektler bütün LED'lere aynı rengi yazar, bir kez paketlenir."""
    def pack(rgb):
        return bytes(min(255, max(0, int(c))) for c in rgb) + b"\0"
    if colors and colors.count(colors[0]) == len(colors):
        return pack(colors[0]) * len(colors)
    return b"".join(pack(rgb) for rgb in colors)


class OpenRGBClient:
    def __init__(self, host="127.0.0.1", port=6742, name="lightcore"):
        self.host, self.port, self.name = host, port, name
        self.sock = None
        self.proto = 0
        self.lock = threading.RLock()  # bir istek sürerken drain() araya girmesin
        self._buf = b""

    def connect(self, timeout=5, command_timeout=10.0):
        """command_timeout: bir komutun onayı en fazla bu kadar beklenir."""
        self._buf = b""
        self.sock = socket.create_connection((self.host, self.port), timeout=timeout)
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self._send(PKT_NAME, 0, self.name.encode() + b"\0")
        self._send(PKT_PROTO, 0, struct.pack("<I", CLIENT_PROTO))
        self.sock.settimeout(1.5)
        try:
            server = struct.unpack("<I", self._recv(PKT_PROTO)[:4])[0]
        except (socket.timeout, ProtocolError, struct.error):
            server = 0  # eski sürüm: protokol sorgusunu bilmiyor
        self.sock.settimeout(max(timeout, command_timeout))  # yavaş cihazlar için komut onayı bekleme süresi
        self.proto = min(CLIENT_PROTO, server)

    def close(self):
        if self.sock:
            self.sock.close()
            self.sock = None

    def _send(self, pkt_id, dev=0, payload=b""):
        self.sock.sendall(HEADER.pack(MAGIC, dev, pkt_id, len(payload)) + payload)

    def _exact(self, n):
        while len(self._buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise ProtocolError("OpenRGB bağlantısı koptu")
            self._buf += chunk
        out, self._buf = self._buf[:n], self._buf[n:]
        return out

    def drain(self):
        """Beklemeden, gelmiş ama istenmemiş paketleri (LED bildirimleri, geç kalmış
        onaylar) okuyup atar. Bir istek sürüyorsa hiçbir şey yapmaz. Atılan bayt sayısını
        döndürür; yarım gelmiş paket bir sonraki okumaya saklanır."""
        if not self.sock or not self.lock.acquire(blocking=False):
            return 0
        try:
            timeout = self.sock.gettimeout()
            self.sock.setblocking(False)
            try:
                while True:
                    chunk = self.sock.recv(65536)
                    if not chunk:
                        break
                    self._buf += chunk
            except (BlockingIOError, InterruptedError):
                pass
            finally:
                self.sock.settimeout(timeout)
            dropped = 0
            while len(self._buf) >= HEADER.size:
                magic, _dev, _pid, size = HEADER.unpack_from(self._buf)
                if magic != MAGIC:
                    raise ProtocolError("OpenRGB'den beklenmeyen veri")
                if len(self._buf) < HEADER.size + size:
                    break
                dropped += HEADER.size + size
                self._buf = self._buf[HEADER.size + size:]
            return dropped
        finally:
            self.lock.release()

    def _recv(self, want):
        while True:  # istenmeyen paketleri (ör. cihaz listesi güncellendi) atla
            magic, _dev, pid, size = HEADER.unpack(self._exact(HEADER.size))
            if magic != MAGIC:
                raise ProtocolError("OpenRGB'den beklenmeyen yanıt")
            payload = self._exact(size)
            if pid == want:
                return payload

    def list_devices(self):
        with self.lock:
            return self._list_devices()

    def _list_devices(self):
        self._send(PKT_COUNT)
        count_data = self._recv(PKT_COUNT)
        count = struct.unpack("<I", count_data[:4])[0]
        if self.proto >= 6:
            expected = 4 + 4 * count
            if len(count_data) != expected:
                raise ProtocolError(f"SDK 6 cihaz kimlik listesi hatalı ({len(count_data)}/{expected} bayt)")
            controller_ids = list(struct.unpack_from(f"<{count}I", count_data, 4)) if count else []
        else:
            controller_ids = list(range(count))
        devices = []
        for i, controller_id in enumerate(controller_ids):
            req = struct.pack("<I", self.proto) if self.proto >= 1 else b""
            self._send(PKT_DATA, controller_id, req)
            devices.append(parse_device(i, self._recv(PKT_DATA), self.proto, controller_id))
        return devices

    def wait_for_devices(self, settle=6.0, min_wait=10.0, timeout=60.0, progress=None):
        """OpenRGB cihazları tararken liste dolar; sayı settle saniye değişmeyene
        (ve en az min_wait geçene) kadar bekler."""
        start = time.monotonic()
        last_n, stable_since = -1, start
        while True:
            devices = self.list_devices()
            now = time.monotonic()
            if len(devices) != last_n:
                last_n, stable_since = len(devices), now
                if progress:
                    progress(last_n)
            if (now - stable_since >= settle and now - start >= min_wait) or now - start >= timeout:
                return devices
            time.sleep(1.0)

    def resize_zone(self, index, zone, size):
        """ARGB başlıkları (JRAINBOW) OpenRGB'de 0 LED ile gelebilir; o zaman hiç renk
        yazılamaz. Bölgenin LED sayısını ayarlar (OpenRGB bunu kendi ayarına kaydeder)."""
        self._send_command(PKT_RESIZE_ZONE, index, struct.pack("<ii", zone.index, size))

    def set_custom_mode(self, index):
        self._send_command(PKT_CUSTOM_MODE, index)

    def update_mode(self, index, mode, rgb):
        mode_data = mode.with_color(rgb)
        body_size = 8 + len(mode_data)
        body = struct.pack("<Ii", body_size, mode.index) + mode_data
        self._send_command(PKT_UPDATE_MODE, index, body)

    def save_mode(self, index, mode, rgb):
        """Modu cihazın kendi belleğine yazar (bilgisayar kapanınca da kalır). Bellek
        ömrü sınırlıdır: yalnızca program kapanırken ve renk değiştiyse çağrılır."""
        mode_data = mode.with_color(rgb)
        body = struct.pack("<Ii", 8 + len(mode_data), mode.index) + mode_data
        self._send_command(PKT_SAVE_MODE, index, body)

    def update_zone_mode(self, index, zone, mode=None, rgb=None):
        # A mode index of -1 makes the zone inherit the device-level mode.
        mode_data = mode.with_color(rgb) if mode is not None else b""
        mode_index = mode.index if mode is not None else -1
        body_size = 12 + len(mode_data)
        body = struct.pack("<Iii", body_size, zone.index, mode_index) + mode_data
        self._send_command(PKT_UPDATE_ZONE_MODE, index, body)

    def update_zone_leds(self, index, zone, colors):
        """Set per-LED colors for one zone through the SDK zone update packet."""
        if len(colors) != zone.led_count:
            raise ValueError(
                f"{zone.name}: {zone.led_count} LED için {len(colors)} renk verildi."
            )
        count = len(colors)
        body_size = 10 + 4 * count
        body = struct.pack("<IIH", body_size, zone.index, count) + _led_bytes(colors)
        self._send_command(PKT_UPDATE_ZONE_LEDS, index, body)

    def _send_command(self, pkt_id, dev, payload=b""):
        with self.lock:
            self._command(pkt_id, dev, payload)

    def _command(self, pkt_id, dev, payload):
        self._send(pkt_id, dev, payload)
        try:
            if self.proto >= 6:
                self._wait_ack(pkt_id)
            else:
                # Eski protokolde komut onayı yok: hiç beklemeden yazmak, yavaş cihazların
                # komutlarını OpenRGB'de biriktirir ve cihaz eski renkte takılı kalır.
                # Cevap isteyen bir istek yollayıp beklemek, OpenRGB'nin önceki komutu
                # işlediğini garantiler (sunucu her bağlantının paketlerini sırayla işler).
                self._send(PKT_COUNT)
                self._recv(PKT_COUNT)
        except socket.timeout:
            raise ProtocolError(f"OpenRGB, {pkt_id} numaralı komuta (cihaz {dev}) zamanında yanıt vermedi")

    def _wait_ack(self, pkt_id):
        while True:
            magic, _ack_dev, response_id, size = HEADER.unpack(self._exact(HEADER.size))
            if magic != MAGIC:
                raise ProtocolError("OpenRGB'den beklenmeyen komut onayı geldi")
            response = self._exact(size)
            if response_id != PKT_ACK:
                continue
            if len(response) < 8:
                raise ProtocolError("OpenRGB'den eksik komut onayı geldi")
            ack_id, status = struct.unpack_from("<II", response)
            if ack_id != pkt_id:
                continue  # önceki bir isteğin gecikmiş onayı
            if status != 0:
                raise ProtocolError(f"OpenRGB komutu reddetti (paket {ack_id}, durum {status})")
            return

    def set_leds(self, index, colors):
        n = len(colors)
        body = struct.pack("<IH", 6 + 4 * n, n) + _led_bytes(colors)
        self._send_command(PKT_UPDATE_LEDS, index, body)
