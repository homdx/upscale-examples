import shutil
import subprocess
import tempfile
import sys
from pathlib import Path

def process_water_file(video_path, INPUT_DIR, RESULTS_WATER_DIR, SORA_VENV_ACTIVATE, SORA_CLI_DIR, SORA_CLI):
    """Run SoraWatermarkCleaner on a video found in WATER_DIR and copy result to INPUT_DIR.
    """

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

    # 1. Check if cleaned file already exists (in root or processed)
    if cleaned_in_root.exists() or cleaned_in_processed.exists():
        found = cleaned_in_root if cleaned_in_root.exists() else cleaned_in_processed
        print(f"✓ Found already-cleaned file: {found}")

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

    # 2. Run Cleaner
    temp_input_dir = Path(tempfile.mkdtemp(prefix=f"sora_input_{video_abs.stem}_"))
    try:
        tmp_dest = temp_input_dir / video_abs.name
        shutil.copy2(str(video_abs), str(tmp_dest))
        print(f"Copied source to temp input dir: {tmp_dest}")

        # Locate python inside venv
        venv_python = SORA_VENV_ACTIVATE.parent / "python3"
        if not venv_python.exists():
            alt = SORA_VENV_ACTIVATE.parent / "python"
            venv_python = alt if alt.exists() else None

        if venv_python:
            python_cmd = str(venv_python)
            cmd = [python_cmd, SORA_CLI, "-i", str(temp_input_dir), "-o", str(results_abs)]
            use_shell = False
        else:
            shell_cmd = f"source {str(SORA_VENV_ACTIVATE)} && python3 {SORA_CLI} -i '{str(temp_input_dir)}' -o '{str(results_abs)}'"
            cmd = ["/bin/bash", "-lc", shell_cmd]
            use_shell = True

        print("Running watermark cleaner (this may take a while)...")

        # Run process
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
            try:
                log_path = results_abs / f"{video_abs.stem}.sora.log"
                with open(log_path, 'w') as lf:
                    lf.write(full_output)
                print(f"✓ Wrote Sora log: {log_path}")
            except Exception:
                pass
            return False

        # Find produced .mp4 files
        produced_files = list(results_abs.glob("*.mp4"))
        if not produced_files:
            print("⚠️  No .mp4 files found in results-water after run")
            return False

        # Select the correct file
        candidate_by_name = [p for p in produced_files if p.name == cleaned_basename]
        if candidate_by_name:
            chosen = candidate_by_name[0]
        else:
            chosen = sorted(produced_files, key=lambda p: p.stat().st_mtime, reverse=True)[0]

        # Ensure cleaned file is in results-water root
        cleaned_target = results_abs / chosen.name
        if not cleaned_target.exists():
            try:
                shutil.copy2(str(chosen), str(cleaned_target))
                print(f"✓ Copied cleaned file into results-water/: {cleaned_target}")
            except Exception as e:
                print(f"⚠️ Failed to copy cleaned file into results-water/: {e}")
        else:
            print(f"✓ Cleaned file present in results-water/: {cleaned_target}")

        # Copy to input_videos
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

        # Move original source to processed_videos
        try:
            moved_source = processed_dir / video_abs.name
            if video_abs.exists():
                shutil.move(str(video_abs), str(moved_source))
                print(f"✓ Moved original source to processed_videos/: {moved_source}")
        except Exception as e:
            print(f"⚠️ Failed to move source to processed_videos: {e}")

        # Write log
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
        # Cleanup temp dir
        try:
            if temp_input_dir.exists():
                shutil.rmtree(str(temp_input_dir))
                print(f"Removed temp input dir: {temp_input_dir}")
        except Exception as e:
            print(f"⚠️ Failed to remove temp input dir {temp_input_dir}: {e}")
