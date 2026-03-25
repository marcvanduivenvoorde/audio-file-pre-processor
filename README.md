# Audio pre-processor

## 1. Idea and reason

This project provides a small Python tool (`audio-pre-processor.py`) for preparing **WAV** files before further work (mixing, mastering, batch pipelines, etc.).

Given a **source folder**, the script scans **top-level** `*.wav` files, classifies each by channel layout (mono, false stereo, true stereo), and writes normalized outputs under **`pre-processed/`** (and true-stereo left/right mono splits under **`pre-processed/split-stereo/`**). True stereo also gets a stereo copy with a `-S` suffix in `pre-processed/`. Filenames are **slugged** (lowercase, alphanumeric and dashes only).

**Why it exists:** to automate a consistent first pass: split true stereo to dual mono where needed, collapse duplicate L/R to a single mono file, copy mono sources with a clear `-M` suffix, apply **crest-aware loudness normalization** (see below), and require an explicit **preview and confirmation** before any files are written—so you see the plan before touching disk.

### Normalization method and targets

Normalization uses **dBFS** relative to full scale (floating-point samples in roughly **±1.0**).

**Crest factor** (per output buffer—each `-L`, `-R`, `-M`, or `-S` file) is **`20 * log10(peak / RMS)`** in dB, using linear **peak** (max absolute sample) and **RMS** (root mean square of samples) over the frames that count (see below).

**RMS** and **peak** for that formula are computed only on samples that remain after **ignoring long silence**:

- A time frame is **silent** if its peak magnitude is below **−60 dBFS** (mono: absolute sample value; stereo: max of |L| and |R| per frame).
- Any **single contiguous** run of silent frames **strictly longer than 0.5 seconds** is **excluded** from the RMS/peak used for crest and for choosing the gain rule. Shorter gaps stay in the stats. The **gain is still applied to the whole waveform**; only the *measurement* ignores those long silent stretches.

**Bands and target levels** (chosen from crest on the retained audio):

| Crest factor | What the script does |
|--------------|----------------------|
| **&lt; 11 dB** | Gain so **RMS = −21 dBFS**. |
| **11 dB ≤ crest ≤ 14 dB** | Gain so **RMS = −21 dBFS**, then if the **full** signal (after that gain) would peak above **−6 dBFS**, scale down so peak **≤ −6 dBFS**. |
| **&gt; 14 dB** | **Peak** normalize so peak **= −6 dBFS** (using peak from the non–long-silence stats for the gain divisor). |

Nearly silent material (no usable stats) is left unchanged. Constants in code: `RMS_TARGET_DBFS = -21`, `PEAK_CAP_DBFS = -6`, crest band edges **11** and **14** dB, **0.5 s** long-silence threshold.

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
