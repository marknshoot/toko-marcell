# Panduan Menjalankan Eksperimen VLM CLIP di Kaggle GPU via Kaggle CLI

Panduan ini memungkinkan Anda menjalankan 4 eksperimen fine-tuning VLM (LoRA, Decoupled LR, SigLIP, Baseline) menggunakan akselerator **Kaggle GPU (NVIDIA Tesla T4 16 GB atau P100 16 GB)** secara otomatis dari terminal.

---

## 1. Persiapan Akun & Autentikasi Kaggle CLI

Kaggle CLI telah terinstal di environment conda `deep-learning`:
```bash
/home/marcell/miniconda3/envs/deep-learning/bin/kaggle --version
# Output: Kaggle CLI 2.2.4
```

### Opsi A: Autentikasi Web Browser (Paling Mudah)
Jalankan perintah berikut di terminal Anda:
```bash
/home/marcell/miniconda3/envs/deep-learning/bin/kaggle auth login
```
Browser akan terbuka untuk konfirmasi satu kali, dan kredensial otomatis tersimpan di `~/.kaggle/access_token`.

### Opsi B: Menggunakan File API Token (`kaggle.json`)
1. Buka [https://www.kaggle.com/settings/api](https://www.kaggle.com/settings/api).
2. Klik tombol **"Create New Token"** (akan mengunduh file `kaggle.json`).
3. Tempatkan file tersebut di direktori home Anda:
   ```bash
   mkdir -p ~/.kaggle
   mv ~/Downloads/kaggle.json ~/.kaggle/
   chmod 600 ~/.kaggle/kaggle.json
   ```

---

## 2. Struktur Dataset & Eksperimen

File-file pendukung:
- `manual/pipelines/kaggle/toko_marcell_vlm_experiments.ipynb`: Notebook Jupyter lengkap yang menguji ke-4 eksperimen secara terisolasi dengan batch size 64/128.
- `manual/pipelines/kaggle/kernel-metadata.json`: Konfigurasi Kaggle CLI untuk menjalankan notebook dengan GPU aktif (`"enable_gpu": "true"`).
- `manual/pipelines/experiments_clip.py`: Script Python modular yang dapat dijalankan langsung di mesin lokal atau cloud.

---

## 3. Menjalankan di Kaggle via CLI (Step-by-Step)

### Langkah 1: Siapkan Folder Dataset Ringan (~165 MB)
Katalog Toko Marcell memiliki 5.378 gambar berukuran total ~163 MB. Kita bungkus ke dalam satu arsip dataset:
```bash
mkdir -p manual/pipelines/kaggle/dataset_upload
cp manual/data/processed/clip_train.json manual/pipelines/kaggle/dataset_upload/
cp manual/data/processed/clip_val.json manual/pipelines/kaggle/dataset_upload/
cp manual/data/processed/clip_test.json manual/pipelines/kaggle/dataset_upload/
tar -czf manual/pipelines/kaggle/dataset_upload/images.tar.gz -C manual/data/processed images
```

Buat metadata dataset `manual/pipelines/kaggle/dataset_upload/dataset-metadata.json`:
```json
{
  "title": "Toko Marcell Fashion VLM Dataset",
  "id": "<USERNAME_KAGGLE_ANDA>/toko-marcell-vlm",
  "licenses": [{"name": "CC0-1.0"}]
}
```

Upload dataset ke Kaggle:
```bash
/home/marcell/miniconda3/envs/deep-learning/bin/kaggle datasets create -p manual/pipelines/kaggle/dataset_upload
```

### Langkah 2: Update `kernel-metadata.json`
Sesuaikan `<USERNAME_KAGGLE_ANDA>` pada file `manual/pipelines/kaggle/kernel-metadata.json`:
```json
{
  "id": "<USERNAME_KAGGLE_ANDA>/toko-marcell-vlm-experiments",
  "title": "Toko Marcell VLM Contrastive Fine-Tuning Experiments",
  "code_file": "toko_marcell_vlm_experiments.ipynb",
  "language": "python",
  "kernel_type": "notebook",
  "is_private": "true",
  "enable_gpu": "true",
  "enable_internet": "true",
  "dataset_sources": ["<USERNAME_KAGGLE_ANDA>/toko-marcell-vlm"]
}
```

### Langkah 3: Push dan Jalankan Kernel di Kaggle GPU
```bash
/home/marcell/miniconda3/envs/deep-learning/bin/kaggle kernels push -p manual/pipelines/kaggle/
```

Pantau progres eksekusi di terminal:
```bash
/home/marcell/miniconda3/envs/deep-learning/bin/kaggle kernels status <USERNAME_KAGGLE_ANDA>/toko-marcell-vlm-experiments
```

### Langkah 4: Unduh Hasil Bobot Model & Scoreboard
Setelah status menunjukkan `complete`, unduh seluruh output (bobot checkpoint `safetensors`, tabel metrik `experiment_summary.json`):
```bash
mkdir -p manual/models/kaggle_outputs
/home/marcell/miniconda3/envs/deep-learning/bin/kaggle kernels output <USERNAME_KAGGLE_ANDA>/toko-marcell-vlm-experiments -p manual/models/kaggle_outputs/
```

---

## 4. Menjalankan Langsung di Web UI Kaggle (Alternatif Tanpa CLI)
1. Buka [https://www.kaggle.com/code](https://www.kaggle.com/code) dan klik **"New Notebook"**.
2. Di panel kanan (Settings):
   - **Accelerator:** Pilih **GPU T4 x2** atau **GPU P100**.
   - **Internet:** Pilih **Internet On**.
3. Klik **File → Import Notebook** dan pilih file `manual/pipelines/kaggle/toko_marcell_vlm_experiments.ipynb`.
4. Tambahkan dataset `images.tar.gz` dan file JSON via menu **+ Add Input**.
5. Klik **"Run All"**. Seluruh 4 eksperimen akan selesai dalam ~15 menit.
