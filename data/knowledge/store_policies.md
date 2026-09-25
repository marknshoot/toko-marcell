# Toko Marcell — Store Operations & Customer Policies
**Document Version:** 1.0 (Store Knowledge Base)  
**Applicability:** Toko Marcell E-Commerce Store & Customer Service  
**Usage:** Grounding reference for AI Store Admin & Policy Tool (UC-5, UC-7)

---

## 1. Store Identity & Authenticity Guarantee

* **What is Toko Marcell?**  
  Toko Marcell is a curated multi-brand fashion apparel and footwear storefront operating out of Jakarta, Indonesia. We offer iconic global workwear, denim, and casual lifestyle brands (Levi's, Dickies, TOMS, Birkenstock, Champion, and Calvin Klein).
* **Authenticity Guarantee:**  
  All products listed in our catalog are **100% original, brand-new goods** sourced from authorized distribution channels with original brand tags attached. We do not sell replicas, counterfeits, or factory rejects.

---

## 2. Payment & QRIS Demo Simulation Rules

* **QRIS Payment Method:**  
  Toko Marcell supports **QRIS (Quick Response Code Indonesian Standard)** for seamless digital payments across all Indonesian mobile banking apps (BCA, Mandiri, BRI, BNI) and e-wallets (GoPay, OVO, Dana, ShopeePay).
* **Demo / Simulation Disclosure:**  
  * **Zero Real Charges:** Toko Marcell is an engineering portfolio demo. The QRIS QR code displayed at checkout is a **realistic simulation demo**. Scanning or confirming payment does **NOT** deduct real funds from any bank account.
  * **Server-Side Token Authority:** Upon confirming payment, our FastAPI backend generates a cryptographically secure **order token** (e.g. `tk_7f9a2b1`) that marks the order as `PAID` in our PostgreSQL database.
  * **Order Success:** Customers can track their order or clear their cart using their unique order token at any time.

---

## 3. Shipping & Delivery Guidelines

* **Fulfillment Origin:**  
  All orders are packed and dispatched from our primary fulfillment hub in **Jakarta Selatan (South Jakarta), Indonesia**.
* **Simulated Courier Partners:**  
  We integrate delivery tracking simulations with **JNE Express**, **SiCepat**, and same-day instant couriers (**GoSend / GrabExpress**).
* **Estimated Delivery Timelines:**
  - **Jabodetabek Area:** 1 to 2 business days.
  - **Java, Bali, & Madura:** 2 to 3 business days.
  - **Sumatra, Kalimantan, & Sulawesi:** 3 to 5 business days.
  - **Eastern Indonesia (Maluku & Papua):** 4 to 7 business days.
* **Shipping Rates:**
  - Standard Flat Rate: **Rp 15.000** for Jabodetabek, **Rp 25.000** outside Java.
  - **Free Shipping:** Automatically applied to all orders with a total value $\ge$ **Rp 300.000**.

---

## 4. 7-Day Sizing Guarantee & Return Policy

Because we curate multi-brand apparel where sizing can vary between manufacturers, Toko Marcell provides a **7-Day Sizing & Return Guarantee**:

* **Return Window:**  
  Customers may request a size exchange or store credit return within **7 calendar days** of receiving the package.
* **Eligible Return Conditions:**
  1. The item must be in **original, unworn, and unwashed condition**.
  2. All original manufacturer tags, labels, and packaging must remain attached and undamaged.
  3. No stains, perfumes, body odors, or signs of wear.
* **Hygiene Exceptions:**  
  Underwear, baselayers, and socks cannot be returned or exchanged due to health and sanitary regulations.
* **Return Procedure:**
  1. Shopper provides their order token (e.g. `tk_...`) to the Admin Toko in chat.
  2. Admin confirms item eligibility and generates a return label.
  3. Replacement size is dispatched as soon as the returned garment arrives at the Jakarta hub.

---

## 5. Frequently Asked Questions (FAQ)

### Q: "Min, bisa COD (Bayar di Tempat) gak?"
**A:** Saat ini Toko Marcell belum mendukung COD ya kak. Kami fokus pada pembayaran instan yang praktis dan aman menggunakan **QRIS** (simulasi demo instan).

### Q: "Min, apakah ada toko fisik / offline store di mall?"
**A:** Toko Marcell saat ini beroperasi penuh secara online dari Jakarta ya kak! Walaupun online, pengalaman belanja di sini didukung oleh AI Stylist & Admin Toko yang siap melayani konsultasi ukuran, bahan, dan padu padan gaya seperti belanja langsung di mall.

### Q: "Kalau barang datang cacat atau salah kirim gimana min?"
**A:** Tenang kak, kami memberikan **garansi ganti baru 100% gratis** jika barang yang diterima cacat produksi atau salah kirim! Cukup infokan nomor token pesanan kakak ke admin dalam kurun waktu 7 hari.

### Q: "Berapa jam batas operasional chat admin?"
**A:** Admin Toko Marcell aktif melayani konsultasi gaya dan operasional toko **setiap hari dari pukul 08:00 hingga 22:00 WIB**.
