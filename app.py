from flask import Flask, request, jsonify, render_template
from auth import register_user, login_user, verify_token, logout_token, push_history, clear_history, USER_HISTORY
import json
import re
import os
import difflib
from openai import OpenAI
from dotenv import load_dotenv
import time
from typing import Any, Dict, List, Optional, Tuple

# ============================================================
#  APP & OPENAI KURULUMU
# ============================================================

app = Flask(
    __name__,
    static_folder='static',
    template_folder='templates'
)

load_dotenv()

_API_KEY = os.getenv("OPENAI_API_KEY")
if not _API_KEY:
    raise RuntimeError("OPENAI_API_KEY bulunamadı: .env veya ortam değişkenini ayarlayın")

client = OpenAI(api_key=_API_KEY)

# Ürün açıklamaları için basit bellek içi cache
# Key: (name, brand, price, tuple(features), usage, budget_band)
DESCRIPTION_CACHE: Dict[Any, str] = {}


def _openai_chat(
    messages: List[Dict[str, str]],
    max_tokens: int = 200,
    temperature: float = 0.4,
    retries: int = 1
) -> str:
    """
    OpenAI Responses API için basit wrapper.
    messages -> tek bir prompt stringine dönüştürülür.
    Hız için sadece 1 deneme yapar; rate limit olursa üst seviyede fallback kullanılır.
    """
    last_err: Optional[Exception] = None

    parts: List[str] = []
    for m in messages:
        role = m.get("role", "user")
        content = m.get("content", "")
        if role == "system":
            parts.append(f"[SİSTEM]: {content}")
        elif role == "assistant":
            parts.append(f"[ASİSTAN]: {content}")
        else:
            parts.append(f"[KULLANICI]: {content}")
    prompt = "\n".join(parts)

    for _ in range(retries):
        try:
            resp = client.responses.create(
                model="gpt-4.1-mini",  # hızlı mini model
                input=prompt,
                temperature=temperature,
                max_output_tokens=max_tokens,
            )
            text = resp.output[0].content[0].text
            return text.strip()
        except Exception as e:
            msg = str(e)
            # Rate limit / 429 → üst seviyede fallback'e izin ver
            if (
                "rate_limit_exceeded" in msg
                or "429" in msg
                or "RateLimitError" in msg
            ):
                print("OpenAI HATASI (_openai_chat): RATE LIMIT - fallback kullanılacak.", repr(e))
                raise RuntimeError("openai_rate_limited")
            print("OpenAI HATASI (_openai_chat):", repr(e))
            last_err = e
            break

    raise RuntimeError("openai_unavailable") from last_err


# ============================================================
#  ÜRÜN YÜKLEME & GENEL YARDIMCI FONKSİYONLAR
# ============================================================

def load_products() -> List[Dict[str, Any]]:
    """
    products.json dosyasını her çağrıldığında okur.
    Böylece yeni eklenen ürünler için sunucuyu yeniden başlatmaya gerek kalmaz.
    JSON yapısı:
      - Düz liste   -> [ {...}, {...} ]
      - veya        -> { "products": [ {...}, {...} ] }
    ikisini de destekler.
    """
    try:
        with open("products.json", "r", encoding="utf-8") as f:
            data = json.load(f)

        if isinstance(data, dict):
            if "products" in data and isinstance(data["products"], list):
                return data["products"]
            for v in data.values():
                if isinstance(v, list) and v and isinstance(v[0], dict):
                    return v
            return []
        elif isinstance(data, list):
            return data
        else:
            return []
    except FileNotFoundError:
        print("UYARI: products.json bulunamadı, boş liste döndürüldü.")
        return []
    except Exception as e:
        print("products.json okunurken hata:", repr(e))
        return []


def _normalize_tr(s: Any) -> str:
    """
    Türkçe karakterleri sadeleştirip lower-case'e çevirir.
    Baştaki/sondaki boşlukları kırpar.
    """
    mapping = {
        "ö": "o", "Ö": "o",
        "ü": "u", "Ü": "u",
        "ş": "s", "Ş": "s",
        "ı": "i", "İ": "i",
        "ğ": "g", "Ğ": "g",
        "ç": "c", "Ç": "c",
    }
    s = "" if s is None else str(s).strip()
    return "".join(mapping.get(ch, ch).lower() for ch in s)


def _same_category(cat1: Any, cat2: Any) -> bool:
    """
    Kategori kıyasını normalize ederek yapar.
    Örn: 'monitör' == 'monitor' → True
    """
    if not cat1 or not cat2:
        return False
    return _normalize_tr(cat1) == _normalize_tr(cat2)


def extract_product_category(text: str) -> Optional[str]:
    """
    Kullanıcı metninden kategori tahmini yapar.
    Yazım hatalarına karşı difflib ile toleranslıdır.
    (Buradaki map sadece temel kategoriler, ana mantık ürün kategorileri ile genişletiliyor.)
    """
    categories_map = {
        "bilgisayar": [
            "bilgisayar", "laptop", "notebook", "pc",
            "masaüstü", "masaustu"
        ],
        "telefon": [
            "telefon", "cep telefonu", "mobile",
            "smartphone", "android", "ios", "phone"
        ],
        "kulaklık": [
            "kulaklık", "kulaklik", "headphone",
            "earphone", "headset"
        ],
        "monitor": [
            "monitor", "monitör", "monitoer", "ekran"
        ],
    }

    t = (text or "").lower()
    norm_t = _normalize_tr(text)
    words = re.findall(r"[a-zA-ZğĞüÜşŞıİöÖçÇ]+", t)
    norm_words = [_normalize_tr(w) for w in words]

    def score_for(keywords: List[str]) -> Tuple[int, bool]:
        score = 0
        direct = False
        for k in keywords:
            if k in t:
                score += 3
                direct = True

            nk = _normalize_tr(k)
            if nk in norm_t:
                score += 2

            for nw in norm_words:
                if nk in nw or nw in nk:
                    score += 2
                if len(nk) >= 3 and len(nw) >= 3:
                    ratio = difflib.SequenceMatcher(None, nw, nk).ratio()
                    if ratio >= 0.82:
                        score += 1
        return score, direct

    best_cat: Optional[str] = None
    best_score = 0
    best_direct = False

    for c, kws in categories_map.items():
        s, d = score_for(kws)
        if s > best_score or (s == best_score and d and not best_direct):
            best_cat = c
            best_score = s
            best_direct = d

    return best_cat


def extract_brand(text: str) -> Optional[str]:
    """
    Kullanıcı metninden marka bilgisini çıkarır.
    products.json içindeki brand alanlarından dinamik liste oluşturur.
    """
    if not text:
        return None

    t_norm = _normalize_tr(text)
    t_lower = t_norm

    # 1) Apple ekosistemi
    apple_triggers = ["macbook", "mac book", "iphone", "ayfon", "mekbuk"]
    for trig in apple_triggers:
        if trig.replace(" ", "") in t_lower.replace(" ", ""):
            return "Apple"
    if "apple" in t_lower:
        return "Apple"

    # 2) ürünlerden marka listesi
    try:
        products = load_products()
    except Exception as e:
        print("extract_brand: load_products hatası:", repr(e))
        products = []

    brands_raw: List[str] = []
    seen_norm: set = set()

    for p in products:
        b = p.get("brand")
        if not b:
            continue
        nb = _normalize_tr(b)
        if not nb:
            continue
        if nb in seen_norm:
            continue
        seen_norm.add(nb)
        brands_raw.append(str(b))

    if not brands_raw:
        return None

    tokens = re.findall(r"[a-zA-ZğĞüÜşŞıİöÖçÇ]+", t_norm)
    ngrams: List[str] = tokens[:]
    for i in range(len(tokens) - 1):
        ngrams.append(tokens[i] + " " + tokens[i + 1])

    best_brand: Optional[str] = None
    best_score: float = 0.0

    for piece in ngrams:
        for b in brands_raw:
            nb = _normalize_tr(b)
            score = difflib.SequenceMatcher(None, piece, nb).ratio()
            if score > best_score:
                best_score = score
                best_brand = b

    if not best_brand or best_score < 0.78:
        return None

    return best_brand


def extract_budget(text: str) -> Optional[int]:
    """
    Metinden bütçe miktarını çıkar (SADECE regex/kural, AI KULLANMAZ).
    Teknik özellik sayıları (GB, Hz, mAh vb.) filtrelenmeye çalışılır.
    """
    if not text:
        return None

    t = text.lower()
    # Para sembollerini normalize et
    t = t.replace("₺", " tl ")
    t = t.replace("türk lirası", " tl ")
    t = t.replace("turk lirası", " tl ")
    t = t.replace("türk lirasi", " tl ")
    t = t.replace("turk lirasi", " tl ")

    # Tekil bütçe için: sayı + (bin/k/milyon) + opsiyonel 'tl/lira'
    candidates: List[int] = []

    pattern = re.compile(
        r"(\d{1,7})\s*(bin|k|milyon)?\s*(tl|lira|lirasi)?"
    )
    for m in pattern.finditer(t):
        num_str, unit, cur = m.groups()
        try:
            val = int(num_str)
        except ValueError:
            continue

        if unit == "milyon":
            val *= 1_000_000
        elif unit in ("bin", "k"):
            val *= 1000

        # Eğer hiç para birimi yoksa, çevresinde "bütçe" gibi kelimeler var mı bak
        window = t[max(0, m.start() - 15): m.end() + 15]
        money_hint = any(w in window for w in ["tl", "lira", "bütçe", "butce", "butcem", "bütçem", "max", "en fazla", "bütçem", "bütçe"])
        if not cur and not money_hint:
            # Büyük ihtimal teknik değer (GB/HZ vs.) -> atla
            continue

        # Çok küçük değerler (örneğin 8, 16) neredeyse kesin RAM/GB, atla
        if val < 1000:
            continue

        candidates.append(val)

    if not candidates:
        return None

    # Birden fazla bütçe varsa en büyüğünü kullan (genelde üst limit)
    val = max(candidates)
    if val <= 0:
        return None
    return val


def extract_budget_range(text: str) -> Optional[Tuple[int, int]]:
    """
    Cümleden bütçe aralığını çıkarır.
    Örn: '90 bin ile 150 bin arası' -> (90000, 150000)
    """
    t = text.lower().replace("₺", " tl ")
    tokens = re.findall(r"(\d{1,7})(?:\s*(bin|k|milyon))?", t)
    vals: List[int] = []

    for num, unit in tokens:
        try:
            v = int(num)
        except Exception:
            continue

        if unit == "milyon":
            v *= 1_000_000
        elif unit in ("bin", "k"):
            v *= 1000

        vals.append(v)

    if "bin" in t and any(v >= 10_000 for v in vals) and any(v < 1000 for v in vals):
        vals = [v * 1000 if v < 1000 else v for v in vals]

    if len(vals) >= 2:
        low, high = sorted(vals[:2])
        return low, high

    return None


def _is_budget_only(text: str) -> bool:
    """
    Kullanıcı sadece bütçe yazdı mı?
    """
    t = _normalize_tr(text or "")
    t = t.replace("tl", " ").replace("turk lirası", " ").replace("turklirasi", " ")

    tokens = re.findall(r"[a-zA-ZğĞüÜşŞıİöÖçÇ]+", t)

    budget_words = {
        "bin", "milyon", "k", "lira", "lirasi", "bütce", "butce",
        "butcem", "bütcem", "butcey", "butcemi", "butcem",
        "butce", "butcesi", "butceme", "butcemin", "butceyi",
        "butu", "butcem", "butcemden", "butceye", "bütçe", "budget"
    }

    remaining = [w for w in tokens if w not in budget_words]

    return len(remaining) == 0


def _looks_like_only_existence_query(text: str) -> bool:
    """
    'beyaz eşya var mı', 'beyaz eşya var m' gibi
    sadece 'X var mı?' tarzı basit istekleri yakalar.
    (Bunlarda kategori AI tahmini yapmayacağız.)
    """
    t = _normalize_tr(text or "")
    # 'var mi', 'var m', 'varmi' gibi kalıplar
    if ("var mi" not in t) and ("var m" not in t) and ("varmi" not in t):
        return False

    # içinde sayı varsa bu sadece 'var mı' değildir (bütçeli cümle olabilir)
    if re.search(r"\d", t):
        return False

    # Eğer zaten bizim desteklediğimiz klasik elektronik kelimeler geçiyorsa
    # (laptop, bilgisayar, telefon vs.), bunları AI ile tahmin etmeye devam edebiliriz.
    base_words = [
        "laptop", "bilgisayar", "notebook", "pc",
        "masaustu", "masaustu", "telefon", "monitor",
        "kulaklik"
    ]
    if any(w in t for w in base_words):
        return False

    return True


def normalize_price(raw: Any) -> Optional[int]:
    """
    Her türlü fiyat formatını sayıya çevir.
    """
    if raw is None:
        return None

    if isinstance(raw, (int, float)):
        try:
            return int(raw)
        except Exception:
            return None

    s = str(raw).strip().lower()
    s = s.replace("tl", "").replace("₺", "")
    s = s.replace(" ", "")

    if "," in s and "." in s:
        s = s.replace(".", "")
        s = s.split(",", 1)[0]
    elif "," in s and "." not in s:
        s = s.split(",", 1)[0]
    elif "." in s and "," not in s:
        if s.count(".") == 1:
            left, right = s.split(".", 1)
            if len(right) == 3:
                s = left + right
            else:
                s = left
        else:
            s = s.replace(".", "")

    digits = "".join(re.findall(r"\d+", s))
    if not digits:
        return None
    try:
        return int(digits)
    except Exception:
        return None


def _extract_price_pref(text: str) -> Optional[str]:
    """
    Kullanıcının 'ucuz / pahalı' tercihini çıkar.
    """
    t = text.lower()
    cheap_keywords = [
        "ucuz", "uygun fiyat", "uygun fiyatlı", "ekonomik",
        "bütçe düşük", "bütçem az", "çok uygun", "en ucuz"
    ]
    expensive_keywords = [
        "pahalı", "en iyisi olsun", "en iyi olsun", "üst seviye",
        "high end", "bütçe sorun değil", "bütçe sıkıntı değil",
        "paraya bakmıyorum"
    ]

    if any(k in t for k in cheap_keywords):
        return "cheap"
    if any(k in t for k in expensive_keywords):
        return "expensive"
    return None


# ============================================================
#  AI-FIRST INPUT PARSING & KATEGORİ / USAGE ANALİZİ
# ============================================================

def _extract_usage(text: str) -> Optional[str]:
    """
    Basit keyword taraması ile kullanım senaryosu çıkarır.
    """
    t = text.lower()
    if any(k in t for k in ["oyun", "gaming", "oyuncu"]):
        return "oyun"
    if any(k in t for k in ["iş", "ofis", "business", "çalışma"]):
        return "iş"
    if any(k in t for k in ["okul", "öğrenci", "ders", "kampüs", "üniversite"]):
        return "okul"
    if any(k in t for k in ["günlük", "daily", "genel", "ev", "multimedya"]):
        return "günlük"
    return None


def _get_all_categories(products: List[Dict[str, Any]]) -> List[str]:
    """
    products.json içindeki benzersiz kategori listesini döndürür.
    """
    cats: List[str] = []
    seen_norm: set = set()
    for p in products:
        c = p.get("category")
        if not c:
            continue
        norm = _normalize_tr(c)
        if norm not in seen_norm:
            seen_norm.add(norm)
            cats.append(str(c))
    return cats


def ai_understand_input(
    user_text: str,
    products: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """
    Kullanıcı isteğini analiz eder. (Kategori ve bütçe için artık sadece local mantık kullanılıyor.)
    """
    user_text = user_text or ""

    budget = extract_budget(user_text)
    budget_range = extract_budget_range(user_text)
    category = extract_product_category(user_text)
    usage = _extract_usage(user_text)
    brand = extract_brand(user_text)
    price_pref = _extract_price_pref(user_text)

    budget_only = False
    if budget is not None and not budget_range:
        budget_only = _is_budget_only(user_text)

    if budget is None and budget_range:
        budget = (budget_range[0] + budget_range[1]) // 2

    categories = _get_all_categories(products)

    # "beyaz eşya var m", "elbiseler var mı" gibi
    # sadece "X var mı?" tipini yakala → bunlarda ek kategori tahmini yapma
    force_category_invalid = _looks_like_only_existence_query(user_text)

    # Kategori yoksa, ürün kategorileri üzerinden ekstra fuzzy
    # (ama existence query ise bunu bile yapmıyoruz)
    if category is None and categories and not force_category_invalid:
        norm_t = _normalize_tr(user_text)
        words = re.findall(r"[a-zA-ZğĞüÜşŞıİöÖçÇ]+", norm_t)
        best_cat = None
        best_score = 0.0

        for c in categories:
            cn = _normalize_tr(c)
            for w in words:
                score = difflib.SequenceMatcher(None, cn, w).ratio()
                if score > best_score:
                    best_score = score
                    best_cat = c

        if best_cat and best_score >= 0.7:
            category = best_cat

    # Ekstra OpenAI kategori tahmini yok; hız ve rate limit için sadece local mantık kullanılıyor.
    return {
        "budget": budget,
        "budget_range": budget_range,
        "category": category,
        "usage": usage,
        "brand": brand,
        "price_pref": price_pref,
        "budget_only": budget_only,
        "force_category_invalid": force_category_invalid,
    }


# ============================================================
#  PUANLAMA, FİYAT BANDI ve AI AÇIKLAMA
# ============================================================

def _price_range_for(
    products: List[Dict[str, Any]],
    category: Optional[str]
) -> Tuple[int, int]:
    """
    Verilen kategori için min/max fiyatı normalize_price kullanarak döndürür.
    """
    prices: List[int] = []
    items = products or []

    if category:
        items = [p for p in items if _same_category(p.get("category", ""), category)]

    for p in items:
        val = normalize_price(p.get("price"))
        if val is not None:
            p["_price"] = val
            prices.append(val)

    if not prices:
        for p in products:
            val = normalize_price(p.get("price"))
            if val is not None:
                p["_price"] = val
                prices.append(val)

    if not prices:
        return 0, 0

    return min(prices), max(prices)


def _normalize_budget(
    budget: Optional[int],
    products: List[Dict[str, Any]],
    category: Optional[str]
) -> int:
    """
    Bütçeyi sistemdeki min/max aralığa oturtur.
    """
    mn, mx = _price_range_for(products, category)
    if mn == 0 and mx == 0:
        return budget or 0

    if budget is None:
        return (mn + mx) // 2
    if budget < mn:
        return mn
    if budget > mx:
        return mx
    return budget


def _usage_score(product: Dict[str, Any], usage: Optional[str]) -> int:
    """
    Ürünün kullanım senaryosuna (oyun/iş/okul/günlük) uygunluğunu puanlar.
    """
    if not usage:
        return 0

    feats = " ".join(product.get("features", [])).lower()
    tags = [str(u).lower() for u in product.get("usage_tags", [])]
    score = 0

    price = product.get("_price")
    if price is None:
        price = normalize_price(product.get("price")) or 0
        product["_price"] = price

    if usage in tags:
        score += 4

    if usage == "oyun":
        if "rtx" in feats or "gaming" in feats:
            score += 4
        if "ryzen 7" in feats or "i7" in feats:
            score += 3
        if "16gb" in feats:
            score += 2
    elif usage == "iş":
        if "i7" in feats or "ryzen 7" in feats:
            score += 3
        if "512gb" in feats:
            score += 2
        if "16gb" in feats:
            score += 2
    elif usage == "okul":
        if price <= 25000:
            score += 3
        if "8gb" in feats:
            score += 1
        if "256gb" in feats:
            score += 1
    elif usage == "günlük":
        if price <= 40000:
            score += 2
        if "16gb" in feats or "8gb" in feats:
            score += 1
        if "512gb" in feats or "256gb" in feats:
            score += 1

    return score


def find_best_products(
    products: List[Dict[str, Any]],
    budget: Optional[int],
    category: Optional[str],
    brand: Optional[str] = None,
    usage: Optional[str] = None,
    budget_range: Optional[Tuple[int, int]] = None
) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Ana seçim motoru.
    Kullanıcı kategori yazsa bile, o kategoride ürün yoksa
    kategoriyi yok sayıp tüm ürünler içinden en iyi eşleşmeyi bulur.
    """
    products = products or []

    # 1) Başlangıçta tüm ürünler aday
    candidates = products[:]

    # 2) Kategoriye göre filtrele, ama *boş kalırsa* fallback olarak tüm ürünleri kullan
    if category:
        cat_filtered = [
            p for p in candidates
            if _same_category(p.get("category", ""), category)
        ]
        if cat_filtered:
            candidates = cat_filtered
        else:
            # Kullanıcı elbise / beyaz eşya yazsa bile,
            # yine de tüm ürünler içinden en yakınları bulacağız.
            candidates = products[:]

    # 3) Marka varsa, marka filtresi
    if brand:
        bnorm = _normalize_tr(brand)
        candidates = [
            p for p in candidates
            if _normalize_tr(p.get("brand", "")) == bnorm
        ]

    if not candidates:
        return None, [], []

    cleaned: List[Dict[str, Any]] = []
    for p in candidates:
        price_val = normalize_price(p.get("price"))
        if price_val is None:
            price_val = 0
        p["_price"] = price_val
        cleaned.append(p)
    candidates = cleaned

    mn, mx = _price_range_for(candidates, category)

    if budget_range and isinstance(budget_range, tuple) and len(budget_range) == 2:
        center = (budget_range[0] + budget_range[1]) // 2
    elif budget is not None:
        center = budget
    else:
        center = (mn + mx) // 2 if (mn or mx) else 0

    base = center

    if budget_range and isinstance(budget_range, tuple) and len(budget_range) == 2:
        rl, rh = budget_range
        low = max(mn, min(rl, rh))
        high = min(mx, max(rl, rh))
        if low <= high:
            band_filtered = [
                p for p in candidates
                if low <= p.get("_price", 0) <= high
            ]
        else:
            band_filtered = []
    else:
        if budget is None:
            band_filtered = []
        else:
            low = max(mn, center - 5000)
            high = min(mx, center + 2000)
            if low <= high:
                band_filtered = [
                    p for p in candidates
                    if low <= p.get("_price", 0) <= high
                ]
            else:
                band_filtered = []

    scored: List[Tuple[Dict[str, Any], int, int, int]] = []
    for p in candidates:
        price = p.get("_price", 0)
        diff = abs(price - base) if base is not None else 0
        over = 1 if (budget is not None and price > budget) else 0
        uscore = _usage_score(p, usage)
        scored.append((p, diff, over, uscore))

    scored.sort(key=lambda x: (x[1], x[2], -x[3]))
    ordered = [x[0] for x in scored]

    if band_filtered:
        band_sorted = [p for p in ordered if p in band_filtered]
        top = band_sorted[0] if band_sorted else ordered[0]
    else:
        top = ordered[0] if ordered else None

    return top, ordered, band_filtered


def _desc_cache_key(
    product: Dict[str, Any],
    budget: Optional[int],
    usage: Optional[str]
) -> Tuple[Any, ...]:
    """
    Açıklama cache key'i.
    """
    name = product.get("name")
    brand = product.get("brand")
    price = product.get("price")
    features = tuple(product.get("features", []))
    if isinstance(budget, (int, float)):
        budget_band = int(budget // 5000)
    else:
        budget_band = None
    return (name, brand, price, features, usage, budget_band)


def generate_ai_short_praise(product: Dict[str, Any]) -> str:
    """
    Alternatif ürünler için 1 cümlelik micro övgü.
    RATE LIMIT VE HIZ NEDENİYLE ARTIK OPENAI KULLANMIYOR.
    Sadece ürünün özelliklerinden statik kısa cümle üretir.
    """
    name = product.get("name", "")
    brand = product.get("brand", "")
    price = product.get("price", "")
    feats = ", ".join(product.get("features", [])[:3])

    base = f"{brand} {name}".strip()
    if feats:
        return f"{base}, {feats} özellikleriyle segmentinde dengeli ve güven veren bir seçenektir."
    else:
        return f"{base}, {price} TL seviyesinde değerlendirilebilecek bir modeldir."


def generate_ai_description(
    product: Dict[str, Any],
    budget: Optional[int],
    usage: Optional[str] = None
) -> str:
    """
    Seçilen ürün için 3–5 cümlelik açıklama.
    (Sadece BURADA 1 adet OpenAI çağrısı yapılıyor; rate limit olursa statik fallback kullanılır.)
    """
    usage_text = usage or ""
    budget_text = f"Bütçe: {budget} TL. " if budget is not None else ""

    cache_key = _desc_cache_key(product, budget, usage_text)
    if cache_key in DESCRIPTION_CACHE:
        return DESCRIPTION_CACHE[cache_key]

    system_msg = (
        "Türkçe yaz. Kısa, teknik ve somut ifade kullan. Abartı yok. "
        "Sadece verilen özelliklerden hareketle kalite ve performans çıkarımı yap."
    )
    user_msg = (
        "Teknik ve güven veren 3-5 cümlelik ürün açıklaması yaz. "
        "Donanım (CPU/GPU/RAM/depolama/ekran) ve kalite algısını netleştir. "
        "Fiyat/performans ve kullanım senaryosuna uygunluğunu belirt.\n\n"
        f"Ürün: {product.get('name')}.\n"
        f"Kategori: {product.get('category', '')}.\n"
        f"Marka: {product.get('brand', '')}.\n"
        f"Özellikler: {', '.join(product.get('features', []))}.\n"
        f"Fiyat: {product.get('price')} TL.\n"
        f"{budget_text}Senaryo: {usage_text}."
    )

    try:
        content = _openai_chat(
            [
                {"role": "system", "content": system_msg},
                {"role": "user", "content": user_msg},
            ],
            max_tokens=180,
            temperature=0.35,
        )
        print("AI AÇIKLAMA ÜRETİLDİ:", content[:120])
        DESCRIPTION_CACHE[cache_key] = content
        return content
    except Exception as e:
        print("AI AÇIKLAMA HATASI, FALLBACK KULLANILDI:", repr(e))
        fallback = (
            f"{product.get('name')} fiyat/performans açısından dengeli bir modeldir. "
            f"{product.get('price')} TL seviyesinde; "
            f"{', '.join(product.get('features', [])[:2])} gibi somut özellikler, "
            f"{usage or 'genel kullanım'} için güven veren bir kalite ve kararlılık sunar."
        )
        DESCRIPTION_CACHE[cache_key] = fallback
        return fallback


# ============================================================
#  FLASK ROUTE'LAR: AUTH, RECOMMEND, HISTORY, MAIN
# ============================================================

@app.route("/")
def home():
    return render_template("index.html")


@app.route("/auth")
def auth_page():
    return render_template("auth.html")


# ---------------- AUTH ENDPOINTLERİ ----------------

@app.route("/register", methods=["POST"])
def register_route():
    data = request.get_json() or {}
    username = (data.get("username") or "").strip()
    password = (data.get("password") or "").strip()

    if not username or not password:
        return jsonify({"error": "Kullanıcı adı ve şifre zorunludur."}), 400

    try:
        user = register_user(username, password)
        return jsonify({"message": "Kayıt başarılı.", "user": user}), 201
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        print("REGISTER HATASI:", repr(e))
        return jsonify({"error": "Kayıt sırasında bir hata oluştu."}), 500


@app.route("/login", methods=["POST"])
def login_route():
    data = request.get_json() or {}
    username = (data.get("username") or "").strip()
    password = (data.get("password") or "").strip()

    if not username or not password:
        return jsonify({"error": "Kullanıcı adı ve şifre zorunludur."}), 400

    try:
        token = login_user(username, password)
        return jsonify({"message": "Giriş başarılı.", "token": token}), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        print("LOGIN HATASI:", repr(e))
        return jsonify({"error": "Giriş sırasında bir hata oluştu."}), 500


@app.route("/me", methods=["GET"])
def me_route():
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return jsonify({"error": "Yetkisiz erişim. Token bulunamadı."}), 401

    token = auth_header.split(" ", 1)[1].strip()
    if not token:
        return jsonify({"error": "Geçersiz token."}), 401

    try:
        user = verify_token(token)
        return jsonify({"user": user}), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 401
    except Exception as e:
        print("VERIFY_TOKEN HATASI:", repr(e))
        return jsonify({"error": "Token doğrulanırken bir hata oluştu."}), 500


@app.route("/logout", methods=["POST"])
def logout_route():
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return jsonify({"message": "Çıkış yapıldı."})
    token = auth_header.split(" ", 1)[1].strip()
    try:
        logout_token(token)
    except Exception:
        pass
    return jsonify({"message": "Çıkış yapıldı."})


# ---------------- RECOMMEND ENDPOINTİ ----------------

@app.route("/recommend", methods=["POST"])
def recommend():
    try:
        data = request.get_json() or {}
        user_input = (data.get("user_input") or "").strip()

        # --------------------------------------------------
        # 0) Boş input
        # --------------------------------------------------
        if not user_input:
            return jsonify({
                "error_title": "İstek Boş",
                "error_message": "Lütfen alışveriş isteğini yazarak tekrar dener misin?",
                "error_type": "empty_input",
                "ask_new_input": True
            }), 400

        products = load_products()
        if not products:
            return jsonify({
                "error_title": "Sistem Hatası",
                "error_message": "Sistemde tanımlı ürün bulunamadı. Lütfen daha sonra tekrar dene.",
                "error_type": "no_products",
                "ask_new_input": True
            }), 500

        parsed = ai_understand_input(user_input, products)
        budget = parsed.get("budget")
        budget_range = parsed.get("budget_range")
        category = parsed.get("category")
        usage = parsed.get("usage")
        brand = parsed.get("brand")
        price_pref = parsed.get("price_pref")
        budget_only = parsed.get("budget_only", False)
        force_category_invalid = parsed.get("force_category_invalid", False)

        # Sistemdeki tüm kategoriler
        all_cats = _get_all_categories(products)

        # Kullanıcı metninde en az bir kelime var mı? (emoji, boşluk vs. hariç)
        word_tokens = re.findall(r"[a-zA-ZğĞüÜşŞıİöÖçÇ]+", user_input)

        # Eğer bu istek "X var mı?" tipindeyse, kategoriyi zorlamayalım
        if force_category_invalid:
            category = None

        # ======================================================
        #  A) DESTEKLENMEYEN KATEGORİ BLOĞU (ŞU AN DEVRE DIŞI)
        # ======================================================
        # Eğer tekrar aktif etmek istersen, aşağıdaki bloğu açıp
        # find_best_products'taki fallback davranışını buna göre güncelleyebilirsin.
        
        if (
       all_cats and
             word_tokens and
             usage is None and
            brand is None and
             price_pref is None and
             not budget_only and
             (force_category_invalid or category is None)
         ):
             return jsonify({
                 "error_title": "Kategori Desteklenmiyor",
                 "error_message": (
                     "Aradığın ürün kategorisi şu anda sistemde tanımlı değil. "
                    
                ),
                "error_type": "category_invalid",
                "ask_new_input": True
         }), 400

        # ======================================================
        #  B) GERÇEKTEN HİÇBİR ŞEY ANLAŞILMADI (parse error)
        # ======================================================
        if (
            budget is None
            and budget_range is None
            and category is None
            and usage is None
            and brand is None
            and price_pref is None
        ):
            return jsonify({
                "error_title": "İstek Anlaşılamadı",
                "error_message": (
                    "Ne aradığını anlayamadım. Lütfen ürün kategorisi "
                    "ve mümkünse kullanım amacını belirt."
                ),
                "error_type": "parse_error",
                "ask_new_input": True
            }), 400

        # ======================================================
        #  C) SADECE BÜTÇE / BÜTÇE BENZERİ DURUM
        # ======================================================
        if budget_only and category is None and usage is None and brand is None:
            return jsonify({
                "error_title": "Eksik Bilgi",
                "error_message": (
                    "Sadece bütçe bilgisi verdin. "
                    "Lütfen ürün kategorisi ekleyerek tekrar dener misin?"
                ),
                "error_type": "budget_only",
                "ask_new_input": True
            }), 400

        # ======================================================
        #  D) BURADAN SONRASI: EN AZ BİR ANLAMLI SİNYAL VAR
        # ======================================================

        # Ana seçim motoru
        main_product, ordered, band_filtered = find_best_products(
            products=products,
            budget=budget,
            category=category,
            brand=brand,
            usage=usage,
            budget_range=budget_range,
        )

        # Bütçe/band + kategori var ama bu bantta ürün yoksa
        if (budget is not None or budget_range is not None) and category and not band_filtered:
            mn, mx = _price_range_for(products, category)
            return jsonify({
                "error_title": "Uygun Ürün Bulunamadı",
                "error_message": (
                    "Verdiğin bütçe aralığında bu kategoride ürün bulunamadı. "
                    "Bütçeyi değiştirerek veya farklı bir kategori seçerek tekrar deneyebilirsin."
                ),
                "error_type": "budget_out_of_range",
                "min_price": mn if mn != 0 else None,
                "max_price": mx if mx != 0 else None,
                "ask_new_input": True
            }), 400

        # Eğer kategori yoksa ve bant yoksa, en popüler kategoriden ürün göster (edge-case)
        if not category and not band_filtered and ordered:
            category_products: Dict[str, List[Dict[str, Any]]] = {}
            for p in products:
                cat = p.get("category")
                if cat:
                    if cat not in category_products:
                        category_products[cat] = []
                    category_products[cat].append(p)

            if category_products:
                most_common_category = max(category_products, key=lambda k: len(category_products[k]))
                band_filtered = category_products[most_common_category][:3]
                if band_filtered:
                    main_product = band_filtered[0]

        if not ordered or main_product is None:
            return jsonify({
                "error_title": "Uygun Ürün Bulunamadı",
                "error_message": (
                    "Bu istek için uygun ürün bulamadım. "
                    "Bütçeyi veya kategoriyi değiştirerek tekrar deneyebilirsin."
                ),
                "error_type": "no_match",
                "ask_new_input": True
            }), 404

        # ------------------- NORMAL AKIŞ -------------------
        ai_description = generate_ai_description(main_product, budget, usage)

        # ---------- FİYAT TERCİHİ (UCUZ / PAHALI) ----------
        if price_pref in ("cheap", "expensive"):
            category_candidates: List[Dict[str, Any]] = []
            for p in products:
                if category and not _same_category(p.get("category", ""), category):
                    continue
                price_val = normalize_price(p.get("price"))
                if price_val is None:
                    price_val = 0
                p["_price_val"] = price_val
                category_candidates.append(p)

            if not category_candidates:
                band_ordered = ordered[:3]
            else:
                if price_pref == "cheap":
                    category_candidates.sort(key=lambda p: p.get("_price_val", 0))
                else:
                    category_candidates.sort(key=lambda p: p.get("_price_val", 0), reverse=True)
                band_ordered = category_candidates[:3]
        else:
            # ---------- NORMAL BAND / BÜTÇE MANTIĞI ----------
            if budget is None and not budget_range:
                band_ordered = ordered[:3]
            else:
                band_ordered = [p for p in ordered if p in band_filtered]
                if not band_ordered:
                    band_ordered = ordered[:3]

            if category:
                band_ordered = [
                    p for p in band_ordered
                    if _same_category(p.get("category", ""), category)
                ]

            if budget_range:
                rlow = min(budget_range[0], budget_range[1])
                rhigh = max(budget_range[0], budget_range[1])
                band_ordered = [
                    p for p in band_ordered
                    if rlow <= (p.get("_price") or normalize_price(p.get("price")) or 0) <= rhigh
                ]

            if not band_ordered and ordered:
                band_ordered = ordered[:3]

        if not band_ordered:
            band_ordered = [main_product]

        display_main = band_ordered[0]

        recommended_payload: List[Dict[str, Any]] = []
        main_name = display_main.get("name")

        for p in band_ordered[:3]:
            if p.get("name") == main_name:
                ai_reason = ai_description
            else:
                short_praise = generate_ai_short_praise(p)
                ai_reason = (
                    f"{short_praise} "
                    f"{p.get('name')} fiyat/performans açısından bütçene ve kullanım senaryona yakın "
                    f"bir alternatif olarak listelendi."
                )

            recommended_payload.append({
                "name": p.get("name"),
                "url": p.get("url", "#"),
                "price": p.get("price"),
                "score": _usage_score(p, usage),
                "ai_reason": ai_reason,
            })

        if budget_range:
            display_low = min(budget_range[0], budget_range[1])
            display_high = max(budget_range[0], budget_range[1])
        else:
            display_low = budget
            display_high = budget

        payload: Dict[str, Any] = {
            "product_name": display_main.get("name"),
            "product_url": display_main.get("url", "#"),
            "product_price": display_main.get("price"),
            "ai_description": ai_description,
            "usage": usage,
            "normalized_budget": _normalize_budget(budget, products, category),
            "recommended": recommended_payload,
            "band_low": display_low,
            "band_high": display_high,
        }

        # Geçmiş kaydı – SADECE GİRİŞ YAPANLAR İÇİN
        record = {
            "timestamp": int(time.time()),
            "input": user_input,
            "product_name": payload["product_name"],
            "product_price": payload["product_price"],
        }

        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header.split(" ", 1)[1].strip()
            try:
                user = verify_token(token)
                uname = user.get("username")
                if uname:
                    push_history(uname, record)
            except Exception as e:
                print("history push hata / misafir:", repr(e))
                pass

        return jsonify(payload)

    except Exception as e:
        print("RECOMMEND HATASI:", repr(e))
        return jsonify({
            "error_title": "Bir Hata Oluştu",
            "error_message": "Beklenmeyen bir hata oluştu. Lütfen tekrar deneyin.",
            "error_type": "server",
            "ask_new_input": True
        }), 500


# ---------------- HISTORY ENDPOINTLERİ ----------------

@app.route("/history", methods=["GET"])
def history_get():
    auth_header = request.headers.get("Authorization", "")

    # Giriş yapılmamışsa sunucu tarafında hiç geçmiş dönme
    if not auth_header.startswith("Bearer "):
        return jsonify({"history": []})

    token = auth_header.split(" ", 1)[1].strip()
    try:
        user = verify_token(token)
        uname = user.get("username")
        if not uname:
            return jsonify({"history": []})
        return jsonify({"history": USER_HISTORY.get(uname, [])})
    except ValueError:
        # Token hatalı / süresi dolmuş → history yok varsay
        return jsonify({"history": []})
    except Exception as e:
        print("HISTORY_GET HATASI:", repr(e))
        return jsonify({"history": []})


@app.route("/history/clear", methods=["POST"])
def history_clear():
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        # Misafir için server tarafında zaten history tutmuyoruz
        return jsonify({"message": "Oturum yok"}), 401

    token = auth_header.split(" ", 1)[1].strip()
    try:
        user = verify_token(token)
        uname = user.get("username")
        if uname:
            clear_history(uname)
        return jsonify({"message": "Geçmiş temizlendi"})
    except ValueError:
        return jsonify({"message": "Oturum süren dolmuş. Lütfen tekrar giriş yap."}), 401
    except Exception as e:
        print("HISTORY_CLEAR HATASI:", repr(e))
        return jsonify({"message": "Geçmiş temizlenemedi"}), 500


@app.route("/history/delete", methods=["POST"])
def history_delete():
    auth_header = request.headers.get("Authorization", "")
    data = request.get_json() or {}
    ts = data.get("timestamp")

    if not auth_header.startswith("Bearer "):
        # Misafir için server tarafında history yok
        return jsonify({"message": "Oturum yok"}), 401

    if not isinstance(ts, (int, float)):
        return jsonify({"message": "Geçersiz kayıt"}), 400

    token = auth_header.split(" ", 1)[1].strip()

    try:
        user = verify_token(token)
        uname = user.get("username")
        if not uname:
            return jsonify({"message": "Oturum yok"}), 401

        items = USER_HISTORY.get(uname, [])
        USER_HISTORY[uname] = [x for x in items if x.get("timestamp") != int(ts)]
        return jsonify({"message": "Silindi"})

    except ValueError as e:
        # Geçersiz veya süresi dolmuş token → 401 dön
        print("HISTORY_DELETE HATASI:", repr(e))
        return jsonify({"message": "Oturum süren dolmuş. Lütfen tekrar giriş yap."}), 401

    except Exception as e:
        print("HISTORY_DELETE HATASI:", repr(e))
        return jsonify({"message": "Silinemedi"}), 500


# ============================================================
#  MAIN
# ============================================================

if __name__ == "__main__":
    app.run(debug=True)