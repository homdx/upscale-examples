import shutil
import subprocess
import tempfile
import sys
from pathlib import Path

# ================= CONFIG =================
USE_CROP_WORKFLOW = False  # True  = 3-stage crop mode, False = full-file mode
USE_BBOX          = True   # True  = remwm2.py (fixed bbox, no detection, faster)
                           # False = remwm.py  (Florence-2 detection, original style)


# Use this USE_BBOX = True and USE_CROP_WORKFLOW = False
# Crop size for the watermark region, in pixels
CROP_W_PX = 192
CROP_H_PX = 128

# --- Settings used when USE_BBOX = False (original detection-based style) ---
FULL_DETECTION_SKIP = 3
FULL_FADE_IN        = 0.5
FULL_FADE_OUT       = 0.5

CROP_DETECTION_SKIP = 1
CROP_FADE_IN        = 1.5
CROP_FADE_OUT       = 1.5

# --- Settings used when USE_BBOX = True (fixed-position style) ---
# Anchor corner: top-left | top-right | bottom-left | bottom-right
#                top-center | bottom-center | center
FULL_BBOX_ANCHOR = "bottom-right"
FULL_BBOX_W_PX   = 192      # can differ from CROP_W_PX if needed
FULL_BBOX_H_PX   = 128

CROP_BBOX_ANCHOR = "bottom-right"
# CROP_W_PX / CROP_H_PX already defined above — reused for crop bbox size
# ==========================================

# ================= WATERMARK REMOVER TOOL PATHS =================
_WATER_VENV_ACTIVATE = Path("/home/homdx/Project/opensource/github/waterenv/bin/activate")
_WATER_CLI_DIR       = Path("/home/homdx/Project/opensource/github/WatermarkRemover-AI/")
_WATER_CLI           = "remwm.py"    # original — Florence-2 detection
_WATER_CLI2          = "remwm2.py"   # new      — fixed bbox, no detection
# ================================================================


def _build_cmd(venv_python, tmp_dest, output_file, mode):
    """
    Build the subprocess cmd list for the watermark remover.

    mode: "full" — full-file processing (USE_CROP_WORKFLOW = False)
          "crop" — Stage 1 of crop workflow (USE_CROP_WORKFLOW = True)

    Branches on USE_BBOX:
      True  -> remwm2.py with --bbox-anchor / --bbox-size  (no detection model)
      False -> remwm.py  with --detection-skip / --fade-in / --fade-out
    """
    if USE_BBOX:
        if mode == "full":
            anchor    = FULL_BBOX_ANCHOR
            bbox_size = f"{FULL_BBOX_W_PX}x{FULL_BBOX_H_PX}"
        else:
            anchor    = CROP_BBOX_ANCHOR
            bbox_size = f"{CROP_W_PX}x{CROP_H_PX}"

        extra_flags = ["--bbox-anchor", anchor, "--bbox-size", bbox_size]
        cli = _WATER_CLI2

    else:
        if mode == "full":
            extra_flags = [
                f"--detection-skip={FULL_DETECTION_SKIP}",
                f"--fade-in={FULL_FADE_IN}",
                f"--fade-out={FULL_FADE_OUT}",
            ]
        else:
            extra_flags = [
                f"--detection-skip={CROP_DETECTION_SKIP}",
                f"--fade-in={CROP_FADE_IN}",
                f"--fade-out={CROP_FADE_OUT}",
            ]
        cli = _WATER_CLI

    if venv_python:
        return [str(venv_python), cli, str(tmp_dest), str(output_file)] + extra_flags

    flags_str = " ".join(extra_flags)
    shell_cmd = (
        f"source {str(_WATER_VENV_ACTIVATE)} && "
        f"python3 {cli} '{str(tmp_dest)}' '{str(output_file)}' {flags_str}"
    )
    return ["/bin/bash", "-lc", shell_cmd]


def combine_audio(intermediate_path, original_path, final_output_path):
    """Full-file mode: AI already cleaned the whole video, just copy audio from original."""
    cmd = [
        "ffmpeg", "-y",
        "-i", str(intermediate_path),
        "-i", str(original_path),
        "-map", "0:v",
        "-map", "1:a?",
        "-c:v", "copy",
        "-c:a", "copy",
        str(final_output_path)
    ]
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=None)
        proc.wait()
        if proc.returncode != 0:
            print(f"❌ FFmpeg audio merge returned non-zero exit status {proc.returncode}")
            return False
        print(f"✓ Audio merged: {final_output_path}")
        return True
    except Exception as e:
        print(f"❌ Exception during audio combination: {e}")
        return False


def run_ffmpeg_overlay(intermediate_path, original_path, final_output_path):
    """Old workflow step: overlay full-file processed watermark area back onto original."""
    print("Running FFmpeg to overlay cleaned watermark area (Full-file mode)...")

    cmd = [
        "ffmpeg", "-y",
        "-i", str(intermediate_path),
        "-i", str(original_path),
        "-filter_complex", "[0:v]crop=iw/5:ih/16:iw*4/5:ih*15/16[cropped];[1:v][cropped]overlay=main_w-overlay_w:main_h-overlay_h:shortest=1[outv]",
        "-map", "[outv]",
        "-map", "1:a?",
        "-c:v", "libx265",
        "-x265-params", "lossless=1",
        "-c:a", "copy",
        str(final_output_path)
    ]

    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=None)
        proc.wait()
        if proc.returncode != 0:
            print(f"❌ FFmpeg returned non-zero exit status {proc.returncode}")
            return False
        print(f"✓ FFmpeg processing successful: {final_output_path}")
        return True
    except Exception as e:
        print(f"❌ Exception during FFmpeg step: {e}")
        return False


def extract_watermark_region(original_path, cropped_output_path):
    """Step 1 (Crop workflow): Extract only the watermark region from original video."""
    print("Extracting watermark region from original video...")

    crop_w = f"trunc({CROP_W_PX}/2)*2"
    crop_h = f"trunc({CROP_H_PX}/2)*2"
    crop_x = f"iw-{crop_w}"
    crop_y = f"ih-{crop_h}"

    cmd = [
        "ffmpeg", "-y",
        "-i", str(original_path),
        "-vf", f"crop={crop_w}:{crop_h}:{crop_x}:{crop_y}",
        "-c:v", "libx265",
        "-x265-params", "lossless=1",
        "-c:a", "copy",
        str(cropped_output_path)
    ]

    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=None)
        proc.wait()
        if proc.returncode != 0:
            print(f"❌ FFmpeg extraction returned non-zero exit status {proc.returncode}")
            return False
        print(f"✓ Watermark region extracted: {cropped_output_path}")
        return True
    except Exception as e:
        print(f"❌ Exception during extraction: {e}")
        return False


def overlay_cleaned_region(original_path, cleaned_region_path, final_output_path):
    """Step 3 (Crop workflow): Overlay the AI-cleaned watermark region back onto the original video."""
    print("Overlaying cleaned watermark region onto original video...")

    cmd = [
        "ffmpeg", "-y",
        "-i", str(original_path),
        "-an", "-i", str(cleaned_region_path),
        "-filter_complex",
        "[0:v][1:v]overlay=main_w-overlay_w:main_h-overlay_h:eof_action=pass[outv]",
        "-map", "[outv]",
        "-map", "0:a?",
        "-c:v", "libx265",
        "-x265-params", "lossless=1",
        "-c:a", "copy",
        str(final_output_path)
    ]

    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=None)
        proc.wait()
        if proc.returncode != 0:
            print(f"❌ FFmpeg overlay returned non-zero exit status {proc.returncode}")
            return False
        print(f"✓ Final video created with cleaned watermark: {final_output_path}")
        return True
    except Exception as e:
        print(f"❌ Exception during overlay: {e}")
        return False


def process_water_file(video_path, INPUT_DIR, RESULTS_WATER_DIR):
    """
    Watermark removal supporting both 3-stage crop and full-file modes,
    and both detection-based (remwm.py) and fixed-bbox (remwm2.py) styles.

    Controlled by CONFIG flags at the top of this module:
      USE_CROP_WORKFLOW : True  = crop mode (3 stages)
                          False = full-file mode
      USE_BBOX          : True  = remwm2.py fixed bbox (faster, no Florence-2)
                          False = remwm.py detection (original style)

    All four combinations work:
      USE_CROP_WORKFLOW=False, USE_BBOX=False  ->  remwm.py  full video, detection
      USE_CROP_WORKFLOW=False, USE_BBOX=True   ->  remwm2.py full video, fixed bbox
      USE_CROP_WORKFLOW=True,  USE_BBOX=False  ->  remwm.py  full video, detection -> crop -> overlay
      USE_CROP_WORKFLOW=True,  USE_BBOX=True   ->  remwm2.py full video, fixed bbox -> crop -> overlay
    """
    print(f"{'='*60}")
    mode_str = "Crop Mode (3-stage)" if USE_CROP_WORKFLOW else "Full-file Mode"
    bbox_str = "fixed bbox" if USE_BBOX else "detection"
    print(f"Watermark removal [{mode_str} / {bbox_str}]: {video_path.name}")
    print(f"Using dir: {_WATER_CLI_DIR}")

    video_abs    = Path(video_path).resolve()
    results_abs  = RESULTS_WATER_DIR.resolve()
    results_abs.mkdir(parents=True, exist_ok=True)
    processed_dir = results_abs / "processed_videos"
    processed_dir.mkdir(parents=True, exist_ok=True)
    INPUT_DIR.resolve().mkdir(parents=True, exist_ok=True)

    cleaned_basename   = f"cleaned_{video_abs.name}"
    cleaned_in_root    = results_abs / cleaned_basename
    cleaned_in_processed = processed_dir / cleaned_basename

    # Save original backup
    original_backup = results_abs / f"original-{video_abs.name}"
    if not original_backup.exists() and video_abs.exists():
        try:
            shutil.copy2(str(video_abs), str(original_backup))
            print(f"✓ Saved original backup: {original_backup}")
        except Exception as e:
            print(f"⚠️ Failed to save original backup: {e}")

    # Check if final cleaned file already exists
    if cleaned_in_root.exists() or cleaned_in_processed.exists():
        found = cleaned_in_root if cleaned_in_root.exists() else cleaned_in_processed
        print(f"✓ Found already-cleaned file: {found}")

        if not video_abs.name.startswith("up_"):
            print(f"⏭️  '{video_abs.name}' does not start with 'up_' — skipping input_videos copy.")
            try:
                moved_source = processed_dir / video_abs.name
                if video_abs.exists():
                    shutil.move(str(video_abs), str(moved_source))
                    print(f"✓ Moved original source to processed_videos/: {moved_source}")
            except Exception as e:
                print(f"⚠️ Failed to move source to processed_videos: {e}")
            return True

        dest = INPUT_DIR.resolve() / found.name
        if not dest.exists():
            try:
                shutil.copy2(str(found), str(dest))
                print(f"✓ Copied cleaned file to input_videos/: {dest}")
            except Exception as e:
                print(f"⚠️ Failed to copy cleaned file to input_videos/: {e}")
        else:
            print(f"✓ Cleaned file already present in input_videos/: {dest}")

        try:
            moved_source = processed_dir / video_abs.name
            if video_abs.exists():
                shutil.move(str(video_abs), str(moved_source))
                print(f"✓ Moved original source to processed_videos/: {moved_source}")
        except Exception as e:
            print(f"⚠️ Failed to move source to processed_videos: {e}")
        return True

    final_target = results_abs / cleaned_basename

    # =========================================================
    # Resolve venv python once — shared by both workflow paths
    # =========================================================
    venv_python = _WATER_VENV_ACTIVATE.parent / "python3"
    if not venv_python.exists():
        alt = _WATER_VENV_ACTIVATE.parent / "python"
        venv_python = alt if alt.exists() else None

    if not USE_CROP_WORKFLOW:
        # =====================================================
        # FULL-FILE WORKFLOW
        # remwm.py or remwm2.py processes the entire video.
        # combine_audio() attaches the original audio track.
        # =====================================================
        temp_input_dir = Path(tempfile.mkdtemp(prefix=f"sora_input_{video_abs.stem}_"))
        try:
            tmp_dest = temp_input_dir / video_abs.name
            shutil.copy2(str(video_abs), str(tmp_dest))
            print(f"Copied source to temp input dir: {tmp_dest}")

            output_file = results_abs / f"intermediate_{video_abs.name}"

            cmd = _build_cmd(venv_python, tmp_dest, output_file, mode="full")

            proc = subprocess.Popen(
                cmd,
                cwd=str(_WATER_CLI_DIR),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                shell=False,
            )

            collected = []
            for line in iter(proc.stdout.readline, ''):
                if line == '' and proc.poll() is not None:
                    break
                print(line, end='')
                collected.append(line)

            proc.stdout.close()
            ret = proc.wait()

            if ret != 0 or not output_file.exists():
                print("❌ Full-file AI step failed")
                return False

            if not combine_audio(output_file, video_abs, final_target):
                return False

            print(f"✓ Final video created: {final_target}")

        finally:
            if temp_input_dir.exists():
                shutil.rmtree(str(temp_input_dir))

    else:
        # =====================================================
        # CROP WORKFLOW (3 stages)
        # Stage 1 : remwm.py / remwm2.py on full video
        # Stage 2 : crop watermark region from AI output
        # Stage 3 : overlay cleaned crop onto original
        # =====================================================
        cleaned_region_basename = f"cleaned_region_{video_abs.name}"
        cleaned_region_file     = results_abs / cleaned_region_basename
        output_file             = results_abs / f"intermediate_{video_abs.name}"

        # ── Stage 1: AI watermark removal on full video ──────────────
        if not output_file.exists():
            temp_input_dir = Path(tempfile.mkdtemp(prefix=f"sora_input_{video_abs.stem}_"))
            try:
                tmp_dest = temp_input_dir / video_abs.name
                shutil.copy2(str(video_abs), str(tmp_dest))
                print(f"Stage 1: Copied source to temp input dir: {tmp_dest}")

                cmd = _build_cmd(venv_python, tmp_dest, output_file, mode="crop")

                print("Stage 1: Running AI watermark remover on full video...")

                proc = subprocess.Popen(
                    cmd,
                    cwd=str(_WATER_CLI_DIR),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                    shell=False,
                )

                collected = []
                for line in iter(proc.stdout.readline, ''):
                    if line == '' and proc.poll() is not None:
                        break
                    print(line, end='')
                    collected.append(line)

                proc.stdout.close()
                ret = proc.wait()

                if ret != 0 or not output_file.exists():
                    print("❌ Stage 1 AI step failed")
                    return False

            finally:
                if temp_input_dir.exists():
                    shutil.rmtree(str(temp_input_dir))
        else:
            print(f"✓ Stage 1 intermediate already exists: {output_file}")

        # ── Stage 2: Crop watermark region from AI output ────────────
        if not cleaned_region_file.exists():
            print("Stage 2: Extracting cleaned watermark region from AI output...")
            if not extract_watermark_region(output_file, cleaned_region_file):
                print("❌ Stage 2 failed to extract watermark region")
                return False
            print(f"✓ Cleaned watermark region extracted: {cleaned_region_file}")
        else:
            print(f"✓ Stage 2 cleaned region already exists: {cleaned_region_file}")

        # ── Stage 3: Overlay cleaned crop onto original ───────────────
        if not final_target.exists():
            if not overlay_cleaned_region(video_abs, cleaned_region_file, final_target):
                print("❌ Stage 3 failed to create final combined video")
                return False
        else:
            print(f"✓ Stage 3 final video already exists: {final_target}")

        print(f"✓ Final combined video created: {final_target}")

    # =========================================================
    # SHARED FINAL STEPS — copy output, move source
    # =========================================================

    if not video_abs.name.startswith("up_"):
        print(f"⏭️  '{video_abs.name}' does not start with 'up_' — skipping input_videos copy.")
        try:
            moved_source = processed_dir / video_abs.name
            if video_abs.exists():
                shutil.move(str(video_abs), str(moved_source))
                print(f"✓ Moved original source to processed_videos/: {moved_source}")
        except Exception as e:
            print(f"⚠️ Failed to move source to processed_videos: {e}")
        return True

    # Copy final output to input_videos
    dest = INPUT_DIR.resolve() / cleaned_basename
    if not dest.exists():
        try:
            shutil.copy2(str(final_target), str(dest))
            print(f"✓ Copied final file to input_videos/: {dest}")
        except Exception as e:
            print(f"❌ Failed to copy final file to input_videos: {e}")
            return False
    else:
        print(f"✓ Final file already exists in input_videos/: {dest}")

    # Move original source to processed_videos
    try:
        moved_source = processed_dir / video_abs.name
        if video_abs.exists():
            shutil.move(str(video_abs), str(moved_source))
            print(f"✓ Moved original source to processed_videos/: {moved_source}")
    except Exception as e:
        print(f"⚠️ Failed to move source to processed_videos: {e}")

    return True
