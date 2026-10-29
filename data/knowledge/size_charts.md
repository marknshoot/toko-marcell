# Toko Marcell — Master Sizing & Measurement Guide
**Document Version:** 3.0 (Store Knowledge Base)
**Applicability:** Toko Marcell multi-brand apparel, footwear, accessories and travel catalog
**Usage:** Grounding reference for the AI Stylist & Sizing Concierge
**Structured source of truth:** [`api/knowledge/size_chart.json`](./size_chart.json)
**Category map:** [`api/knowledge/size_kind_map.json`](./size_kind_map.json) · [`api/knowledge/catalog_categories.json`](./catalog_categories.json)
**Label:** *panduan umum toko* — this is NOT an official brand size chart.

---

## 1. Category-Aware Size Scope

The catalog is not one uniform apparel chart. Every `(department, category)` pair is mapped to one
size kind in `size_kind_map.json`; unknown pairs fall back to **unsupported**, never to a tops size.
The map is extracted from the seed catalog by `pipelines/extract_categories.py`.

| Size kind | What it means | Required shopper input |
|---|---|---|
| `tops` | Tops, shirts, sweaters, jackets, outerwear | TB (height, cm) + BB (weight, kg) |
| `bottoms` | Pants, jeans, shorts, leggings, skirts | TB + BB; optional waist (cm) |
| `dress` | Dresses, jumpsuits, rompers, overalls | TB + BB |
| `footwear` | Adult shoes, boots, sandals, slippers | Foot length (cm) |
| `belt` | Belts | Waist circumference (cm) |
| `headwear` | Hats, caps, beanies | Head circumference (cm) |
| `one_size` | Sunglasses, watches, wallets, jewelry, scarves, ties | None |
| `dimensions` | Luggage, bags, backpacks, travel gear | None — read product dimensions/capacity |
| `unsupported` | Lingerie, hosiery, swimwear, kids, costumes, mixed/unknown categories | None — do not guess |

**Kids are excluded.** Baby, Boys and Girls categories are unsupported even when the category is
"Shoes". **Lingerie, underwear, hosiery and socks are excluded** because they need body measurements
this demo does not model. A mixed category such as Novelty or Accessories can only be refined when
the product title clearly identifies a garment or accessory; otherwise it stays unsupported.

---

### A. Tops & Outerwear Size Chart (TB/BB)

This is an authored **store estimate** (`source: store_estimate`, `confidence: medium`), not an
official brand chart. Men and women have separate charts; the women's chart is intentionally
different. If height and weight point to different sizes, prioritize chest/shoulder fit.

**Men — tops**

| Size | Height (TB) | Weight (BB) | Chest circumference | Fit feel |
| :---: | :---: | :---: | :---: | :--- |
| **S** | 150 – 167.9 cm | 45 – 57.9 kg | 88 – 93.9 cm | Fitted / Slim |
| **M** | 168 – 173.9 cm | 58 – 67.9 kg | 94 – 99.9 cm | Regular / Ideal daily fit |
| **L** | 174 – 179.9 cm | 68 – 77.9 kg | 100 – 107.9 cm | Regular / Comfortable drape |
| **XL** | 180 – 185.9 cm | 78 – 87.9 kg | 108 – 115.9 cm | Relaxed / Roomy |
| **XXL** | 186 – 200 cm | 88 – 120 kg | 116 – 130 cm | Oversized / Big & Tall |

**Women — tops**

| Size | Height (TB) | Weight (BB) | Chest circumference | Fit feel |
| :---: | :---: | :---: | :---: | :--- |
| **S** | 150 – 157.9 cm | 42 – 49.9 kg | 78 – 83.9 cm | Fitted / Slim |
| **M** | 158 – 164.9 cm | 50 – 57.9 kg | 84 – 89.9 cm | Regular / Ideal daily fit |
| **L** | 165 – 171.9 cm | 58 – 67.9 kg | 90 – 97.9 cm | Regular / Comfortable drape |
| **XL** | 172 – 179.9 cm | 68 – 79.9 kg | 98 – 107.9 cm | Relaxed / Roomy |
| **XXL** | 180 – 195 cm | 80 – 110 kg | 108 – 125 cm | Oversized / Big & Tall |

*Rule of thumb:* if TB and BB disagree, use chest/shoulder as the tie-breaker. The JSON `tops.rule`
carries the same instruction, so the tool and RAG answer from one source.

---

### B. Bottoms Size Chart — Tabel Ukuran Celana & Pinggang (TB/BB)

Authored **store estimate** (`source: store_estimate`, `confidence: medium`). Waist labels are
nominal inches; the cm range is the store's body-waist estimate, not the garment's flat measurement.
When the shopper supplies an actual waist circumference, the standard inch→cm conversion below takes
precedence.

**Men — bottoms**

| Waist size | Height (TB) | Weight (BB) | Waist circumference | Asian size |
| :---: | :---: | :---: | :---: | :---: |
| **28** | 160 – 167.9 cm | 50 – 59.9 kg | 71.1 – 73.0 cm | XS / S |
| **30** | 168 – 173.9 cm | 60 – 69.9 kg | 76.2 – 78.0 cm | S / M |
| **32** | 174 – 179.9 cm | 70 – 79.9 kg | 81.3 – 83.1 cm | M / L |
| **34** | 180 – 185.9 cm | 80 – 89.9 kg | 86.4 – 88.2 cm | L / XL |
| **36** | 186 – 200 cm | 90 – 120 kg | 91.4 – 93.2 cm | XL / XXL |

**Women — bottoms**

| Waist size | Height (TB) | Weight (BB) | Waist circumference | Asian size |
| :---: | :---: | :---: | :---: | :---: |
| **26** | 150 – 157.9 cm | 42 – 49.9 kg | 66.0 – 67.9 cm | XS |
| **28** | 158 – 164.9 cm | 50 – 57.9 kg | 71.1 – 73.0 cm | S |
| **30** | 165 – 171.9 cm | 58 – 67.9 kg | 76.2 – 78.0 cm | M |
| **32** | 172 – 179.9 cm | 68 – 79.9 kg | 81.3 – 83.1 cm | L |
| **34** | 180 – 195 cm | 80 – 110 kg | 86.4 – 88.2 cm | XL |

---

### C. Dress Size Guidance (TB/BB + Bust-Waist-Hip)

Dresses use the authored **store estimate** table below, with a separate bust/waist/hip check in the
product detail. The chart is a starting point: a bodycon and an A-line dress in the same size can fit
differently. `source: store_estimate`, `confidence: medium`.

| Size | Height (TB) | Weight (BB) | Chest circumference | Fit feel |
| :---: | :---: | :---: | :---: | :--- |
| **S** | 150 – 157.9 cm | 42 – 49.9 kg | 78 – 83.9 cm | Fitted |
| **M** | 158 – 164.9 cm | 50 – 57.9 kg | 84 – 89.9 cm | Regular |
| **L** | 165 – 171.9 cm | 58 – 67.9 kg | 90 – 97.9 cm | Regular |
| **XL** | 172 – 179.9 cm | 68 – 79.9 kg | 98 – 107.9 cm | Relaxed |
| **XXL** | 180 – 195 cm | 80 – 110 kg | 108 – 125 cm | Relaxed |

Always compare the shopper's **bust, waist and hip** with the product's own measurement table before
deciding; the TB/BB chart cannot see the garment cut.

---

### D. Footwear Size Conversion (Foot Length)

Footwear uses **standard conversions**, not a store estimate (`source: standard`, `confidence: high`).
Measure the foot in centimetres (heel to longest toe) and use the nearest 0.5 cm row. The underlying
standard is **Mondopoint ISO 9407** foot-length measurement with standard men's/women's US/UK/EU
retail conversion tables. **Kids are not covered.** Brand lasts can still differ; the brand notes
below are demo data.

**Men — footwear**

| Foot length | US | UK | EU |
| :---: | :---: | :---: | :---: |
| 24.0 cm | 6.0 | 5.5 | 38 |
| 24.5 cm | 6.5 | 6.0 | 39 |
| 25.0 cm | 7.0 | 6.0 | 40 |
| 25.5 cm | 7.5 | 6.5 | 40.5 |
| 26.0 cm | 8.0 | 7.0 | 41 |
| 26.5 cm | 8.5 | 7.5 | 42 |
| 27.0 cm | 9.0 | 8.0 | 42.5 |
| 27.5 cm | 9.5 | 8.5 | 43 |
| 28.0 cm | 10.0 | 9.0 | 44 |
| 28.5 cm | 10.5 | 9.5 | 44.5 |
| 29.0 cm | 11.0 | 10.0 | 45 |
| 29.5 cm | 11.5 | 10.5 | 46 |
| 30.0 cm | 12.0 | 11.0 | 46.5 |
| 30.5 cm | 12.5 | 11.5 | 47 |

**Women — footwear**

| Foot length | US | UK | EU |
| :---: | :---: | :---: | :---: |
| 22.0 cm | 4.5 | 2.5 | 35 |
| 22.5 cm | 5.0 | 3.0 | 35.5 |
| 23.0 cm | 5.5 | 3.5 | 36 |
| 23.5 cm | 6.0 | 4.0 | 36.5 |
| 24.0 cm | 6.5 | 4.5 | 37 |
| 24.5 cm | 7.0 | 5.0 | 38 |
| 25.0 cm | 7.5 | 5.5 | 38.5 |
| 25.5 cm | 8.0 | 6.0 | 39 |
| 26.0 cm | 8.5 | 6.5 | 40 |
| 26.5 cm | 9.0 | 7.0 | 40.5 |
| 27.0 cm | 9.5 | 7.5 | 41 |
| 27.5 cm | 10.0 | 8.0 | 42 |

---

### E. Waist & Belt Size Conversion

Waist conversion is standard: **1 inch = 2.54 cm**. The belt rule of thumb is
**belt length ≈ waist + 2 inches**, then converted to cm (`source: standard`, `confidence: high`).
These are nominal labels; a thick belt or a low-rise fit can change the comfortable length.

| Waist size | Waist circumference | Belt length |
| :---: | :---: | :---: |
| **26** | 66.0 cm | 28 in / 71.1 cm |
| **28** | 71.1 cm | 30 in / 76.2 cm |
| **30** | 76.2 cm | 32 in / 81.3 cm |
| **32** | 81.3 cm | 34 in / 86.4 cm |
| **34** | 86.4 cm | 36 in / 91.4 cm |
| **36** | 91.4 cm | 38 in / 96.5 cm |
| **38** | 96.5 cm | 40 in / 101.6 cm |
| **40** | 101.6 cm | 42 in / 106.7 cm |

---

### F. Headwear Size Bands

Adult hats use common head-circumference bands (`source: standard`, `confidence: high`). Measure
around the widest part of the head, above the ears.

| Size | Head circumference |
| :---: | :---: |
| **S** | 54 – 55 cm |
| **M** | 56 – 57 cm |
| **L** | 58 – 59 cm |
| **XL** | 60 – 61 cm |

---

### G. One-Size Accessories

Sunglasses, watches, wallets, jewelry, scarves and ties are sold as **ukuran tunggal (one size)**.
There is no body-size calculation to make. If a product has an adjustable strap or band, the product
detail is the source of truth.

---

### H. Dimensions, Not Body Size

Luggage, backpacks, handbags and travel accessories are specified by **dimensions and capacity**, not
body size. The correct answer is to read the product's own dimensions (cm/in), volume (litres) and
weight, and compare them with the trip or carry-on limit. The sizing tool returns
`applicable=false` for this kind; it does not invent an S/M/L.

---

### I. Unsupported Size Categories

Lingerie, underwear, hosiery, socks, swimwear, costumes and all kids categories are **unsupported**
in this demo. They need measurements (band/cup, rise, kids' age/height) or safety rules that this
knowledge base does not model. The honest answer is to say so and point to the product detail, not to
map the shopper to a tops size.

---

## 2. Source & Confidence Labels

Every row in `size_chart.json` carries two fields:

| Field | Values | Meaning |
|---|---|---|
| `source` | `standard` | A cited standard/formula: Mondopoint ISO 9407, 1 in = 2.54 cm, belt = waist + 2 in, head bands |
| | `store_estimate` | Authored Toko Marcell estimate (tops, bottoms, dress) |
| | `demo` | Demo-only data: the six brand cut offsets |
| `confidence` | `high` | Standard conversion with a direct measurement |
| | `medium` | Authored store estimate |
| | `low` | Demo brand offset applied; treat as directional only |

The six brand offsets are **demo data**, not measured from the catalog. They are useful for showing
how a brand layer overrides a baseline, but they must not be presented as official fit data.

---

## 3. Brand-Specific Deviations & Cut Rules

> **Demo data.** These six brand rules are authored examples (`source: demo`, `confidence: low`), not
> a measured fit model. Apply them after the category baseline, and keep the disclaimer.

### 1. Dickies (Workwear Pants & Shorts — e.g. Original 874, Double Knee)
* **Deviation Rule:** **RUNS 1 TO 2 SIZES SMALLER THAN STANDARD DENIM.**
* **Why:** The waistband is constructed from **8.5 oz unwashed, rigid polyester/cotton twill**. It has zero elastane/stretch and sits higher on the natural waist rather than the low hip.
* **Stylist Action:**
  - If a customer normally wears Size 32 in standard jeans, **recommend Size 34** for Dickies 874.
  - If the customer wants an oversized/baggy streetwear drape over sneakers, **recommend Size 34 or 36**.
  - Explain why constructively: *"Because Dickies 874 is authentic non-stretch workwear twill, sizing up 1–2 inches provides ideal everyday sitting comfort."*

---

### 2. Levi's (Denim Pants & Jackets — e.g. 501, 505, Trucker)
* **Deviation Rule:** **TRUE TO AMERICAN DENIM SIZE, CUT VARIES BY MODEL NUMBER.**
* **Cut Breakdown:**
  - **Levi's 501 Original:** Sits at the waist, regular fit through the thigh with a classic straight leg and button-fly. True to size in waist, zero stretch (100% cotton).
  - **Levi's 505 Regular:** Sits at the waist with **extra room in the seat and thigh**, plus a zip fly. Ideal for customers with athletic thighs or muscular legs.
  - **Levi's Denim Trucker Jackets:** Tailored snug in the shoulders. If layering over a thick hoodie or sweater, **recommend sizing up 1 size**.

---

### 3. Carhartt (US Heavyweight Workwear)
* **Deviation Rule:** **SLIGHTLY ROOMIER THAN STANDARD, BUT NOT A FULL SIZE LARGE.**
* **Why:** US workwear cut with moderate shoulder drop and a wider chest. It sits close to true to size — near-balanced and leaning only marginally roomy.
* **Stylist Action:**
  - Recommend **standard size** for a comfortable workwear fit.
  - Only suggest sizing down if the customer specifically asks for a slim/fitted look.
  - For an oversized streetwear look, they can stay with their normal size or size up 1.

---

### 3b. Champion (US Athletic Hoodies & Sweatshirts)
* **Deviation Rule:** **TRUE TO SIZE — NOT "RUNS LARGE" AS COMMONLY ASSUMED.**
* **Why:** Champion **leans slightly small** rather than large. Standard US athletic cut, but not oversized.
* **Stylist Action:**
  - Recommend **standard size** for normal fit.
  - If the customer is between sizes, suggest sizing **up** rather than down.
  - For an oversized look, recommend sizing up 1 full size.

---

### 4. Birkenstock (Contoured Footwear — e.g. Arizona, Boston)
* **Deviation Rule:** **EUROPEAN SIZING (EU 35–47 IN THE STANDARD TABLE) WITH CONTOURED ARCH.**
* **Why:** Molded cork-latex footbed requires toes and heels to sit within the rim without resting on the outer lip.
* **Stylist Action:**
  - Ask for foot length in cm or standard sneaker EU size.
  - If between sizes (e.g. 41.5), advise sizing down to EU 41 because the footbed length is generous.
  - Mention the break-in period: *"The cork footbed molds to your unique foot contour after 7–10 days of wear."*

---

### 5. TOMS (Canvas Slip-On Shoes)
* **Deviation Rule:** **TRUE TO US SIZE; CANVAS STRETCHES SLIGHTLY WITH WEAR.**
* **Why:** 100% canvas upper relaxes and conforms to the foot width after 3 to 5 wears.
* **Stylist Action:**
  - Advise the shoe should feel comfortably snug on first wear.
  - If wearing without socks (recommended): order the normal standard size.
  - If wearing with medium cotton socks or has wide feet: recommend ordering **half a size up**.

---

## 4. Conversational Sizing Examples

These examples show the intended voice: ask for the measurement the category needs, apply the chart,
and stay advisory. They are **not** exact product claims.

### Example 1: Dickies 874 consultation
* **User:** *"Min, TB 175 BB 70 di Dickies 874 ambil apa?"*
* **AI Output:** *"Halo kak! Di celana standar kakak biasanya Size 32, tapi khusus **Dickies 874** mimin sarankan ambil **Size 34** ya kak. Karena bahan twill Dickies 874 tebal (8.5 oz) dan non-stretch di pinggang, naik 1–2 size dari ukuran standar bakal lebih leluasa. Ini panduan umum toko ya, bukan patokan pasti."*

### Example 2: Levi's 501 vs 505 for muscular thighs
* **User:** *"Min, paha saya agak besar, enaknya ambil 501 atau 505?"*
* **AI Output:** *"Halo kak! Kalau kakak punya paha agak berisi, mimin lebih rekomendasikan **Levi's 505 Regular** kak. Model 505 punya ruang ekstra di bagian paha dan pinggul dibanding 501, plus pakai resleting praktis. Tapi kalau kakak suka siluet straight leg klasik dengan button-fly ikonik, Levi's 501 tetap pilihan yang timeless kak."*

### Example 3: Shoes by foot length
* **User:** *"Min, panjang kaki saya 26,5 cm, sepatu pria ukuran berapa?"*
* **AI Output:** *"Untuk panjang kaki 26,5 cm, tabel standar mengarah ke **US 8.5 / UK 7.5 / EU 42** kak. Ini konversi standar, bukan jaminan last setiap brand; kalau kakinya lebar atau sering pakai kaos kaki tebal, boleh pertimbangkan setengah size up."*

### Example 4: Luggage is dimensions, not body size
* **User:** *"Koper 20 inci muat untuk saya yang TB 170?"*
* **AI Output:** *"Untuk koper, patokannya bukan TB/BB kak, tapi dimensi dan kapasitasnya. Cek ukuran cm, volume liter, dan batas carry-on maskapai di halaman produk. Mimin tidak menebak ukuran badan untuk kategori ini."*

### Example 5: Lingerie is honestly unsupported
* **User:** *"Bra ukuran saya apa kalau BB 55?"*
* **AI Output:** *"Maaf kak, untuk lingerie mimin belum punya dasar ukuran yang bisa dipertanggungjawabkan — bra butuh ukuran band dan cup, bukan cuma berat badan. Mimin tidak mau menebak; paling aman cek size chart produknya langsung ya."*
