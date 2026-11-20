"""Arayüz dili. Metinler kodda Türkçe yazılır; İngilizce seçiliyse pencere öğelerine yazılırken çevrilir.
Karşılığı olmayan metin Türkçe kalır: arayüz bozulmaz. Tanı kayıtları (Log) çevrilmez."""
import ctypes
import re
import tkinter as tk

EN = {
    "Işıklar": "Lights", "Fanlar": "Fans", "Ayarlar": "Settings", "Cihazlar": "Devices", "Efekt": "Effect", "Renkler": "Colors",
    "Sabit": "Static", "Renk geçişi": "Color cycle", "Nefes": "Breathe", "Akış": "Flow", "Gökkuşağı": "Rainbow", "Pastel": "Pastel",
    "Nabız": "Pulse", "Mum": "Candle", "Neon": "Neon", "Yıldız": "Twinkle", "Disko": "Disco", "Alarm": "Alarm", "Ateş": "Fire",
    "Lav": "Lava", "Aurora": "Aurora", "Okyanus": "Ocean", "Fırtına": "Storm", "Oyun": "Game", "Gün batımı": "Sunset",
    "Siber": "Cyber", "Vaporwave": "Vaporwave", "Galaksi": "Galaxy", "Orman": "Forest", "Buzul": "Glacier", "Sakura": "Sakura",
    "Şeker": "Candy", "Altın": "Gold", "Retro": "Retro", "Matrix": "Matrix", "Şömine": "Fireplace", "Kuzey ışığı": "Northern lights",
    "Gece": "Night", "Seçili ilk renk sabit yanar": "The first selected color stays on",
    "Seçili renkler arasında yumuşakça geçer": "Fades smoothly between the selected colors",
    "Yavaşça yanıp söner; birden çok renk seçiliyse her nefeste sıradakine geçer": "Fades in and out; moves to the next selected color on each breath",
    "Seçili renkler arasında beklemeden, sürekli akar": "Flows continuously between the selected colors",
    "Bütün renkler arasında döner": "Cycles through all colors", "Gökkuşağının yumuşak tonları": "Soft rainbow tones",
    "Kalp atışı gibi iki vuruş": "Two beats, like a heartbeat", "Mum ışığı gibi hafif titrer": "Flickers gently like a candle",
    "Neon tabela gibi sabit yanar, ara sıra titrer": "Steady like a neon sign, flickers now and then",
    "Loş zeminde ara sıra parlar": "Sparkles now and then on a dim base",
    "Renkler vuruşla değişir; 2 renkten azı seçiliyse rastgele renkler": "Colors change on the beat; random colors if fewer than 2 are selected",
    "İki renk arasında hızlı gidip gelir; 2 renk seçili değilse kırmızı-mavi": "Swings fast between two colors; red and blue if 2 are not selected",
    "Kızıl ve sarı arasında düzensiz titrer (kendi renkleri)": "Flickers between red and yellow (own colors)",
    "Koyu kırmızı ve turuncu, ağır dalgalanma (kendi renkleri)": "Dark red and orange, slow waves (own colors)",
    "Kuzey ışıkları: yeşil, turkuaz, mor (kendi renkleri)": "Northern lights: green, turquoise, purple (own colors)",
    "Lacivert ve turkuaz dalgalar (kendi renkleri)": "Navy and turquoise waves (own colors)",
    "Koyu mavi zeminde ara sıra şimşek (kendi renkleri)": "Occasional lightning on dark blue (own colors)",
    "Ekran Kartı": "Graphics Card", "Klavye": "Keyboard", "Anakart": "Motherboard", "Fare": "Mouse", "Soğutucu": "Cooler", "LED Şerit": "LED Strip",
    "Kulaklık": "Headset", "Işık": "Light", "Hoparlör": "Speaker", "Kasa": "Case", "Kasa Fanları": "Case Fans",
    "⏻  Bilgisayar açılınca başlasın": "⏻  Start with Windows", "⟳  Cihazları yeniden tara": "⟳  Rescan devices",
    "Hazır ayar ve efekt": "Preset and effect", "Hazır ayar": "Preset", "Seçili değil": "None selected", "Kapalı": "Off",
    "Işıkları kapat": "Lights off", "Işıkları aç": "Lights on", "Kendi ayarlarım": "My presets",
    "O anki renk ve efekti adıyla kaydet; Hazır ayar listesine eklenir. Yedek, kendi ayarlarını, fan eğrilerini ve kısayolları tek dosyada taşır.": "Save the current color and effect by name; it joins the preset list. A backup carries your presets, fan curves and shortcuts in one file.",
    "Kaydet": "Save", "Seçili ayarı sil": "Delete selected", "Yedeği dışa aktar": "Export backup", "Yedeği içe aktar": "Import backup",
    "Seçili renkler": "Selected colors", "Parlaklık": "Brightness", "Bütün açık cihazlar": "All enabled devices", "Efekt hızı": "Effect speed",
    "Nefes, Gökkuşağı, Pastel ve Nabız için": "For Breathe, Rainbow, Pastel and Pulse", "Her rengin süresi": "Time on each color",
    "Renk geçişinde bir renk ne kadar sabit kalsın": "How long each color stays in Color cycle", "Geçiş süresi": "Fade time",
    "Sıradaki renge kaç saniyede geçilsin": "Seconds to fade to the next color", "Cihaza özel renk": "Per-device color",
    "Efekti izliyor": "Follows the effect", "Efekte döndür": "Follow effect", "Renk seç": "Pick color", "Seç": "Select", "Ekle": "Add", "Vazgeç": "Cancel",
    "Köprü": "Bridge", "Bağlı değil": "Not connected", "Köprüyü bul": "Find bridge", "Eşleştir": "Pair", "Kullan": "Use", "IP adresi": "IP address",
    "Köprünün üstündeki büyük yuvarlak düğmeye bas, sonra 30 saniye içinde Eşleştir'e tıkla.": "Press the big round button on the bridge, then click Pair within 30 seconds.",
    "Köprü bulunamazsa IP adresini yaz (Hue uygulaması > Ayarlar > Hue Bridge'ler > i)": "If the bridge is not found, enter its IP address (Hue app > Settings > Hue Bridges > i)",
    "Lamba yok": "No lamps", "Köprü eşleşince renkli lambalar burada ve soldaki cihaz listesinde görünür.": "Once paired, color lamps appear here and in the device list.",
    "Kayıt dosyası": "Log file", "Klasörü aç": "Open folder", "Hata raporu kopyala": "Copy bug report", "Uyumluluk raporu kopyala": "Copy compatibility report",
    "Canlı durum ve son kayıtlar": "Live status and recent log", "Son 5 dakika": "Last 5 minutes",
    "Rapor panoya alındı: kullanıcı adı, adresler ve anahtarlar çıkarıldı. Göndermek senin elinde.": "Report copied: user name, addresses and keys removed. Sending it is up to you.",
    "Cihazlar yeniden taranıyor...": "Rescanning devices...", "OpenRGB başlatılıyor...": "Starting OpenRGB...", "Cihazlar taranıyor...": "Scanning devices...",
    "Cihazlar taranıyor, bu yarım dakikaya\nkadar sürebilir...": "Scanning devices, this can take\nup to half a minute...",
    "Köprü aranıyor...": "Looking for the bridge...", "LightRGB kapalı.": "LightRGB is off.", "En az bir özellik açık kalmalı.": "At least one feature must stay on.",
    "Çıkış": "Exit", "Aç": "Open", "Oyun modunu aç": "Game mode on", "Oyun modunu kapat": "Game mode off", "Yedek yüklendi.": "Backup loaded.",
    "Takıldı": "Stuck", "Fan kontrolü": "Fan control", "Kapalı: fanları anakart (BIOS) sürüyor": "Off: the motherboard (BIOS) drives the fans",
    "Sıcaklıklar": "Temperatures", "Eğriler": "Curves", "Otomatik": "Auto", "Eğri": "Curve", "Düzenle": "Edit", "Sil": "Delete", "Kapat": "Close", "Ayrıntılar…": "Details…",
    "Kalibre et": "Calibrate", "Kalibrasyonu durdur": "Stop calibration", "Donanımın kendi (BIOS) eğrisiyle çalışır.": "Runs on the hardware's own (BIOS) curve.",
    "Oyun eğrisini her zaman izler: sessiz başlar, ısındıkça hızlanır.": "Always follows the game curve: starts quiet, speeds up as it heats.",
    "Kullanan yok": "Not used", "İzlenecek fan": "Fan to follow", "Fark (ofset) %": "Offset %", "＋ Yeni eğri": "＋ New curve",
    "Oluşturmak istediğin eğri türünü seç.": "Choose the type of curve.", "Grafik": "Graph", "Doğrusal": "Linear", "Eşitle": "Sync", "Karışım": "Mix", "Tetik": "Trigger",
    "Sıcaklık kaynağı": "Temperature source", "Ad": "Name", "Birleştirme": "Function", "Birleştirilecek eğriler": "Curves to combine",
    "Hız %": "Speed %", "En düşük sıcaklık °C": "Lowest temp °C", "En yüksek sıcaklık °C": "Highest temp °C", "En düşük hız %": "Lowest speed %", "En yüksek hız %": "Highest speed %",
    "Boşta sıcaklığı °C": "Idle temp °C", "Yük sıcaklığı °C": "Load temp °C", "Boşta hızı %": "Idle speed %", "Yük hızı %": "Load speed %",
    "Histerezis (fanın her küçük sıcaklık oynamasında hız değiştirmemesi için)": "Hysteresis (so small temperature changes do not change fan speed)",
    "Artış eşiği °C": "Rise threshold °C", "Azalış eşiği °C": "Fall threshold °C", "Artış bekleme sn": "Rise delay s", "Azalış bekleme sn": "Fall delay s",
    "Eğrinin uçlarında beklemeden geç": "No delay at curve ends", "Hızlanma %/sn": "Speed up %/s", "Yavaşlama %/sn": "Slow down %/s", "En az %": "Minimum %", "Durma %": "Stop %",
    "Fanın hız değişim sınırları ve en düşük hızı. Adım 100: sınırsız, hemen değişir.": "Speed change limits and minimum speed. Step 100: no limit, changes at once.",
    "Noktayı sürükle, boş yere çift tıklayıp nokta ekle, sağ tıkla sil. Değişiklik anında geçerli olur.": "Drag a point, double-click to add, right-click to delete. Changes apply at once.",
    "En düşük sıcaklıkta en düşük hız, en yüksekte en yüksek hız; arası düz çizgi.": "Lowest speed at the lowest temperature, highest at the highest; a straight line between.",
    "Her zaman aynı hız.": "Always the same speed.", "İzlenecek fanı ve farkı üstteki karttan değiştirebilirsin.": "Change the followed fan and offset on the card above.",
    "Birden çok eğriyi birleştirir (en yüksek, en düşük, ortalama ya da toplam).": "Combines curves (highest, lowest, average or sum).",
    "Yük sıcaklığına ulaşınca yük hızına geçer, boşta sıcaklığına inince boşta hızına döner.": "Goes to load speed at the load temperature, back to idle speed at the idle temperature.",
    "Yedeği içe aktar ": "Import backup", "FanControl'den geçiş ve yedek": "FanControl migration and backup", "Yedeği içe aktar": "Import backup",
    "Bu programın yedeğini ve FanControl yedeğini (.json) bu pencerenin üstüne sürükleyip bırak ya da dosya seç. Eğriler, fan adları ve ayarlar aktarılır; önce ne aktarılacağı gösterilir.": "Drop a backup of this program or of FanControl (.json) on this window, or choose a file. Curves, fan names and settings are imported; you see what will change first.",
    "Dosya seç…": "Choose file…", "Bu programın yedeği": "This program's backup", "Dışa aktar…": "Export…",
    "Eğrileri ve fan ayarlarını dosyaya kaydet; bu dosya da aynı 'Dosya seç' ile geri yüklenir.": "Save curves and fan settings to a file; restore it with the same 'Choose file'.",
    "Önceki ayarlara dön": "Restore earlier settings", "Fan ayarı değişince önceki hali otomatik saklanır, son beşi durur.": "Fan settings are saved automatically when changed; the last five are kept.",
    "Henüz yedek yok": "No backups yet", "Yedek seç": "Choose backup", "Fan kontrolü açılınca görünür.": "Shown when fan control is on.", "Fan kontrolü açılınca seçilebilir.": "Available when fan control is on.",
    "Turuncu: işlemci °C · Turkuaz: ekran kartı °C · Mavi: en hızlı fan %": "Orange: CPU °C · Turquoise: GPU °C · Blue: fastest fan %",
    "Kasa, işlemci ve ekran kartı fanlarını sıcaklığa göre ya da sabit hızda sürer. Güvenlik: hiçbir fan en az hızın altına inmez; işlemci 90°C'yi ya da ekran kartı 85°C'yi geçerse bütün fanlar %100'e çıkar; program kapanınca ya da donarsa fanlar anakartın (BIOS) kontrolüne geri döner.": "Drives case, CPU and GPU fans by temperature. Safety: no fan goes below its minimum; above 90°C CPU or 85°C GPU all driven fans go to 100%; if the program closes or hangs, the fans return to the motherboard (BIOS).",
    "Ekran kartı fanlarını gizlemek için tıkla": "Click to hide graphics card fans", "Ekran kartı fanlarını görmek için tıkla": "Click to show graphics card fans",
    "Görüntü al": "Capture", "Şimdi al": "Capture now", "Görüntü panoya alınır; istediğin yere Ctrl+V ile yapıştırırsın.": "The image goes to the clipboard; paste it anywhere with Ctrl+V.",
    "Bölge": "Region", "Tam ekran": "Full screen", "Bütün ekranlar": "All screens", "Sürükleyerek bir alan seç": "Drag to select an area", "İmlecin olduğu ekran": "The screen under the cursor",
    "Bütün monitörler tek görüntüde": "All monitors in one image", "Kısayollar": "Shortcuts", "Tuşlara bas…": "Press keys…",
    "Düzenle'ye bas, sonra istediğin tuşa ya da tuş birleşimine bas (PrtScn dahil). Esc: vazgeç.": "Click Edit, then press the key or combination you want (PrtScn included). Esc: cancel.",
    "En az bir Ctrl, Alt ya da Shift ile birlikte bas (F tuşları ve PrtScn tek başına olur).": "Press with Ctrl, Alt or Shift (F keys and PrtScn work alone).",
    "Link ile paylaşım": "Share by link", "Sağlayıcı": "Provider", "Özel sunucu": "Custom server",
    "Yükleme yalnızca Yükle'ye basınca yapılır. Linki alan herkes görüntüyü görebilir; şifre ya da özel mesaj içeren görüntüleri yükleme.": "Uploads happen only when you click Upload. Anyone with the link can see the image; do not upload passwords or private messages.",
    "Dosyayı form ile alıp yanıtta bağlantı döndüren adres ve dosya alanının adı.": "An address that takes the file as a form and replies with a link, and the file field name.",
    "Otomatik yükle": "Auto upload", "Her görüntüyü sormadan yükler ve linki panoya koyar. Görüntü panoda değil, internette olur; dikkatli aç.": "Uploads every image without asking and copies the link. The image goes online; enable with care.",
    "Geçmiş": "History", "Son görüntüleri tut": "Keep recent images", "Kayıt": "Saving", "Otomatik kaydet": "Auto save", "Panoya alındı": "Copied", "Yükleniyor…": "Uploading…", "Link panoya alındı": "Link copied",
    "Alan seçmek için sürükle · Esc: vazgeç": "Drag to select · Esc: cancel", "Ctrl+V ile yapıştırabilirsin.": "Paste with Ctrl+V.", "Yükle": "Upload", "Yazı ekle": "Add text", "Yazı": "Text", "Ok": "Arrow", "Kalem": "Pen",
    "Kareleme": "Pixelate", "Kopyala": "Copy", "Panoya alınamadı": "Could not copy", "Yüklenemedi": "Upload failed", "Kaydedildi": "Saved", "Kaydedilemedi": "Could not save",
    "Özellikler": "Features", "Oyun modu": "Game mode", "Şimdi": "Now", "Dil": "Language",
    "RGB ve Philips Hue. Kapalıyken OpenRGB hiç başlamaz.": "RGB and Philips Hue. When off, OpenRGB does not start.",
    "Fan kontrolü. Kapalıyken FanHost çalışmaz, fanları BIOS sürer.": "Fan control. When off, FanHost does not run and the BIOS drives the fans.",
    "Ekran görüntüsü. Kapalıyken kısayollar Windows'tan kaldırılır.": "Screenshots. When off, the shortcuts are removed from Windows.",
    "Kapalıyken otomatik algılama çalışmaz, tepsi menüsünde de görünmez.": "When off, there is no auto detection and no tray menu entry.",
    "Işıklar söner, ekran görüntüsü kısayolları kapanır, eğri kullanan fanlar oyun eğrisine geçer: sessiz başlar, ısındıkça hızlanır. Otomatik fanlar BIOS'ta kalır.": "Lights go off, screenshot shortcuts are disabled, fans on a curve switch to the game curve: quiet at first, faster as it heats. Auto fans stay on the BIOS.",
    "Ekranı kaplayan pencere ve 10 sn boyunca %50 üstü ekran kartı yükü olunca açılır; bitince 30 sn sonra kapanır.": "Turns on with a full-screen window and over 50% GPU load for 10 s; turns off 30 s after it ends.",
    "Işıklar açık kalsın": "Keep lights on", "Oyunda ışıklar ve efekt sürer. Çok az işlemci kullanır; en hafifi ışıkların sönmesidir.": "Lights and effect keep running in games. Uses very little CPU; lights off is the lightest.",
    "OpenRGB'yi tam kapat": "Close OpenRGB completely", "En hafifi. Oyundan çıkınca renkler 10-30 sn sonra gelir. Kapalıysa yalnızca ışıklar söner, renkler hemen döner.": "The lightest. Colors return 10-30 s after the game. When off, lights only go dark and return at once.",
    "Dışlananlar": "Excluded", "Oyun sayılmayacak programların exe adları, virgülle. Örnek: premiere.exe, blender.exe": "Exe names not to treat as games, comma separated. Example: premiere.exe, blender.exe",
    "Kurulum kontrolü": "Setup check", "Eksikler": "Missing", "Kontrol ediliyor...": "Checking...", "Yeniden kontrol et": "Check again", "Eksik yok.": "Nothing missing.",
    "Program yönetici olarak açık değil: RAM, anakart ve fanlar çalışmaz.": "Not running as administrator: RAM, motherboard and fans will not work.",
    "LibreHardwareMonitor bulunamadı: programın yanındaki 'fan' klasörü eksik, paketi yeniden aç.":
        "LibreHardwareMonitor not found: the 'fan' folder next to the program is missing, extract the package again.",
    "OpenRGB bulunamadı: Windows sürümünü programın yanındaki 'openrgb' klasörüne aç.": "OpenRGB not found: extract the Windows build to the 'openrgb' folder next to the program.",
    "Bileşenlerini sil": "Delete components", "Program dili": "Program language", "Yeniden açınca geçerli olur.": "Applies after restart.",
}
EN["JRAINBOW 1-2 · doğrudan HID"] = "JRAINBOW 1-2 · direct HID"
EN.update({
    "Silmek için listeden kendi kaydettiğin bir ayarı seç.": "Select one of your own saved presets to delete.",
    "Kaydetmek için bir ad yaz.": "Enter a name to save.",
    "Fanlar için doğrudan HID açılamadı, OpenRGB kullanılıyor (Log).": "Could not open direct HID for fans, using OpenRGB (Log).",
    "Önce 'Köprüyü bul' ya da IP adresi yaz.": "First use 'Find bridge' or enter an IP address.",
    "Ağda Hue Bridge bulunamadı. Lambalar köprüsüz (Bluetooth) kullanılıyorsa bilgisayar onlara ulaşamaz.":
        "No Hue Bridge found on the network. Lamps used without a bridge (Bluetooth) cannot be reached by the computer.",
    "Bırakılan dosya .json değil; FanControl yedeği .json olmalı.": "The dropped file is not .json; a FanControl backup must be .json.",
    "Fan kontrolü için programı yönetici olarak aç (sağ tık > Yönetici olarak çalıştır).":
        "Open the program as administrator for fan control (right click > Run as administrator).",
    "LibreHardwareMonitor bulunamadı: programın fan klasörü eksik.": "LibreHardwareMonitor not found: the program's fan folder is missing.",
    "Başlatılıyor...": "Starting...",
    "Sıcaklık sınırı aşıldı, kalibrasyon durduruldu.": "Temperature limit exceeded, calibration stopped.",
    "Sıcaklık normale döndü.": "Temperature is back to normal.",
    "Görüntü panoya yazılamadı.": "Could not write the image to the clipboard.",
    "Pano başka bir program tarafından kullanılıyor.": "The clipboard is in use by another program.",
    "Özel sunucu adresi http:// ya da https:// ile başlamalı.": "The custom server address must start with http:// or https://.",
    "Ekran okunamadı.": "Could not read the screen.", "Pano için bellek ayrılamadı.": "Could not allocate clipboard memory.",
    "Bluetooth LED şerit": "Bluetooth LED strip", "Şerit": "Strip", "Şerit bul": "Find strip", "Unut": "Forget",
    "Karışık renkler beyazımsı ya da pembe görünüyorsa eğriyi artır.":
        "If mixed colors look whitish or pink, raise the curve.",
    "Renk düzeltme": "Color correction", "Kırmızı %": "Red %", "Yeşil %": "Green %", "Mavi %": "Blue %",
    "Bazı şeritlerde bir renk diğerlerinden parlak yanar: sarı yeşile kaçıyorsa yeşili azalt.":
        "On some strips one color is brighter than the others: if yellow looks green, lower green.",
    "Kayıtlı şerit yok. Telefondaki Lotus Lantern uygulamasını kapat, sonra Şerit bul'a bas.":
        "No strip saved. Close the Lotus Lantern app on your phone, then press Find strip.",
    "Aranıyor... (10 saniye kadar)": "Searching... (about 10 seconds)",
    "Şerit ekle": "Add strip", "Ekle": "Add", "Bağlı değil: yeniden denenecek": "Not connected: will retry",
    "Telefondaki Lotus Lantern uygulamasını kapat, sonra Şerit bul'a bas. Kayıtlı ve bağlı şeritler aramada görünmez.":
        "Close the Lotus Lantern app on your phone, then press Find strip. Saved and connected strips do not show up in the search.",
    "Renk düzeltme: sarı yeşile kaçıyorsa yeşili azalt; karışık renkler beyazımsı ya da pembe görünüyorsa eğriyi artır.":
        "Color correction: if yellow looks green, lower green; if mixed colors look whitish or pink, raise the curve.",
    "Yeni şerit bulunamadı. Şerit açık mı, telefon uygulaması kapalı mı, bilgisayarın Bluetooth'u açık mı?":
        "No new strip found. Is the strip on, the phone app closed and the computer's Bluetooth on?",
    "Şerit bulunamadı. Şerit açık mı, telefon uygulaması kapalı mı, bilgisayarın Bluetooth'u açık mı?":
        "No strip found. Is the strip on, the phone app closed and the computer's Bluetooth on?",
    "tmpfiles (2 gün sonra silinir)": "tmpfiles (deleted after 2 days)", "Uguu (kısa süre sonra silinir)": "Uguu (deleted after a short time)",
})
_FAN_PARTS = (("fan program tarafından kontrol ediliyor", "fans controlled by the program"), ("fan BIOS kontrolünde", "fans controlled by the BIOS"),
              ("ekran kartı program tarafından kontrol ediliyor", "graphics card controlled by the program"),
              ("ekran kartı BIOS kontrolünde", "graphics card controlled by the BIOS"),
              ("bir fan 2 dakikadır tam hızda, eğriye ya da soğutmaya bak", "a fan has been at full speed for 2 minutes, check the curve or cooling"),
              ("eğri sensörü okunamadı", "curve sensor could not be read"),
              ("yerine işlemci ya da ekran kartı kullanılıyor: eğrinin kaynağını seç", "using CPU or graphics card instead: choose the curve's source"),
              ("arayüz sinyali gecikti, fanlar BIOS'ta", "interface signal delayed, fans on BIOS"))


def _fan_status(match):
    text = match.group(1)
    for tr, en in sorted(_FAN_PARTS, key=lambda p: -len(p[0])):
        text = text.replace(tr, en)
    return "On · " + text


# Pano sayfası ve simge rayı.
for _tr, _en in {
    "Pano": "Dashboard", "Işıklar şu an": "Lights are on", "ayarında": "preset", "bir ayarda": "setting", "özel": "a custom",
    "kapalı": "off", "Bir hazır ayar seçince yeniden yanar": "Pick a preset to turn them back on",
    "Sıradaki ayara geç  →": "Next preset  →", "İşlemci": "Processor", "Ekran kartı": "Graphics card",
    "Fan eğrisi": "Fan curve", "Ekran görüntüleri": "Screenshots", "Olaylar": "Events", "Oyun modu": "Game mode",
    "Hazır ayarlar": "Presets", "Tümü": "All", "Bölge al": "Capture region", "Veri bekleniyor": "Waiting for data",
    "Cihazlar taranıyor...": "Scanning devices...", "LightRGB kapalı.": "LightRGB is off.", "LightFan kapalı.": "LightFan is off.",
    "LightSnap kapalı.": "LightSnap is off.",
    "Fan kontrolü kapalı: sıcaklık ve fan verisi okunmuyor. LightFan sayfasından aç.":
        "Fan control is off: no temperature or fan data. Turn it on from the LightFan page.",
    "İşlemci sıcaklığı okunamıyor.": "Cannot read the processor temperature.",
    "Ekran kartı sıcaklığı okunamıyor.": "Cannot read the graphics card temperature.",
    "fanı program sürüyor": "fans driven by the program", "fan BIOS'ta": "fans on BIOS", "Fan bulunamadı.": "No fans found.",
    "Eğri kullanan fan yok. LightFan sayfasından bir fana eğri seç.": "No fan uses a curve. Pick a curve for a fan on the LightFan page.",
    "Ayarlar'dan kapatılmış": "Turned off in Settings", "Açık: ışıklar ve fanlar oyun düzeninde": "On: lights and fans are in game setup",
    "Bekliyor · açınca pencere simge tepsisine iner": "Waiting · the window goes to the tray when turned on",
    "Henüz görüntü yok ya da geçmiş kapalı.": "No screenshots yet, or history is off.",
    "Panoya almak için tıkla": "Click to copy", "Henüz kayıt yok.": "No events yet.", "Cihazlar ve durum": "Devices and status",
    "Oyun eğrisi": "Game curve", "Program kullanımı": "Program usage",
    "Görünüm": "Appearance", "Tema": "Theme", "Koyu": "Dark", "Aydınlık": "Light",
    "Koyu ya da açık. Yeniden açınca geçerli olur.": "Dark or light. Takes effect after reopening.",
    "Tema değişti: programı kapatıp yeniden açınca geçerli olur.": "Theme changed: close and reopen the program to apply it.",
    "Kayıt klasörü": "Save folder", "Değiştir": "Change", "Varsayılan": "Default", "Klasörde göster": "Show in folder",
    "Her görüntü panoya alınırken bu klasöre de kaydedilir.": "Every image is also saved to this folder when copied.",
    "Ekran görüntülerinin kaydedileceği klasör": "Folder for saved screenshots",
    "Oyun modu eğrisi": "Game mode curve", "Oyun eğrisi": "Game curve",
    "Ekran Görüntüsü": "Screenshots", "OpenRGB'ye bağlanılamadı": "OpenRGB did not respond",
    "PawnIO'yu kur": "Install PawnIO", "Sürüm": "Version",
    "Fan kontrolü için PawnIO sürücüsü gerekli: 'PawnIO'yu kur' düğmesiyle kur.":
        "Fan control needs the PawnIO driver: install it with 'Install PawnIO'.",
    "Fan kontrolü için PawnIO sürücüsü gerekli: Ayarlar > Kurulum kontrolü'ndeki 'PawnIO'yu kur' düğmesiyle kur, sonra "
    "fan kontrolünü yeniden aç.": "Fan control needs the PawnIO driver: install it with 'Install PawnIO' under Settings > "
                                  "Setup check, then turn fan control on again.",
    "PawnIO kuruluyor (winget, resmi paket)... Bu bir iki dakika sürebilir.":
        "Installing PawnIO (winget, official package)... This can take a minute or two.",
    "PawnIO kuruldu.": "PawnIO installed.",
    "PawnIO kuruldu. Bilgisayarı yeniden başlatman gerekebilir.": "PawnIO installed. You may need to restart the PC.",
    "winget bulunamadı; PawnIO'nun indirme sayfası açıldı.": "winget not found; opened the PawnIO download page.",
    "Fan kontrolü Windows tarafından kapatıldı; fanları anakart sürüyor.":
        "Fan control was closed by Windows; the motherboard drives the fans.",
    "Noktayı sürükle, boş yere çift tıklayıp nokta ekle, sağ tıkla sil. Değişiklik anında geçerli olur. Kesikli çizgi: "
    "işlemci ve ekran kartından sıcak olanı.": "Drag a point, double-click empty space to add one, right-click to "
                                               "delete. Changes apply at once. Dashed line: the hotter of CPU and GPU.",
}.items():
    EN.setdefault(_tr, _en)

_MODE_NAMES = {k: EN[k] for k in ("Sabit", "Renk geçişi", "Nefes", "Akış", "Gökkuşağı", "Pastel", "Nabız", "Mum", "Neon", "Yıldız",
                                  "Disko", "Alarm", "Ateş", "Lav", "Aurora", "Okyanus", "Fırtına", "Kapalı")}
PATTERNS = [
    (r"^(" + "|".join(_MODE_NAMES) + r") · %(\d+)(.*)$", lambda m: f"{_MODE_NAMES[m.group(1)]} · {m.group(2)}%{T_rest(m.group(3))}"),
    (r"^(\d+) cihaz · (\d+) açık · fanlar HID ile$", r"\1 devices · \2 on · fans via HID"),
    (r"^(\d+) cihaz · (\d+) açık · fanlar OpenRGB ile$", r"\1 devices · \2 on · fans via OpenRGB"),
    (r"^(\d+) cihaz hazır\.$", r"\1 devices ready."),
    (r"^Açık · (.*)$", _fan_status),
    (r"^Kayıtlı: (.*)\. Telefon uygulaması bağlıyken program şeride bağlanamaz\.$",
     r"Saved: \1. The program cannot connect while the phone app is connected."),
    (r"^Bağlı: (.*)\. Başka bir şerit eklemek için önce Unut'a bas\.$", r"Connected: \1. Press Forget first to add another strip."),
    (r"^(.*) şu an ulaşılamıyor, birazdan yeniden denenecek$", r"\1 cannot be reached now, will retry shortly"),
    (r"^Bulundu · sinyal (-?\d+) dBm \(0'a yakın olan daha yakındır\)$", r"Found · signal \1 dBm (closer to 0 is nearer)"),
    (r"^(\d+) şerit bulundu\. Seninkini ekle; yanlış olanı Unut ile kaldırabilirsin\.$",
     r"\1 strips found. Add yours; a wrong one can be removed with Forget."),
    (r"^Eklendi: (.*)$", r"Added: \1"), (r"^LED Şerit (\d+)$", r"LED Strip \1"),
    (r"^LED Şerit( \d+)? · (.*)$", r"LED Strip\1 · \2"),
    (r"^Bulundu: (.*)$", r"Found: \1"), (r"^Bluetooth · (.*)$", r"Bluetooth · \1"),
    (r"^Bluetooth taraması başarısız: (.*)$", r"Bluetooth scan failed: \1"),
    (r"^PawnIO kurulamadı \(kod (-?\d+)\): (.*)$", r"PawnIO could not be installed (code \1): \2"),
    (r"^LightCore (\S+) · deneme sürümü: hata bulursan GitHub'da bildir \(Log sayfasındaki hata raporuyla\)\.$",
     r"LightCore \1 · beta: if you find a bug, report it on GitHub (with the bug report from the Log page)."),
    (r"^winget çalıştırılamadı: (.*)$", r"Could not run winget: \1"),
    (r"^Hata: (.*)$", r"Error: \1"), (r"^Fan: (.*)$", r"Fan: \1"), (r"^Bağlanılamadı: (.*)$", lambda m: "Could not connect: " + T(m.group(1))),
    (r"^Tarama başarısız: (.*)$", r"Scan failed: \1"), (r"^Cihazlar taranıyor\.\.\. (\d+) cihaz bulundu$", r"Scanning devices... \1 found"),
    (r"^Ayarlar kaydedilemedi: (.*)$", r"Could not save settings: \1"), (r"^'(.*)' kaydedildi\.$", r"'\1' saved."),
    (r"^'(.*)' hazır ayarlarda var, başka bir ad yaz\.$", r"'\1' already exists in presets, enter another name."),
    (r"^Yedek kaydedildi: (.*)$", r"Backup saved: \1"), (r"^Yedek kaydedilemedi: (.*)$", r"Could not save backup: \1"),
    (r"^Yedek yüklenemedi: (.*)$", r"Could not load backup: \1"), (r"^Klasör açılamadı: (.*)$", r"Could not open folder: \1"),
    (r"^(.*) bileşenleri silindi\.$", r"\1 components deleted."), (r"^Silinemedi: (.*)$", r"Could not delete: \1"),
    (r"^Ekran görüntüsü alınamadı: (.*)$", r"Could not take screenshot: \1"),
    (r"^Oyun modu açıkken eğriyle çalışan fanlar bu eğriye geçer: işlemci fanı işlemciye, diğerleri işlemci ve ekran "
     r"kartından sıcak olana bakar\.\n(Kendi eğrin|Varsayılan) · (.*)$",
     lambda m: "In game mode, fans on a curve switch to this curve: the CPU fan follows the CPU, the others follow "
               "the hotter of CPU and GPU.\n" + ("Your curve" if m.group(1) == "Kendi eğrin" else "Default")
               + " · " + m.group(2)),
    (r"^Köprü bulundu: (.*)\. Düğmesine basıp Eşleştir'e tıkla\.$", r"Bridge found: \1. Press its button, then click Pair."),
    (r"^(.*) kullanılacak\. Şimdi köprünün düğmesine basıp Eşleştir'e tıkla\.$", r"\1 will be used. Now press the bridge's button and click Pair."),
    (r"^(.*) ile eşleşiliyor\.\.\.$", r"Pairing with \1..."), (r"^(.*) köprüsüne bağlanılıyor\.\.\.$", r"Connecting to bridge \1..."),
    (r"^Bağlı: (.*) · (\d+) renkli lamba$", r"Connected: \1 · \2 color lamps"),
    (r"^(.*) yanıt vermiyor, OpenRGB yeniden başlatılıyor\.\.\.$", r"\1 is not responding, restarting OpenRGB..."),
    (r"^(.*) cihazına ulaşılamıyor\. Kablosunu kontrol et ya da programı yeniden aç\.$", r"Cannot reach \1. Check its cable or reopen the program."),
    (r"^ACİL DURUM: sıcaklık sınırı aşıldı \(işlemci (.*)°C, ekran kartı (.*)°C\).*$", r"EMERGENCY: temperature limit exceeded (CPU \1°C, GPU \2°C): all fans driven by the program are at 100%."),
    (r"^Sunucu reddetti \(HTTP (\d+)\): (.*)\. Başka bir sağlayıcı dene\.$", r"Server refused (HTTP \1): \2. Try another provider."),
    (r"^Sunucuya ulaşılamadı: (.*)$", r"Could not reach the server: \1"), (r"^Sunucu bağlantı döndürmedi: (.*)$", r"The server returned no link: \1"),
    (r"^Toplam: (.*) işlemci · (.*)$", r"Total: \1 CPU · \2"),
    (r"^(.*) · (\d+) cihaza uygulanıyor$", r"\1 · applied to \2 devices"),
    (r"^Kendi rengi: (.*)$", r"Own color: \1"), (r"^(.*) için renk$", r"Color for \1"), (r"^Kullanan: (.*)$", r"Used by: \1"),
    (r"^(.*) düzenleniyor$", r"Editing \1"), (r"^(.*): ayrıntılar$", r"\1: details"),
    (r"^Panoya alındı: (.*)$", r"Copied: \1"), (r"^(.*) program kontrolünde$", r"\1 program controlled"), (r"^(.*) BIOS kontrolünde$", r"\1 BIOS controlled"),
    (r"^program kontrolünde$", "program controlled"), (r"^BIOS kontrolünde$", "BIOS controlled"),
    # Pano
    (r"^(" + "|".join(_MODE_NAMES) + r") · parlaklık %(\d+)(?: · (\d+) cihaz yanıyor)?( · oyun modu açık)?$",
     lambda m: f"{_MODE_NAMES[m.group(1)]} · brightness {m.group(2)}%" + (f" · {m.group(3)} devices lit" if m.group(3) else "")
     + (" · game mode on" if m.group(4) else "")),
    (r"^Son 5 dakikada en yüksek (.*)°C · koruma sınırı (.*)°C$", r"Peak in the last 5 minutes \1°C · safety limit \2°C"),
    (r"^koruma sınırı (.*)°C$", r"safety limit \1°C"), (r"^Sıcak nokta (.*)°C$", r"Hot spot \1°C"),
    (r"^Koruma sınırı (.*)°C · (.*)°C pay var$", r"Safety limit \1°C · \2°C headroom"),
    (r"^Gri sütun: BIOS sürüyor\. Kesikli çizgi: alt sınır %(\d+)\.$", r"Grey bar: driven by BIOS. Dashed line: \1% floor."),
    (r"^\+(\d+) cihaz daha, cihaz panelinde$", r"+\1 more in the device panel"),
    (r"^(.*) · Oyun eğrisi$", r"\1 · Game curve"),
    (r"^%(.*) işlemci · (.*) MB$", r"\1% CPU · \2 MB"),
]

_active = False


def T_rest(text):
    match = re.match(r"^ · (\d+) cihaza uygulanıyor$", text)
    return f" · applied to {match.group(1)} devices" if match else text


def T(text):
    if not _active or not isinstance(text, str) or not text:
        return text
    if text in EN:
        return EN[text]
    for pattern, template in PATTERNS:
        if re.match(pattern, text):
            return re.sub(pattern, template, text)
    return re.sub(r"%(\d+(?:\.\d+)?)", r"\1%", text)  # sayılar ve grafik etiketleri: %41 -> 41%


def default_language():
    try:
        return "tr" if ctypes.windll.kernel32.GetUserDefaultUILanguage() & 0x3FF == 0x1F else "en"
    except (AttributeError, OSError):
        return "en"


def install(language):
    """Pencere öğeleri yazı alırken çevrilir; kodun her yerini değiştirmek gerekmez."""
    global _active
    if language != "en" or _active:
        return
    _active = True
    init, configure, create_text = tk.BaseWidget.__init__, tk.Misc.configure, tk.Canvas.create_text

    def widget_init(self, master, widget_name, cnf={}, kw={}, extra=()):
        if "text" in kw:
            kw = {**kw, "text": T(kw["text"])}
        if "text" in cnf:
            cnf = {**cnf, "text": T(cnf["text"])}
        init(self, master, widget_name, cnf, kw, extra)

    def new_configure(self, cnf=None, **kw):
        if "text" in kw:
            kw["text"] = T(kw["text"])
        return configure(self, cnf, **kw)

    def new_create_text(self, *args, **kw):
        if "text" in kw:
            kw["text"] = T(kw["text"])
        return create_text(self, *args, **kw)

    tk.BaseWidget.__init__ = widget_init
    tk.Misc.configure = tk.Misc.config = new_configure
    tk.Canvas.create_text = new_create_text
