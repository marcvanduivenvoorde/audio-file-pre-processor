# Audio pre-processor

## 1. Idea and reason

This project provides a small Python tool (`audio-pre-processor.py`) for preparing **WAV** files before further work (mixing, mastering, batch pipelines, etc.).

Given a **source folder**, the script scans **top-level** `*.wav` files, classifies each by channel layout (mono, false stereo, true stereo), and writes normalized outputs under **`pre-processed/`** (and true-stereo left/right mono splits under **`pre-processed/split-stereo/`**). True stereo also gets a stereo copy with a `-S` suffix in `pre-processed/`. Filenames are **slugged** (lowercase, alphanumeric and dashes only).

**Why it exists:** to automate a consistent first pass: split true stereo to dual mono where needed, collapse duplicate L/R to a single mono file, copy mono sources with a clear `-M` suffix, apply **crest-aware loudness normalization** (RMS and peak targets, with long silent passages ignored for measurement), and require an explicit **preview and confirmation** before any files are written—so you see the plan before touching disk.

## 2. Python environment (`.venv`)

Use a virtual environment in the **project root** so dependencies stay isolated.

**Create the venv** (from the repository root):

```powershell
python -m venv .venv
```

**Activate it** (PowerShell on Windows):

```powershell
.\.venv\Scripts\Activate.ps1
```

**Install dependencies:**

```powershell
python -m pip install -r requirements.txt
```

You need **Python 3** with `pip`. The script depends on **NumPy** and **SoundFile** (and a working system backend for audio I/O, as provided by SoundFile/libsndfile on most setups).

## 3. Command-line usage

Run the script with Python, passing the **source directory** that contains the WAV files to process:

```powershell
python .\audio-pre-processor.py "C:\path\to\your\source_folder"
```

### Arguments and options

| Item | Description |
|------|-------------|
| **`source_dir`** (positional, required) | Directory whose **immediate** contents are scanned for `.wav` files. It is not recursive into subfolders. |
| **`--dry-run`** | Print the plan (inputs, output paths, normalization strategy per file) and **exit without writing** any files or creating folders. Does not show the confirmation prompt. |
| **`--yes`** | **Skip** the interactive “Proceed with processing? (y/N)” prompt and run immediately after printing the plan. |
| **`--overwrite`** | Allow **replacing** output files if they already exist. Without this flag, existing outputs cause a write error for that file. |

### Exit codes

- **0** — Finished successfully (including dry-run, or nothing to do).
- **1** — Error (e.g. invalid path, read/write failures).
- **2** — You declined the confirmation prompt (not used with `--yes`).

### Examples

```powershell
# Preview only
python .\audio-pre-processor.py "D:\audio\inbox" --dry-run

# Run without confirmation (e.g. in automation)
python .\audio-pre-processor.py "D:\audio\inbox" --yes

# Overwrite previous outputs in pre-processed / split-stereo
python .\audio-pre-processor.py "D:\audio\inbox" --yes --overwrite
```
