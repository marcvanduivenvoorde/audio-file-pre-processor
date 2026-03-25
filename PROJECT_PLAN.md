# Audio File Converter — Project Plan

## Goal

Build a Python CLI tool that takes a **source directory** as an argument, scans for audio files ending in **`.wav`**, analyzes channel configuration, and writes **mono** outputs into a `pre-processed/` subdirectory after the user confirms a preview of planned actions.

## Inputs / Outputs

- **Input (argument)**: `SOURCE_DIR` (directory path)
- **Source directory**: the directory passed on the CLI
- **Target directory**: `SOURCE_DIR/pre-processed/` (created if missing) — holds `-M` and `-S` in the **root** of this folder.
- **True-stereo split directory**: `SOURCE_DIR/pre-processed/split-stereo/` (created when needed) — holds **only** `-L` and `-R` (one copy each; not duplicated in `pre-processed/` root).
- **Files to process**: all files matching `*.wav` in the source directory (non-recursive unless explicitly extended later)

## Processing rules (required behavior)

For each `*.wav` file found in the source directory:

1. **Ensure output directories exist**
   - Create `pre-processed/` inside the source directory (if it does not exist).
   - Create `pre-processed/split-stereo/` when needed for true-stereo `-L`/`-R` outputs (if it does not exist).

2. **Read WAV audio**
   - Inspect channel count and compare left vs right channels (when stereo).

3. **True stereo (L != R)**
   - If the track is stereo and **left and right are not identical**:
     - Write a mono file for the **left** channel under `pre-processed/split-stereo/` with postfix `-L` (not in `pre-processed/` root).
     - Write a mono file for the **right** channel under `pre-processed/split-stereo/` with postfix `-R`.
     - Write a **stereo copy** of the source to `pre-processed/` **root** with postfix `-S`.

4. **False stereo (L == R)**
   - If the track is stereo and **left and right are identical**:
     - Write a single mono file derived from the **left** channel to the target directory with postfix `-M`.

5. **Already mono**
   - If the track is mono:
     - Copy/emit it to the target directory with postfix `-M`.

6. **Normalize each target file (before saving)**
   - For **each** output file (`-L`, `-R`, `-M`, `-S`), measure **crest factor** in dB as \(20 \log_{10}(\mathrm{peak}/\mathrm{RMS})\) on the samples that file will contain (mono: that channel; `-S`: full stereo interleaved samples). **Contiguous silence** (per-frame peak magnitude below about **-60 dBFS**) **longer than 0.5 second** is **omitted** from the RMS/peak used for crest and for choosing the normalize band; the **entire** waveform is still normalized and written.
   - **Crest &lt; 11 dB**: normalize to **-21 dBFS RMS** (full scale ±1.0).
   - **11 dB ≤ crest ≤ 14 dB**: normalize to **-21 dBFS RMS**, then if peak would exceed **-9 dBFS**, reduce gain so peak is **≤ -9 dBFS**.
   - **Crest &gt; 14 dB**: **peak** normalize so peak is **-9 dBFS**.
   - Nearly silent material: leave unchanged.

## User confirmation (required UX)

Before writing any output files, show an overview including:

- **File list** discovered
- **Planned action per file**
  - Split to dual mono (`-L`/`-R`) plus stereo copy (`-S`) when true stereo
  - Convert to mono (`-M`)
  - Keep as is (only if we later decide some files should be skipped; for MVP, every `*.wav` results in output)
- **Resulting output filenames** (target paths)
- **Planned normalization** per output (crest-based strategy as above)

Then ask:

- “Proceed with processing? (y/N)”

If the user answers anything other than an explicit yes, exit without creating/modifying outputs.

## Proposed CLI

### Command

`python -m audio_file_converter SOURCE_DIR`

### Options (MVP)

- `--yes`: skip confirmation prompt
- `--dry-run`: print the plan only; do not write files

### Exit codes (MVP)

- `0`: success (including “nothing to do”)
- `1`: validation / runtime error
- `2`: user declined confirmation

## Technical approach (Python)

### Audio I/O (recommended)

- Use a reliable WAV reader/writer that preserves sample rate and bit depth where possible.
- Write mono output as single-channel WAV files.

### True/false stereo detection

Define:

- **False stereo**: left and right channels are identical (or indistinguishable within a configurable tolerance).
- **True stereo**: left and right channels differ.

Implementation notes:

- Prefer an efficient comparison:
  - Quick check: compare metadata + length + a hash/summary of samples
  - Full check: exact sample-by-sample equality (MVP) or tolerance-based comparison (future)

### Output naming

Given input `name.wav`, output files should be:

- True stereo:
  - `pre-processed/split-stereo/name-L.wav`
  - `pre-processed/split-stereo/name-R.wav`
  - `pre-processed/name-S.wav` (stereo copy, root of target only)
- False stereo or mono:
  - `pre-processed/name-M.wav`

`-L` and `-R` exist **only** under `pre-processed/split-stereo/` (single copy). `-M` and `-S` sit in `pre-processed/` root.

## Edge cases / decisions

- **Non-audio `.wav` / corrupt files**: report clearly; default to skipping with an error summary (no partial writes unless user proceeds and we can continue).
- **Overwrite behavior** (choose for MVP):
  - If output exists, either overwrite or skip with warning. (Implement as a flag later if needed.)
- **Traversal**:
  - MVP: process only the top-level of `SOURCE_DIR`.
  - Future: add `--recursive`.
- **Large files**:
  - MVP: load into memory if feasible.
  - Future: streaming/chunked processing.

## Milestones

### Milestone 1 — Skeleton + dry-run planning

- Validate `SOURCE_DIR` exists and is a directory
- Discover `*.wav` files
- Build a processing plan per file (without writing)
- Print the overview table
- Implement `--dry-run` and confirmation prompt

### Milestone 2 — Audio processing + writing outputs

- Implement WAV read
- Implement mono extraction (L / R / M)
- Implement true/false stereo detection
- Write outputs into `pre-processed/`

### Milestone 3 — Hardening

- Better error handling and summarized reporting
- Safer writes (write temp file then rename)
- Deterministic overwrite/skip behavior
- Add tolerance option for false-stereo detection (optional)

### Milestone 4 — Packaging + tests

- Package as an installable module with console entry point
- Unit tests for:
  - plan generation
  - naming rules
  - true vs false stereo classification
  - mono extraction behavior

## Definition of done

- Given a directory of `*.wav` files, the tool prints a clear plan, asks for confirmation, and then produces the correct mono outputs in `pre-processed/` according to the rules above.

