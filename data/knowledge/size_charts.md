# Toko Marcell — Master Sizing & Measurement Guide
**Document Version:** 2.0 (Store Knowledge Base)  
**Applicability:** Toko Marcell Multi-Brand Apparel & Footwear Catalog  
**Usage:** Grounding reference for AI Stylist & Sizing Concierge (UC-2)  
**Structured source of truth:** [`api/knowledge/size_chart.json`](./size_chart.json) — machine-readable baseline tables and data-driven brand offsets. This markdown explains and contextualises those values for the RAG knowledge base.  
**Label:** *panduan umum toko* — this is NOT an official brand size chart.

---

## 1. General Standard Baseline (Asian / Indonesian Standard)

This baseline applies to all standard tops, pants, and footwear in the catalog unless overridden by the **Brand-Specific Deviations** in Section 3.

### A. Tops & Outerwear (Height & Weight Guidelines)

| Size | Height (TB) | Weight (BB) | Chest Circumference (cm) | Standard Fit Feel |
| :---: | :---: | :---: | :---: | :--- |
| **S** | 160 – 168 cm | 50 – 58 kg | 88 – 94 cm | Fitted / Slim |
| **M** | 168 – 174 cm | 58 – 68 kg | 94 – 100 cm | Regular / Ideal daily fit |
| **L** | 174 – 180 cm | 68 – 78 kg | 100 – 108 cm | Regular / Comfortable drape |
| **XL** | 180 – 186 cm | 78 – 88 kg | 108 – 116 cm | Relaxed / Roomy |
| **XXL** | 185 – 192 cm | 88 – 100 kg | 116 – 124 cm | Oversized / Big & Tall |

*Stylist Rule of Thumb:* If customer height and weight point to two different sizes (e.g. TB 175 cm but BB 55 kg), prioritize chest/shoulder fit (Size M for width, Size S for snugness).

---

### B. Standard Bottoms (Waist Sizing: Inches to Centimeters)

| Waist Size (US/UK) | Waist Circumference (cm) | Equivalent Asian Size | Recommended Body Weight |
| :---: | :---: | :---: | :---: |
| **28** | 71 – 73 cm | XS / S | 48 – 54 kg |
| **30** | 76 – 78 cm | S / M | 54 – 62 kg |
| **32** | 81 – 83 cm | M / L | 62 – 72 kg |
| **34** | 86 – 88 cm | L / XL | 72 – 82 kg |
| **36** | 91 – 93 cm | XL / XXL | 82 – 92 kg |
| **38** | 96 – 98 cm | XXL | 92 – 102 kg |

---

### C. Standard Footwear Conversion

| US Men | US Women | EU Standard | Foot Length (cm) | Indonesian Fit Note |
| :---: | :---: | :---: | :---: | :--- |
| **7.0** | 8.5 | **40** | 25.0 cm | Standard Men's Small |
| **8.0** | 9.5 | **41** | 26.0 cm | Indonesian Bestseller Size |
| **9.0** | 10.5 | **42** | 27.0 cm | Indonesian Bestseller Size |
| **10.0** | 11.5 | **43** | 28.0 cm | Standard Large |
| **11.0** | 12.5 | **44 / 45** | 29.0 cm | Extra Large |

---

## 2. Brand-Specific Deviations & Cut Rules

Certain iconic brands in Toko Marcell have proprietary heritage patterns, fabric characteristics, or international cuts that deviate from the standard baseline above. The AI Stylist must apply these specific override rules:

---

### 1. Dickies (Workwear Pants & Shorts — e.g. Original 874, Double Knee)
* **Deviation Rule:** **RUNS 1 TO 2 SIZES SMALLER THAN STANDARD DENIM.**
* **Why:** The waistband is constructed from **8.5 oz unwashed, rigid polyester/cotton twill**. It has zero elastane/stretch and sits higher on the natural waist rather than the low hip.
* **Stylist Action:**
  - If a customer normally wears Size 32 in standard jeans, **recommend Size 34** for Dickies 874.
  - If the customer wants an oversized/baggy streetwear drape over sneakers, **recommend Size 34 or 36**.
  - Always explain *why* constructively: *"Because Dickies 874 is authentic non-stretch workwear twill, sizing up 1–2 inches provides ideal everyday sitting comfort."*

---

### 2. Levi's (Denim Pants & Jackets — e.g. 501, 505, Trucker)
* **Deviation Rule:** **TRUE TO AMERICAN DENIM SIZE, CUT VARIES BY MODEL NUMBER.**
* **Cut Breakdown:**
  - **Levi's 501 Original:** Sits at the waist, regular fit through the thigh with a classic straight leg and button-fly. True to size in waist, zero stretch (100% cotton).
  - **Levi's 505 Regular:** Sits at the waist with **extra room in the seat and thigh**, plus a zip fly. Ideal recommendation for customers with athletic thighs or muscular legs.
  - **Levi's Denim Trucker Jackets:** Tailored snug in the shoulders. If the customer plans to wear it over a t-shirt, standard size fits great. If layering over a thick hoodie or sweater, **recommend sizing up 1 size**.

---

### 3. Carhartt (US Heavyweight Workwear)
* **Deviation Rule:** **SLIGHTLY ROOMIER THAN STANDARD, BUT NOT A FULL SIZE LARGE.**
* **Why:** US workwear cut with moderate shoulder drop and wider chest. Review data (n=11,491) shows Carhartt is close to TTS: 21.1% say runs small, 54.9% TTS, 24.0% runs large — near-balanced, leaning only marginally roomy.
* **Stylist Action:**
  - Recommend **standard size** for a comfortable workwear fit.
  - Only suggest sizing down if the customer specifically asks for a slim/fitted look.
  - For an oversized streetwear look, they can stay with their normal size or size up 1.

---

### 3b. Champion (US Athletic Hoodies & Sweatshirts)
* **Deviation Rule:** **TRUE TO SIZE — NOT "RUNS LARGE" AS COMMONLY ASSUMED.**
* **Why:** Review data (n=6,086) shows Champion actually **leans slightly small** — 25.4% say runs small vs only 19.5% say runs large. Standard US athletic cut, but not oversized.
* **Stylist Action:**
  - Recommend **standard size** for normal fit.
  - If the customer is between sizes, suggest sizing **up** rather than down.
  - For an oversized look, recommend sizing up 1 full size.

---

### 4. Birkenstock (Contoured Footwear — e.g. Arizona, Boston)
* **Deviation Rule:** **EUROPEAN SIZING (EU 39–45) WITH CONTOURED ARCH.**
* **Why:** Molded cork-latex footbed requires toes and heels to sit within the rim without resting on the outer lip.
* **Stylist Action:**
  - Ask for their foot length in cm or standard sneaker EU size.
  - If between sizes (e.g. 41.5), advise sizing down to EU 41 because footbed length is generous.
  - Inform customer about the break-in period: *"The cork footbed molds to your unique foot contour after 7–10 days of wear."*

---

### 5. TOMS (Canvas Slip-On Shoes)
* **Deviation Rule:** **TRUE TO US SIZE; CANVAS STRETCHES SLIGHTLY WITH WEAR.**
* **Why:** 100% canvas upper relaxes and conforms to the foot width after 3 to 5 wears.
* **Stylist Action:**
  - Advise customer that the shoe should feel comfortably snug on first wear.
  - If wearing without socks (recommended): Order normal standard size.
  - If wearing with medium cotton socks or has wide feet: Recommend ordering **half a size up**.

---

## 3. Conversational Sizing Prompt Snippets (For AI Training)

### Example 1: Dickies 874 Consultation
* **User:** *"Min, TB 175 BB 70 di Dickies 874 ambil apa?"*
* **AI Output:** *"Halo kak! Di celana standar kakak biasanya Size 32, tapi khusus **Dickies 874** mimin sangat sarankan ambil **Size 34** ya kak! Karena bahan twill Dickies 874 ini tebal (8.5 oz) dan non-stretch di pinggang, naik 1–2 size dari ukuran standar bakal jauh lebih leluasa dan nyaman dipakai beraktivitas kak~"*

### Example 2: Levi's 501 vs 505 for Muscular Thighs
* **User:** *"Min, paha saya agak besar, enaknya ambil 501 atau 505?"*
* **AI Output:** *"Halo kak! Kalau kakak punya paha agak berisi, mimin lebih rekomendasikan **Levi's 505 Regular** kak. Model 505 punya ruang ekstra di bagian paha dan pinggul dibanding 501, plus pakai resleting praktis. Tapi kalau kakak suka siluet straight leg klasik dengan button-fly ikonik, Levi's 501 tetap pilihan yang timeless kak!"*
