# 🛒 AI Alışveriş Asistanı

Bu proje, doğal dildeki alışveriş isteklerini işleyen ve uygun ürünleri öneren bir web uygulamasıdır. Flask framework'ü ve OpenAI API'si kullanılarak geliştirilmiştir.

## ✨ Özellikler

- 🤖 **Doğal Dil İşleme**: Bütçe, ürün türü ve marka otomatik ayrıştırma
- 🔍 **Esnek Ürün Arama**: Bütçe ±5000/2000 TL aralığında akıllı arama
- 💬 **AI Açıklamaları**: OpenAI GPT-3.5 ile ikna edici ürün açıklamaları
- 🔄 **Alternatif Ürün Önerileri**: Ana ürün bulunamazsa kategori bazlı alternatifler
- 🎨 **Modern UI**: Responsive tasarım ve kullanıcı dostu arayüz
- ⚡ **Hızlı Yanıt**: AJAX ile anlık sonuçlar

## 📋 Gereksinimler

- Python 3.8+
- Flask
- OpenAI Python kütüphanesi

## 🚀 Kurulum

1. **Proje dizinine gidin:**
   ```bash
   cd proje
   ```

2. **Gerekli paketleri yükleyin:**
   ```bash
   pip install flask openai
   ```

3. **OpenAI API anahtarınızı ayarlayın:**
   `app.py` dosyasındaki `client = OpenAI(api_key="YOUR_API_KEY")` satırında kendi OpenAI API anahtarınızı girin.

4. **Uygulamayı çalıştırın:**
   ```bash
   python app.py
   ```

5. **Tarayıcınızda açın:**
   http://127.0.0.1:5000/

## 💡 Kullanım

1. Ana sayfada metin kutusuna istediğinizi yazın
2. "Gönder" butonuna tıklayın
3. AI'ın önerisini görün!

## 🔗 API Endpoints

- `GET /`: Ana sayfa
- `POST /recommend`: Ürün önerisi API'si

## 📁 Dosya Yapısı

```
proje/
├── app.py                 # Ana Flask uygulaması
├── products.json          # 12+ simüle edilmiş ürün
├── requirements.txt       # Python bağımlılıkları
├── README.md             # Bu dokümantasyon
└── templates/
    ├── index.html        # Ana arayüz
    └── styles.css        # CSS stilleri
```

## 🎯 Örnek Kullanım

### ✅ Doğrudan Ürün Bulunan Örnekler:
- "25000 TL ile iPhone istiyorum"
- "35000 TL bütçe Apple MacBook"
- "15000 TL Samsung telefon"

### 🔄 Alternatif Ürün Gösterilen Örnekler:
- "5000 TL ile Samsung telefon" → Çok düşük bütçe, alternatif öner
- "100000 TL bilgisayar" → Çok yüksek bütçe, uygun ürünler öner

## ⚠️ Notlar

- Bu uygulama simüle edilmiş veriler kullanmaktadır
- Gerçek e-ticaret entegrasyonu için ek geliştirmeler gerekebilir
- OpenAI API kullanım ücreti uygulanabilir

## 📄 Lisans

Bu proje açık kaynak kodludur ve MIT lisansı altında yayınlanmıştır.