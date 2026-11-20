"""Ayarlar sayfası: özellikleri açıp kapatma, oyun modu, kurulum kontrolü."""
import subprocess
import threading
import tkinter as tk

import os
from tkinter import messagebox

from . import fans as fanctl
from . import lang
from .launcher import find_openrgb
from .ui_fans import Dropdown, find_fan_lib
from .ui import ACCENT, AMBER, CARD, CONTROL, CONTROL_EDGE, FONT, GREEN, MUTED, TEXT, THEME, THEMES, Pill, Toggle

FEATURES = (("rgb", "LightRGB", "RGB ve Philips Hue. Kapalıyken OpenRGB hiç başlamaz."),
            ("fan", "LightFan", "Fan kontrolü. Kapalıyken FanHost çalışmaz, fanları BIOS sürer."),
            ("snap", "LightSnap", "Ekran görüntüsü. Kapalıyken kısayollar Windows'tan kaldırılır."))
CONFLICTS = {"signalrgb.exe": "SignalRGB", "msi center.exe": "MSI Center", "fancontrol.exe": "FanControl"}


class SettingsPage:
    def __init__(self, app):
        self.app = app
        self.page = app._page("Ayarlar")
        self.feature_toggles = {}
        self.delete_buttons = {}
        self._build()

    def cfg(self):
        return self.app.config.setdefault("game", {})

    def _build(self):
        app = self.app
        box = app._section(self.page, "Özellikler")
        for key, title, desc in FEATURES:
            _, right, _ = app._row(box, title, desc)
            delete = Pill(right, "Bileşenlerini sil", lambda k=key: self._delete(k), CARD)
            self.delete_buttons[key] = delete
            toggle = Toggle(right, lambda k=key: self._flip_feature(k), CARD)
            toggle.set(app.feature(key))
            toggle.pack(side="right")
            self.feature_toggles[key] = toggle
            self._show_delete(key)
        box = app._section(self.page, "Oyun modu")
        _, right, _ = app._row(box, "Oyun modu", "Kapalıyken otomatik algılama çalışmaz, tepsi menüsünde de görünmez.")
        self._setting_toggle(right, "enabled", True, app.game_settings_changed)
        _, right, self.game_desc = app._row(
            box, "Şimdi", "Işıklar söner, ekran görüntüsü kısayolları kapanır, eğri kullanan fanlar oyun eğrisine geçer: "
                          "sessiz başlar, ısındıkça hızlanır. Otomatik fanlar BIOS'ta kalır.")
        self.game_button = Pill(right, "Oyun modunu aç", app.toggle_game, CARD, style="chip", width=170)
        self.game_button.pack()
        _, right, _ = app._row(box, "Otomatik", "Ekranı kaplayan pencere ve 10 sn boyunca %50 üstü ekran kartı yükü olunca "
                                                "açılır; bitince 30 sn sonra kapanır.")
        self._setting_toggle(right, "auto", True, app.game_settings_changed)
        _, right, _ = app._row(box, "Işıklar açık kalsın", "Oyunda ışıklar ve efekt sürer. Çok az işlemci kullanır; en hafifi "
                                                           "ışıkların sönmesidir.")
        self._setting_toggle(right, "keep_lights", False)
        _, right, _ = app._row(box, "OpenRGB'yi tam kapat", "En hafifi. Oyundan çıkınca renkler 10-30 sn sonra gelir. "
                                                            "Kapalıysa yalnızca ışıklar söner, renkler hemen döner.")
        self._setting_toggle(right, "close_openrgb", True)
        _, right, _ = app._row(box, "Dışlananlar", "Oyun sayılmayacak programların exe adları, virgülle. "
                                                   "Örnek: premiere.exe, blender.exe")
        self.exclude = tk.StringVar(value=self.cfg().get("exclude", ""))
        entry = tk.Entry(right, textvariable=self.exclude, width=32, bg=CONTROL, fg=TEXT, insertbackground=TEXT,
                         relief="flat", font=(FONT, 10), highlightthickness=1, highlightbackground=CONTROL_EDGE,
                         highlightcolor=ACCENT)
        entry.pack(ipady=5)
        entry.bind("<FocusOut>", lambda _e: self._save_exclude())
        box = app._section(self.page, "Görünüm")
        _, right, _ = app._row(box, "Tema", "Koyu ya da açık. Yeniden açınca geçerli olur.")
        Dropdown(right, THEMES, THEME, self._set_theme, width=160).pack()
        box = app._section(self.page, "Dil")
        _, right, _ = app._row(box, "Program dili", "Yeniden açınca geçerli olur.")
        Dropdown(right, (("tr", "Türkçe"), ("en", "English")), app.config.get("language") or lang.default_language(),
                 self._set_language, width=160).pack()
        self.checks_box = app._section(self.page, "Kurulum kontrolü")
        _, right, self.checks_desc = app._row(self.checks_box, "Eksikler", "Kontrol ediliyor...")
        Pill(right, "Yeniden kontrol et", self.run_checks, CARD).pack()

    def _setting_toggle(self, parent, key, default, then=lambda: None):
        toggle = Toggle(parent, lambda: self._flip_setting(toggle, key, then), CARD)
        toggle.set(self.cfg().get(key, default))
        toggle.pack()
        return toggle

    def _flip_setting(self, toggle, key, then):
        toggle.set(not toggle.value)
        self.cfg()[key] = toggle.value
        self.app._save_config()
        then()

    def _save_exclude(self):
        self.cfg()["exclude"] = self.exclude.get()
        self.app._save_config()

    def excluded(self):
        return {n.strip().casefold() for n in self.cfg().get("exclude", "").split(",") if n.strip()}

    def _set_theme(self, key):
        self.app.config["theme"] = key
        self.app._save_config()
        self.app._status("Tema değişti: programı kapatıp yeniden açınca geçerli olur.", GREEN)

    def _set_language(self, key):
        self.app.config["language"] = key
        self.app._save_config()

    def _flip_feature(self, key):
        self.app.set_feature(key, not self.app.feature(key))
        self.feature_toggles[key].set(self.app.feature(key))
        self._show_delete(key)

    def _show_delete(self, key):
        button = self.delete_buttons[key]
        if self.app.feature(key):
            button.pack_forget()
        else:
            button.pack(side="left", padx=(0, 12))

    def _delete(self, key):
        """Önce ne silineceği gösterilir. FanControl, OpenRGB'nin başka yerdeki kopyası ve ekran görüntülerine dokunulmaz."""
        name = dict((k, t) for k, t, _d in FEATURES)[key]
        paths = self.app.component_paths(key)
        listed = "\n".join(os.path.basename(p) + "\\" for p in paths) or "(klasör yok)"
        if not messagebox.askyesno(name, f"{name} bileşenleri silinsin mi?\n\n{listed}\n\nGeri getirmek için paketi yeniden "
                                         "indirmek gerekir. FanControl ve ekran görüntülerine dokunulmaz.", parent=self.app.root):
            return
        settings = messagebox.askyesno(name, f"{name} ayarları da silinsin mi?", parent=self.app.root)
        try:
            self.app.delete_components(key, settings)
            self.app._status(f"{name} bileşenleri silindi.", GREEN)
        except (OSError, RuntimeError) as e:
            self.app._status(f"Silinemedi: {e}", AMBER)

    def game_changed(self, on):
        self.game_button.set_active(on)
        self.game_button.set_text("Oyun modunu kapat" if on else "Oyun modunu aç", keep_width=True)

    # ---- kurulum kontrolü: yalnızca açılışta ve istenince, arka planda
    def run_checks(self):
        self.checks_desc.configure(text="Kontrol ediliyor...", fg=MUTED)
        threading.Thread(target=self._check_work, daemon=True).start()

    def _check_work(self):
        problems = []
        app = self.app
        if not fanctl.is_admin():
            problems.append("Program yönetici olarak açık değil: RAM, anakart ve fanlar çalışmaz.")
        if app.feature("rgb") and app.args.openrgb:
            try:
                find_openrgb(app.args.openrgb)
            except FileNotFoundError:
                problems.append("OpenRGB bulunamadı: Windows sürümünü programın yanındaki 'openrgb' klasörüne aç.")
        if app.feature("fan"):
            if not find_fan_lib():
                problems.append("LibreHardwareMonitor bulunamadı: programın yanındaki 'fan' klasörü eksik, paketi "
                                "yeniden aç.")
            if not fanctl.pawnio_installed():
                problems.append(f"Fan kontrolü için PawnIO sürücüsü gerekli: {fanctl.PAWNIO_URL} adresinden kur.")
        try:
            out = subprocess.run(["tasklist", "/NH", "/FO", "CSV"], capture_output=True, text=True, errors="replace",
                                 timeout=15, creationflags=fanctl.CREATE_NO_WINDOW).stdout.casefold()
            for exe, label in CONFLICTS.items():
                if f'"{exe}"' in out:
                    problems.append(f"{label} açık: aynı donanımı sürdüğü için kapat.")
        except (OSError, subprocess.SubprocessError):
            pass
        app.events.put(("checks", problems))

    def show_checks(self, problems):
        if problems:
            self.checks_desc.configure(text="\n".join("• " + lang.T(p) for p in problems), fg=AMBER)  # satır satır çevrilir
        else:
            self.checks_desc.configure(text="Eksik yok.", fg=GREEN)
