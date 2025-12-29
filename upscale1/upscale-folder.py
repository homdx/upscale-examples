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

# ================= CONFIGURATION =================
INPUT_DIR = Path("./input_videos")
OUTPUT_BASE_DIR = Path("./results")

# ✅ YOUR REAL PATHS
UPSCALE_ROOT = Path("/home/homdx/Progs/squashfs-root/resources")
UPSCALE_BIN = UPSCALE_ROOT / "bin" / "upscayl-bin"
MODELS_PATH = UPSCALE_ROOT / "models"
MODEL_MODE = "ultrasharp-4x"
GPU_ID = "0"
# =================================================

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
    
    print(f"\n{'='*60}")
    print(f"Processing: {video_name}")
    print(f"Source: {video_path}")
    print(f"{'='*60}")

    # Get video info
    total_video_frames = get_total_frames(video_path)
    framerate = get_framerate(video_path)
    print(f"Video info: {total_video_frames} frames @ {framerate} fps")

    # 1. Extract Frames
    print(f"\nStep 1: Extracting frames...")
    frame_pattern = frames_dir / "thumb%04d.png"
    
    existing_frames = len(list(frames_dir.glob("thumb*.png")))
    if existing_frames == 0:
        cmd_extract = [
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-i", str(video_path), str(frame_pattern)
        ]
        try:
            print("Extracting frames (this may take a while)...")
            subprocess.run(cmd_extract, check=True)
            existing_frames = len(list(frames_dir.glob("thumb*.png")))
            print(f"✓ Extracted {existing_frames} frames to: {frames_dir}")
        except subprocess.CalledProcessError as e:
            print(f"❌ Failed to extract frames: {e}")
            return False
    else:
        print(f"✓ Found {existing_frames} existing frames")

    # 2. Extract Audio
    print(f"\nStep 2: Extracting audio...")
    audio_path = project_dir / "audio.aac"
    
    if not audio_path.exists():
        cmd_audio = [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-i", str(video_path), "-c:a", "copy", str(audio_path)
        ]
        try:
            subprocess.run(cmd_audio, check=True)
            print(f"✓ Audio saved: {audio_path}")
        except subprocess.CalledProcessError as e:
            print(f"⚠️ Failed to extract audio: {e}")
            # Continue without audio
    else:
        print("✓ Audio already exists")

    # 3. Upscayl Processing
    print(f"\nStep 3: Upscayl processing ({MODEL_MODE})...")
    
    # Get all frames and sort numerically
    frames = sorted(
        frames_dir.glob("thumb*.png"),
        key=lambda x: extract_frame_number(x.name)
    )
    
    if not frames:
        print("❌ No frames found in frames directory!")
        return False
    
    total_frames = len(frames)
    print(f"Found {total_frames} frames to process")
    
    # Load progress
    start_frame = load_progress(progress_file)
    if start_frame > total_frames:
        start_frame = total_frames
    
    print(f"Starting from frame {start_frame:04d} of {total_frames:04d}")
    print("-" * 50)
    
    # Statistics
    processed_count = 0
    total_processed = 0
    start_time = time.time()
    process_times = []
    
    # Process frames
    for i in range(start_frame - 1, total_frames):
        if shutdown_flag:
            print("\n⚠️  Shutdown detected, saving progress...")
            save_progress(progress_file, i + 1)
            return False
        
        frame = frames[i]
        frame_num = extract_frame_number(frame.name)
        output_frame = upscaled_dir / frame.name
        
        # Skip if already processed
        if output_frame.exists():
            print(f"[{i+1:04d}/{total_frames:04d}] Skipping: {frame.name}")
            total_processed += 1
            # Update progress
            save_progress(progress_file, i + 2)
            continue

        # ✅ FIXED: Save BEFORE upscaling → Ctrl+C retries CURRENT frame
        save_progress(progress_file, i + 1)

        # Build Upscayl command (matching your bash script)
        cmd_upscayl = [
            str(UPSCALE_BIN),
            "-i", str(frame),
            "-o", str(output_frame),
            "-m", str(MODELS_PATH),
            "-n", MODEL_MODE,
            "-f", "png",
            "-g", GPU_ID
        ]
        
        frame_start = time.time()
        
        try:
            # Run Upscayl
            result = subprocess.run(
                cmd_upscayl,
                capture_output=True,
                text=True,
                timeout=300  # 5 minutes per frame
            )
            
            frame_time = time.time() - frame_start
            
            if result.returncode != 0:
                print(f"[{i+1:04d}/{total_frames:04d}] ❌ Failed: {result.stderr[:100]}")
                save_progress(progress_file, i + 1)
                # Continue with next frame instead of breaking
                continue
############            
            # Update statistics
            # Update statistics
            processed_count += 1
            total_processed += 1
            process_times.append(frame_time)  # Keep history

            # ✅ IMPROVED: Use MEDIAN of last 10 frames (or all if <10)
            recent_times = process_times[-10:]  # Last 10 frames
            recent_times.sort()
            median_time = recent_times[len(recent_times)//2]  # Median

            remaining_frames = total_frames - (i + 1)
            remaining_time = median_time * remaining_frames

            current_time_str = format_time(frame_time)
            remain_time_str = format_time(remaining_time)
            total_elapsed = time.time() - start_time
            total_elapsed_str = format_time(total_elapsed)

            print(f"[{i+1:04d}/{total_frames:04d}] {current_time_str} (total {total_elapsed_str}) (remain {remain_time_str}) [median {format_time(median_time)}]")


#            processed_count += 1
#            total_processed += 1
#            process_times.append(frame_time)
            
            # Calculate times
#            avg_time = sum(process_times) / len(process_times) if process_times else 0
#            remaining_frames = total_frames - (i + 1)
#            remaining_time = avg_time * remaining_frames
            
#            current_time_str = format_time(frame_time)
#            remain_time_str = format_time(remaining_time)
#            total_elapsed = time.time() - start_time
#            total_elapsed_str = format_time(total_elapsed)
            
            # Display progress as requested
#            print(f"[{i+1:04d}/{total_frames:04d}] {current_time_str} (total {total_elapsed_str}) (remain {remain_time_str})")
            

#############

            # Save progress
            save_progress(progress_file, i + 2)
            
#        except subprocess.TimeoutExpired:
#            print(f"[{i+1:04d}/{total_frames:04d}] ⏰ Timeout on {frame.name}")
#            save_progress(progress_file, i + 1)
#            # Continue with next frame
#            continue
        except subprocess.TimeoutExpired:
            print(f"[{i+1:04d}/{total_frames:04d}] ⏰ Timeout - retrying with CPU")
            cmd_upscayl_cpu = cmd_upscayl.copy()
            cmd_upscayl_cpu[-1] = "-1"  # CPU fallback
            result = subprocess.run(cmd_upscayl_cpu, timeout=600)  # Longer timeout
            continue

        except Exception as e:
            print(f"[{i+1:04d}/{total_frames:04d}] ⚠️ Error: {str(e)[:100]}")
            save_progress(progress_file, i + 1)
            continue
    
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
    final_video = project_dir / f"{video_name}_upscaled.mp4"
    
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
    print(f"✅ COMPLETE: {final_video}")
    print(f"    Size: {final_video.stat().st_size // (1024*1024)} MB")
    print(f"{'='*60}")
    
    return True

def main():
    """Main function with continuous monitoring"""
    
    # Setup signal handler
    signal.signal(signal.SIGINT, signal_handler)
    
    # Create directories
    INPUT_DIR.mkdir(exist_ok=True)
    OUTPUT_BASE_DIR.mkdir(exist_ok=True)
    
    print(f"{'='*60}")
    print(f"Video Upscaling Processor")
    print(f"{'='*60}")
    print(f"Input directory: {INPUT_DIR.absolute()}")
    print(f"Output directory: {OUTPUT_BASE_DIR.absolute()}")
    print(f"Upscayl path: {UPSCALE_BIN}")
    print(f"Model: {MODEL_MODE}")
    print(f"{'='*60}\n")
    
    # Check required tools
    if not check_tools():
        sys.exit(1)
    
    print("Monitoring for MP4 files... (Ctrl+C to stop)")
    print("Place MP4 files in:", INPUT_DIR.absolute())
    print("-" * 60)
    
    processed_videos = set()
    
    try:
        while not shutdown_flag:
            # Get all MP4 files
            mp4_files = list(INPUT_DIR.glob("*.mp4"))
            
            for video in mp4_files:
                if shutdown_flag:
                    break
                    
                # Check if already processed
                video_id = video.stem
                if video_id in processed_videos:
                    continue
                
                # Check if final output exists
                final_output = OUTPUT_BASE_DIR / video_id / f"{video_id}_upscaled.mp4"
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
                    success = process_video(video)
                    if success:
                        processed_videos.add(video_id)
                        print(f"✓ Finished processing: {video.name}")
                    else:
                        print(f"⚠️  Processing incomplete: {video.name}")
                except Exception as e:
                    print(f"❌ Error processing {video.name}: {e}")
                
                if shutdown_flag:
                    break
            
            # Sleep if no new videos
            if not mp4_files:
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
