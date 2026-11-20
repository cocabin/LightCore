# Değişiklikler

## 0.0.5 (beta)

İlk herkese açık deneme sürümü. Hata bulursan [Issues](https://github.com/cocabin/LightCore/issues) sayfasında bildir; Log sayfasındaki **Hata raporu kopyala** kişisel bilgileri çıkarılmış bir rapor verir.

- **PawnIO'yu kur:** fan kontrolü için gereken PawnIO sürücüsü kurulu değilse Ayarlar > Kurulum kontrolü'nde düğme çıkar; geliştiricinin resmi paketi winget ile kurulur.
- **FanControl'e bağımlılık kalmadı:** fan kütüphanesi yalnızca paketin `fan` klasöründen yüklenir.
- **Fan güvenliği:** acil durum (%100) sınırın 5°C altına inince biter, sınırda gidip gelmez. Arayüz kısa süre takılırsa fanlar 10 saniye beklemeden BIOS'a dönmez. Bir donanımın sensör okuması çökerse fan kontrolü düşmez, o donanım atlanır.
- Windows kapanırken fan kontrolünün kapatılması artık "beklenmedik kapanma" sayılmıyor.
- İngilizce görünümde Ekran Görüntüsü başlığı ve OpenRGB bağlantı hatası çevriliyor; yüzdeler 41% biçiminde.
- Ayarlar'da sürüm satırı ve GitHub düğmesi.
- Pakette bütün bileşenlerin lisans metinleri (`LICENSES\BENIOKU.txt`).

## Yayımlanmamış iç sürümler

Aşağıdakiler geliştirme sırasında yalnızca yerel olarak denenen sürümlerdir; 0.0.5'in içindedir.

### 1.2.5

- **Kurulum kontrolü** artık "FanControl kurulu olmalı" demiyor: fan kütüphanesi paketin `fan` klasöründen gelir, FanControl'ü kurmak gerekmez. Kontrol, fan sayfasının baktığı yere bakar; `fan` klasörü gerçekten eksikse onu söyler.
- Kurulum kontrolü PawnIO sürücüsünün kurulu olup olmadığına da bakar.

### 1.2.4

- **Oyun modu eğrisi düzenlenebilir:** Fanlar sayfasında **Oyun modu eğrisi** bölümü. **Düzenle** grafiği açar (nokta sürükle, çift tıkla ekle, sağ tıkla sil) ve histerezis ayarlarını gösterir; değişiklik anında geçerli olur. **Varsayılan** programın eğrisine döner.
- Oyun eğrisi fan yedeğine, dışa aktarmaya ve otomatik yedeklere dahil; Pano'daki fan eğrisi kartı düzenlenen eğriyi gösterir.
- Eğri grafiğinde en üstteki noktanın etiketi artık kesilmiyor.

### 1.2.3

- **Oyun modu fan eğrisi yumuşatıldı:** eski eğri 72°C'de %60, 80°C'de %75 veriyordu ve oyuna girince fanlar normal eğriden 15-20 puan hızlı dönüyordu. Yeni eğri 72°C'de ~%43, 80°C'de ~%58; %100 yine 90°C'de.
- Oyun modunda sıcaklık en az 4°C ve 10 saniye yüksek kalmadan fan hızlanmaz; işlemcinin anlık sıçramaları fanı oynatmaz.

### 1.2.2

- **Ekran Görüntüsü > Kayıt:** kayıt klasörü artık **Değiştir** ile istenen yere alınır, **Varsayılan** ile `Resimler\LightCore`'a döner.
- Görüntü kaydedilince bildirimde **Klasörde göster** düğmesi çıkar; Gezgin dosya seçili açılır.

### 1.2.1

- Simge rayının en üstündeki logo artık Pano'nun düğmesi; ayrı ev simgesi kaldırıldı. Pano açıkken logonun çevresinde halka görünür.
- Açılır liste (Hazır ayar, Efekt ve diğerleri) açıkken fare tekerleği sayfayı kaydırmaz; eskiden sayfa kayıyor, liste havada kalıyordu. Ekrana sığmayan uzun liste tekerlekle kendi içinde kayar.

### 1.2.0

- **Koyu tema** eklendi ve varsayılan oldu (çok koyu yeşil-siyah zemin, nane vurgu). Tema **Ayarlar > Görünüm**'den değişir, yeniden açınca geçerli olur.
- **Tam ekrana geçiş ve geri dönüş:** pencere artık dizilirken kaybolmuyor. Görüntüsü yerinde durur, yeni düzen hazır olunca yumuşakça değişir.
- **Canlı renk göstergeleri** (Işıklar sayfasındaki önizleme şeridi, cihaz noktaları, Pano'daki daireler) efekt hareketliyken saniyede 4 yerine 25 kez yenilenir.
- **Bluetooth şerit:** geçişlerde saniyede 12 yerine 20 renk gider ve yazmalar eşit aralıklıdır; geçiş bitince son renk bir kez yinelenir.
- **Pano:** programın işlemci ve bellek kullanımı sağ üstte görünür.
- Pano'daki renk geçişi resimleri pencere boyutu değişince yeniden üretilmez.

### 1.1.0

Yeni görünüm ve Pano sayfası.

- **Pano:** program artık bu sayfayla açılır. Etkin hazır ayar, cihazlar (kendi anlık renkleriyle), işlemci ve ekran kartı sıcaklığı, fan hızları, fan eğrisi, oyun modu anahtarı, hazır ayar düğmeleri, son ekran görüntüleri ve son olaylar tek ekranda.
- **Açık tema:** nane zemin, beyaz kartlar, koyu yeşil vurgu. Önceki koyu görünüm `lightcore/ui.py` içindeki `THEME = "dark"` ile geri gelir.
- **Simge rayı:** sol menünün yerini aldı. Cihaz listesi, durum ve başlangıç düğmeleri rayın altındaki düğmeyle açılan panelde; durum yazısı sayfanın altında da görünür.
- **Logolar** yeni temaya göre yeniden çizildi (program, tepsi ve exe simgesi dahil); Pano'daki cihaz dairelerinde cihaz türüne göre simge var.
- Pano ve ray yazılarının İngilizce çevirisi eklendi.

### 1.0.0

İlk sürüm: LightRGB, LightFan, LightSnap, otomatik oyun modu.

- Eğrinin sensörü okunamazsa işlemci ya da ekran kartı sıcaklığı kullanılır ve fan sayfası uyarır; hiç sıcaklık yoksa fanlar %100 olur.
- Bir cihaz yanıt vermezse OpenRGB en fazla 10 dakikada bir yeniden başlatılır, olmazsa uyarı gösterilir.
- Çalışmayan Litterbox ve Catbox yükleme servisleri kaldırıldı.
- Hata ve durum mesajlarının İngilizce çevirisi genişletildi.
- Bluetooth LED şerit desteği (Lotus Lantern / ELK-BLEDOM): birden fazla şerit, şerit başına renk düzeltme ve eğri, ulaşılamayan şeridin arka planda yeniden denenmesi.
- OpenRGB yeniden başlatılırken eskisi tamamen kapanmadan yenisi açılmaz; kapatma işi artık atlanamaz.
- Hata raporundan Bluetooth ve ağ kartı adresleri de çıkarılır.
- Açılış hafifletildi: Hue ve Bluetooth kütüphaneleri yalnızca kullanılınca yüklenir.
- Lisans: GNU GPL v3.
- PawnIO sürücüsü kurulu değilse fan kontrolü başlamaz ve nereden kurulacağı söylenir.
