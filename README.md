# LightCore

*English: lightweight RGB, fan and screenshot control for Windows. Download `LightCore.zip` from Releases, extract it and run `LightCore.exe` as administrator. Language can be changed in Settings; some messages are still Turkish.*

RGB, fan ve ekran görüntüsünü tek hafif programda toplar. Yalnızca Windows.

| Modül | Ne yapar |
|---|---|
| **Pano** | Açılış sayfası: etkin hazır ayar, cihazlar, sıcaklıklar, fanlar, fan eğrisi, oyun modu ve son olaylar tek ekranda |
| **LightRGB** | RGB cihazlar ve Philips Hue: efektler, hazır ayarlar, cihaza özel renk |
| **LightFan** | Fan eğrileri, kalibrasyon, FanControl yedeğinden geçiş |
| **LightSnap** | Bölge / ekran / bütün ekranlar, panoya alma, yazı, ok, kalem, kareleme, link ile paylaşım |

Kullanmadığın modülü **Ayarlar**'dan kapatabilirsin; kapalı modül arka planda çalışmaz.

## Kurulum

**Normal kullanım:** [Releases](../../releases) sayfasından son sürümün zip dosyasını indir, bir klasöre aç, `LightCore.exe`'yi çalıştır, yönetici iznini onayla. Python, OpenRGB ve fan kütüphanesi paketin içindedir.

**Fan kontrolü için PawnIO:** Anakart fanlarına ve işlemci sıcaklığına erişim için [PawnIO](https://pawnio.eu) sürücüsü gerekir. FanControl ya da LibreHardwareMonitor kullandıysan büyük ihtimalle zaten kuruludur. Kurulu değilse **Ayarlar > Kurulum kontrolü**'nde çıkan **PawnIO'yu kur** düğmesine bas: geliştiricinin resmi paketi winget ile kurulur. Sürücü pakete koyulmaz, çünkü resmi imzalı sürümü kapalı kaynaktır ve geliştiricisi GPL lisanslı programların onu paketlemek yerine kurdurmasını istiyor. FanControl'ü kaldırırken PawnIO'yu kaldırma.

SignalRGB, MSI Center ve FanControl gibi aynı donanımı süren programları kapat.

Windows imzasız program uyarısı verebilir; antivirüs düşük seviyeli donanım erişimi için kullanılan sürücüleri bildirebilir. Kaynak kod burada açıktır.

**Kaynak koddan:** Python 3.12+, Bluetooth şerit için `pip install bleak`, OpenRGB'yi `openrgb` klasörüne aç, LibreHardwareMonitor 0.9.6 kütüphanesini ve bağımlılıklarını (NuGet `LibreHardwareMonitorLib`, netstandard2.0) `fan` klasörüne koy, PawnIO kurulu olsun, `0_arayuz.bat` dosyasını yönetici olarak çalıştır.

## Uyumluluk

**RGB:** OpenRGB'nin gördüğü cihazlar: anakart, RAM, ekran kartı, klavye, fare, LED şerit. Destek cihaza ve OpenRGB sürümüne bağlıdır.

**Kasa fanı RGB:** OpenRGB üzerinden gider ve anakarta göre yanıp sönebilir. Kodda doğrudan HID yolu bulunan MSI anakartlarda yanıp sönmeden sürülür.

**Fan:** LibreHardwareMonitor'ın kontrol kanalı olarak gösterdiği donanım (anakart fan yongası, ekran kartı). Bazı dizüstü bilgisayarlarda ve anakartlarda kanal görünmeyebilir.

**Lamba:** Hue Bridge'e bağlı renkli Philips Hue lambaları.

**Bluetooth LED şerit:** Lotus Lantern uygulamasıyla çalışan ELK-BLEDOM ailesi şeritler; yalnızca bir ELK-BLEDOB şeritte denendi. Işıklar sayfasında **Şerit bul** ile eklenir, birden fazla şerit eklenebilir, Windows'ta eşleştirme gerekmez. Bu şeritler şifresizdir: aramada yakındaki başkasının şeridi de çıkabilir, birden fazla bulunursa seninkini sen seçersin. Şerit aynı anda tek bağlantı kabul eder: telefon uygulaması bağlıyken program bağlanamaz. Şeride ulaşılamazsa program arka planda aralığı uzayarak yeniden dener ve bağlanınca son rengi yazar. Renkler kaçıyorsa her şeridin kendi **Renk düzeltme** ayarıyla kanal oranlarını ve eğriyi ayarla.

**Ekran görüntüsü:** Windows'taki ekranlar, farklı çözünürlük ve ölçekler dahil.

**Çalıştığını bildir:** **Log > Uyumluluk raporu kopyala** kişisel bilgi içermeyen bir donanım listesi verir, istersen Issues'a yapıştır.

**Sınırlar:**
- Bir cihazın bütün LED'leri aynı rengi alır; LED başına efekt yoktur.
- Bluetooth şerit saniyede en fazla 20 renk alır; hareketli efektler orada diğer cihazlardan biraz daha az akıcıdır.
- Windows 10 denenmedi.
- Birçok donanımda denenmemiştir: sorun olursa **Log > Hata raporu kopyala** ile bildir.

## Kullanım

- Sayfalar soldaki simge rayından açılır; simgenin üstüne gelince adı görünür. Rayın en altındaki düğme **cihaz panelini** açar: cihaz listesi ve aç/kapat anahtarları, durum, **Bilgisayar açılınca başlasın** ve **Cihazları yeniden tara** oradadır.
- **Küçült** düğmesi simge tepsisine indirir. Sol tık açar, sağ tık menüsünde ışıklar, hazır ayarlar ve oyun modu vardır.
- **Kapat** programı tamamen kapatır. Son renk donanımda kalır, fanlar güvenlik için BIOS'a döner. Fan eğrilerinin çalışması için programı kapatma, küçült.
- **Bilgisayar açılınca başlasın** Görev Zamanlayıcı ile çalışır. Yönetici olarak açıkken işaretle, yoksa fan kontrolü çalışmaz.

### LightSnap

Varsayılan kısayollar `Ctrl+Shift+1` bölge, `Ctrl+Shift+2` imlecin olduğu ekran, `Ctrl+Shift+3` bütün ekranlar. Görüntü panoya gider, `Ctrl+V` ile yapıştırılır. Kısayollar **Düzenle** ile değiştirilir.

**Link ile paylaşım:** Yükleme varsayılan olarak yalnızca sen basınca yapılır. Linki alan herkes görüntüyü görebilir; şifre ya da özel mesaj varsa önce **Kareleme** ile kapat. Sağlayıcılar üçüncü taraftır, kuralları ve saklama süreleri onlara aittir.

### Oyun modu

Ekranı kaplayan pencere ve 10 saniye boyunca %50 üstü ekran kartı yükü olunca açılır. Bitince 30 saniye sonra kapanır. Tepsi menüsünden elle de açılır. Pencereli oyunlar otomatik algılanmaz; oyun sayılmaması gereken programlar **Ayarlar**'dan dışlanır.

Açıkken ışıklar söner (istersen açık kalır), OpenRGB kapanır, ekran görüntüsü kısayolları kalkar. Eğri kullanan fanlar oyun eğrisine geçer: 40°C'de %25, 65°C'de %45, 80°C'de %75, 90°C'de %100. Kendi farkların ve hız sınırların korunur, Otomatik fanlar BIOS'ta kalır. Kasa fanları işlemci ve ekran kartından sıcak olanı izler.

Oyun modu **Ayarlar**'dan tamamen kapatılabilir.

## Fan güvenliği

Bu kurallar FanHost içindedir, arayüz ne gönderirse göndersin geçerlidir:
- Hiçbir fan %25'in (pompa %60) altına inmez.
- İşlemci 90°C, ekran kartı 85°C ya da sıcak nokta 100°C'yi geçerse sürülen fanlar %100 olur.
- Program kapanır ya da 5 saniye yanıt vermezse fanlar BIOS'a döner.

**Bilinen sınır:** FanHost zorla sonlandırılırsa (Görev Yöneticisi ya da çökme) fan son hızında kalır, gerçek donanımda ölçüldü. Program FanHost'u hemen yeniden başlatır, sıcaklık koruması geri gelir. Ama programı da kapatırsan fan **bilgisayar yeniden başlayana kadar** sıcaklığa tepki vermeyebilir. Bu yüzden FanHost'u Görev Yöneticisi'nden kapatma.

Fan kontrolü donanımını etkiler; **kullanım sorumluluğu sana aittir**. İlk kullanımda sıcaklıkları izle.

## Veriler

- Ayarlar `%APPDATA%\LightCore\lightcore_config.json` dosyasındadır. Hue anahtarı burada durur, **paylaşma**. Yedek dosyasına girmez.
- Kayıtlar program klasöründe `lightcore_log.txt`. **Hata raporu kopyala** kullanıcı adını, ağ adreslerini ve anahtarları çıkarır.
- Program kendiliğinden veri göndermez. İnternete yalnızca sen istediğinde bağlanır: Hue köprüsü arama ve görüntü yükleme.

## Geliştirme

```bash
python -m unittest discover -s tests
```

Testler sahte OpenRGB ve Hue sunucusu kullanır, gerçek donanıma dokunmaz. Yalnızca standart kütüphane gerekir.

`build_exe.bat` PyInstaller ile exe üretir. `CHANGELOG.md` sürümleri listeler.

## Lisans

LightCore [GNU GPL v3](LICENSE) ile lisanslıdır: kullanabilir, değiştirebilir ve dağıtabilirsin; dağıttığın sürümün kaynak kodu da aynı lisansla açık olmalıdır. Garanti verilmez.

## Kullanılan projeler

Bu depo aşağıdakileri içermez; hazır pakette yanında gelirler. Lisans metinleri [`licenses`](licenses) klasöründedir, pakette `LICENSES` olarak gelir; hangi dosyanın neye ait olduğu `licenses/BENIOKU.txt`'de yazar.

- [OpenRGB](https://gitlab.com/CalcProgrammer1/OpenRGB) 1.0, GPL-2.0-or-later: değiştirilmeden, ayrı süreç olarak SDK bağlantısıyla (içinde Qt 6 LGPL-3.0, libusb LGPL-2.1, hidapi BSD, PawnIOLib)
- [LibreHardwareMonitor](https://github.com/LibreHardwareMonitor/LibreHardwareMonitor) 0.9.6, MPL-2.0, ve bağımlılıkları (BlackSharp/DiskInfoToolkit/RAMSPDToolkit MPL-2.0, HidSharp Apache-2.0, .NET kütüphaneleri MIT)
- [bleak](https://github.com/hbldh/bleak) ve [PyWinRT](https://github.com/pywinrt/pywinrt), MIT: Bluetooth şerit için
- Python (PSF), Tcl/Tk (BSD benzeri), PyInstaller önyükleyicisi (GPL-2.0, dağıtım istisnasıyla)
- [PawnIO](https://pawnio.eu): pakette yoktur; program içinden geliştiricinin resmi winget paketiyle kurulur
- FanControl kullanılmaz ve gerekmez; yalnızca yedek dosyası (.json) içe aktarılabilir

Ekran görüntüsü paylaşımı [tmpfiles.org](https://tmpfiles.org/tos) ya da [uguu.se](https://uguu.se/faq) kullanır, yalnızca **Yükle**'ye basınca (ya da otomatik yüklemeyi açınca). Bu sitelerin kuralları geçerlidir: başkalarının kişisel bilgisini ve telif hakkı olan içeriği yükleme; tmpfiles'ta saatte en fazla 200 yükleme, dosyalar 48 saatte, uguu'da 3 saatte silinir.
