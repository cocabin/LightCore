"""FanControl (Rem0o) yedeğini / userConfig.json'unu bu programın fan ayarlarına çevirir.

FanControl dosyasında (sürüm 284 ile doğrulandı):
- Controls: her fan çıkışı. NickName, Identifier, Enable, SelectedFanCurve{Name},
  SelectedOffset, SelectedStart, SelectedStop, MinimumPercent,
  SelectedCommandStepUp/Down, ManualControl + ManualControlValue.
- FanCurves: türü alanlarından anlaşılır:
  Points -> Grafik, SelectedControl -> Eşitle (başka fanı ofsetle izler),
  SelectedFanCurves -> Karışım, IdleValue/LoadValue -> Tetik, yalnız sabit değer -> Sabit,
  Minimum/MaximumTemperature + Minimum/MaximumCommand -> Doğrusal.
  HysteresisConfig: HysteresisValueUp/Down (°C), ResponseTimeUp/Down (sn),
  IgnoreHysteresisAtLimits.

Kimlikler: FanControl "/lpc/superio/control/3" yazar, kütüphane bu bilgisayarda
"/lpc/superio/0/control/3" verir; ekran kartı fanlarını FanControl kendi NVApi
eklentisiyle "NVApiWrapper/<kart>/control/0" diye adlandırır (sıra ile eşlenir).
Eşlenemeyen her şey rapora yazılır; hiçbir şey sessizce atlanmaz.
"""
import re

OWN_FORMAT = "lightcore_fans"
MIX_FUNCTIONS = {0: "max", 1: "min", 2: "avg", 3: "sum"}
_LPC = re.compile(r"^/lpc/([^/]+)/(control|fan|temperature)/(\d+)$")
_NVAPI = re.compile(r"^NVApiWrapper/[^/]+/(control|fan|temperature)/(\d+)$")


class ImportResult:
    def __init__(self):
        self.curves, self.channels, self.notes, self.warnings = [], {}, [], []
        self.game_curve = None  # yalnızca bu programın yedeğinde olur


def _ids(hello, key):
    return [x["id"] for x in hello.get(key, [])]


def map_id(fc_id, hello, key):
    """FanControl kimliği -> bu bilgisayardaki kimlik (key: controls/fans/temps) ya da None."""
    if not fc_id:
        return None
    ids = _ids(hello, key)
    if fc_id in ids:
        return fc_id
    m = _LPC.match(fc_id)
    if m:
        chip, kind, n = m.groups()
        guess = f"/lpc/{chip}/0/{kind}/{n}"
        return guess if guess in ids else None
    m = _NVAPI.match(fc_id)
    if m:
        kind, n = m.group(1), int(m.group(2))
        nv = sorted((i for i in ids if i.startswith("/gpu-nvidia/")), key=lambda i: int(i.rsplit("/", 1)[1]))
        nv = [i for i in nv if f"/{kind}/" in i]
        return nv[n] if n < len(nv) else None
    return None


def map_temp(fc_id, hello):
    """Sıcaklık kaynağı: önce birebir, sonra yaklaşık (işlemci -> "cpu", ekran kartı -> "gpu")."""
    exact = map_id(fc_id, hello, "temps")
    if exact:
        return exact, True
    low = (fc_id or "").lower()
    if "/amdcpu/" in low or "/intelcpu/" in low or "cpu" in low:
        return "cpu", False
    if "gpu" in low or "nvapi" in low or "adlx" in low:
        return "gpu", False
    return "cpu", False


def _hysteresis(cfg):
    if not cfg:
        return None
    return {"up": cfg.get("HysteresisValueUp", 0), "down": cfg.get("HysteresisValueDown", 0),
            "response_up": cfg.get("ResponseTimeUp", 0), "response_down": cfg.get("ResponseTimeDown", 0),
            "ignore_at_limits": cfg.get("IgnoreHysteresisAtLimits", True)}


def _source(fc_curve, hello, result):
    src = (fc_curve.get("SelectedTempSource") or {}).get("Identifier")
    mapped, exact = map_temp(src, hello)
    if src and not exact:
        result.warnings.append(f"'{fc_curve.get('Name')}' eğrisinin sıcaklık kaynağı ({src}) bu bilgisayarda "
                               f"birebir bulunamadı; {'işlemci' if mapped == 'cpu' else 'ekran kartı'} sıcaklığı "
                               "kullanılacak.")
    return mapped


def convert_curve(fc, hello, result):
    name = fc.get("Name") or "Adsız"
    if fc.get("Points"):
        points = []
        for p in fc["Points"]:
            t, v = (float(x) for x in str(p).replace(" ", "").split(",")[:2])
            points.append([t, v])
        return {"type": "graph", "name": name, "source": _source(fc, hello, result),
                "points": sorted(points), "hysteresis": _hysteresis(fc.get("HysteresisConfig"))}
    if fc.get("SelectedControl") is not None:
        target = map_id((fc["SelectedControl"] or {}).get("Identifier"), hello, "controls")
        if not target:
            result.warnings.append(f"'{name}' (Eşitle) eğrisinin izlediği fan bu bilgisayarda yok; eğri atlandı.")
            return None
        if fc.get("Proportional"):
            result.warnings.append(f"'{name}': orantılı eşitleme desteklenmiyor, ofsetle eşitleme kullanılacak.")
        return {"type": "sync", "name": name, "control": target, "offset": fc.get("SelectedOffset", 0)}
    if fc.get("SelectedFanCurves") is not None:
        names = [c.get("Name") for c in fc.get("SelectedFanCurves") or [] if c and c.get("Name")]
        fn = fc.get("SelectedMixFunction", 0)
        return {"type": "mix", "name": name, "curves": names,
                "function": MIX_FUNCTIONS.get(fn, fn if isinstance(fn, str) else "max")}
    if "IdleValue" in fc or "LoadValue" in fc:
        return {"type": "trigger", "name": name, "source": _source(fc, hello, result),
                "idle_temp": fc.get("IdleTemperature", 50), "load_temp": fc.get("LoadTemperature", 70),
                "idle_value": fc.get("IdleValue", 30), "load_value": fc.get("LoadValue", 100)}
    if "MinimumCommand" in fc or "MaximumCommand" in fc:
        return {"type": "linear", "name": name, "source": _source(fc, hello, result),
                "min_temp": fc.get("MinimumTemperature", 40), "max_temp": fc.get("MaximumTemperature", 80),
                "min_value": fc.get("MinimumCommand", 30), "max_value": fc.get("MaximumCommand", 100)}
    for key in ("Value", "FlatValue", "SelectedValue"):
        if key in fc:
            return {"type": "flat", "name": name, "value": float(fc[key])}
    result.warnings.append(f"'{name}' eğrisinin türü tanınmadı; atlandı.")
    return None


def import_config(data, hello):
    """FanControl dosyası ya da bu programın kendi yedeği -> ImportResult."""
    result = ImportResult()
    if isinstance(data, dict) and data.get(OWN_FORMAT):
        result.curves = list(data.get("curves", []))
        result.channels = {cid: ch for cid, ch in data.get("channels", {}).items()
                           if cid in _ids(hello, "controls")}
        if isinstance(data.get("game_curve"), dict):
            result.game_curve = data["game_curve"]
        result.notes.append(f"LightCore yedeği: {len(result.curves)} eğri, {len(result.channels)} fan"
                            + (", oyun eğrisi." if result.game_curve else "."))
        return result
    fc = data.get("FanControl") if isinstance(data, dict) else None
    if not isinstance(fc, dict):
        raise ValueError("Bu dosya bir FanControl yapılandırması ya da LightCore yedeği değil.")
    result.notes.append(f"FanControl yapılandırması (sürüm {data.get('__VERSION__', '?')}).")
    for c in fc.get("FanCurves", []):
        curve = convert_curve(c, hello, result)
        if curve:
            result.curves.append(curve)
    curve_names = {c["name"] for c in result.curves}
    for ctrl in fc.get("Controls", []):
        fc_id, nick = ctrl.get("Identifier"), ctrl.get("NickName") or ctrl.get("Identifier")
        cid = map_id(fc_id, hello, "controls")
        if not cid:
            if ctrl.get("Enable"):
                result.warnings.append(f"'{nick}' ({fc_id}) bu bilgisayarda bulunamadı; atlandı.")
            continue
        ch = {"name": nick, "offset": ctrl.get("SelectedOffset", 0) or 0,
              "minimum": ctrl.get("MinimumPercent", 0) or 0, "start": ctrl.get("SelectedStart", 0) or 0,
              "stop": ctrl.get("SelectedStop", 0) or 0,
              "step_up": ctrl.get("SelectedCommandStepUp", 8) or 100,
              "step_down": ctrl.get("SelectedCommandStepDown", 8) or 100}
        curve = (ctrl.get("SelectedFanCurve") or {}).get("Name")
        if not ctrl.get("Enable"):
            ch["mode"] = "auto"
        elif ctrl.get("ManualControl"):
            ch.update(mode="fixed", value=float(ctrl.get("ManualControlValue", 50)))
        elif curve in curve_names:
            ch.update(mode="curve", curve=curve)
        else:
            ch["mode"] = "auto"
            if curve:
                result.warnings.append(f"'{nick}': '{curve}' eğrisi aktarılamadığı için BIOS'ta bırakıldı.")
        if ch["mode"] != "auto" and ch["stop"] > 0:
            result.notes.append(f"'{nick}': durma yüzdesi %{ch['stop']:g} — bu program fanı tamamen durdurmaz, "
                                "en düşük güvenli hıza indirir.")
        result.channels[cid] = ch
    enabled = sum(1 for ch in result.channels.values() if ch["mode"] != "auto")
    result.notes.append(f"{len(result.curves)} eğri, {len(result.channels)} fan aktarıldı ({enabled} tanesi "
                        "program tarafından sürülecek, diğerleri BIOS'ta).")
    return result


def export_config(fan_cfg):
    """Bu programın fan ayarları yedeği (içe aktarırken tanınır)."""
    out = {OWN_FORMAT: 1, "curves": fan_cfg.get("curves", []), "channels": fan_cfg.get("channels", {})}
    if fan_cfg.get("game_curve"):
        out["game_curve"] = fan_cfg["game_curve"]
    return out
