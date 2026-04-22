import shutil
import subprocess
import tempfile
import sys
from pathlib import Path

# ================= CONFIG =================
USE_CROP_WORKFLOW = True #False # True   # True = new 3-stage mode, False = old full-file mode

# Crop size for the watermark region, in pixels
CROP_W_PX = 192
CROP_H_PX = 128

# Full-file mode settings
FULL_DETECTION_SKIP = 3
FULL_FADE_IN = 0.5
FULL_FADE_OUT = 0.5

# Crop mode settings
CROP_DETECTION_SKIP = 1
CROP_FADE_IN = 1.5
CROP_FADE_OUT = 1.5
# ==========================================

# ================= WATERMARK REMOVER TOOL PATHS =================
# These are the real paths used by process_water_file().
# Edit here — not inside the function body.
_WATER_VENV_ACTIVATE = Path("/home/homdx/Project/opensource/github/waterenv/bin/activate")
_WATER_CLI_DIR       = Path("/home/homdx/Project/opensource/github/WatermarkRemover-AI/")
_WATER_CLI           = "remwm.py"
# ================================================================

def combine_audio(intermediate_path, original_path, final_output_path):
    """Full-file mode: AI already cleaned the whole video, just copy audio from original."""
    cmd = [
        "ffmpeg", "-y",
        "-i", str(intermediate_path),   # full AI-cleaned video
        "-i", str(original_path),        # original (for audio only)
        "-map", "0:v",                   # video from AI output
        "-map", "1:a?",                  # audio from original
        "-c:v", "copy",                  # no re-encode needed!
        "-c:a", "copy",
        str(final_output_path)
    ]
    # ADDED EXECUTION LOGIC HERE:
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        proc.wait()
        if proc.returncode != 0:
            print(f"❌ FFmpeg audio merge returned non-zero exit status {proc.returncode}")
            return False
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
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=None,
        )
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
    print("Step 1: Extracting watermark region from original video...")

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
        "-c:a", "copy",  # Keep original audio
        str(cropped_output_path)
    ]

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=None,
        )
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
    print("Step 3: Overlaying cleaned watermark region onto original video...")

    cmd = [
        "ffmpeg", "-y",
        "-i", str(original_path),
        "-an", "-i", str(cleaned_region_path),   # -an: ignore any audio on the region clip
        "-filter_complex",
        # Use eof_action=pass so if region clip is 1-2 frames short, original video
        # continues unchanged rather than being truncated (no shortest=1).
        "[0:v][1:v]overlay=main_w-overlay_w:main_h-overlay_h:eof_action=pass[outv]",
        "-map", "[outv]",
        "-map", "0:a?",  # audio always from original
        "-c:v", "libx265",
        "-x265-params", "lossless=1",
        "-c:a", "copy",
        str(final_output_path)
    ]

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=None,
        )
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
    """Watermark removal supporting both 3-stage crop and full-file modes.
    Tool paths are configured via _WATER_VENV_ACTIVATE / _WATER_CLI_DIR / _WATER_CLI
    at the top of this module.
    """
    print(f"{'='*60}")
    mode_str = "Crop Mode (3-stage)" if USE_CROP_WORKFLOW else "Full-file Mode"
    print(f"Watermark removal [{mode_str}]: {video_path.name}")
    print(f"Using dir: {_WATER_CLI_DIR}")

    # Resolve absolute paths
    video_abs = Path(video_path).resolve()
    results_abs = RESULTS_WATER_DIR.resolve()
    results_abs.mkdir(parents=True, exist_ok=True)
    processed_dir = results_abs / "processed_videos"
    processed_dir.mkdir(parents=True, exist_ok=True)
    INPUT_DIR.resolve().mkdir(parents=True, exist_ok=True)

    # Names for final and intermediate files
    cleaned_basename = f"cleaned_{video_abs.name}"
    cleaned_in_root = results_abs / cleaned_basename
    cleaned_in_processed = processed_dir / cleaned_basename

    # SAVE ORIGINAL: copy source to results-water as original-FILENAME before any work
    original_backup = results_abs / f"original-{video_abs.name}"
    if not original_backup.exists() and video_abs.exists():
        try:
            shutil.copy2(str(video_abs), str(original_backup))
            print(f"\u2713 Saved original backup: {original_backup}")
        except Exception as e:
            print(f"\u26a0\ufe0f Failed to save original backup: {e}")

    # 1. Check if final cleaned file already exists
    if cleaned_in_root.exists() or cleaned_in_processed.exists():
        found = cleaned_in_root if cleaned_in_root.exists() else cleaned_in_processed
        print(f"✓ Found already-cleaned file: {found}")

        # [PREFIX CHECK] If the original filename does not start with "up_",
        # skip the INPUT_DIR copy entirely. The source is still archived.
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

        # Copy to INPUT_DIR
        dest = INPUT_DIR.resolve() / found.name
        if not dest.exists():
            try:
                shutil.copy2(str(found), str(dest))
                print(f"✓ Copied cleaned file to input_videos/: {dest}")
            except Exception as e:
                print(f"⚠️ Failed to copy cleaned file to input_videos/: {e}")
        else:
            print(f"✓ Cleaned file already present in input_videos/: {dest}")

        # Move original source to processed_videos
        try:
            moved_source = processed_dir / video_abs.name
            if video_abs.exists():
                shutil.move(str(video_abs), str(moved_source))
                print(f"✓ Moved original source to processed_videos/: {moved_source}")
        except Exception as e:
            print(f"⚠️ Failed to move source to processed_videos: {e}")
        return True

    final_target = results_abs / cleaned_basename

    if not USE_CROP_WORKFLOW:
        # =========================================================
        # OLD WORKFLOW: full file -> AI -> overlay cleaned result onto original
        # =========================================================
        temp_input_dir = Path(tempfile.mkdtemp(prefix=f"sora_input_{video_abs.stem}_"))
        try:
            tmp_dest = temp_input_dir / video_abs.name
            shutil.copy2(str(video_abs), str(tmp_dest))
            print(f"Copied source to temp input dir: {tmp_dest}")

            venv_python = _WATER_VENV_ACTIVATE.parent / "python3"
            if not venv_python.exists():
                alt = _WATER_VENV_ACTIVATE.parent / "python"
                venv_python = alt if alt.exists() else None

            output_file = results_abs / f"intermediate_{video_abs.name}"

            if venv_python:
                cmd = [
                    str(venv_python), _WATER_CLI, str(tmp_dest), str(output_file),
                    f"--detection-skip={FULL_DETECTION_SKIP}",
                    f"--fade-in={FULL_FADE_IN}",
                    f"--fade-out={FULL_FADE_OUT}",
                ]
            else:
                shell_cmd = (
                    f"source {str(_WATER_VENV_ACTIVATE)} && "
                    f"python3 {_WATER_CLI} '{str(tmp_dest)}' '{str(output_file)}' "
                    f"--detection-skip={FULL_DETECTION_SKIP} "
                    f"--fade-in={FULL_FADE_IN} --fade-out={FULL_FADE_OUT}"
                )
                cmd = ["/bin/bash", "-lc", shell_cmd]

            proc = subprocess.Popen(
                cmd,
                cwd=str(_WATER_CLI_DIR),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                shell=False
            )

            collected = []
            for line in iter(proc.stdout.readline, ''):
                if line == '' and proc.poll() is not None:
                    break
                print(line, end='')
                collected.append(line)

            proc.stdout.close()
            ret = proc.wait()
            full_output = ''.join(collected)

            if ret != 0 or not output_file.exists():
                print("❌ Full-file AI step failed")
                return False

#            if not run_ffmpeg_overlay(output_file, video_abs, final_target):
#                return False
            if not combine_audio(output_file, video_abs, final_target):
                return False

        finally:
            if temp_input_dir.exists():
                shutil.rmtree(str(temp_input_dir))

    else:
        # =========================================================
        # NEW WORKFLOW: crop -> AI -> overlay cleaned crop back
        # =========================================================
        cropped_region_basename = f"cropped_region_{video_abs.name}"
        cleaned_region_basename = f"cleaned_region_{video_abs.name}"

        # 2. STAGE 1: Extract watermark region
        cropped_region_file = results_abs / cropped_region_basename
        if not cropped_region_file.exists():
            extract_success = extract_watermark_region(video_abs, cropped_region_file)
            if not extract_success or not cropped_region_file.exists():
                print("❌ Failed to extract watermark region")
                return False
        else:
            print(f"✓ Watermark region already extracted: {cropped_region_file}")

        # 3. STAGE 2: Run AI Watermark Remover
        cleaned_region_file = results_abs / cleaned_region_basename

        if not cleaned_region_file.exists():
            temp_input_dir = Path(tempfile.mkdtemp(prefix=f"sora_input_{video_abs.stem}_"))
            try:
                tmp_dest = temp_input_dir / cropped_region_file.name
                shutil.copy2(str(cropped_region_file), str(tmp_dest))
                print(f"Step 2: Copied cropped region to temp dir: {tmp_dest}")

                venv_python = _WATER_VENV_ACTIVATE.parent / "python3"
                if not venv_python.exists():
                    alt = _WATER_VENV_ACTIVATE.parent / "python"
                    venv_python = alt if alt.exists() else None

                if venv_python:
                    cmd = [str(venv_python), _WATER_CLI, str(tmp_dest), str(cleaned_region_file),
                           f"--detection-skip={CROP_DETECTION_SKIP}",
                           f"--fade-in={CROP_FADE_IN}",
                           f"--fade-out={CROP_FADE_OUT}"]
                else:
                    shell_cmd = (
                        f"source {str(_WATER_VENV_ACTIVATE)} && "
                        f"python3 {_WATER_CLI} '{str(tmp_dest)}' '{str(cleaned_region_file)}' "
                        f"--detection-skip={CROP_DETECTION_SKIP} "
                        f"--fade-in={CROP_FADE_IN} --fade-out={CROP_FADE_OUT}"
                    )
                    cmd = ["/bin/bash", "-lc", shell_cmd]

                print("Step 2: Running AI watermark remover on cropped region...")

                proc = subprocess.Popen(
                    cmd,
                    cwd=str(_WATER_CLI_DIR),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                    shell=False
                )
                collected = []
                try:
                    for line in iter(proc.stdout.readline, ''):
                        if line == '' and proc.poll() is not None:
                            break
                        print(line, end='')
                        collected.append(line)
                except Exception as e:
                    print(f"⚠️ Error while streaming AI remover output: {e}")
                finally:
                    proc.stdout.close()
                    ret = proc.wait()

                full_output = ''.join(collected)

                if ret != 0:
                    print(f"❌ AI Watermark remover returned non-zero exit status {ret}")
                    return False

                if not cleaned_region_file.exists():
                    print("⚠️ Cleaned region file not found after AI run")
                    return False

                print(f"✓ AI watermark removal complete on cropped region: {cleaned_region_file}")

            except subprocess.TimeoutExpired:
                print("❌ Watermark cleaner timed out")
                return False

            except Exception as e:
                print(f"❌ Exception during AI watermark removal: {e}")
                return False

            finally:
                try:
                    if temp_input_dir.exists():
                        shutil.rmtree(str(temp_input_dir))
                        print(f"Removed temp input dir: {temp_input_dir}")
                except Exception as e:
                    print(f"⚠️ Failed to remove temp input dir {temp_input_dir}: {e}")
        else:
            print(f"✓ Cleaned region already exists: {cleaned_region_file}")

        # 4. STAGE 3: Overlay cleaned region back onto original video
        if not final_target.exists():
            overlay_success = overlay_cleaned_region(video_abs, cleaned_region_file, final_target)
            if not overlay_success or not final_target.exists():
                print("❌ Failed to create final combined video with overlay.")
                return False
        else:
            print(f"✓ Final combined video already exists: {final_target}")

        print(f"✓ Final combined video created: {final_target}")


    # =========================================================
    # SHARED FINAL STEPS: Copy output and move source
    # =========================================================

    # [PREFIX CHECK] If the original filename does not start with "up_",
    # skip INPUT_DIR entirely — cleaning result is kept in results-water,
    # but the file will NOT be queued for upscaling.
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

    # 5. Copy final combined output to input_videos
    dest = INPUT_DIR.resolve() / cleaned_basename
    if not dest.exists():
        try:
            shutil.copy2(str(final_target), str(dest))
            print(f"✓ Copied final combined file to input_videos/: {dest}")
        except Exception as e:
            print(f"❌ Failed to copy final combined file to input_videos: {e}")
            return False
    else:
        print(f"✓ Final combined file already exists in input_videos/: {dest}")

    # 6. Move original source to processed_videos
    try:
        moved_source = processed_dir / video_abs.name
        if video_abs.exists():
            shutil.move(str(video_abs), str(moved_source))
            print(f"✓ Moved original source to processed_videos/: {moved_source}")
    except Exception as e:
        print(f"⚠️ Failed to move source to processed_videos: {e}")

    return True
