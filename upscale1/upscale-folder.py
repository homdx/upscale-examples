#!/bin/python3

import os
import time
import subprocess
import sys
import shutil
from pathlib import Path
from datetime import timedelta
import re
import signal
import concurrent.futures  # [NEW] For parallel processing
# ... existing imports ...

# ================= CONFIGURATION =================
# ... existing config ...
# [NEW] Number of threads for indicator processing (default: all cores)
INDICATOR_THREADS = os.cpu_count() or 4

# [NEW] Import the indicator function
try:
    from indicator import add_video_frame_indicator_png
except ImportError:
    print("❌ ERROR: indicator.py not found. Please create it next to this script.")
    sys.exit(1)

# ================= CONFIGURATION =================
INPUT_DIR = Path("./input_videos")
OUTPUT_BASE_DIR = Path("./results")

# Watermark cleaner dirs
WATER_DIR = Path("./water_remove")
RESULTS_WATER_DIR = Path("./results-water")
# Virtualenv activate script (user provided)
SORA_VENV_ACTIVATE = Path("/home/homdx/Project/opensource/github/sorawcleanvenv/bin/activate")
# SoraWatermarkCleaner repo folder (where cli.py lives)
SORA_CLI_DIR = Path("/home/homdx/Project/opensource/github/SoraWatermarkCleaner")
SORA_CLI = "cli.py"

# ✅ YOUR REAL PATHS
UPSCALE_ROOT = Path("/home/homdx/Progs/squashfs-root/resources")
UPSCALE_BIN = UPSCALE_ROOT / "bin" / "upscayl-bin"
# Windows UPSCALE_ROOT = Path("C:/upscale20024/resources")
# Windows UPSCALE_BIN = UPSCALE_ROOT / "bin" / "upscayl-bin.exe"
MODELS_PATH = UPSCALE_ROOT / "models"
#MODEL_MODE = "ultrasharp-4x"
#MODEL_MODE = "high-fidelity-4x"
#MODEL_MODE = "upscayl-standard-4x"
#MODEL_MODE = "ultramix-balanced-4x"
#MODEL_MODE = "remacri-4x"
MODEL_MODE = "4x_NMKD-Siax_200k"
SCALE_FACTOR = "4"


GPU_ID = "0"
num_of_video = 3
num_of_video_total = 4
# ========================

# Global flag for graceful shutdown
shutdown_flag = False


def signal_handler(sig, frame):
    global shutdown_flag
    print("\n\n⚠️  Shutdown requested. Finishing current frame...")
    shutdown_flag = True


def format_time(seconds):
    """Format seconds to hh:mm or mm:ss based on duration"""
    if seconds < 3600:
        return str(timedelta(seconds=int(seconds)))[2:7]  # mm:ss
    return str(timedelta(seconds=int(seconds)))[:8]  # hh:mm:ss


def save_progress(progress_file, current_index):
    """Save current progress to file"""
    try:
        with open(progress_file, 'w') as f:
            f.write(str(current_index))
    except Exception as e:
        print(f"⚠️  Failed to save progress: {e}")


def load_progress(progress_file):
    """Load progress from file, return 1 if not exists"""
    try:
        if progress_file.exists():
            with open(progress_file, 'r') as f:
                content = f.read().strip()
                if content:
                    # Handle both formats: "0010" and "10"
                    try:
                        return int(content)
                    except ValueError:
                        # Try removing leading zeros
                        return int(content.lstrip('0') or 1)
    except Exception as e:
        print(f"⚠️  Failed to load progress: {e}")
    return 1


def extract_frame_number(filename):
    """Extract frame number from filename like thumb0001.png"""
    match = re.search(r'thumb(\d+)\.png', str(filename))
    if match:
        return int(match.group(1))
    return 0


def get_framerate(video_path):
    """Get original video framerate"""
    cmd = ["ffprobe", "-v", "quiet", "-select_streams", "v:0",
           "-show_entries", "stream=r_frame_rate", "-of", "csv=p=0",
           str(video_path)]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        framerate = result.stdout.strip()
        # Handle fractions like "30000/1001"
        if '/' in framerate:
            num, den = map(int, framerate.split('/'))
            return str(round(num/den, 2))
        return framerate or "30"
    except (subprocess.CalledProcessError, ValueError):
        return "30"


def check_tools():
    """Check if required tools are available"""
    required_tools = ["ffmpeg", "ffprobe"]
    missing_tools = []

    for tool in required_tools:
        try:
            subprocess.run([tool, "-version"], stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, check=True)
        except (subprocess.CalledProcessError, FileNotFoundError):
            missing_tools.append(tool)

    if missing_tools:
        print(f"❌ Missing required tools: {', '.join(missing_tools)}")
        print("Please install them and ensure they're in PATH")
        return False

    if not UPSCALE_BIN.exists():
        print(f"❌ Upscayl not found: {UPSCALE_BIN}")
        print("Please check the UPSCALE_ROOT path")
        return False

    return True


def get_total_frames(video_path):
    """Get total number of frames in video"""
    cmd = [
        "ffprobe", "-v", "quiet", "-select_streams", "v:0",
        "-show_entries", "stream=nb_frames", "-of", "csv=p=0",
        str(video_path)
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return int(result.stdout.strip())
    except:
        return 0


def process_water_file(video_path):
    """Run SoraWatermarkCleaner on a video found in WATER_DIR and copy result to INPUT_DIR.

    Behavior adjustments per user request:
      - Ensure the cleaned file produced by Sora is *saved* in results-water (root).
      - After successful cleaning, move the original source file from water_remove -> results-water/processed_videos
        (so it's no longer in water_remove and won't be reprocessed).
      - If a cleaned file already exists in results-water (root) or results-water/processed_videos, skip running Sora.

    Note: Sora expects an input *directory*, so we create a temporary directory and copy the single file there.
    """
    import tempfile

    print(f"{'='*60}")
    print(f"Watermark removal: {video_path.name}")
    print(f"Using Sora dir: {SORA_CLI_DIR}")

    # Resolve absolute paths
    video_abs = Path(video_path).resolve()
    results_abs = RESULTS_WATER_DIR.resolve()
    results_abs.mkdir(parents=True, exist_ok=True)
    processed_dir = results_abs / "processed_videos"
    processed_dir.mkdir(parents=True, exist_ok=True)
    INPUT_DIR.resolve().mkdir(parents=True, exist_ok=True)

    cleaned_basename = f"cleaned_{video_abs.name}"
    cleaned_in_root = results_abs / cleaned_basename
    cleaned_in_processed = processed_dir / cleaned_basename

    # If cleaned already exists in root or processed -> ensure copied to input and move source, skip
    if cleaned_in_root.exists() or cleaned_in_processed.exists():
        found = cleaned_in_root if cleaned_in_root.exists() else cleaned_in_processed
        print(f"✓ Found already-cleaned file: {found}")
        dest = INPUT_DIR.resolve() / found.name
        if not dest.exists():
            try:
                shutil.copy2(str(found), str(dest))
                print(f"✓ Copied cleaned file to input_videos/: {dest}")
            except Exception as e:
                print(f"⚠️ Failed to copy cleaned file to input_videos/: {e}")
        else:
            print(f"✓ Cleaned file already present in input_videos/: {dest}")
        # Move the source file out of water_remove into processed_videos to avoid re-trigger
        try:
            moved_source = processed_dir / video_abs.name
            if video_abs.exists():
                shutil.move(str(video_abs), str(moved_source))
                print(f"✓ Moved original source to processed_videos/: {moved_source}")
        except Exception as e:
            print(f"⚠️ Failed to move source to processed_videos: {e}")
        return True

    # Otherwise we need to run the cleaner
    temp_input_dir = Path(tempfile.mkdtemp(prefix=f"sora_input_{video_abs.stem}_"))
    try:
        tmp_dest = temp_input_dir / video_abs.name
        shutil.copy2(str(video_abs), str(tmp_dest))
        print(f"Copied source to temp input dir: {tmp_dest}")

        # Locate python inside venv if possible (try python3 then python)
        venv_python = SORA_VENV_ACTIVATE.parent / "python3"
        if not venv_python.exists():
            alt = SORA_VENV_ACTIVATE.parent / "python"
            venv_python = alt if alt.exists() else None

        if venv_python:
            python_cmd = str(venv_python)
            cmd = [python_cmd, SORA_CLI, "-i", str(temp_input_dir), "-o", str(results_abs)]
            use_shell = False
        else:
            # fallback: source the venv activate script then run python3
            shell_cmd = f"source {str(SORA_VENV_ACTIVATE)} && python3 {SORA_CLI} -i '{str(temp_input_dir)}' -o '{str(results_abs)}'"
            cmd = ["/bin/bash", "-lc", shell_cmd]
            use_shell = True

        print("Running watermark cleaner (this may take a while)...")

        # Stream output live to console and also collect for logs
        proc = subprocess.Popen(
            cmd,
            cwd=str(SORA_CLI_DIR),
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
            print(f"⚠️ Error while streaming Sora output: {e}")
        finally:
            proc.stdout.close()
            ret = proc.wait()

        full_output = ''.join(collected)

        if ret != 0:
            print(f"❌ Watermark cleaner returned non-zero exit status {ret}")
            # write log for debugging
            try:
                log_path = results_abs / f"{video_abs.stem}.sora.log"
                with open(log_path, 'w') as lf:
                    lf.write(full_output)
                print(f"✓ Wrote Sora log: {log_path}")
            except Exception:
                pass
            return False

        # find produced .mp4 files in results_abs
        produced_files = list(results_abs.glob("*.mp4"))
        if not produced_files:
            print("⚠️  No .mp4 files found in results-water after run")
            return False

        # Prefer standardized cleaned name, otherwise newest
        candidate_by_name = [p for p in produced_files if p.name == cleaned_basename]
        if candidate_by_name:
            chosen = candidate_by_name[0]
        else:
            # fallback: use newest file
            chosen = sorted(produced_files, key=lambda p: p.stat().st_mtime, reverse=True)[0]

        # Ensure the cleaned file remains in results-water root (do NOT move it)
        cleaned_target = results_abs / chosen.name
        if not cleaned_target.exists():
            # If chosen is not in results root (unlikely), copy it there
            try:
                shutil.copy2(str(chosen), str(cleaned_target))
                print(f"✓ Copied cleaned file into results-water/: {cleaned_target}")
            except Exception as e:
                print(f"⚠️ Failed to copy cleaned file into results-water/: {e}")
        else:
            print(f"✓ Cleaned file present in results-water/: {cleaned_target}")

        # Copy to input_videos if not exists
        dest = INPUT_DIR.resolve() / chosen.name
        if not dest.exists():
            try:
                shutil.copy2(str(chosen), str(dest))
                print(f"✓ Copied cleaned file to input_videos/: {dest}")
            except Exception as e:
                print(f"❌ Failed to copy cleaned file to input_videos: {e}")
                return False
        else:
            print(f"✓ Cleaned file already exists in input_videos/: {dest}")

        # Move the original source file from water_remove to processed_videos
        try:
            moved_source = processed_dir / video_abs.name
            if video_abs.exists():
                shutil.move(str(video_abs), str(moved_source))
                print(f"✓ Moved original source to processed_videos/: {moved_source}")
        except Exception as e:
            print(f"⚠️ Failed to move source to processed_videos: {e}")

        # Write the Sora log into processed_videos for reference
        try:
            log_path = processed_dir / f"{video_abs.stem}.sora.log"
            with open(log_path, 'w') as lf:
                lf.write(full_output)
        except Exception:
            pass

        return True

    except subprocess.TimeoutExpired:
        print("❌ Watermark cleaner timed out")
        return False

    except Exception as e:
        print(f"❌ Exception during watermark step: {e}")
        return False

    finally:
        # cleanup temp input dir (comment out if you want to keep it for debugging)
        try:
            if temp_input_dir.exists():
                shutil.rmtree(str(temp_input_dir))
                print(f"Removed temp input dir: {temp_input_dir}")
        except Exception as e:
            print(f"⚠️ Failed to remove temp input dir {temp_input_dir}: {e}")


    # If cleaned file exists in results-water root (not yet moved), move it to processed and copy
    if cleaned_in_root.exists():
        print(f"✓ Found cleaned file in results-water: {cleaned_in_root}")
        try:
            dest_input = INPUT_DIR.resolve() / cleaned_in_root.name
            if not dest_input.exists():
                shutil.copy2(str(cleaned_in_root), str(dest_input))
                print(f"✓ Copied cleaned file to input_videos/: {dest_input}")
            # Move to processed_videos to avoid being picked up again
            moved = processed_dir / cleaned_in_root.name
            shutil.move(str(cleaned_in_root), str(moved))
            print(f"✓ Moved cleaned file to processed_videos/: {moved}")
        except Exception as e:
            print(f"⚠️ Failed to copy/move existing cleaned file: {e}")
        return True

    # Otherwise we need to run the cleaner
    temp_input_dir = Path(tempfile.mkdtemp(prefix=f"sora_input_{video_abs.stem}_"))
    try:
        tmp_dest = temp_input_dir / video_abs.name
        shutil.copy2(str(video_abs), str(tmp_dest))
        print(f"Copied source to temp input dir: {tmp_dest}")

        # Locate python inside venv if possible (try python3 then python)
        venv_python = SORA_VENV_ACTIVATE.parent / "python3"
        if not venv_python.exists():
            alt = SORA_VENV_ACTIVATE.parent / "python"
            venv_python = alt if alt.exists() else None

        if venv_python:
            python_cmd = str(venv_python)
            cmd = [python_cmd, SORA_CLI, "-i", str(temp_input_dir), "-o", str(results_abs)]
            use_shell = False
        else:
            # fallback: source the venv activate script then run python3
            shell_cmd = f"source {str(SORA_VENV_ACTIVATE)} && python3 {SORA_CLI} -i '{str(temp_input_dir)}' -o '{str(results_abs)}'"
            cmd = ["/bin/bash", "-lc", shell_cmd]
            use_shell = True

        print("Running watermark cleaner (this may take a while)...")

        # Stream output live to console and also collect for logs
        proc = subprocess.Popen(
            cmd,
            cwd=str(SORA_CLI_DIR),
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
            print(f"⚠️ Error while streaming Sora output: {e}")
        finally:
            proc.stdout.close()
            ret = proc.wait()

        full_output = ''.join(collected)

        if ret != 0:
            print(f"❌ Watermark cleaner returned non-zero exit status {ret}")
            # write log for debugging
            try:
                log_path = results_abs / f"{video_abs.stem}.sora.log"
                with open(log_path, 'w') as lf:
                    lf.write(full_output)
                print(f"✓ Wrote Sora log: {log_path}")
            except Exception:
                pass
            return False

        # find produced .mp4 files in results_abs
        produced_files = list(results_abs.glob("*.mp4"))
        if not produced_files:
            print("⚠️  No .mp4 files found in results-water after run")
            return False

        # Prefer standardized cleaned name, otherwise newest
        candidate_by_name = [p for p in produced_files if p.name == cleaned_basename]
        if candidate_by_name:
            chosen = candidate_by_name[0]
        else:
            # fallback: use newest file
            chosen = sorted(produced_files, key=lambda p: p.stat().st_mtime, reverse=True)[0]

        # Copy to input_videos if not exists
        dest = INPUT_DIR.resolve() / chosen.name
        if not dest.exists():
            try:
                shutil.copy2(str(chosen), str(dest))
                print(f"✓ Copied cleaned file to input_videos/: {dest}")
            except Exception as e:
                print(f"❌ Failed to copy cleaned file to input_videos: {e}")
                return False
        else:
            print(f"✓ Cleaned file already exists in input_videos/: {dest}")

        # Move the chosen file into processed_videos to avoid re-processing
        moved_target = processed_dir / chosen.name
        try:
            shutil.move(str(chosen), str(moved_target))
            print(f"✓ Moved cleaned file to processed_videos/: {moved_target}")
        except Exception as e:
            print(f"⚠️ Failed to move cleaned file to processed_videos: {e}")
            # attempt to remove original in results root to avoid loop
            try:
                chosen.unlink(missing_ok=True)
            except Exception:
                pass

        # Optionally write the Sora log next to the moved file
        try:
            log_path = processed_dir / f"{video_abs.stem}.sora.log"
            with open(log_path, 'w') as lf:
                lf.write(full_output)
        except Exception:
            pass

        return True

    except subprocess.TimeoutExpired:
        print("❌ Watermark cleaner timed out")
        return False

    except Exception as e:
        print(f"❌ Exception during watermark step: {e}")
        return False

    finally:
        # cleanup temp input dir (comment out if you want to keep it for debugging)
        try:
            if temp_input_dir.exists():
                shutil.rmtree(str(temp_input_dir))
                print(f"Removed temp input dir: {temp_input_dir}")
        except Exception as e:
            print(f"⚠️ Failed to remove temp input dir {temp_input_dir}: {e}")

def process_indicator_task(args):
    """Worker function for parallel indicator processing"""
    path, frame_idx, total, v_num, v_tot = args

    start = time.time()
    tmp_target = path.with_suffix(f".tmp_{frame_idx}.png")

    try:
        # Import inside worker to ensure clean state in multiprocessing
        from indicator import add_video_frame_indicator_png

        ok = add_video_frame_indicator_png(
            path, tmp_target, frame_idx, total, v_num, v_tot
        )

        if ok and tmp_target.exists():
            shutil.move(str(tmp_target), str(path))

        return (True, time.time() - start)
    except Exception as e:
        return (False, 0.0)

def process_video(video_path):
    global shutdown_flag

    video_name = video_path.stem
    project_dir = OUTPUT_BASE_DIR / video_name
    frames_dir = project_dir / "frames"
    upscaled_dir = project_dir / "upscaled"
    progress_file = project_dir / "progress.txt"

    # Create directories
    frames_dir.mkdir(parents=True, exist_ok=True)
    upscaled_dir.mkdir(parents=True, exist_ok=True)

    print(f"{'='*60}")
    print(f"Processing: {video_name}")
    print(f"Source: {video_path}")
    print(f"{'='*60}")

    # Get video info
    total_video_frames = get_total_frames(video_path)
    framerate = get_framerate(video_path)
    print(f"Video info: {total_video_frames} frames @ {framerate} fps")

    # 1. Extract Frames (Existing code...)
    print(f"Step 1: Extracting frames...")
    frame_pattern = frames_dir / "thumb%04d.png"
    existing_frames = len(list(frames_dir.glob("thumb*.png")))
    if existing_frames == 0:
        cmd_extract = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(video_path), str(frame_pattern)]
        try:
            print("Extracting frames...")
            subprocess.run(cmd_extract, check=True)
        except subprocess.CalledProcessError as e:
            print(f" Failed to extract frames: {e}")
            return False
    else:
        print(f" Found {existing_frames} existing frames")

    # 2. Extract Audio (Existing code...)
    print(f"Step 2: Extracting audio...")
    audio_path = project_dir / "audio.aac"
    if not audio_path.exists():
        try:
            subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(video_path), "-c:a", "copy", str(audio_path)], check=True)
            print(f" Audio saved")
        except: pass
    else:
        print(" Audio already exists")

    # 3. Upscayl Processing
    print(f"Step 3: Upscayl processing ({MODEL_MODE})...")

    frames = sorted(frames_dir.glob("thumb*.png"), key=lambda x: extract_frame_number(x.name))
    if not frames: return False
    total_frames = len(frames)

    start_frame = load_progress(progress_file)
    if start_frame > total_frames: start_frame = total_frames

    print(f"Starting from frame {start_frame:04d} of {total_frames:04d}")
    print(f" Parallel processing enabled: {INDICATOR_THREADS} threads")
    print("-" * 50)

    # State variables
    processed_count = 0
    total_processed = 0
    start_time = time.time()
    process_times = []

    # [NEW] Batch Queue for Parallel Processing
    batch_queue = []

    def flush_batch():
        """Helper to process queued frames in parallel with live updates"""
        nonlocal processed_count, total_processed

        # Check shutdown immediately
        if shutdown_flag or not batch_queue:
            return

        print(f" Processing batch of {len(batch_queue)} existing frames...")

        # Map futures to their frame index for correct ordering in logs
        future_to_index = {}

        # [FIX] Use 'with' block, but monitor shutdown_flag inside the loop
        executor = concurrent.futures.ProcessPoolExecutor(max_workers=INDICATOR_THREADS)
        try:
            # Submit all tasks first
            for item in batch_queue:
                f_obj, idx_0 = item
                out_f = upscaled_dir / f_obj.name
                fut = executor.submit(process_indicator_task, (out_f, idx_0 + 1, total_frames, num_of_video, num_of_video_total))
                future_to_index[fut] = idx_0

            # Process as they complete
            for future in concurrent.futures.as_completed(future_to_index):
                # [FIX] CRITICAL: Break loop immediately if shutdown requested
                if shutdown_flag:
                    print(" Aborting batch processing...")
                    executor.shutdown(wait=False, cancel_futures=True)
                    return

                idx_0 = future_to_index[future]
                try:
                    success, duration = future.result()
                except Exception as e:
                    print(f"Error in thread: {e}")
                    success, duration = False, 0

                if success:
                    process_times.append(duration)
                    processed_count += 1
                    total_processed += 1

                # Update Stats
                total_elapsed = time.time() - start_time
                if process_times:
                    recent = sorted(process_times[-20:])
                    median = recent[len(recent)//2]
                else:
                    median = 0

                # Correct median for parallel throughput
                effective_frame_time = median / max(1, INDICATOR_THREADS)
                remaining_frames = total_frames - (idx_0 + 1)
                remain_time = effective_frame_time * remaining_frames

                print(f"[{idx_0+1:04d}/{total_frames:04d}] Fragment: {format_time(duration)} | Median: {format_time(median)} | Total: {format_time(total_elapsed)} | Remain: {format_time(remain_time)}")
                save_progress(progress_file, idx_0 + 2)

        finally:
            # Ensure executor is closed
            executor.shutdown(wait=False)
            batch_queue.clear()

    # --- MAIN LOOP ---
    for i in range(start_frame - 1, total_frames):
        if shutdown_flag:
            flush_batch() # Finish what we have
            save_progress(progress_file, i + 1)
            return False

        frame = frames[i]
        output_frame = upscaled_dir / frame.name

        # [MODIFIED] Check if frame exists -> Add to Batch
        if output_frame.exists():
            batch_queue.append((frame, i))
            continue

        # If we hit a missing frame, FLUSH the batch first
        if batch_queue:
            flush_batch()

        # --- Standard GPU Upscaling (Sequential) ---
        cmd_upscayl = [
            str(UPSCALE_BIN), "-i", str(frame), "-o", str(output_frame),
            "-m", str(MODELS_PATH), "-n", MODEL_MODE, "-f", "png",
            "-s", SCALE_FACTOR, "-c", "100", "-g", GPU_ID
        ]

        frame_start = time.time()

        # GPU / CPU Fallback Logic
        try:
            res = subprocess.run(cmd_upscayl, capture_output=True, text=True, timeout=300)
            if res.returncode != 0: # Retry CPU
                 cmd_cpu = cmd_upscayl.copy(); cmd_cpu[-1] = "-1"
                 res = subprocess.run(cmd_cpu, capture_output=True, text=True, timeout=600)
        except subprocess.TimeoutExpired: # Retry CPU
             cmd_cpu = cmd_upscayl.copy(); cmd_cpu[-1] = "-1"
             try: res = subprocess.run(cmd_cpu, capture_output=True, text=True, timeout=600)
             except: pass

        if not output_frame.exists():
            print(f" Frame failed: {frame.name}")
            continue

        # Apply indicator to new frame (Single thread, since it's just one)
        tmp_annot = output_frame.with_suffix(".tmp.png")
        add_video_frame_indicator_png(output_frame, tmp_annot, (i+1), total_frames, num_of_video, num_of_video_total)
        if tmp_annot.exists(): shutil.move(str(tmp_annot), str(output_frame))

        # Update Stats
        frame_time = time.time() - frame_start
        processed_count += 1
        total_processed += 1
        process_times.append(frame_time)

        # Display Stats
        recent_times = process_times[-10:]; recent_times.sort()
        median_time = recent_times[len(recent_times)//2] if recent_times else 0
        remain = median_time * (total_frames - (i + 1))

        print(f"[{i+1:04d}/{total_frames:04d}] {format_time(frame_time)} (Total {format_time(time.time()-start_time)}) (Remain {format_time(remain)})")
        save_progress(progress_file, i + 2)

    # Final flush if any remaining
    if batch_queue:
        flush_batch()

    print(f"\n✓ Upscaling completed: {total_processed}/{total_frames} frames")



    # Check if we have enough upscaled frames
    upscaled_frames = list(upscaled_dir.glob("thumb*.png"))
    if len(upscaled_frames) == 0:
        print("❌ No upscaled frames found!")
        return False

    # 4. Create Final Video
    print(f"\nStep 4: Creating final video...")

    # Use original framerate
    print(f"✓ Using framerate: {framerate} fps")

    # Temporary lossless video
    temp_video = project_dir / "temp_lossless.mkv"
    final_video = project_dir / f"{video_name}_upscaled_{MODEL_MODE}.mp4"

    try:
        # Step 1: Create video from upscaled frames
        cmd_video = [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "info",
            "-framerate", framerate,
            "-i", str(upscaled_dir / "thumb%04d.png"),
            "-c:v", "libx264",
            "-preset", "veryslow",
            "-crf", "0",  # Losless and huge
            "-pix_fmt", "yuv420p",
            str(temp_video)
        ]

        print("Creating video from frames...")
        subprocess.run(cmd_video, check=True)

        # Step 2: Add audio if available
        if audio_path.exists():
            cmd_final = [
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "info",
                "-i", str(temp_video),
                "-i", str(audio_path),
                "-c:v", "copy",
                "-c:a", "aac", "-b:a", "192k",
                "-shortest",  # Match video duration
                str(final_video)
            ]
            print("Adding audio to video...")
        else:
            cmd_final = [
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "info",
                "-i", str(temp_video),
                "-c:v", "copy",
                str(final_video)
            ]
            print("Creating final video without audio...")
        subprocess.run(cmd_final, check=True)

        # Cleanup
        temp_video.unlink(missing_ok=True)
        # Save options to file
        options_path = project_dir / f"{video_name}-options.txt"
        with open(options_path, "w") as f:
            f.write(f"Model: {MODEL_MODE}\n")
            f.write(f"Scale: {SCALE_FACTOR}\n")
    except subprocess.CalledProcessError as e:
        print(f"❌ Failed to create video: {e}")
        return False
    # Move source video to processed folder
    PROCESSED_DIR = OUTPUT_BASE_DIR / "processed_videos"
    PROCESSED_DIR.mkdir(exist_ok=True)
    try:
        source_processed = PROCESSED_DIR / video_path.name
        shutil.move(str(video_path), str(source_processed))
        print(f"✓ Moved source video to: {source_processed}")
    except Exception as e:
        print(f"⚠️  Could not move source video: {e}")
    print(f"\n{'='*60}")
    print(f"✅ COMPLETE: {final_video.absolute()}")  # Full path
    print(f"    Size: {final_video.stat().st_size // (1024*1024)} MB")
    print(f"{'='*60}")
    return True


def main():
    """Main function with continuous monitoring

    New behaviour:
      - Monitor WATER_DIR for mp4 files. When present, run SoraWatermarkCleaner
        (using the provided venv) and copy the cleaned result into INPUT_DIR.
      - Continue to monitor INPUT_DIR and process upscaling as before.

    The program does not attempt to process watermark removal and upscaling in parallel;
    operations are performed sequentially in the main loop.
    """
    # Setup signal handler
    signal.signal(signal.SIGINT, signal_handler)
    # Create directories
    INPUT_DIR.mkdir(exist_ok=True)
    OUTPUT_BASE_DIR.mkdir(exist_ok=True)
    WATER_DIR.mkdir(exist_ok=True)
    RESULTS_WATER_DIR.mkdir(exist_ok=True)

    print(f"{'='*60}")
    print(f"Video Upscaling Processor (with watermark-cleaning monitor)")
    print(f"{'='*60}")
    print(f"Input directory: {INPUT_DIR.absolute()}")
    print(f"Output directory: {OUTPUT_BASE_DIR.absolute()}")
    print(f"Water-remove directory: {WATER_DIR.absolute()}")
    print(f"Results-water directory: {RESULTS_WATER_DIR.absolute()}")
    print(f"Upscayl path: {UPSCALE_BIN}")
    print(f"Model: {MODEL_MODE}")
    print(f"Sora CLI dir: {SORA_CLI_DIR}")
    print(f"Sora venv activate: {SORA_VENV_ACTIVATE}")
    print(f"{'='*60}\n")
    # Check required tools
    if not check_tools():
        sys.exit(1)
    print("Monitoring for MP4 files... (Ctrl+C to stop)")
    print("Place MP4 files in:", INPUT_DIR.absolute())
    print("Place MP4 files to be cleaned in:", WATER_DIR.absolute())
    print("Indicator is ", num_of_video, "/", num_of_video_total)
    print("-" * 60)
    processed_videos = set()
    processed_waters = set()
    try:
        while not shutdown_flag:
            # FIRST: Check water_remove folder and process any new files sequentially
            water_files = list(WATER_DIR.glob("*.mp4"))
            for w in water_files:
                if shutdown_flag:
                    break
                water_id = w.stem
                if water_id in processed_waters:
                    continue
                print(f"🔎 Found water-remove file: {w.name}")
                try:
                    ok = process_water_file(w)
                    if ok:
                        processed_waters.add(water_id)
                        print(f"✓ Watermark removal successful for: {w.name}")
                    else:
                        print(f"⚠️  Watermark removal failed or incomplete for: {w.name}")
                except Exception as e:
                    print(f"❌ Error running watermark removal for {w.name}: {e}")
                if shutdown_flag:
                    break

            # SECOND: Process input_videos for upscaling as before
            mp4_files = list(INPUT_DIR.glob("*.mp4"))
            for video in mp4_files:
                if shutdown_flag:
                    break
                # Check if already processed
                video_id = video.stem
                if video_id in processed_videos:
                    continue
                # Check if final output exists
                final_output = OUTPUT_BASE_DIR / video_id / f"{video_id}_upscaled_{MODEL_MODE}.mp4"
                if final_output.exists():
                    print(f"✅ Skipping (already processed): {video.name}")
                    processed_videos.add(video_id)
                    continue
                # Check if processing was started but not finished
                project_dir = OUTPUT_BASE_DIR / video_id
                if project_dir.exists():
                    print(f"🔄 RESUMING: {video.name}")
                else:
                    print(f"🆕 NEW: {video.name}")
                try:
                    # process_video prints all the details itself
                    success = process_video(video)
                    if success:
                        processed_videos.add(video_id)
                        print(f"✓ Finished processing: {video.name}")
                        print(f"✓ Using model: {MODEL_MODE }")
                    else:
                        print(f"⚠️  Processing incomplete: {video.name}")
                except Exception as e:
                    print(f"❌ Error processing {video.name}: {e}")
                if shutdown_flag:
                    break

            # Sleep if nothing to do
            if not mp4_files and not water_files:
                time.sleep(10)
            else:
                time.sleep(5)
    except KeyboardInterrupt:
        print("\n\n⚠️  Process interrupted by user")
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
    finally:
        print("\n" + "="*60)
        print("Video processor stopped")
        print("="*60)


if __name__ == "__main__":
    main()
