# Audio pre-processor

## 1. Idea and reason 

This project provides a small Python tool (`audio-pre-processor.py`) for preparing **WAV** files before further work (mixing, mastering, batch pipelines, etc.).

Given a **source folder**, the script scans **top-level** `*.wav` files, classifies each by channel layout (mono, false stereo, true stereo), and writes outputs under `**pre-processed/`** (and true-stereo left/right mono splits under `**pre-processed/split-stereo/**`). True stereo also gets a stereo copy with a `-S` suffix in `pre-processed/`. Filenames are **slugged** (lowercase, alphanumeric and dashes only). By default, **levels are unchanged** (samples written as float32). Pass **`--normalize`** to run **crest-aware loudness normalization** (see below).

**Why it exists:** to automate a consistent first pass: split true stereo to dual mono where needed, collapse duplicate L/R to a single mono file, copy mono sources with a clear `-M` suffix, optionally apply crest-aware normalization, and require an explicit **preview and confirmation** before any files are written—so you see the plan before touching disk.

### Normalization method and targets (`--normalize`)

Normalization uses **dBFS** relative to full scale (floating-point samples in roughly **±1.0**).

**Crest factor** (per output buffer—each `-L`, `-R`, `-M`, or `-S` file) is `**20 * log10(peak / RMS)`** in dB, using linear **peak** (max absolute sample) and **RMS** (root mean square of samples) over the frames that count (see below).

**RMS** and **peak** for that formula are computed only on samples that remain after **ignoring long silence**:

- A time frame is **silent** if its peak magnitude is below **−60 dBFS** (mono: absolute sample value; stereo: max of |L| and |R| per frame).
- Any **single contiguous** run of silent frames **strictly longer than 0.5 seconds** is **excluded** from the RMS/peak used for crest and for choosing the gain rule. Shorter gaps stay in the stats. The **gain is still applied to the whole waveform**; only the *measurement* ignores those long silent stretches.

**Bands and target levels** (chosen from crest on the retained audio):


| Crest factor              | What the script does                                                                                                                      |
| ------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------- |
| **< 11 dB**               | Gain so **RMS = −21 dBFS**.                                                                                                               |
| **11 dB ≤ crest ≤ 14 dB** | Gain so **RMS = −21 dBFS**, then if the **full** signal (after that gain) would peak above **−9 dBFS**, scale down so peak **≤ −9 dBFS**. |
| **> 14 dB**               | **Peak** normalize so peak **= −9 dBFS** (using peak from the non–long-silence stats for the gain divisor).                               |


Nearly silent material (no usable stats) is left unchanged. Constants in code: `RMS_TARGET_DBFS = -21`, `PEAK_CAP_DBFS = -9`, crest band edges **11** and **14** dB, **0.5 s** long-silence threshold.

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

Run the script with Python, passing the source directory that contains WAV files:

```powershell
python .\audio-pre-processor.py "C:\path\to\your\source_folder"
```

### CLI options

| Item                                    | Description |
| --------------------------------------- | ----------- |
| `source_dir` (positional, required)     | Directory whose immediate contents are scanned for `.wav` files (not recursive). |
| `--dry-run`                             | Print the plan and exit without writing files. |
| `--normalize`                           | Enable crest-based normalization; without this flag outputs keep source levels. |
| `--yes`                                 | Skip the interactive confirmation prompt. |
| `--overwrite`                           | Overwrite existing outputs; otherwise write errors are reported for existing targets. |

### Exit codes

- **0** — success (including dry-run, or nothing to do)
- **1** — error (invalid path, read/write failures, etc.)
- **2** — confirmation declined (only when prompt is shown)

### CLI examples

```powershell
# Preview only
python .\audio-pre-processor.py "D:\audio\inbox" --dry-run

# Run immediately (no confirmation)
python .\audio-pre-processor.py "D:\audio\inbox" --yes

# Run with crest-based normalization
python .\audio-pre-processor.py "D:\audio\inbox" --yes --normalize

# Overwrite previous outputs
python .\audio-pre-processor.py "D:\audio\inbox" --yes --overwrite
```

## 4. GUI usage

Run the desktop GUI (Tkinter) from the repository root:

```powershell
python .\audio-pre-processor-gui.py
```

The GUI uses the same processing engine as the CLI (`audio-pre-processor.py`) so output behavior stays aligned.

### GUI workflow

1. Choose a source folder.
2. Set options:
   - `Normalize`
   - `Overwrite`
   - `Dry run`
3. If `Normalize` is enabled, set integer targets:
   - RMS target (dBFS), default `-21`
   - Peak cap (dBFS), default `-9`
4. Click **Preview Overview**.
5. Watch live analysis progress:
   - spinner + `analysing audio`
   - table rows appear file-by-file
6. In the overview table, override strategy per output by clicking one of:
   - `peak`
   - `rms + peak`
   - `rms`
7. Click **Process**.

### GUI overview columns

- `Source file`
- `Output`
- `Normalize plan`
- strategy selectors: `peak`, `rms + peak`, `rms`
- `Reason`

