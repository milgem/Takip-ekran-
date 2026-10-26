# Panel — Danışmanlık firmaları tek ekranda

Tüm müşteri firmaların **Ticimax / Shopify sipariş** ve **Meta / Google Ads reklam** verilerini API ile otomatik çeken, telefondan ve bilgisayardan açılan kişisel kontrol paneli.

**Neler var**
- Genel bakış: tüm firmaların ciro, sipariş, sepet ortalaması, reklam harcaması, ROAS, lead ve bekleyen siparişleri; önceki döneme göre % değişim
- Uyarılar: bağlantı hatası, ROAS eşiğin altında, 48 saattir kargolanmayan sipariş, öğleden sonra hâlâ sipariş yok, ciro %30+ düştü, reklam harcaması %50+ arttı
- Satış kanalları: Ticimax/Shopify'a düşen Trendyol, Hepsiburada, Amazon, N11, LC Waikiki vb. pazaryeri siparişleri otomatik ayrılır; kanal bazında ciro, pay ve değişim
- Firma detayı: günlük ciro (web + pazaryeri) / reklam grafiği, bugün vs dün saatlik ciro, kanal bazlı ROAS, son ve bekleyen siparişler
- Sadece reklam yönetilen firmalar (ör. konut projesi) için lead ve lead başı maliyet
- Tarih aralığı: bugün, dün, 7/30 gün, bu ay, geçen ay, özel tarih
- Veriler her 30 dakikada bir otomatik güncellenir, istendiğinde "Şimdi güncelle"
- API anahtarları sunucuda şifreli saklanır, panel şifre ile korunur ve HTTPS üzerinden açılır

---

## 1. Sunucu alın

Küçük bir Linux sunucu yeterli (1–2 vCPU, 2 GB RAM, Ubuntu 24.04). Örnek: Hetzner CX22, DigitalOcean 2 GB droplet ya da Türkiye'de bir VPS sağlayıcısı. Aylık yaklaşık 5–10 $.

## 2. Alan adı yönlendirin

Alan adınızın DNS ayarlarında bir alt alan adı açın, örn. `panel.siteniz.com` → **A kaydı** → sunucunun IP adresi.

## 3. Sunucuya kurulum (bir kez, ~10 dakika)

Sunucuya SSH ile bağlanın (`ssh root@SUNUCU_IP`) ve sırayla çalıştırın:

```bash
# Docker kurulumu
curl -fsSL https://get.docker.com | sh

# Panel dosyalarını yükleyin (bilgisayarınızdan):
#   scp panel.zip root@SUNUCU_IP:/root/
apt-get install -y unzip && unzip panel.zip -d /opt && cd /opt/panel

# Ayarlar
cp .env.example .env
openssl rand -hex 32          # çıkan değeri kopyalayın
nano .env                     # DOMAIN, ADMIN_PASSWORD ve SECRET_KEY'i doldurun, Ctrl+O, Enter, Ctrl+X

# Başlat
docker compose up -d --build
```

Birkaç dakika sonra `https://panel.siteniz.com` adresini açın, `.env`'deki kullanıcı adı ve şifreyle girin.

## 4. Firmaları ve bağlantıları ekleyin

**Ayarlar → Yeni firma** ile firmayı ekleyin, ardından firmanın altındaki **+ Shopify / + Ticimax / + Meta Ads / + Google Ads** butonlarıyla API bilgilerini girin. Her bağlantıda **Test et** ile kontrol edin. Kaydettikten sonra son 60 günün verisi arka planda çekilir.

API bilgilerini nereden alacağınız: **docs/API-ANAHTARLARI.md**

## Günlük kullanım ve bakım

| İş | Komut (sunucuda, /opt/panel içinde) |
|---|---|
| Kayıtları görmek | `docker compose logs -f panel` |
| Yeniden başlatmak | `docker compose restart panel` |
| Güncelleme yüklemek | yeni dosyaları kopyalayıp `docker compose up -d --build` |
| Yedek almak | `cp data/panel.db panel-yedek-$(date +%F).db` |

- **Yedek:** Tüm veri `data/panel.db` dosyasında. Kaybolsa bile API'den yeniden çekilir; sadece firma/bağlantı ayarlarını tekrar girmeniz gerekir.
- **SECRET_KEY'i değiştirmeyin**; değişirse kayıtlı API anahtarları okunamaz.
- **API sürümleri:** Meta ve Google eski API sürümlerini belli aralıklarla kapatır. Bir bağlantı "version deprecated / sunset" hatası verirse `.env`'deki sürümü güncelleyip `docker compose up -d` yapmanız yeterli.

## Ciro ve ROAS nasıl hesaplanıyor

- **Ciro:** İptal / iade edilmemiş siparişlerin toplam tutarı (Ticimax'ta "ön sipariş" ve "ödeme bekliyor" ciroya katılmaz).
- **Bekleyen sipariş:** Onaylanmış ama henüz kargoya verilmemiş siparişler.
- **Satış kanalı:** Her siparişin kaynağı (Shopify'da sipariş uygulaması / kaynak / etiket, Ticimax'ta sipariş kaynağı alanları) okunur. İçinde pazaryeri adı geçmeyen siparişler "Web sitesi" sayılır.
- **ROAS (web):** Sadece **web sitesi** cirosu ÷ Meta + Google toplam harcama. Pazaryeri satışları reklamdan gelmediği için ROAS'a katılmaz; toplam ciroda görünür.
- **ROAS (platform):** Reklam ağlarının kendi raporladığı dönüşüm değeri ÷ harcama.
- Bugün seçiliyken karşılaştırma "dün aynı saate kadar" ile yapılır.

## Kanal tespitini kontrol etmek

Bağlantıyı ekledikten sonra **Test et**'e basın: son 7 günün siparişleri kanallara göre sayılarak gösterilir (örn. "Web sitesi 140, Trendyol 52, Hepsiburada 18"). Bu sayıları mağaza panelindeki rakamlarla karşılaştırın.

- **Ticimax'ta pazaryeri siparişleri "Web sitesi" görünüyorsa:** Test mesajındaki "Kanal alanları" kısmında pazaryeri adını taşıyan alanı bulun ve bağlantıdaki **Satış kanalı alanı (gelişmiş)** kutusuna yazın.
- Düzeltmeden sonra eski siparişlerin de güncellenmesi için bağlantıda **Geçmişi yeniden çek**'e basın.

## Kendi bilgisayarında Docker ile çalıştırmak

Docker Desktop açıkken Terminal'de:

```bash
bash ~/Documents/panel/docker-baslat.sh
```

Panel `panel-local` adlı container olarak başlar ve `http://localhost:8000` adresinde açılır. Docker Desktop açık olduğu sürece arka planda çalışmaya devam eder (Terminal'i kapatabilirsin). Durdurmak için: `docker compose -f docker-compose.local.yml down`

## Kendi bilgisayarında Docker olmadan çalıştırmak (Mac)

Terminal'i aç ve şunu yaz:

```bash
bash ~/Documents/panel/baslat.command
```

İlk seferde gerekenleri kendisi kurar (1–3 dakika), kullanıcı adı ve şifreyi ekrana yazar ve tarayıcıda `http://localhost:8000` adresini açar. Terminal penceresi açık kaldığı sürece panel çalışır ve veriler 30 dakikada bir güncellenir; kapatmak için Ctrl+C. Bu kurulumda panel sadece bu bilgisayardan açılır.
