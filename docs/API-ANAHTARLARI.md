# API bilgilerini alma rehberi

Her platform için bir kez yapılır. Önerilen sıra: **Google Ads developer token başvurusunu hemen yapın** (onay birkaç gün sürebilir), diğerlerini o arada bağlayın.

---

## Ticimax

Panelde istenen: **site alan adı** + **web servis yetki kodu**

1. Firmanın Ticimax yönetim paneline girin.
2. **Ayarlar → Web Servis** (ya da Entegrasyon / Web Servis Ayarları) bölümünde yeni bir yetki kodu oluşturun ve **Sipariş servisi** için okuma izni verin. Menüyü bulamazsanız Ticimax destekten "Sipariş web servisi için yetki kodu" isteyin.
3. Web servislerde IP kısıtlaması açıksa, **sunucunuzun IP adresini** izinli listeye ekleyin.
4. Panelde alan adını `www.siteniz.com` şeklinde girin, yetki kodunu yapıştırın, **Test et**'e basın.

Test sonucunda ciro mantıksız görünürse (ör. 0 ya da çok büyük), test mesajındaki "Gelen alanlar" listesinden toplam tutar alanının adını bulup bağlantıdaki **Tutar alanı (gelişmiş)** kutusuna yazın.

---

## Shopify

Panelde istenen: **mağaza adresi** (`xxx.myshopify.com`) + **erişim token'ı** *ya da* **Client ID + Client Secret**

**Mağazada daha önce oluşturulmuş eski tip özel uygulama varsa:** Shopify admin → Ayarlar → Uygulamalar ve satış kanalları → Uygulama geliştir → uygulama → API kimlik bilgileri → `shpat_…` token'ını kopyalayın. Bu yeterli.

**Yoksa (1 Ocak 2026'dan sonra yeni tip):**
1. Mağaza sahibinin hesabıyla **dev.shopify.com** (Dev Dashboard) → **Create app**.
2. Uygulamanın sürümünde Admin API izinlerinden **read_orders**, **read_products** ve **read_inventory** seçin (stok ve beden analizi için); müşteri adlarının görünmesi için **read_customers** de ekleyin. 60 günden eski siparişler gerekiyorsa **read_all_orders** de ekleyin. Sürümü yayınlayın.
3. Uygulamayı ilgili mağazaya **yükleyin (Install)**.
4. **Settings** sayfasındaki **Client ID** ve **Client Secret**'ı panele girin. Panel token'ı kendisi alır ve her gün yeniler.

Not: Bu yöntem mağazanın sahibi olan organizasyonda oluşturulan uygulamalarda çalışır; müşteri mağazasıysa uygulamayı müşterinin hesabında (ya da size verdiği yetkiyle) oluşturun.

---

## Meta Ads (Facebook / Instagram)

Panelde istenen: **reklam hesabı ID** + **sistem kullanıcısı erişim token'ı**

Bir kez kurulum (kendi Business Manager'ınızda):
1. **developers.facebook.com** → Uygulamalarım → **Uygulama oluştur** → tür: **İşletme**. Uygulamaya **Marketing API** ürününü ekleyin.
2. **business.facebook.com** → İşletme ayarları → **Sistem kullanıcıları** → Ekle (rol: Yönetici).
3. Müşteri firmaların reklam hesaplarına **iş ortağı erişimi** aldıysanız bu hesaplar sizin Business Manager'ınızda görünür. Sistem kullanıcısına → **Varlık ata** → her müşterinin reklam hesabını seçin (en az "Performansı görüntüleme").
4. Sistem kullanıcısında **Yeni token oluştur** → 1. adımdaki uygulamayı seçin → süre: **Hiçbir zaman sona ermesin** → izin: **ads_read** → token'ı kopyalayın.

Her firma için: aynı token + o firmanın reklam hesabı ID'si (Reklam Yöneticisi'nin adres çubuğundaki `act=` değeri ya da hesap seçicideki numara).

---

## Google Ads

Panelde istenen: **müşteri hesap ID**, **MCC ID**, **developer token**, **OAuth Client ID / Secret**, **refresh token**

Bir kez kurulum:
1. **MCC (Yönetici hesap):** Yoksa ads.google.com/home/tools/manager-accounts adresinden ücretsiz açın ve müşteri hesaplarını bağlayın.
2. **Developer token:** MCC → Yönetici → **API Merkezi** → başvuru formunu doldurun. Kendi müşterileriniz için raporlama amacı yazmanız yeterli. "Test" erişimi sadece test hesaplarında çalışır; **Basic (temel) erişim** onayını bekleyin.
3. **Google Cloud:** console.cloud.google.com → yeni proje → **Google Ads API**'yi etkinleştirin → OAuth izin ekranı (Harici) → **Kimlik bilgileri → OAuth istemci kimliği → Web uygulaması**, yetkili yönlendirme adresi: `https://developers.google.com/oauthplayground` → Client ID ve Secret'ı kopyalayın.
   - OAuth izin ekranını **Yayınla (In production)** durumuna alın; "Test" durumunda refresh token 7 günde bir geçersiz olur.
4. **Refresh token:** developers.google.com/oauthplayground → sağ üstteki ⚙ → "Use your own OAuth credentials" → Client ID/Secret'ı girin → kapsam kutusuna `https://www.googleapis.com/auth/adwords` yazın → **Authorize** (MCC'ye erişimi olan Google hesabıyla) → **Exchange authorization code for tokens** → **Refresh token**'ı kopyalayın.

Her firma için: aynı developer token / client / refresh token + o firmanın 10 haneli müşteri ID'si + MCC ID.

---

## Güvenlik notları

- Tüm anahtarlar sadece okuma izinli olsun (read_orders, ads_read vb.); panel hiçbir platformda değişiklik yapmaz.
- Anahtarlar veritabanında `SECRET_KEY` ile şifreli saklanır; düzenleme ekranında maskeli görünür.
- Bir anahtar sızarsa ilgili platformdan iptal edip yenisini panele girmeniz yeterli.
