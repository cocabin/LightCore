"""Philips Hue köprüsü (yerel REST API v1). Sadece standart kütüphane.

- Bulma: Philips'in bulma servisi, o boş dönerse yerel ağdaki /24 adreslerine
  /api/config sorulur (köprü bunu kimlik doğrulamadan yanıtlar).
- Eşleştirme: köprünün üstündeki yuvarlak düğmeye basıldıktan sonra 30 sn içinde
  POST /api; dönen kullanıcı adı lightcore_config.json'a kaydedilir.
- Renk: köprü saniyede ~10 lamba komutu kaldırır. Renkler seyrek yollanır ve
  aradaki geçiş lambanın kendi yumuşak geçişine (transitiontime) bırakılır.
"""
import json

DISCOVERY_URL = "https://discovery.meethue.com/"
DEVICE_TYPE = "lightcore#pc"
COMMANDS_PER_SECOND = 10  # Philips'in önerdiği üst sınır (lamba komutları)


class HueError(RuntimeError):
    pass


class HueLinkButtonNotPressed(HueError):
    pass


def rgb_to_xy_bri(rgb):
    """sRGB -> CIE xy + parlaklık (1-254). Philips'in önerdiği dönüşüm; köprü,
    lambanın gamı dışındaki xy'yi kendisi en yakın renge çeker."""
    r, g, b = (c / 255 for c in rgb)
    r, g, b = (((c + 0.055) / 1.055) ** 2.4 if c > 0.04045 else c / 12.92 for c in (r, g, b))
    x = r * 0.664511 + g * 0.154324 + b * 0.162028
    y = r * 0.283881 + g * 0.668433 + b * 0.047685
    z = r * 0.000088 + g * 0.072310 + b * 0.986039
    total = x + y + z
    if total == 0:
        return (0.3127, 0.3290), 0  # beyaz noktası, kapalı
    bri = max(1, min(254, round(max(rgb) / 255 * 254)))
    return (round(x / total, 4), round(y / total, 4)), bri


def _get_json(host, path, timeout=2.0):
    import http.client  # yalnızca Hue kullanılınca yüklenir (ssl ve email'i de getirir): açılışı hafifletir
    conn = http.client.HTTPConnection(host, timeout=timeout)  # "ip" ya da "ip:port"
    try:
        conn.request("GET", path)
        return json.loads(conn.getresponse().read().decode("utf-8", "replace"))
    finally:
        conn.close()


def _is_bridge(host, timeout=0.8):
    import http.client
    try:
        cfg = _get_json(host, "/api/config", timeout)
    except (OSError, ValueError, http.client.HTTPException):
        return None
    if isinstance(cfg, dict) and cfg.get("bridgeid"):
        return {"ip": host, "id": cfg["bridgeid"], "name": cfg.get("name", "Hue Bridge")}
    return None


def _local_networks():
    """Bu bilgisayarın IPv4 adreslerinin /24 ağları (Ethernet/Wi-Fi)."""
    import ipaddress
    import socket
    nets = set()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("192.0.2.1", 9))  # paket gitmez; sadece çıkış arayüzünün adresi
        nets.add(ipaddress.ip_network(s.getsockname()[0] + "/24", strict=False))
        s.close()
    except OSError:
        pass
    return [n for n in nets if n.is_private]


def discover(scan=True, progress=None):
    """Köprüleri bulur: [{"ip", "id", "name"}]. Önce bulma servisi, sonra ağ taraması."""
    import concurrent.futures  # yalnızca arama sırasında gerekir, açılışı yavaşlatmasın
    import urllib.request
    found = {}
    try:
        with urllib.request.urlopen(DISCOVERY_URL, timeout=5) as r:
            for item in json.loads(r.read().decode()):
                bridge = _is_bridge(item.get("internalipaddress", ""))
                if bridge:
                    found[bridge["id"]] = bridge
    except (OSError, ValueError):
        pass
    if not found and scan:
        for net in _local_networks():
            if progress:
                progress(f"{net} ağı taranıyor...")
            with concurrent.futures.ThreadPoolExecutor(64) as pool:
                for bridge in pool.map(_is_bridge, (str(h) for h in net.hosts())):
                    if bridge:
                        found[bridge["id"]] = bridge
    return list(found.values())


class HueBridge:
    def __init__(self, ip, username=None, port=None):
        """ip "192.0.2.1" ya da "192.0.2.1:80" olabilir; port verilmezse 80."""
        self.ip, self.username, self.port = ip, username, port
        self._conn = None

    def _request(self, method, path, body=None, timeout=3.0):
        """Kalıcı HTTP bağlantısı (her istekte yeniden bağlanmak köprüyü yorar)."""
        import http.client
        data = json.dumps(body).encode() if body is not None else None
        for attempt in (0, 1):
            if self._conn is None:
                self._conn = http.client.HTTPConnection(self.ip, self.port, timeout=timeout)
            try:
                self._conn.request(method, path, body=data, headers={"Content-Type": "application/json"})
                return json.loads(self._conn.getresponse().read().decode("utf-8", "replace"))
            except (OSError, http.client.HTTPException, ValueError) as e:
                self.close()
                if attempt:
                    raise HueError(f"Hue köprüsüne ({self.ip}) ulaşılamadı: {e}")

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None

    @staticmethod
    def _check(reply):
        if isinstance(reply, list):
            for item in reply:
                err = item.get("error") if isinstance(item, dict) else None
                if err:
                    if err.get("type") == 101:
                        raise HueLinkButtonNotPressed("Köprüdeki yuvarlak düğmeye basılmadı.")
                    raise HueError(f"Hue köprüsü hatası {err.get('type')}: {err.get('description')}")
        return reply

    def pair(self):
        """Düğmeye basıldıktan sonra çağrılır; kullanıcı adını döndürür ve saklar."""
        reply = self._check(self._request("POST", "/api", {"devicetype": DEVICE_TYPE}))
        self.username = reply[0]["success"]["username"]
        return self.username

    def lights(self):
        """Renkli lambalar: [{"id", "name", "product"}]."""
        reply = self._request("GET", f"/api/{self.username}/lights")
        if isinstance(reply, list):
            self._check(reply)  # ör. yetkisiz kullanıcı adı
        result = []
        for light_id, info in sorted(reply.items(), key=lambda kv: int(kv[0])):
            if "xy" in info.get("state", {}):
                result.append({"id": light_id, "name": info.get("name", f"Lamba {light_id}"),
                               "product": info.get("productname", info.get("type", "Hue"))})
        return result

    def set_color(self, light_id, rgb, transition=0.0):
        xy, bri = rgb_to_xy_bri(rgb)
        state = {"on": False} if bri == 0 else {"on": True, "xy": list(xy), "bri": bri}
        state["transitiontime"] = max(0, round(transition * 10))  # 100 ms birimi
        self._check(self._request("PUT", f"/api/{self.username}/lights/{light_id}/state", state))


class HueSink:
    """Motorun yazdığı hedef: seçili lambaların hepsine aynı renk. interval, köprünün
    komut sınırına göre hesaplanır; geçiş süresi interval'e eşittir, böylece lamba bir
    sonraki renge kadar yumuşakça kayar."""
    key = "hue"

    def __init__(self, bridge, light_ids):
        self.bridge, self.light_ids = bridge, list(light_ids)
        self.interval = max(0.25, len(self.light_ids) / COMMANDS_PER_SECOND)

    def write(self, rgb):
        for light_id in self.light_ids:
            self.bridge.set_color(light_id, rgb, self.interval)
