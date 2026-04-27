#!/bin/python3

import os
import time
import subprocess
import sys
import shutil
from pathlib import Path
from datetime import timedelta, datetime   # FIX #1: single import (was duplicated on two lines)
import re
import signal
import concurrent.futures                  # FIX #1: single import (was imported again on line 35)
import json

# [NEW] Import the watermark cleaner function
try:
    from sora_cleaner import process_water_file
except ImportError:
    print("❌ ERROR: sora_cleaner.py not found. Please create it next to this script.")
    sys.exit(1)

# [NEW] Number of threads for indicator processing (default: all cores)
INDICATOR_THREADS = os.cpu_count() or 4

# [NEW] Import the indicator function
try:
    from indicator import add_video_frame_indicator_png
except ImportError:
    print("❌ ERROR: indicator.py not found. Please create it next to this script.")
    sys.exit(1)

try:
    from black_check import is_black_anomaly, generate_black_diagnostics
except ImportError:
    print("❌ ERROR: black_check.py not found. Please create it next to this script.")
    sys.exit(1)

# ================= CONFIGURATION =================
INPUT_DIR        = Path("./input_videos")
OUTPUT_BASE_DIR  = Path("./results")

# Watermark cleaner dirs
# NOTE: Tool paths (venv, CLI dir, CLI script) are now configured inside
# sora_cleaner.py at the top of that file — no longer passed from here.
WATER_DIR        = Path("./water_remove")
RESULTS_WATER_DIR = Path("./results-water")

# ✅ YOUR REAL PATHS - AUTO DETECT OS
if sys.platform == "win32":
    UPSCALE_ROOT = Path("C:/upscale20024/resources")
    UPSCALE_BIN  = UPSCALE_ROOT / "bin" / "upscayl-bin.exe"
else:
    UPSCALE_ROOT = Path("/home/homdx/Progs/squashfs-root/resources")
    UPSCALE_BIN  = UPSCALE_ROOT / "bin" / "upscayl-bin"

MODELS_PATH = UPSCALE_ROOT / "models"  # FIX #1: single assignment (was assigned twice)
MODEL_MODE  = "remacri-4x"             # FIX #1: single assignment (was assigned twice)
#MODEL_MODE = "ultrasharp-4x"
#MODEL_MODE = "high-fidelity-4x"
#MODEL_MODE = "upscayl-standard-4x"
#MODEL_MODE = "ultramix-balanced-4x"
#MODEL_MODE = "4x_NMKD-Siax_200k"
SCALE_FACTOR = "4"

GPU_ID = "0"
num_of_video = 1
num_of_video_total = 4
# ========================

# Global flag for graceful shutdown
shutdown_flag = False

def get_processor_name():

    import platform
    import subprocess
    import time
    import shutil
    system = platform.system()
    
    if system == "Windows":
        try:
            return subprocess.check_output(["wmic", "cpu", "get", "name"], text=True).strip().split('\n')[1].strip()
        except Exception:
            pass
    elif system == "Darwin": # macOS
        try:
            return subprocess.check_output(["sysctl", "-n", "machdep.cpu.brand_string"], text=True).strip()
        except Exception:
            pass
    elif system == "Linux":
        try:
            with open("/proc/cpuinfo") as f:
                for line in f:
                    if "model name" in line:
                        return line.split(":")[1].strip()
        except Exception:
            pass
    return platform.processor()

# Evaluate ONCE globally
CPU_NAME = get_processor_name()
# Set flag: True only if the CPU string contains "i5" and "8600"
DO_BLACK_BOX_CHECK = ("i5" in CPU_NAME and "8600" in CPU_NAME)

print(f"Detected CPU: {CPU_NAME}")
print(f"Black box check enabled: {DO_BLACK_BOX_CHECK}")



def backup_failed_frame(project_dir, frame_idx, source_frame_path, output_frame_path, upscayl_stdout=None, upscayl_stderr=None):
    """
    Create a timestamped debug folder for this failed frame and copy useful artifacts there.
    Returns path to debug folder (Path).
    """
    project_dir = Path(project_dir)
    debug_root = project_dir / "failed_debug"
    debug_root.mkdir(parents=True, exist_ok=True)

    ts = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    folder_name = f"frame_{frame_idx:04d}_{ts}"
    debug_folder = debug_root / folder_name
    debug_folder.mkdir(parents=True, exist_ok=True)

    # Copy source and output (if present)
    try:
        if source_frame_path.exists():
            shutil.copy2(str(source_frame_path), str(debug_folder / f"source_{source_frame_path.name}"))
        if output_frame_path.exists():
            shutil.copy2(str(output_frame_path), str(debug_folder / f"output_{output_frame_path.name}"))
    except Exception as e:
        print(f"⚠️ Error copying images to debug folder: {e}")

    # Save subprocess stdout/stderr if provided
    meta = {
        "frame_index": int(frame_idx),
        "time_utc": ts,
        "source": str(source_frame_path),
        "output": str(output_frame_path)
    }

    if upscayl_stdout is not None:
        meta["upscayl_stdout"] = str(upscayl_stdout)[:10000]
    if upscayl_stderr is not None:
        meta["upscayl_stderr"] = str(upscayl_stderr)[:10000]

    # Save metadata json
    try:
        with open(debug_folder / "meta.json", "w") as mf:
            json.dump(meta, mf, indent=2)
    except Exception as e:
        print(f"⚠️ Error writing meta.json: {e}")

    # Run diagnostics (mask + overlay + json) from black_check
    try:
        # Pass the source frame, output frame, and the folder where it should save
        diag = generate_black_diagnostics(source_frame_path, output_frame_path, debug_folder)

        # merge diag into meta file for convenience
        try:
            meta.update({"diagnostics": diag})
            with open(debug_folder / "meta.json", "w") as mf:
                json.dump(meta, mf, indent=2)
        except Exception:
            pass
    except Exception as e:
        print(f"⚠️ Error generating diagnostics: {e}")

    # (DO NOT use .unlink() here. Leave the original file alone!)
    
    print(f"🗂️  Backed up failed frame to: {debug_folder}")
    return debug_folder

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
        if not batch_queue:
            return

        print(f" Processing batch of {len(batch_queue)} existing frames...")

        # Map futures to their frame index for correct ordering in logs
        future_to_index = {}

        with concurrent.futures.ProcessPoolExecutor(max_workers=INDICATOR_THREADS) as executor:
            for item in batch_queue:
                f_obj, idx_0 = item
                out_f = upscaled_dir / f_obj.name
                # Submit task
                fut = executor.submit(process_indicator_task, (out_f, idx_0 + 1, total_frames, num_of_video, num_of_video_total))
                future_to_index[fut] = idx_0

            # Process as they complete (Live Updates)
            for future in concurrent.futures.as_completed(future_to_index):
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

                # --- STATS CALCULATION ---
                total_elapsed = time.time() - start_time

                if process_times:
                    recent = sorted(process_times[-20:])
                    median = recent[len(recent)//2]
                else:
                    median = 0

                # [FIX] Adjust median for parallel throughput
                # If 8 threads each take 22s, we actually finish 1 frame every 22/8 = 2.75s
                effective_frame_time = median / max(1, INDICATOR_THREADS)

                remaining_frames = total_frames - (idx_0 + 1)
                remain_time = effective_frame_time * remaining_frames

                # Print verbose status
                print(f"[{idx_0+1:04d}/{total_frames:04d}] Fragment: {format_time(duration)} | Median: {format_time(median)} | Total: {format_time(total_elapsed)} | Remain: {format_time(remain_time)}")

                # Save progress frequently
                save_progress(progress_file, idx_0 + 2)

        # Clear queue after all are done
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



#########
        frame_start = time.time()
        max_retries = 2
        retry_count = 0
        
        # Default to False. We only set it to True if we ACTUALLY detect a black box.
        is_anomaly = False 
        upscayl_failed = False

        while retry_count < max_retries:
            if shutdown_flag: break
            is_anomaly = False # Reset on each retry
            upscayl_failed = False

            try:
                # 1. Run the upscaler (Standard GPU call)
                res = subprocess.run(cmd_upscayl, capture_output=True, encoding="utf-8", text=True, timeout=300)
                
                # 2. Fallback to CPU if GPU returns error code
                if res.returncode != 0:
                    cmd_cpu = cmd_upscayl.copy(); cmd_cpu[-1] = "-1"
                    res = subprocess.run(cmd_cpu, capture_output=True, encoding="utf-8", text=True, timeout=600)

                # 3. Check for the Black Box anomaly ONLY if on Intel i5 8600
                if DO_BLACK_BOX_CHECK:
                    detected, boxes = is_black_anomaly(frame, output_frame, min_size=(50, 50))
                    if detected:
                        is_anomaly = True
                
                # If no anomaly AND Upscayl succeeded, we are good!
                if not is_anomaly:
                    break # SUCCESS: Exit the retry loop
                
                # 4. If we are here, a black box anomaly was found
                retry_count += 1
                if retry_count < max_retries:
                    print(f"⚠️ Anomaly in {output_frame.name}. Pausing 20 sec before retry {retry_count}/{max_retries}...")
                    for _ in range(20):
                        if shutdown_flag: break
                        time.sleep(1)
                
            except Exception as e:
                print(f"⚠️ Upscayl error: {e}")
                upscayl_failed = True
                retry_count += 1

        # 5. Final check: If still broken after all retries, skip it
        if (is_anomaly or upscayl_failed) and not shutdown_flag:
            reason = "FAILED_ANOMALY" if is_anomaly else "FAILED_UPSCAYL_ERROR"
            log_msg = f"{reason}: {output_frame.name} in project {video_name}"
            print(f"❌ {log_msg}. Backing up artifacts for debugging and skipping to next frame.")
            # Append a concise log line
            with open(OUTPUT_BASE_DIR / "failed_frames.txt", "a") as f:
                f.write(f"{log_msg}\n")

            debug_folder = None

            # Backup source + output + diagnostics (only for this failed frame)
            try:
                # source frame path is 'frame' (original in frames_dir) and output_frame is the upscaled
                debug_folder = backup_failed_frame(project_dir, i + 1, frame, output_frame)
            except Exception as e:
                print(f"⚠️ Could not backup failed frame artifacts: {e}")

            # Keep original output in place; optionally create an extra debug copy
            try:
                if debug_folder is not None and output_frame.exists():
                    target = debug_folder / f"copied_{output_frame.name}"
                    shutil.copy2(str(output_frame), str(target))
            except Exception as e:
                print(f"⚠️ Error copying broken output frame: {e}")

            continue

        if shutdown_flag:
            return False  # Exit function if user requested shutdown

        # --- Proceed to Indicators and Stats ---
        # (This part only runs if the frame is GOOD)
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
                    # FIX #2 (HIGH): process_water_file now owns its own tool paths.
                    # Signature is (video_path, INPUT_DIR, RESULTS_WATER_DIR) — 3 args only.
                    # Previously 6 args were passed, causing a TypeError at runtime.
                    ok = process_water_file(
                        w,
                        INPUT_DIR,
                        RESULTS_WATER_DIR
                    )
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
