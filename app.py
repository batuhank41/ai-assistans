from flask import Flask, request, jsonify, render_template
from auth import register_user, login_user, verify_token, logout_token, push_history, clear_history, USER_HISTORY
import json
import re
import os
import mysql.connector
from mysql.connector import Error
import difflib
import random
from openai import OpenAI
from dotenv import load_dotenv
import time
import random
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
DESCRIPTION_CACHE: Dict[Any, str] = {}


def _openai_chat(
    messages: List[Dict[str, str]],
    max_tokens: int = 250,
    temperature: float = 0.3,
    retries: int = 1
) -> str:
    """OpenAI API Wrapper"""
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
                model="gpt-4o-mini", # Model ismini güncelledik (4.1-mini yok, 4o-mini veya 3.5-turbo kullan)
                input=prompt,
                temperature=temperature,
                max_output_tokens=max_tokens,
            )
            text = resp.output[0].content[0].text
            return text.strip()
        except Exception as e:
            msg = str(e)
            if "rate_limit" in msg or "429" in msg:
                print("OpenAI HATASI: RATE LIMIT.")
                raise RuntimeError("openai_rate_limited")
            print("OpenAI HATASI:", repr(e))
            last_err = e
            break
    raise RuntimeError("openai_unavailable") from last_err


# ============================================================
#  ÜRÜN YÜKLEME & GENEL YARDIMCI FONKSİYONLAR
# ============================================================

def load_products(min_price=None, max_price=None, limit=100) -> List[Dict[str, Any]]:
    products = []
    
    db_config = {
        'host': 'localhost',
        'user': 'root',
        'password': '', 
        'database': 'projedb'
    }

    try:
        connection = mysql.connector.connect(**db_config)
        
        if connection.is_connected():
            cursor = connection.cursor(dictionary=True)
            
            # ============================================================
            # 1. BÖLÜM: LAPTOPLARI ÇEK (hepsiburada_dizustu_bilgisayar)
            # ============================================================
            sql_laptop = "SELECT * FROM `hepsiburada_dizustu_bilgisayar` WHERE 1=1"
            params_laptop = []
            
            # Laptop Fiyat Filtresi (Fiyat sütunu: '15.000 TL')
            if min_price is not None:
                sql_laptop += " AND CAST(REPLACE(REPLACE(Fiyat, ' TL', ''), '.', '') AS UNSIGNED) >= %s"
                params_laptop.append(min_price)
            if max_price is not None:
                sql_laptop += " AND CAST(REPLACE(REPLACE(Fiyat, ' TL', ''), '.', '') AS UNSIGNED) <= %s"
                params_laptop.append(max_price)
            
            # Her iki kategoriden de dengeli gelsin diye limiti paylaştırıyoruz (Örn: 50 laptop, 50 telefon)
            split_limit = int(limit / 2) + 10 # Biraz espayı bırakalım
            sql_laptop += " ORDER BY RAND() LIMIT %s"
            params_laptop.append(split_limit)

            cursor.execute(sql_laptop, tuple(params_laptop))
            laptop_rows = cursor.fetchall()

            for row in laptop_rows:
                features = []
                if row.get('_lemci'): features.append(row['_lemci'])
                
                # Nesil düzeltmesi (12.Nesil. Nesil hatasını önler)
                raw_gen = str(row.get('_lemci_Nesli', ''))
                clean_gen = raw_gen.lower().replace('nesil', '').replace('.', '').strip()
                if clean_gen: features.append(f"{clean_gen}. Nesil")
                
                if row.get('Ram_Sistem_Belle_i_'): features.append(f"{row['Ram_Sistem_Belle_i_']} RAM")
                if row.get('SSD_Kapasitesi'): features.append(f"{row['SSD_Kapasitesi']} SSD")
                if row.get('Ekran_Kart_'): features.append(row['Ekran_Kart_'])

                raw_name = row.get('_r_n_Ad_', '')
                brand_guess = raw_name.split(" ")[0] if raw_name else "Bilinmiyor"
                
                # Fiyat Temizleme
                raw_price_str = row.get('Fiyat', '0')
                try:
                    clean_price = int(raw_price_str.replace('.','').replace(' TL','').strip())
                except:
                    clean_price = 0

                products.append({
                    "name": raw_name,
                    "price": raw_price_str,
                    "brand": brand_guess,
                    "category": "Bilgisayar",
                    "features": features,
                    "url": "#",
                    "_price": clean_price
                })

            # ============================================================
            # 2. BÖLÜM: TELEFONLARI ÇEK (Trendyol_Smartphones_dataset)
            # ============================================================
            sql_phone = "SELECT * FROM `Trendyol_Smartphones_dataset` WHERE 1=1"
            params_phone = []

            # Telefon Fiyat Filtresi (Price sütunu: '3500' veya '3500 TL')
            if min_price is not None:
                sql_phone += " AND CAST(Price AS UNSIGNED) >= %s"
                params_phone.append(min_price)
            if max_price is not None:
                sql_phone += " AND CAST(Price AS UNSIGNED) <= %s"
                params_phone.append(max_price)

            sql_phone += " ORDER BY RAND() LIMIT %s"
            params_phone.append(split_limit)

            cursor.execute(sql_phone, tuple(params_phone))
            phone_rows = cursor.fetchall()

            for row in phone_rows:
                features = []
                if row.get('CPU'): features.append(f"İşlemci: {row['CPU']}")
                if row.get('RAM'): features.append(f"{row['RAM']}GB RAM")
                if row.get('Storage'): features.append(f"{row['Storage']}GB Hafıza")
                if row.get('Screen_Size'): features.append(f"{row['Screen_Size']} inç Ekran")
                if row.get('Camera_Resolution'): features.append(f"{row['Camera_Resolution']} MP Kamera")
                if row.get('Battery_Power'): features.append(f"{row['Battery_Power']} mAh")

                # Marka ve Model Birleştirme
                brand_val = row.get('Brand', '')
                model_val = row.get('Model', '')
                full_name = f"{brand_val} {model_val}".strip()

                # Fiyat Temizleme
                raw_price_str = str(row.get('Price', '0'))
                try:
                    # Virgül veya nokta karmaşasını temizle
                    clean_price = int(float(raw_price_str.replace('TL', '').replace('.', '').replace(',', '.').strip()))
                except:
                    clean_price = 0

                products.append({
                    "name": full_name,
                    "price": clean_price, # Telefonlar için direkt sayı basıyoruz
                    "brand": brand_val,
                    "category": "Telefon",
                    "features": features,
                    "url": row.get('Link', '#') if row.get('Link') else "#", # Link varsa al
                    "_price": clean_price
                })

            cursor.close()
            connection.close()
            
            # Listeyi karıştır ki hep önce bilgisayarlar gelmesin
            import random
            random.shuffle(products)
            
            print(f"Toplam {len(products)} ürün çekildi (Laptop + Telefon).")

    except Error as e:
        print(f"Veritabanı Hatası: {e}")
        return []

    return products


def _normalize_tr(s: Any) -> str:
    mapping = {"ö": "o", "Ö": "o", "ü": "u", "Ü": "u", "ş": "s", "Ş": "s", "ı": "i", "İ": "i", "ğ": "g", "Ğ": "g", "ç": "c", "Ç": "c"}
    s = "" if s is None else str(s).strip()
    return "".join(mapping.get(ch, ch).lower() for ch in s)


def _same_category(cat1: Any, cat2: Any) -> bool:
    if not cat1 or not cat2: return False
    return _normalize_tr(cat1) == _normalize_tr(cat2)


def extract_category_dynamically(user_text: str, all_products: List[Dict[str, Any]]) -> Optional[str]:
    """
    EN AKILLI KATEGORİ BULUCU (Apple Watch sorununu çözen yer)
    Kullanıcının yazdığı kelimeler ile veritabanındaki ürün isimlerini kıyaslar.
    Manuel listeye gerek duymaz.
    """
    if not user_text or not all_products:
        return None

    user_text = _normalize_tr(user_text)
    user_words = set(re.findall(r"[a-zA-Z0-9ğĞüÜşŞıİöÖçÇ]+", user_text))
    
    stopwords = {"bana", "lazim", "istiyorum", "var", "mi", "bir", "tane", "alcam", "fiyat"}
    user_words = {w for w in user_words if w not in stopwords and len(w) >= 2}

    best_category = None
    max_match_count = 0

    for product in all_products:
        p_name = _normalize_tr(product.get("name", ""))
        p_cat = product.get("category")
        if not p_cat: continue

        product_words = set(re.findall(r"[a-zA-Z0-9ğĞüÜşŞıİöÖçÇ]+", p_name))
        
        # Kesişim sayısı (Örn: "Apple" ve "Watch" -> 2 kelime tuttu)
        common_words = user_words.intersection(product_words)
        match_count = len(common_words)

        if match_count > max_match_count:
            max_match_count = match_count
            best_category = p_cat
            
    if max_match_count > 0:
        return best_category
    t = _normalize_tr(user_text)
    syn_map = {
        "bilgisayar": ["bilgisayar","laptop","notebook","pc","dizustu","dizüstü","tasinabilir","taşınabilir"],
        "telefon": ["telefon","cep","smartphone","android","ios","phone"],
        "kulaklık": ["kulaklik","kulaklık","earphone","headphone","headset","buds"],
        "monitor": ["monitor","monitör","ekran"],
        "akıllı saat": ["akilli saat","akıllı saat","watch","saat"]
    }
    words = set(re.findall(r"[a-z0-9ğĞüÜşŞıİöÖçÇ]+", t))
    for cat, kws in syn_map.items():
        for k in kws:
            if k in words or k in t:
                return cat
    return None


def extract_brand(text: str) -> Optional[str]:
    """
    AKILLI MARKA BULUCU (AirPods -> Apple sorununu çözen yer)
    """
    if not text: return None
    t_lower = _normalize_tr(text)
    try: products = load_products()
    except: return None

    # 1. Marka listesi kontrolü
    known_brands = set()
    for p in products:
        b = p.get("brand")
        if b: known_brands.add(str(b))
    
    for brand in known_brands:
        b_norm = _normalize_tr(brand)
        if re.search(r"(?:^|\s)" + re.escape(b_norm) + r"(?:$|\s)", t_lower):
            return brand

    # 2. Ürün isminden kontrol (AirPods yazınca Apple bulsun)
    user_words = re.findall(r"[a-z0-9]+", t_lower)
    for word in user_words:
        if len(word) < 3: continue 
        for p in products:
            p_name = _normalize_tr(p.get("name", ""))
            p_brand = p.get("brand")
            if word in p_name and p_brand:
                return str(p_brand)
    return None


def extract_budget(text: str) -> Optional[int]:
    if not text: return None
    t = text.lower().replace("₺", " tl ")
    candidates: List[int] = []
    pattern = re.compile(r"(\d{1,7})\s*(bin|k|milyon)?\s*(tl|lira|lirasi)?")
    for m in pattern.finditer(t):
        num_str, unit, cur = m.groups()
        try: val = int(num_str)
        except: continue
        if unit == "milyon": val *= 1_000_000
        elif unit in ("bin", "k"): val *= 1000
        
        window = t[max(0, m.start() - 15): m.end() + 15]
        money_hint = any(w in window for w in ["tl", "lira", "bütçe", "fiyat"])
        if not cur and not money_hint: continue
        if val < 1000: continue
        candidates.append(val)
    if not candidates: return None
    val = max(candidates)
    if val <= 0: return None
    return val


def extract_budget_range(text: str) -> Optional[Tuple[int, int]]:
    t = text.lower().replace("₺", " tl ")
    tokens = re.findall(r"(\d{1,7})(?:\s*(bin|k|milyon))?", t)
    vals: List[int] = []
    for num, unit in tokens:
        try: v = int(num)
        except: continue
        if unit == "milyon": v *= 1_000_000
        elif unit in ("bin", "k"): v *= 1000
        vals.append(v)
    if "bin" in t and any(v >= 10_000 for v in vals) and any(v < 1000 for v in vals):
        vals = [v * 1000 if v < 1000 else v for v in vals]
    if len(vals) >= 2:
        low, high = sorted(vals[:2])
        return low, high
    return None


def _is_budget_only(text: str) -> bool:
    t = _normalize_tr(text or "").replace("tl", " ").replace("turk lirası", " ")
    tokens = re.findall(r"[a-zA-ZğĞüÜşŞıİöÖçÇ]+", t)
    budget_words = {"bin", "milyon", "k", "lira", "bütce", "butce", "fiyat", "tl"}
    remaining = [w for w in tokens if w not in budget_words]
    return len(remaining) == 0


def _looks_like_only_existence_query(text: str) -> bool:
    t = _normalize_tr(text or "")
    if ("var mi" not in t) and ("var m" not in t) and ("varmi" not in t): return False
    if re.search(r"\d", t): return False
    return True

def _detect_unsupported_category(text: str) -> bool:
    t = _normalize_tr(text or "")
    unsupported = [
        "buzdolabi","buzdolabı","beyaz esya","beyaz eşya","camasir","çamaşır",
        "bulasik","bulaşık","firin","fırın","ocak","klima","mikrodalga","ankastre","takim","takımı","set"
    ]
    base = ["laptop","bilgisayar","notebook","pc","telefon","monitor","kulaklik","kulaklık","akilli saat","akıllı saat","watch","saat"]
    return any(k in t for k in unsupported) and not any(b in t for b in base)


def normalize_price(raw: Any) -> Optional[int]:
    if raw is None: return None
    if isinstance(raw, (int, float)):
        try: return int(raw)
        except: return None
    s = str(raw).strip().lower().replace("tl", "").replace("₺", "").replace(" ", "")
    if "," in s and "." in s: s = s.replace(".", "").split(",", 1)[0]
    elif "," in s: s = s.split(",", 1)[0]
    elif "." in s:
        if s.count(".") == 1 and len(s.split(".")[1]) == 3: s = s.replace(".", "")
        elif s.count(".") > 1: s = s.replace(".", "")
    digits = "".join(re.findall(r"\d+", s))
    if not digits: return None
    try: return int(digits)
    except: return None


def _extract_price_pref(text: str) -> Optional[str]:
    t = _normalize_tr(text or "")
    neg_patterns = ["pahali degil", "pahali olmayan", "cok pahali degil", "cok pahali olmayan", "pahali olmayan"]
    cheap_kws = ["ucuz", "uygun", "ekonomik", "makul", "bütçe düsük", "butce dusuk", "fiyat uygun"]
    expensive_kws = ["pahali", "cok pahali", "ust seviye", "en iyi", "high end", "premium", "butce sorun degil", "fiyat sorun degil"]
    if any(p in t for p in neg_patterns):
        return "cheap"
    if any(k in t for k in cheap_kws):
        return "cheap"
    if any(k in t for k in expensive_kws):
        return "expensive"
    return None


def _extract_usage(text: str) -> Optional[str]:
    t = text.lower()
    if any(k in t for k in ["oyun", "gaming"]): return "oyun"
    if any(k in t for k in ["iş", "ofis", "business"]): return "iş"
    if any(k in t for k in ["okul", "öğrenci", "ders"]): return "okul"
    if any(k in t for k in ["günlük", "ev"]): return "günlük"
    return None


def _get_all_categories(products: List[Dict[str, Any]]) -> List[str]:
    cats: List[str] = []
    seen_norm: set = set()
    for p in products:
        c = p.get("category")
        if not c: continue
        norm = _normalize_tr(c)
        if norm not in seen_norm:
            seen_norm.add(norm)
            cats.append(str(c))
    return cats


def ai_understand_input(user_text: str, products: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Kullanıcı isteğini analiz eder.
    iPhone 17 sorununu çözen yer: Sayıları 'required_numbers' olarak ayırır.
    """
    user_text = user_text or ""
    t_lower = _normalize_tr(user_text)

    budget = extract_budget(user_text)
    budget_range = extract_budget_range(user_text)
    
    # Kategori Bulma (SADECE DİNAMİK YÖNTEM)
    category = extract_category_dynamically(user_text, products)

    usage = _extract_usage(user_text)
    brand = extract_brand(user_text)
    price_pref = _extract_price_pref(user_text)
    
    budget_only = False
    if budget is not None and not budget_range:
        budget_only = _is_budget_only(user_text)

    if budget is None and budget_range:
        budget = (budget_range[0] + budget_range[1]) // 2

    # --- TEKNİK ÖZELLİK FİLTRESİ ---
    spec_regex = r"(?:^|\s)(i[3579]|ryzen\s?\d*|rtx\s?\d*|gtx\s?\d*|\d+gb|\d+tb|ssd)(?:$|\s)"
    found_specs = re.findall(spec_regex, t_lower)
    spec_filters = [s.strip().replace(" ", "") for s in found_specs]
    spec_nums_exclude = set()
    for s in spec_filters:
        m = re.match(r"^(\d+)(gb|tb)$", s)
        if m:
            spec_nums_exclude.add(m.group(1))

    # --- MODEL NUMARASI FİLTRESİ (ZORUNLU SAYILAR) ---
    all_numbers = re.findall(r"\b\d+\b", t_lower)
    required_numbers = []
    for n in all_numbers:
        val = int(n)
        if n in spec_nums_exclude:
            continue
        if val < 10:
            continue
        if val < 10000 and val != budget:
            required_numbers.append(n)

    force_category_invalid = _looks_like_only_existence_query(user_text) or _detect_unsupported_category(user_text)

    return {
        "budget": budget,
        "budget_range": budget_range,
        "category": category,
        "usage": usage,
        "brand": brand,
        "price_pref": price_pref,
        "budget_only": budget_only,
        "force_category_invalid": force_category_invalid,
        "spec_filters": spec_filters,
        "required_numbers": required_numbers # <-- ÖNEMLİ
    }


# ============================================================
#  PUANLAMA & ÖNERİ MOTORU
# ============================================================

def _price_range_for(products: List[Dict[str, Any]], category: Optional[str]) -> Tuple[int, int]:
    items = products or []
    if category:
        items = [p for p in items if _same_category(p.get("category", ""), category)]
    
    prices = []
    for p in items:
        val = normalize_price(p.get("price"))
        if val is not None:
            p["_price"] = val
            prices.append(val)
            
    if not prices:
        for p in products:
             val = normalize_price(p.get("price"))
             if val is not None: prices.append(val)
    if not prices: return 0, 0
    return min(prices), max(prices)


def _normalize_budget(budget: Optional[int], products: List[Dict[str, Any]], category: Optional[str]) -> int:
    mn, mx = _price_range_for(products, category)
    if mn == 0 and mx == 0: return budget or 0
    if budget is None: return (mn + mx) // 2
    if budget < mn: return mn
    if budget > mx: return mx
    return budget


def _usage_score(product: Dict[str, Any], usage: Optional[str]) -> int:
    if not usage: return 0
    feats = " ".join(product.get("features", [])).lower()
    tags = [str(u).lower() for u in product.get("usage_tags", [])]
    score = 0
    if usage in tags: score += 4
    if usage == "oyun" and ("rtx" in feats or "16gb" in feats): score += 3
    if usage == "iş" and ("ssd" in feats or "i7" in feats): score += 3
    if usage == "okul" and "8gb" in feats: score += 2
    return score


def find_best_products(
    products: List[Dict[str, Any]],
    budget: Optional[int],
    category: Optional[str],
    brand: Optional[str] = None,
    usage: Optional[str] = None,
    budget_range: Optional[Tuple[int, int]] = None,
    spec_filters: List[str] = None,
    required_numbers: List[str] = None,
    user_input_text: str = ""
) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    
    candidates = products[:]
    
    # 1. Kategori Filtresi
    if category:
        filtered = [p for p in candidates if _same_category(p.get("category"), category)]
        if filtered: candidates = filtered

    # 2. Marka Filtresi
    if brand:
        candidates = [p for p in candidates if _normalize_tr(p.get("brand")) == _normalize_tr(brand)]

    # 3. Teknik Özellik Filtresi
    if spec_filters:
        for spec in spec_filters:
            filtered_by_spec = []
            for p in candidates:
                p_text = (_normalize_tr(p.get("name", "")) + " " + " ".join([_normalize_tr(f) for f in p.get("features", [])]))
                if spec in p_text.replace(" ", "") or spec in p_text:
                    filtered_by_spec.append(p)
            if filtered_by_spec:
                candidates = filtered_by_spec

    # 4. KATI SAYI FİLTRESİ
    if required_numbers:
        strict_candidates = []
        for p in candidates:
            p_name = _normalize_tr(p.get("name", ""))
            matches_all = True
            for num in required_numbers:
                if num not in p_name: 
                    matches_all = False
                    break
            if matches_all:
                strict_candidates.append(p)
        
        if strict_candidates:
            candidates = strict_candidates
        else:
            candidates = []

    if not candidates: return None, [], []

    # Fiyat Temizliği
    for p in candidates: p["_price"] = normalize_price(p.get("price")) or 0

    # Fiyat Bandı Kontrolü
    if budget_range:
        low, high = budget_range
    elif budget:
        low, high = budget * 0.7, budget * 1.3
    else:
        low, high = 0, float('inf')

    # Band içindeki ürünleri bul
    band_filtered = [p for p in candidates if low <= p["_price"] <= high]

    # --- OPTİMİZASYON: LİMİTLEME (Fix for 500+ items crash) ---
    # Eğer 50'den fazla ürün varsa, sistemi yormamak için listeyi daraltıyoruz.
    if len(band_filtered) > 50:
        # Fiyata göre sırala
        band_filtered.sort(key=lambda x: x["_price"])
        
        # Listeyi 3 parçaya böl (Ucuz, Orta, Pahalı) ve her parçadan örnek al
        # Böylece 8000-60000 arası dendiğinde sadece 8000likler gelmez, 60000likler de gelir.
        chunk_size = len(band_filtered) // 3
        
        part1 = band_filtered[:15] # En ucuzlar
        part2 = band_filtered[chunk_size : chunk_size + 15] # Ortalar
        part3 = band_filtered[-15:] # En pahalılar
        
        # Yeni daraltılmış liste
        band_filtered = part1 + part2 + part3
    # ----------------------------------------------------------

    # Puanlama
    scored = []
    target = budget or (low+high)/2
    if target == float('inf') or target == 0: target = 1
    
    user_words = []
    if user_input_text:
        user_words = _normalize_tr(user_input_text).split()

    for p in band_filtered: # Artık sadece daraltılmış listede döngü kuruyoruz
        price = p["_price"]
        diff = abs(price - target)
        uscore = _usage_score(p, usage)
        
        name_match_score = 0
        p_name_norm = _normalize_tr(p.get("name", ""))
        for w in user_words:
            if len(w) < 2: continue
            if w in p_name_norm:
                name_match_score += 100 

        scored.append((p, -name_match_score, diff, -uscore))
        
    scored.sort(key=lambda x: (x[1], x[2], x[3]))
    ordered = [x[0] for x in scored]
    
    # Eğer filtreleme sonucu boş kaldıysa (çok nadir), ham ordered listesini dön
    final_band = ordered if ordered else band_filtered
    top = final_band[0] if final_band else None
    
    return top, final_band, final_band


def _desc_cache_key(p, budget, usage):
    return (p.get("name"), p.get("brand"), p.get("price"), tuple(p.get("features", [])), usage, budget)


def generate_ai_short_praise(product: Dict[str, Any]) -> str:
    feats = ", ".join(product.get("features", [])[:3])
    base = f"{product.get('brand')} {product.get('name')}".strip()
    return f"{base}, {feats} özellikleriyle öne çıkıyor."


def generate_ai_alt_snippet(product: Dict[str, Any]) -> str:
    brand = (product.get("brand") or "").strip()
    name = (product.get("name") or "").strip()
    base = f"{brand} {name}".strip()
    feats = product.get("features", [])
    feat_line = ", ".join(feats[:3])
    left = f"{base}, {feat_line} özellikleriyle öne çıkıyor." if feat_line else f"{base} öne çıkıyor."
    right = f"fiyat/performans açısından bütçene ve kullanım senaryona uygun bir alternatif olarak listelendi."
    return f"{left} {right}"

def generate_ai_description(product: Dict[str, Any], budget: Optional[int], usage: Optional[str] = None) -> str:
    """
    TEKNİK VE CİDDİ AÇIKLAMA ÜRETİCİ
    """
    usage_text = usage or "genel kullanım"
    cache_key = _desc_cache_key(product, budget, usage_text)
    if cache_key in DESCRIPTION_CACHE: return DESCRIPTION_CACHE[cache_key]

    system_msg = (
        "Sen deneyimli bir teknoloji editörüsün. "
        "Ürünleri tanıtırken asla 'harika', 'mükemmel' gibi satış ağzı kullanma. "
        "Tamamen teknik özelliklere ve bunların kullanıcıya sağladığı somut faydaya odaklan. "
        "Cümlelerin nesnel, bilgilendirici ve profesyonel olsun. "
        "Şu yapıyı takip et: 1. İşlemci/RAM/Güç, 2. Ekran/Görüntü kalitesi, 3. Depolama/Kapasite, 4. Segment konumu."
    )
    user_msg = (
        f"Ürün: {product.get('name')}\n"
        f"Özellikler: {', '.join(product.get('features', []))}\n"
        f"Fiyat: {product.get('price')} TL\n"
        f"Kullanım Amacı: {usage_text}\n\n"
        "Bu ürün için teknik detayları vurgulayan, 4-5 cümlelik, akıcı ve profesyonel bir paragraf yaz."
    )

    try:
        content = _openai_chat(
            [{"role": "system", "content": system_msg}, {"role": "user", "content": user_msg}],
            max_tokens=250, temperature=0.3
        )
        DESCRIPTION_CACHE[cache_key] = content
        return content
    except Exception as e:
        return f"{product.get('name')}, teknik donanımıyla {usage_text} senaryolarında kararlı bir performans sunar."


# ============================================================
#  FLASK ROUTE'LAR
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

# 1. Önce bütçeyi input içinden bulalım (Hafif işlem)
        pre_budget_range = extract_budget_range(user_input)
        pre_budget = extract_budget(user_input)

        min_p = None
        max_p = None

        if pre_budget_range:
            min_p = pre_budget_range[0]
            max_p = pre_budget_range[1]
        elif pre_budget:
            # Kullanıcı tek fiyat verdiyse (Örn: 20.000) %30 aşağı ve yukarısına bak
            min_p = int(pre_budget * 0.7)
            max_p = int(pre_budget * 1.3)
        
        # 2. Şimdi veritabanından SADECE bu aralığı isteyelim (Ağır işlem optimize edildi)
        # Limit=50 diyerek en fazla 50 ürün çekiyoruz, 500 tane çekip sistemi yormuyoruz.
        products = load_products(min_price=min_p, max_price=max_p, limit=50)

        if not products:
             # Eğer bütçe aralığında ürün yoksa hata dön
             return jsonify({
                "error_title": "Ürün Bulunamadı",
                "error_message": "Aradığın kriterlere veya bütçe aralığına uygun ürün veritabanında bulunamadı.",
                "error_type": "no_products",
                "ask_new_input": True
            }), 404

        if _detect_unsupported_category(user_input):
            return jsonify({
                "error_title": "Kategori Desteklenmiyor",
                "error_message": "Bu istek mevcut kategorilerle eşleşmiyor.",
                "error_type": "category_invalid",
                "ask_new_input": True
            }), 400

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
            not parsed.get("spec_filters") and
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
       # ------------------------------------------------------------------
        # GÜNCELLENMİŞ ARAMA MOTORU ÇAĞRISI
        # ------------------------------------------------------------------
        main_product, ordered, band_filtered = find_best_products(
            products=products,
            budget=budget,
            category=category,
            brand=brand,
            usage=usage,
            budget_range=budget_range,
            # --- YENİ EKLENEN KRİTİK PARAMETRELER ---
            spec_filters=parsed.get("spec_filters"),       # i5, 16gb, ssd vb.
            required_numbers=parsed.get("required_numbers"), # 17, 15, 3060, 4060 vb.
            user_input_text=user_input                     # Puanlama için tam metin
        )

        # 1. KATI FİLTRE SONUCU ÜRÜN BULUNAMADIYSA (Örn: iPhone 17 yoksa)
        if not main_product:
             return jsonify({
                "error_title": "Ürün Bulunamadı",
                "error_message": "Aradığın modele veya teknik özelliklere sahip bir ürün şu an stoğumuzda bulunmuyor.",
                "error_type": "no_match_filtered",
                "ask_new_input": True
            }), 404

        # 2. ÜRÜN VAR AMA BÜTÇE YETMİYORSA
        if (budget is not None or budget_range is not None) and category and not band_filtered:
            mn, mx = _price_range_for(products, category)
            return jsonify({
                "error_title": "Bütçe Aralığında Ürün Yok",
                "error_message": (
                    "Verdiğin bütçe aralığında bu kategoride ürün bulunamadı. "
                    "Bütçeyi artırmayı veya farklı bir kategori seçmeyi deneyebilirsin."
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

# --- ESKİ KODUN YERİNE GELECEK YENİ BLOK ---

        if not band_ordered:
            band_ordered = [main_product]

        pool = band_ordered if band_ordered else ordered
        seen_keys = set()
        unique_pool: List[Dict[str, Any]] = []
        for p in pool:
            key = p.get("id") or p.get("name")
            if key and key not in seen_keys:
                seen_keys.add(key)
                unique_pool.append(p)

        display_main = main_product or (unique_pool[0] if unique_pool else pool[0])
        main_key = display_main.get("id") or display_main.get("name")
        alt_candidates = [p for p in unique_pool if (p.get("id") or p.get("name")) != main_key]
        if len(alt_candidates) >= 2:
            try:
                alt_selection = random.sample(alt_candidates, 2)
            except Exception:
                alt_selection = alt_candidates[:2]
        else:
            alt_selection = alt_candidates

        selection = [display_main] + alt_selection

        recommended_payload: List[Dict[str, Any]] = []
        for p in selection:
            if (p.get("id") or p.get("name")) == main_key:
                ai_reason = ai_description
            else:
                ai_reason = generate_ai_alt_snippet(p)

            recommended_payload.append({
                "name": p.get("name"),
                "url": p.get("url", "#"),
                "price": p.get("price"),
                "score": _usage_score(p, usage),
                "ai_reason": ai_reason,
            })
            
        # --- BLOK SONU ---

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
