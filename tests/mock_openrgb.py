"""Protokol 6 konuşan sahte OpenRGB sunucusu (gerçek cihaz olmadan test için)."""
import socket
import struct
import threading
import time

from lightcore.openrgb_client import HEADER, MAGIC

ACK = 10


def s(text):
    b = text.encode() + b"\0"
    return struct.pack("<H", len(b)) + b


def mode(name, cmin):
    f = [0] * 11
    f[5] = f[6] = cmin  # colors_min, colors_max
    return s(name) + struct.pack("<11I", *f) + struct.pack("<H", cmin) + b"\xff\0\0\0" * cmin


def pack_device(name, vendor, zones, leds, modes=(("Direct", 0), ("Static", 1))):
    body = struct.pack("<I", 5) + s(name) + s(vendor) + b"".join(s(x) for x in ("d", "1", "sn", "loc"))
    body += struct.pack("<Hi", len(modes), 0) + b"".join(mode(n, c) for n, c in modes)
    body += struct.pack("<H", len(zones))
    for zname, count, *limits in zones:  # (ad, LED) ya da (ad, LED, en az, en çok)
        lo, hi = limits or (count, count)
        body += s(zname) + struct.pack("<4I", 1, lo, hi, count) + struct.pack("<H", 0)
        body += struct.pack("<H", 0) + struct.pack("<I", 0)          # segment sayısı, bölge bayrakları
        body += struct.pack("<IH", 0, 0) + s(zname)                   # aktif bölge modu, bölge modları, ad
    body += struct.pack("<H", leds) + b"".join(s(f"LED {i}") for i in range(leds))
    body += struct.pack("<H", leds) + b"\0\0\0\0" * leds              # mevcut renkler
    body += struct.pack("<HI", 0, 0) + s(name) + struct.pack("<I", 0)  # alt adlar, bayraklar, ad, json
    return struct.pack("<I", len(body) + 4) + body


class MockServer(threading.Thread):
    BASE_ID = 10  # denetleyici kimlikleri sıra numarasından farklı olsun

    def __init__(self, devices, ack=True, delays=None, fail_first=None, notify=False):
        super().__init__(daemon=True)
        self.devices, self.ack = devices, ack
        self.delays = delays or {}  # cihaz_id -> komut onayından önceki bekleme (yavaş cihaz)
        self.fail_first = dict(fail_first or {})  # cihaz_id -> ilk kaç komut reddedilsin
        # notify: gerçek OpenRGB gibi her LED güncellemesini BÜTÜN bağlantılara 1150 paketiyle
        # bildir. Gönderme tamponu küçük tutulur: okumayan bir bağlantı sunucuyu kilitler.
        self.notify = notify
        self.conns = {}  # soket -> gönderme kilidi
        self.commands = []  # (paket_id, cihaz_id, yük)
        self.srv = socket.socket()
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(1)
        self.port = self.srv.getsockname()[1]

    def _pkt(self, dev, pid, payload=b""):
        return HEADER.pack(MAGIC, dev, pid, len(payload)) + payload

    def _read(self, c, n):
        buf = b""
        while len(buf) < n:
            chunk = c.recv(n - len(buf))
            if not chunk:
                raise EOFError
            buf += chunk
        return buf

    def run(self):
        self.srv.listen(8)
        while True:  # her bağlantı (ör. cihaz başına ayrı istemci) kendi iş parçacığında
            try:
                c, _ = self.srv.accept()
            except OSError:
                return
            if self.notify:
                c.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 8192)
            self.conns[c] = threading.Lock()
            threading.Thread(target=self._serve, args=(c,), daemon=True).start()

    def _out(self, c, data):
        with self.conns[c]:
            c.sendall(data)

    def _serve(self, c):
        try:
            while True:
                _, dev, pid, size = HEADER.unpack(self._read(c, 16))
                payload = self._read(c, size)
                if pid == 40:
                    self._out(c, self._pkt(0, 40, struct.pack("<I", 6)))
                elif pid == 0:
                    ids = [self.BASE_ID + i for i in range(len(self.devices))]
                    self._out(c, self._pkt(0, 100))  # istenmeyen paket: istemci atlamalı
                    self._out(c, self._pkt(0, 0, struct.pack(f"<I{len(ids)}I", len(ids), *ids)))
                elif pid == 1:
                    assert struct.unpack("<I", payload)[0] == 6
                    self._out(c, self._pkt(dev, 1, self.devices[dev - self.BASE_ID]))
                elif pid != 50:
                    self.commands.append((pid, dev, payload))
                    if self.notify and pid in (1050, 1051):
                        for other in list(self.conns):  # okumayan bağlantıda burada kilitlenir
                            try:
                                self._out(other, self._pkt(dev, 1150, bytes(139)))
                            except OSError:
                                self.conns.pop(other, None)
                    if dev in self.delays:
                        time.sleep(self.delays[dev])
                    status = 0
                    if self.fail_first.get(dev, 0) > 0:
                        self.fail_first[dev] -= 1
                        status = 1
                    if self.ack:
                        self._out(c, self._pkt(dev, ACK, struct.pack("<II", pid, status)))
        except (EOFError, OSError):
            pass
        finally:
            self.conns.pop(c, None)

    def of(self, pid, dev=None):
        return [p for i, d, p in self.commands if i == pid and (dev is None or d == dev)]
