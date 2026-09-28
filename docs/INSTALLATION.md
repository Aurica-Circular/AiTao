# AiTao - Installation Guide (Windows Portable)

## Requirements

- Windows 10 or later (x64 or ARM64)
- PowerShell 5.1+ (included in all Windows 10/11 installations)
- Internet connection for first-time setup (~1-2 GB download: Python, Meilisearch, Ollama)

---

## Installation (First Time)

### Step 1 - Download the archive

Go to the [Releases page](https://github.com/Aurica-Circular/AiTao/releases) and download the archive for your platform:

| Platform | Archive |
|---|---|
| Windows 10/11 x64 (most PCs) | `aitao-vX.Y.Z-windows-x64.zip` |
| Windows ARM64 (Snapdragon / Surface Pro) | `aitao-vX.Y.Z-windows-arm64.zip` |

### Step 2 - Extract the archive

Right-click the zip, click **Extract All**, and choose a folder (e.g. `C:\AiTao`).

### Step 3 - Run the setup script

Open PowerShell in the extracted folder and run:

```powershell
.\setup-portable.ps1
```

> **If PowerShell blocks the script**, run this first:
> ```powershell
> Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
> ```

The setup downloads Python, Meilisearch and Ollama (~1-2 GB), installs Python packages,
and configures the environment. This takes several minutes on first run.

### Step 4 - Configure AiTao

After setup, the file `aitao\config\config.toml` is generated from the template.
Open it in any text editor and set at minimum:

```toml
[indexing]
include_paths = [
  "C:/Users/YourName/Documents/",
  "C:/Users/YourName/Desktop/",
]
```

---

## Optional - OCR for scanned PDFs & images (Premium)

*Skip this unless you have a Premium license and want AiTao to read **scanned PDFs or images**. Text documents (Word, PDF with real text, Markdown…) need nothing here.*

Windows has no built-in OCR engine, so two free tools must be installed manually:
**Tesseract** (recognises the text) and **Poppler** (turns scanned PDF pages into images).

### Step 1 - Install Tesseract

Download the **64-bit** installer from the UB Mannheim build:
<https://github.com/UB-Mannheim/tesseract/wiki>

During installation:

- Under **Additional language data**, tick the languages you need — at least
  **French (fra)**, **English (eng)**, and **Chinese - Traditional (chi_tra)**.
- Leave **Add Tesseract to PATH** ticked (or add `C:\Program Files\Tesseract-OCR`
  to your PATH afterwards).

### Step 2 - Install Poppler (only for scanned PDFs)

Download the latest release from
<https://github.com/oschwartz10612/poppler-windows/releases>, unzip it (e.g. to
`C:\Poppler`), and add its `Library\bin` folder to your PATH. Plain image files
(PNG/JPG) do **not** need Poppler.

### Step 3 - Verify

Open a **new** PowerShell window (so the updated PATH is loaded) and run:

```powershell
tesseract --version
tesseract --list-langs     # must list: fra  eng  chi_tra
pdftoppm -h                # confirms Poppler is on your PATH
```

> On **ARM64** Windows, install the **x64** builds above — they run under the
> built-in x64 emulation.

> **Prefer no Tesseract?** AiTao can use **qwen_vl** instead — a vision AI model
> served by the bundled Ollama, so no extra recognition binary is needed. It reads
> tables well but is much slower (~40 s per page). Scanned PDFs still need Poppler.
> Enable it in `config.toml`:
> ```toml
> [ocr]
> engine_order = ["macos_vision", "tesseract", "qwen_vl"]
> ```

---

## Starting AiTao

```powershell
.\start-aitao.ps1
```

Once started, open your browser at: **http://localhost:8200**

---

## Stopping AiTao

```powershell
.\stop-aitao.ps1
```

---

## Updating AiTao

To update to the latest version without losing your data or configuration:

```powershell
.\update-aitao.ps1
```

Then restart:

```powershell
.\stop-aitao.ps1
.\start-aitao.ps1
```

To also check pre-release versions:

```powershell
.\update-aitao.ps1 -IncludePrerelease
```

> Your `data\` folder and `aitao\config\config.toml` are **never modified** during an update.

---

## Uninstalling AiTao

```powershell
.\uninstall-aitao.ps1
```

The script stops all services and prompts before deleting data.
To remove everything, delete the AiTao folder afterwards.

---

## Common Problems

### "PowerShell blocks the script"

```powershell
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
```

### "Port already in use"

Another application is using port 8200 (AiTao API), 7700 (Meilisearch) or 11434 (Ollama).
Stop the conflicting application or change the ports in `aitao\config\config.toml`.

### "Ollama does not start"

On ARM64, Ollama runs in native ARM64 mode. No WSL or Hyper-V required.

Run manually to see the error:

```powershell
.\ollama\ollama.exe serve
```

### "Setup fails during Python package installation"

Ensure you have an active internet connection.
For x64, LanceDB requires the Visual C++ redistributable (usually pre-installed on Windows 10+).

If the issue persists, run:

```powershell
.\python\python.exe -m pip install -r requirements-portable.txt --no-cache-dir
```

### "Scanned documents are indexed but their text is not searchable"

OCR is missing or not on PATH. Confirm the tools are installed (see
[Optional - OCR](#optional---ocr-for-scanned-pdfs--images-premium)):

```powershell
tesseract --list-langs     # 'tesseract is not recognized' → not on PATH
pdftoppm -h                # needed for scanned PDFs (not for plain images)
```

Open a **new** terminal after editing the PATH, then re-index the file. Remember
OCR is a **Premium** feature — on a Core license, scanned files are indexed
without their text.

---

## Service URLs

| Service | URL |
|---|---|
| AiTao API | http://localhost:8200 |
| Health check | http://localhost:8200/api/health |
| Meilisearch | http://localhost:7700 |
| Ollama | http://localhost:11434 |
