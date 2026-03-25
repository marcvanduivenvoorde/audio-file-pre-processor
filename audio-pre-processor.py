from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence, Set, Tuple

import numpy as np
import soundfile as sf


TARGET_SUBDIR = "pre-processed"
# True-stereo -L / -R mono files only here (not in pre-processed/ root).
SPLIT_STEREO_SUBDIR = "split-stereo"

# Loudness / crest (dBFS full scale, samples in [-1, 1])
RMS_TARGET_DBFS = -21.0
PEAK_CAP_DBFS = -6.0
# Crest factor dB = 20*log10(peak/RMS): <11 → RMS -21; 11–14 → RMS -21 + peak cap; >14 → peak -6
CREST_BAND_LOW_LT_DB = 11.0
CREST_BAND_MID_MAX_DB = 14.0
_EPS = 1e-10

# Crest stats: drop contiguous silence longer than this (not applied to the written waveform).
LONG_SILENCE_SECONDS = 0.5
# Frames quieter than this (peak magnitude per frame) count as silence.
SILENCE_THRESHOLD_LINEAR = 10.0 ** (-60.0 / 20.0)


@dataclass(frozen=True)
class PlannedOutput:
    path: Path
    channel: str  # "L" | "R" | "M" | "S" (stereo copy)
    normalize_label: str  # human-readable; matches execution


@dataclass(frozen=True)
class PlannedAction:
    source: Path
    kind: str  # "split" | "mono" | "skip"
    outputs: Tuple[PlannedOutput, ...]
    reason: str


def _is_wav(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() == ".wav"


def _iter_source_wavs(source_dir: Path) -> List[Path]:
    return sorted([p for p in source_dir.iterdir() if _is_wav(p)])


def _target_dir(source_dir: Path) -> Path:
    return source_dir / TARGET_SUBDIR


def _split_stereo_dir(target_dir: Path) -> Path:
    return target_dir / SPLIT_STEREO_SUBDIR


_NON_ALNUM_RE = re.compile(r"[^0-9A-Za-z]+")


def _slug_stem(stem: str) -> str:
    """
    Output filename stem rules:
    - spaces become dashes
    - no non-alphanumerical characters (only [0-9A-Za-z-])
    - collapse duplicate dashes, trim leading/trailing dashes
    """
    stem = stem.replace(" ", "-")
    stem = _NON_ALNUM_RE.sub("-", stem)
    stem = re.sub(r"-{2,}", "-", stem)
    stem = stem.strip("-")
    stem = stem.lower()
    return stem or "audio"


def _unique_output_name(base_stem: str, postfix: str, used_names: Set[str]) -> str:
    candidate = f"{base_stem}{postfix}.wav"
    if candidate not in used_names:
        used_names.add(candidate)
        return candidate

    i = 2
    while True:
        candidate = f"{base_stem}-{i}{postfix}.wav"
        if candidate not in used_names:
            used_names.add(candidate)
            return candidate
        i += 1


def _rms_linear(x: np.ndarray) -> float:
    x64 = np.asarray(x, dtype=np.float64)
    return float(np.sqrt(np.mean(np.square(x64))))


def _peak_linear(x: np.ndarray) -> float:
    x64 = np.asarray(x, dtype=np.float64)
    return float(np.max(np.abs(x64)))


def _per_frame_peak_magnitude(x: np.ndarray) -> np.ndarray:
    """One magnitude per time frame: mono |x|; stereo max(|L|,|R|)."""
    x64 = np.asarray(x, dtype=np.float64)
    if x64.ndim == 1:
        return np.abs(x64)
    return np.max(np.abs(x64), axis=1)


def _include_mask_skip_long_silence(x: np.ndarray, sample_rate: int) -> np.ndarray:
    """
    True = include frame in crest/RMS/peak stats. Contiguous silence runs strictly longer
    than LONG_SILENCE_SECONDS seconds are excluded; shorter silent runs stay included.
    """
    x64 = np.asarray(x, dtype=np.float64)
    n = int(x64.shape[0])
    if n == 0:
        return np.zeros(0, dtype=bool)

    silent = _per_frame_peak_magnitude(x64) < SILENCE_THRESHOLD_LINEAR
    min_run = int(sample_rate * LONG_SILENCE_SECONDS) + 1  # strictly > LONG_SILENCE_SECONDS

    include = np.ones(n, dtype=bool)
    i = 0
    while i < n:
        if not silent[i]:
            i += 1
            continue
        j = i + 1
        while j < n and silent[j]:
            j += 1
        run_len = j - i
        if run_len >= min_run:
            include[i:j] = False
        i = j
    return include


def _rms_peak_for_crest(x: np.ndarray, sample_rate: int) -> Tuple[float, float]:
    """RMS and peak on samples kept after dropping long silent sections."""
    x64 = np.asarray(x, dtype=np.float64)
    if x64.ndim == 1:
        mask = _include_mask_skip_long_silence(x64, sample_rate)
        sel = x64[mask]
    else:
        mask = _include_mask_skip_long_silence(x64, sample_rate)
        sel = x64[mask, :].ravel()
    if sel.size == 0:
        return 0.0, 0.0
    return float(np.sqrt(np.mean(np.square(sel)))), float(np.max(np.abs(sel)))


def _crest_factor_db(x: np.ndarray, sample_rate: int) -> float:
    """Crest factor in dB from peak/RMS on non–long-silence samples."""
    rms, peak = _rms_peak_for_crest(x, sample_rate)
    if rms < _EPS:
        return float("nan")
    return float(20.0 * np.log10(peak / rms))


def _normalization_strategy_label(x: np.ndarray, sample_rate: int) -> str:
    crest = _crest_factor_db(x, sample_rate)
    rms, peak = _rms_peak_for_crest(x, sample_rate)
    if rms < _EPS and peak < _EPS:
        return "silent (unchanged)"
    if not np.isfinite(crest):
        return "silent (unchanged)"
    c = f"{crest:.1f} dB crest"
    if crest < CREST_BAND_LOW_LT_DB:
        return f"RMS {RMS_TARGET_DBFS:.0f} dBFS ({c} < {CREST_BAND_LOW_LT_DB:g} dB)"
    if crest <= CREST_BAND_MID_MAX_DB:
        return (
            f"RMS {RMS_TARGET_DBFS:.0f} dBFS, peak ≤ {PEAK_CAP_DBFS:.0f} dBFS "
            f"({CREST_BAND_LOW_LT_DB:g}–{CREST_BAND_MID_MAX_DB:g} dB, {c})"
        )
    return f"Peak {PEAK_CAP_DBFS:.0f} dBFS ({c} > {CREST_BAND_MID_MAX_DB:g} dB)"


def _normalize_output_audio(x: np.ndarray, sample_rate: int) -> np.ndarray:
    """Apply crest-based gain; returns float32 array same shape as input."""
    x64 = np.asarray(x, dtype=np.float64)
    rms, peak = _rms_peak_for_crest(x64, sample_rate)
    if rms < _EPS and peak < _EPS:
        return np.asarray(x, dtype=np.float32)

    crest = _crest_factor_db(x64, sample_rate)
    if not np.isfinite(crest):
        return np.asarray(x, dtype=np.float32)

    rms_tgt = 10.0 ** (RMS_TARGET_DBFS / 20.0)
    peak_tgt = 10.0 ** (PEAK_CAP_DBFS / 20.0)

    if crest < CREST_BAND_LOW_LT_DB:
        gain = rms_tgt / rms
    elif crest <= CREST_BAND_MID_MAX_DB:
        gain_rms = rms_tgt / rms
        peak_after = _peak_linear(x64 * gain_rms)
        gain = gain_rms * (peak_tgt / peak_after) if peak_after > peak_tgt else gain_rms
    else:
        gain = peak_tgt / peak if peak >= _EPS else 1.0

    y = x64 * gain
    return np.clip(y, -1.0, 1.0).astype(np.float32)


def _planned_output_path(
    *,
    target_dir: Path,
    source_file: Path,
    postfix: str,
    used_names: Set[str],
) -> Path:
    base = _slug_stem(source_file.stem)
    name = _unique_output_name(base, postfix, used_names)
    return target_dir / name


def _plan_for_file(
    source_file: Path,
    target_dir: Path,
    split_stereo_dir: Path,
    used_names: Set[str],
) -> PlannedAction:
    try:
        info = sf.info(str(source_file))
    except Exception as e:  # noqa: BLE001 - show user the actual failure
        return PlannedAction(
            source=source_file,
            kind="skip",
            outputs=tuple(),
            reason=f"Unreadable WAV: {e}",
        )

    if info.channels == 1:
        try:
            data, sr = sf.read(str(source_file), always_2d=True)
        except Exception as e:  # noqa: BLE001
            return PlannedAction(
                source=source_file,
                kind="skip",
                outputs=tuple(),
                reason=f"Failed to read samples: {e}",
            )
        sr_i = int(round(sr))
        mono = data[:, 0]
        out = _planned_output_path(
            target_dir=target_dir, source_file=source_file, postfix="-M", used_names=used_names
        )
        return PlannedAction(
            source=source_file,
            kind="mono",
            outputs=(
                PlannedOutput(out, "M", _normalization_strategy_label(mono, sr_i)),
            ),
            reason="Mono input -> copy/emit as -M",
        )

    if info.channels == 2:
        try:
            data, sr = sf.read(str(source_file), always_2d=True)
        except Exception as e:  # noqa: BLE001
            return PlannedAction(
                source=source_file,
                kind="skip",
                outputs=tuple(),
                reason=f"Failed to read samples: {e}",
            )
        sr_i = int(round(sr))

        left = data[:, 0]
        right = data[:, 1]
        if np.array_equal(left, right):
            out = _planned_output_path(
                target_dir=target_dir, source_file=source_file, postfix="-M", used_names=used_names
            )
            return PlannedAction(
                source=source_file,
                kind="mono",
                outputs=(
                    PlannedOutput(out, "M", _normalization_strategy_label(left, sr_i)),
                ),
                reason="False stereo (L == R) -> emit left as -M",
            )

        out_l = _planned_output_path(
            target_dir=split_stereo_dir,
            source_file=source_file,
            postfix="-L",
            used_names=used_names,
        )
        out_r = _planned_output_path(
            target_dir=split_stereo_dir,
            source_file=source_file,
            postfix="-R",
            used_names=used_names,
        )
        out_s = _planned_output_path(
            target_dir=target_dir, source_file=source_file, postfix="-S", used_names=used_names
        )
        return PlannedAction(
            source=source_file,
            kind="split",
            outputs=(
                PlannedOutput(out_l, "L", _normalization_strategy_label(left, sr_i)),
                PlannedOutput(out_r, "R", _normalization_strategy_label(right, sr_i)),
                PlannedOutput(out_s, "S", _normalization_strategy_label(data, sr_i)),
            ),
            reason="True stereo (L != R) -> -L/-R only in split-stereo/; -S in pre-processed/ root",
        )

    return PlannedAction(
        source=source_file,
        kind="skip",
        outputs=tuple(),
        reason=f"Unsupported channel count: {info.channels}",
    )


def build_plan(source_dir: Path) -> List[PlannedAction]:
    target = _target_dir(source_dir)
    split_dir = _split_stereo_dir(target)
    wavs = _iter_source_wavs(source_dir)
    used_names: Set[str] = set()
    return [_plan_for_file(w, target, split_dir, used_names) for w in wavs]


def _format_table(rows: Sequence[Sequence[str]]) -> str:
    if not rows:
        return ""
    widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
    lines = []
    for idx, row in enumerate(rows):
        line = "  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row))
        lines.append(line)
        if idx == 0:
            lines.append("  ".join("-" * w for w in widths))
    return "\n".join(lines)


def print_plan(plan: Sequence[PlannedAction], source_dir: Path) -> None:
    target = _target_dir(source_dir)
    split_root = _split_stereo_dir(target)
    print(f"Source: {source_dir}")
    print(f"Target (pre-processed): {target}")
    print(f"True-stereo split mono (-L/-R only, not in pre-processed root): {split_root}")
    print()

    if not plan:
        print("No .wav files found.")
        return

    rows: List[List[str]] = [
        ["Source file", "Output", "Normalize (per output)", "Reason"],
    ]
    for a in plan:
        if a.kind == "skip" or not a.outputs:
            rows.append([a.source.name, "-", "-", a.reason])
            continue
        action_note = (
            "split + stereo copy"
            if a.kind == "split"
            else "convert/copy to mono"
            if a.kind == "mono"
            else "skip"
        )
        for idx, o in enumerate(a.outputs):
            src_col = a.source.name if idx == 0 else ""
            reason_col = f"{a.reason} ({action_note})" if idx == 0 else ""
            try:
                out_rel = str(o.path.relative_to(source_dir))
            except ValueError:
                out_rel = o.path.name
            rows.append([src_col, out_rel, o.normalize_label, reason_col])

    print(_format_table(rows))
    print(
        "Normalize: crest factor (peak/RMS on audio excluding contiguous silence "
        f"below -60 dBFS longer than {LONG_SILENCE_SECONDS:g} s): "
        f"< {CREST_BAND_LOW_LT_DB:g} dB → RMS {RMS_TARGET_DBFS:.0f} dBFS; "
        f"{CREST_BAND_LOW_LT_DB:g}–{CREST_BAND_MID_MAX_DB:g} dB → RMS {RMS_TARGET_DBFS:.0f} dBFS "
        f"with peak ≤ {PEAK_CAP_DBFS:.0f} dBFS; "
        f"> {CREST_BAND_MID_MAX_DB:g} dB → peak {PEAK_CAP_DBFS:.0f} dBFS."
    )
    print()


def _confirm() -> bool:
    ans = input("Proceed with processing? (y/N) ").strip().lower()
    return ans in {"y", "yes"}


def _safe_write_mono(
    out_path: Path,
    mono_samples: np.ndarray,
    samplerate: int,
    subtype: Optional[str],
    overwrite: bool,
) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.exists() and not overwrite:
        raise FileExistsError(f"Output exists: {out_path}")

    tmp = out_path.with_suffix(out_path.suffix + ".tmp")
    if tmp.exists():
        tmp.unlink()

    # Force WAV output format explicitly.
    sf.write(str(tmp), mono_samples, samplerate=samplerate, subtype=subtype, format="WAV")
    tmp.replace(out_path)


def _safe_write_stereo(
    out_path: Path,
    stereo_samples: np.ndarray,
    samplerate: int,
    subtype: Optional[str],
    overwrite: bool,
) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.exists() and not overwrite:
        raise FileExistsError(f"Output exists: {out_path}")

    tmp = out_path.with_suffix(out_path.suffix + ".tmp")
    if tmp.exists():
        tmp.unlink()

    sf.write(str(tmp), stereo_samples, samplerate=samplerate, subtype=subtype, format="WAV")
    tmp.replace(out_path)


def execute_plan(
    plan: Sequence[PlannedAction],
    source_dir: Path,
    overwrite: bool,
) -> Tuple[int, List[str]]:
    errors: List[str] = []
    target = _target_dir(source_dir)
    target.mkdir(parents=True, exist_ok=True)
    if any(a.kind == "split" for a in plan):
        _split_stereo_dir(target).mkdir(parents=True, exist_ok=True)

    for action in plan:
        if action.kind == "skip":
            continue

        try:
            data, sr = sf.read(str(action.source), always_2d=True)
            info = sf.info(str(action.source))
            subtype = info.subtype
        except Exception as e:  # noqa: BLE001
            errors.append(f"{action.source.name}: failed to read: {e}")
            continue

        channels = data.shape[1]
        if channels not in (1, 2):
            errors.append(f"{action.source.name}: unsupported channels at execution time ({channels})")
            continue

        left = data[:, 0]
        right = data[:, 1] if channels == 2 else None
        sr_i = int(round(sr))

        for out in action.outputs:
            if out.channel == "S":
                if channels != 2:
                    errors.append(f"{action.source.name}: planned S output but input is not stereo")
                    continue
                try:
                    normalized = _normalize_output_audio(data, sr_i)
                    _safe_write_stereo(out.path, normalized, sr, subtype, overwrite)
                except Exception as e:  # noqa: BLE001
                    errors.append(f"{action.source.name}: write failed ({out.path.name}): {e}")
                continue

            if out.channel == "M":
                mono = left
            elif out.channel == "L":
                mono = left
            elif out.channel == "R":
                if right is None:
                    errors.append(f"{action.source.name}: planned R output but input is not stereo")
                    continue
                mono = right
            else:
                errors.append(f"{action.source.name}: unknown planned channel '{out.channel}'")
                continue

            try:
                normalized = _normalize_output_audio(mono, sr_i)
                _safe_write_mono(out.path, normalized, sr, subtype, overwrite)
            except Exception as e:  # noqa: BLE001
                errors.append(f"{action.source.name}: write failed ({out.path.name}): {e}")

    return (0 if not errors else 1, errors)


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="audio-pre-processor",
        description="Analyze .wav files and write mono outputs into pre-processed/",
    )
    p.add_argument("source_dir", type=Path, help="Source directory containing .wav files")
    p.add_argument("--dry-run", action="store_true", help="Print planned actions only")
    p.add_argument("--yes", action="store_true", help="Skip confirmation prompt")
    p.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite outputs if they already exist",
    )
    return p.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    source_dir: Path = args.source_dir

    if not source_dir.exists() or not source_dir.is_dir():
        print(f"Error: not a directory: {source_dir}", file=sys.stderr)
        return 1

    plan = build_plan(source_dir)
    print_plan(plan, source_dir)

    actionable = [a for a in plan if a.kind != "skip"]
    if not plan:
        return 0
    if not actionable:
        print("Nothing to process (all files skipped).")
        return 0

    if args.dry_run:
        return 0

    if not args.yes and not _confirm():
        print("Cancelled.")
        return 2

    exit_code, errors = execute_plan(plan, source_dir, overwrite=bool(args.overwrite))
    if errors:
        print()
        print("Errors:")
        for e in errors:
            print(f"- {e}")
    else:
        print("Done.")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())

