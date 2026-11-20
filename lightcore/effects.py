import colorsys
import math


def parse_hex(s):
    s = s.strip().lstrip("#")
    if len(s) != 6:
        raise ValueError(f"Geçersiz renk: {s!r} (örnek: 8B7CFF)")
    return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4))


def scale(rgb, brightness):
    return tuple(round(c * brightness) for c in rgb)


class Static:
    is_static = True

    def steady_for(self, t):
        return 0.0

    def __init__(self, color):
        self.color = color

    def at(self, t):
        return self.color


def _smooth(f):
    return f * f * (3 - 2 * f)


def _mix(a, b, f):
    return tuple(round(x + (y - x) * f) for x, y in zip(a, b))


class _Moving:
    is_static = False

    def steady_for(self, t):
        """Rengin en az kaç saniye değişmeyeceği (0: her kare değişebilir)."""
        return 0.0


class HoldCycle(_Moving):
    """Her renk hold saniye sabit yanar, sonra fade saniyede sıradakine yumuşakça geçer."""

    def __init__(self, colors, hold=2.0, fade=1.5):
        if len(colors) < 2:
            raise ValueError("Renk geçişi için en az 2 renk gerekir")
        self.colors, self.hold, self.fade = colors, max(0.0, hold), max(0.1, fade)
        self.seconds = len(colors) * (self.hold + self.fade)

    def at(self, t):
        segment = self.hold + self.fade
        p = t % self.seconds
        i = int(p // segment)
        r = p - i * segment
        a = self.colors[i % len(self.colors)]
        if r < self.hold:
            return a
        return _mix(a, self.colors[(i + 1) % len(self.colors)], _smooth((r - self.hold) / self.fade))

    def steady_for(self, t):
        r = t % (self.hold + self.fade)
        return self.hold - r if r < self.hold else 0.0


class Rainbow(_Moving):
    def __init__(self, seconds=8.0, saturation=1.0):
        self.seconds, self.saturation = seconds, saturation

    def at(self, t):
        r, g, b = colorsys.hsv_to_rgb(t / self.seconds % 1, self.saturation, 1)
        return round(r * 255), round(g * 255), round(b * 255)


class Breathe(_Moving):
    """Yavaşça yanıp söner. Birden çok renk verilirse her nefeste sıradakine geçer."""

    def __init__(self, color, seconds=4.0, colors=None):
        self.colors = list(colors) if colors else [color]
        self.color, self.seconds = self.colors[0], seconds

    def at(self, t):
        level = 0.5 - 0.5 * math.cos(2 * math.pi * t / self.seconds)
        color = self.colors[int(t / self.seconds) % len(self.colors)]
        return scale(color, level)


class Pulse(_Moving):
    """Kalp atışı: iki yumuşak vuruş, sonra kısa dinlenme."""

    def __init__(self, color, seconds=1.6):
        self.color, self.seconds = color, seconds

    def at(self, t):
        p = t / self.seconds % 1

        def beat(center, width, height):
            d = (p - center) / width
            return height * math.exp(-d * d)

        level = 0.12 + beat(0.15, 0.06, 0.88) + beat(0.38, 0.07, 0.6)
        return scale(self.color, min(1.0, level))


class Candle(_Moving):
    """Mum ışığı: renk etrafında yumuşak, düzensiz titreşim (sinüslerin toplamı)."""
    seconds = 4.0

    def __init__(self, color):
        self.color = color

    def at(self, t):
        wobble = (0.5 * math.sin(t * 2.1) + 0.3 * math.sin(t * 3.7 + 1.3)
                  + 0.2 * math.sin(t * 7.3 + 0.4))
        return scale(self.color, 0.72 + 0.22 * wobble)


def _noise(i):
    """Tekrarlanabilir rastgele sayı (0-1): cihazlar ayrı iş parçacığında aynı rengi hesaplasın."""
    return (math.sin(i * 12.9898) * 43758.5453) % 1.0


class Flow(_Moving):
    """Seçili renkler arasında beklemeden, sürekli akar."""

    def __init__(self, colors, seconds=8.0):
        self.colors, self.seconds = list(colors), seconds

    def at(self, t):
        n = len(self.colors)
        p = (t / self.seconds % 1) * n
        i = int(p)
        return _mix(self.colors[i % n], self.colors[(i + 1) % n], _smooth(p - i))


class Fire(_Moving):
    """Ateş: kızıl ile sarı arasında düzensiz titrer."""
    seconds = 4.0

    def at(self, t):
        w = 0.5 + 0.5 * (0.5 * math.sin(t * 5.1) + 0.3 * math.sin(t * 8.3 + 1.1) + 0.2 * math.sin(t * 13.7 + 2.0))
        return scale(_mix((255, 25, 0), (255, 150, 15), w), 0.55 + 0.45 * w)


class Lava(_Moving):
    """Lav: koyu kırmızı ile turuncu arasında ağır ağır dalgalanır."""

    def __init__(self, seconds=10.0):
        self.seconds = seconds

    def at(self, t):
        w = 0.5 + 0.5 * math.sin(2 * math.pi * t / self.seconds) * math.cos(1.3 * math.pi * t / self.seconds)
        return _mix((120, 8, 0), (255, 90, 0), w)


class Aurora(_Moving):
    """Kuzey ışıkları: yeşil, turkuaz ve mor arasında dalgalanır."""
    PALETTE = ((20, 255, 120), (0, 210, 200), (120, 60, 255))

    def __init__(self, seconds=14.0):
        self.seconds = seconds

    def at(self, t):
        p = (t / self.seconds + 0.08 * math.sin(t * 0.9)) % 1 * 3
        i = int(p)
        color = _mix(self.PALETTE[i % 3], self.PALETTE[(i + 1) % 3], _smooth(p - i))
        return scale(color, 0.7 + 0.3 * math.sin(t * 1.7))


class Ocean(_Moving):
    """Okyanus: lacivert ile turkuaz arasında dalga dalga."""

    def __init__(self, seconds=9.0):
        self.seconds = seconds

    def at(self, t):
        a = 2 * math.pi * t / self.seconds
        return _mix((0, 35, 150), (0, 200, 210), 0.5 + 0.35 * math.sin(a) + 0.15 * math.sin(a * 2.7 + 1))


class Neon(_Moving):
    """Neon tabela: sabit yanar, ara sıra kısa süre titrer."""
    seconds = 4.0

    def __init__(self, color):
        self.color = color

    def at(self, t):
        slot = int(t * 12)
        flicker = _noise(slot // 24) > 0.55 and slot % 24 < 5 and _noise(slot) > 0.5
        return scale(self.color, 0.2 if flicker else 1.0)


class Twinkle(_Moving):
    """Yıldızlar: loş zeminde ara sıra yumuşakça parlar."""
    seconds = 6.0

    def __init__(self, color):
        self.color = color

    def at(self, t):
        slot = int(t / 1.2)
        peak = _noise(slot) * 0.85
        return scale(self.color, 0.12 + peak * math.sin(math.pi * (t / 1.2 - slot)) ** 2)


class Storm(_Moving):
    """Fırtına: koyu mavi zeminde ara sıra şimşek çakar."""
    seconds = 6.0

    def at(self, t):
        slot = int(t / 2.5)
        r = t / 2.5 - slot
        flash = 0.0
        if _noise(slot) > 0.4:
            start = 0.2 + 0.5 * _noise(slot + 0.5)
            if 0 <= r - start < 0.1:
                flash = 1 - (r - start) / 0.1
        return _mix((10, 18, 60), (225, 235, 255), flash)


class Disco(_Moving):
    """Disko: renkler vuruşla değişir (renk seçilmediyse gökkuşağından)."""

    def __init__(self, colors=None, seconds=0.5):
        self.colors, self.seconds = list(colors or ()), seconds

    def at(self, t):
        slot = int(t / self.seconds)
        if len(self.colors) >= 2:
            return self.colors[slot % len(self.colors)]
        r, g, b = colorsys.hsv_to_rgb(_noise(slot), 1, 1)
        return round(r * 255), round(g * 255), round(b * 255)


class Alarm(_Moving):
    """Alarm: iki renk arasında yumuşak gidip gelir (varsayılan kırmızı-mavi)."""

    def __init__(self, a=(255, 0, 20), b=(0, 40, 255), seconds=1.0):
        self.a, self.b, self.seconds = a, b, seconds

    def at(self, t):
        return _mix(self.a, self.b, 0.5 - 0.5 * math.cos(2 * math.pi * t / self.seconds))


class Fade:
    """Bir önceki renkten yeni efekte yumuşak geçiş; parlaklığı da uygular."""

    def __init__(self, start_rgb, effect, brightness=1.0, seconds=0.6):
        self.start, self.effect = start_rgb, effect
        self.brightness, self.seconds = brightness, seconds
        self._last_t = 0.0

    @property
    def is_static(self):
        return self.effect.is_static and self._last_t >= self.seconds

    @property
    def settles(self):
        """Geçişten sonra sabit kalacak mı (saniyede ~30 kare gerekmeyecek mi)."""
        return self.effect.is_static

    def steady_for(self, t):
        return self.effect.steady_for(t) if t >= self.seconds else 0.0

    def at(self, t):
        self._last_t = t
        return self.value(t)

    def value(self, t):
        """at() ile aynı renk, ama durumu değiştirmez (arayüz okuması için)."""
        target = scale(self.effect.at(t), self.brightness)
        if t >= self.seconds:
            return target
        return _mix(self.start, target, _smooth(t / self.seconds))
