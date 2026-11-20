"""MSI Mystic Light HID raporları (Windows'a bağlı değil, test edilebilir).

0x52 (185 bayt): anakartın TÜM ışık ayarı. Bağlantı başına bir kez gönderilir ve
JRAINBOW başlıklarını LED başına (0x25) moda alır. Anakartın mevcut ayarı
okunabildiyse (base) JRGB ve kart üstü LED'ler ondan aynen alınır.

0x53 (725 bayt): tek bir JRAINBOW başlığının LED renkleri. Her renk değişiminde
yalnızca bu rapor gider; anakart modu yeniden yazılmadığı için fanlar yanıp
sönmez ve renk geçişi akıcı olur. (Eskiden yalnızca 0x52 gönderiliyordu: 0x25
modu 0x53 verisi beklediği için fanlar sönüyordu.)
"""
REPORT_ID = 0x52
REPORT_SIZE = 185
PER_LED_REPORT_ID = 0x53
PER_LED_REPORT_SIZE = 725
JRAINBOW_OFFSETS = {1: 0x1F, 2: 0x2A}
HEADER_LED_COUNT = 60
MODE_PER_LED = 0x25
MODE_STATIC = 0x01


def default_packet():
    """Anakart okunamazsa kullanılan varsayılan 0x52 raporu."""
    packet = bytearray(REPORT_SIZE)
    packet[0] = REPORT_ID

    def block(offset, values):
        packet[offset:offset + len(values)] = bytes(values)

    block(0x01, (0x01, 0, 0, 0, 0x08, 0, 0, 0, 0x80, 0))
    block(0x0B, (0x01, 0, 0, 0, 0x2A, 0, 0, 0, 0x80, 0))
    block(0x15, (0x01, 0, 0, 0, 0x2A, 0, 0, 0, 0x80, 0))
    block(0x1F, (MODE_PER_LED, 0, 0, 0, 0x29, 0, 0, 0, 0x80, 0, HEADER_LED_COUNT))
    block(0x2A, (MODE_PER_LED, 0, 0, 0, 0x29, 0, 0, 0, 0x80, 0, HEADER_LED_COUNT))
    block(0x35, (0x25, 0, 0, 0, 0x29, 0, 0, 0, 0x82, 0, HEADER_LED_COUNT))
    block(0x40, (0x01, 0, 0, 0, 0x28, 0, 0, 0, 0x80, 0))
    block(0x4A, (0x25, 0, 0, 0, 0xA9, 0, 0, 0, 0xB1, 0))
    for offset in range(0x54, 0xA5, 0x0A):
        block(offset, (0x01, 0, 0, 0, 0x28, 0, 0, 0, 0x80, 0))
    block(0xAE, (0x01, 0, 0, 0, 0x2A, 0, 0, 0, 0x80, 0))
    return packet


def build_config(base=None):
    """0x52 ayar raporu: JRAINBOW1/2 LED başına moda alınır, gerisi base'den gelir."""
    if base is not None and len(base) >= REPORT_SIZE and base[0] == REPORT_ID:
        packet = bytearray(base[:REPORT_SIZE])
    else:
        packet = default_packet()
    for offset in JRAINBOW_OFFSETS.values():
        leds = packet[offset + 10] or HEADER_LED_COUNT
        packet[offset:offset + 11] = bytes((MODE_PER_LED, 0, 0, 0, 0x29, 0, 0, 0, 0x80, 0, leds))
    packet[REPORT_SIZE - 1] = 0  # kalıcı kayıt yok
    return packet


def build_hold(base, rgb, headers=tuple(JRAINBOW_OFFSETS), save=True):
    """0x52 raporu: JRAINBOW başlıkları anakartın kendi Static modunda bu renkte kalır.
    Program kapanırken yollanır; save ile anakart belleğine de yazılır (kapanıp açılınca kalır)."""
    packet = build_config(base)
    r, g, b = (int(c) for c in rgb)
    for header in headers:
        offset = JRAINBOW_OFFSETS[header]
        leds = packet[offset + 10]
        packet[offset:offset + 11] = bytes((MODE_STATIC, r, g, b, 0x2A, r, g, b, 0x80, 0, leds))
    packet[REPORT_SIZE - 1] = 1 if save else 0
    return packet


def build_frame(header, rgb, leds=HEADER_LED_COUNT):
    """0x53 LED raporu: bir JRAINBOW başlığının bütün LED'leri aynı renk."""
    if header not in JRAINBOW_OFFSETS:
        raise ValueError("Yalnızca JRAINBOW1 veya JRAINBOW2 seçilebilir.")
    if len(rgb) != 3 or any(not 0 <= int(c) <= 255 for c in rgb):
        raise ValueError("Renk RGB 0-255 aralığında olmalı.")
    frame = bytearray((PER_LED_REPORT_ID, 0x25, 0x04, header - 1, 0x00))
    frame += bytes(int(c) for c in rgb) * leds
    if len(frame) > PER_LED_REPORT_SIZE:
        raise ValueError(f"{leds} LED 0x53 raporuna sığmıyor.")
    frame += bytes(PER_LED_REPORT_SIZE - len(frame))
    return frame
