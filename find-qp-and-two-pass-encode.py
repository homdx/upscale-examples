#!/usr/bin/python3

import subprocess
import re
import time
import argparse
import os
import sys

TARGET_QP = 22.0    # Desired average QP value
QP_TOL = 0.5        # Acceptable tolerance for the target QP
MAX_ITER = 10       # Maximum iterations for the intelligent search
MAX_ATTEMPTS = 3    # Maximum attempts for final pass check before proceeding

# Global flag indicating whether to use QSV for search/final encode.
USE_QSV = False

# ---------------- Test QSV Function ----------------

def test_qsv():
    """Test if QSV hardware acceleration is available using a short nullsrc test."""
    try:
        cmd = [
            "ffmpeg",
            "-init_hw_device", "qsv=hw",
            "-filter_hw_device", "hw",
            "-hwaccel", "qsv",
            "-v", "error",
            "-f", "lavfi",
            "-i", "nullsrc=s=128x128",
            "-t", "1",
            "-f", "null", "-"
        ]
        result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, timeout=15)
        return result.returncode == 0
    except subprocess.TimeoutExpired:
        log_message("QSV test timed out.")
        return False

# ---------------- Helper Functions for Progress ----------------

def get_duration(input_file):
    """Return the duration of the input file in seconds using ffprobe."""
    cmd = [
        "ffprobe",
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        input_file
    ]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        return float(result.stdout.strip())
    except Exception:
        return None

def hms_to_seconds(hms):
    """Convert a time string in HH:MM:SS.xx format to seconds."""
    parts = hms.split(":")
    if len(parts) == 3:
        h, m, s = parts
        return int(h)*3600 + int(m)*60 + float(s)
    return 0.0

def seconds_to_hms(seconds):
    """Convert seconds to a string in HH:MM:SS.xx format."""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h:02d}:{m:02d}:{s:05.2f}"

def monitor_ffmpeg_output(process, total_duration, start_time):
    """
    Monitors ffmpeg output from the given process.
    For each output line it:
      - Searches for a timestamp (time=HH:MM:SS.xx)
      - Captures per-frame QP values from a "q=" field.
    It calculates the wall-clock elapsed time from start_time and, by comparing
    successive progress updates, computes an instantaneous encoding speed. The
    remaining wall time is then estimated as:
         remain_wall = (total_duration - current_secs) / current_speed
    The progress bar is updated on the same line and includes:
         percent complete, current encoding time, estimated remaining wall time,
         and the running average QP.
    Returns a tuple (output_lines, last_qp) where last_qp is the final QP 
    (either a summary "Avg QP:" if found or the running average).
    """
    output_lines = []
    time_pattern = re.compile(r"time=(\d{2}:\d{2}:\d{2}\.\d{2})")
    summary_qp_pattern = re.compile(r"Avg QP:\s*([\d\.]+)")
    per_frame_qp_pattern = re.compile(r"\bq=([\d\.\-]+)")
    
    last_summary_qp = None
    qp_values = []  # accumulate per-frame q values

    last_progress_secs = 0.0
    last_wall_time = start_time
    current_speed = 1.0  # default speed: real time

    while True:
        line = process.stdout.readline()
        if not line:
            break

        m_summary = summary_qp_pattern.search(line)
        if m_summary:
            last_summary_qp = m_summary.group(1)

        m_frame = per_frame_qp_pattern.search(line)
        if m_frame:
            try:
                qp_val = float(m_frame.group(1))
                qp_values.append(qp_val)
            except ValueError:
                pass

        m_time = time_pattern.search(line)
        if m_time and total_duration:
            current_time_str = m_time.group(1)
            current_secs = hms_to_seconds(current_time_str)
            percent = (current_secs / total_duration) * 100

            now = time.time()
            delta_wall = now - last_wall_time
            delta_progress = current_secs - last_progress_secs
            if delta_wall > 0 and delta_progress > 0:
                current_speed = delta_progress / delta_wall
            last_progress_secs = current_secs
            last_wall_time = now

            remain_wall = (total_duration - current_secs) / current_speed if current_speed > 0 else (total_duration - current_secs)
            running_avg_qp = sum(qp_values)/len(qp_values) if qp_values else 0
            progress_bar = (f"Progress: {percent:6.2f}% | "
                            f"Current: {seconds_to_hms(current_secs)} | "
                            f"Remain: {seconds_to_hms(remain_wall)}")
            progress_bar += f" | Avg QP: {running_avg_qp:.2f}"
            print("\r" + " " * 80, end="\r")
            print(progress_bar, end="", flush=True)
        else:
            print("\r" + " " * 80, end="\r")
            print(line, end="")

        output_lines.append(line)
    print()
    if last_summary_qp is not None:
        return output_lines, last_summary_qp
    elif qp_values:
        return output_lines, sum(qp_values)/len(qp_values)
    else:
        return output_lines, None

def log_message(message):
    print(message)

# ---------------- CPU-based Functions ----------------

def run_ffmpeg_pass1(input_file, bitrate, preset="veryfast"):
    """
    Runs a first pass (CPU) encode using libx265.
    Displays a progress bar with real remaining time and running average QP.
    Returns a tuple (output_text, last_qp).
    """
    command = [
        "ffmpeg",
        "-y",
        "-i", input_file,
        "-c:v", "libx265",
        "-preset", preset,
        "-b:v", f"{bitrate}k",
        "-pass", "1",
        "-c:a", "aac",
        "-b:a", "192k",
        "-f", "null", "/dev/null"
    ]
    log_message(f"\nRunning CPU pass at bitrate: {bitrate}k, preset: {preset}")
    total_duration = get_duration(input_file)
    start_time = time.time()
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    output_lines, last_qp = monitor_ffmpeg_output(process, total_duration, start_time)
    process.wait()
    elapsed = time.time() - start_time
    log_message(f"\nTime taken for CPU ffmpeg run: {elapsed:.2f} seconds")
    return ''.join(output_lines), last_qp

# ---------------- QSV-based Functions ----------------

def run_ffmpeg_pass1_qsv(input_file, bitrate, preset="veryfast", encoder="h264_qsv"):
    """
    Runs a first pass using QSV with the given encoder.
    Displays a progress bar with real remaining time and running average QP.
    Returns a tuple (output_text, last_qp).
    """
    command = [
        "ffmpeg",
        "-init_hw_device", "qsv=hw",
        "-filter_hw_device", "hw",
        "-y",
        "-i", input_file,
        "-c:v", encoder,
        "-preset:v", preset,
        "-b:v", f"{bitrate}k",
        "-pass", "1",
        "-c:a", "aac",
        "-b:a", "192k",
        "-f", "null", "/dev/null"
    ]
    log_message(f"\nRunning QSV pass at bitrate: {bitrate}k, preset: {preset}, encoder: {encoder}")
    total_duration = get_duration(input_file)
    start_time = time.time()
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    output_lines, last_qp = monitor_ffmpeg_output(process, total_duration, start_time)
    process.wait()
    elapsed = time.time() - start_time
    log_message(f"\nTime taken for QSV ffmpeg run: {elapsed:.2f} seconds")
    return ''.join(output_lines), last_qp

def extract_avg_qp(ffmpeg_output):
    """
    Extracts the final 'Avg QP:' value from the entire ffmpeg output.
    Returns the value as a float, or None if not found.
    """
    m = re.search(r"Avg QP:\s*([\d\.]+)", ffmpeg_output)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            return None
    return None

def measure_qp(input_file, bitrate, preset="veryfast"):
    """
    Measures QP by running a veryfast encode.
    Uses QSV if USE_QSV is True; otherwise uses CPU.
    Returns the measured QP.
    """
    if USE_QSV:
        output, last_qp = run_ffmpeg_pass1_qsv(input_file, bitrate, preset)
    else:
        output, last_qp = run_ffmpeg_pass1(input_file, bitrate, preset)
    final_qp = extract_avg_qp(output)
    if final_qp is None:
        log_message(f"Could not extract final QP for bitrate {bitrate}k. Using running average QP.")
        try:
            final_qp = float(last_qp)
        except (TypeError, ValueError):
            final_qp = 999
    log_message(f"Measured Avg QP at {bitrate}k: {final_qp}")
    return final_qp

def intelligent_bitrate_search(input_file, target_qp=TARGET_QP, tol=QP_TOL, max_iter=MAX_ITER):
    """
    Iteratively searches for a candidate bitrate that yields a QP near the target.
    Returns (candidate_bitrate, candidate_qp, debug_info).
    """
    debug_info = []
    # -- Fixed bounds: use CPU typical values --
    lower_bitrate = 36698
#35000
    qp_lower = measure_qp(input_file, lower_bitrate, preset="veryfast")
    upper_bitrate = 55000
    qp_upper = measure_qp(input_file, upper_bitrate, preset="veryfast")
    iter_count = 0

    while qp_upper > target_qp and iter_count < max_iter:
        upper_bitrate += 500
        qp_upper = measure_qp(input_file, upper_bitrate, preset="veryfast")
        iter_count += 1
        if iter_count == max_iter and qp_upper > target_qp:
            log_message("Reached maximum iterations while searching for an upper bound; continuing with current values.")
            break

    log_message(f"\nInitial bounds:")
    log_message(f"  Lower: {lower_bitrate}k -> QP: {qp_lower}")
    log_message(f"  Upper: {upper_bitrate}k -> QP: {qp_upper}")

    candidate_bitrate = None
    candidate_qp = None
    iter_count = 0

    while iter_count < max_iter:
        if upper_bitrate == lower_bitrate:
            candidate_bitrate = lower_bitrate
            candidate_qp = qp_lower
            break

        slope = (qp_upper - qp_lower) / (upper_bitrate - lower_bitrate)
        intercept = qp_lower - slope * lower_bitrate
        candidate_bitrate = int((target_qp - intercept) / slope)
        if candidate_bitrate <= lower_bitrate:
            candidate_bitrate = lower_bitrate + 100
        elif candidate_bitrate >= upper_bitrate:
            candidate_bitrate = upper_bitrate - 100

        log_message(f"\nIteration {iter_count+1}:")
        log_message(f"  Slope: {slope:.4f}, Intercept: {intercept:.4f}")
        log_message(f"  Predicted bitrate for target QP ({target_qp}) is {candidate_bitrate}k")
        candidate_qp = measure_qp(input_file, candidate_bitrate, preset="veryfast")
        debug_info.append({
            'iteration': iter_count + 1,
            'lower_bitrate': lower_bitrate,
            'qp_lower': qp_lower,
            'upper_bitrate': upper_bitrate,
            'qp_upper': qp_upper,
            'slope': slope,
            'intercept': intercept,
            'candidate_bitrate': candidate_bitrate,
            'candidate_qp': candidate_qp
        })
        iter_count += 1

        if abs(candidate_qp - target_qp) <= tol:
            log_message(f"Candidate bitrate {candidate_bitrate}k achieved QP {candidate_qp} within tolerance ±{tol}.")
            break

        if candidate_qp > target_qp:
            lower_bitrate = candidate_bitrate
            qp_lower = candidate_qp
            log_message(f"QP too high at {candidate_bitrate}k. New lower bound: {lower_bitrate}k (QP: {qp_lower}).")
        else:
            upper_bitrate = candidate_bitrate
            qp_upper = candidate_qp
            log_message(f"QP too low at {candidate_bitrate}k. New upper bound: {upper_bitrate}k (QP: {qp_upper}).")

    log_message(f"\nIntelligent search complete. Final candidate bitrate: {candidate_bitrate}k with QP: {candidate_qp}")
    return candidate_bitrate, candidate_qp, debug_info

def run_two_pass_encoding(input_file, bitrate, output_file):
    """
    Runs CPU two-pass encoding.
    Uses a progress bar in each pass.
    """
    log_message("\nStarting CPU two-pass encoding (Pass 2 with 'verslow')...\n")
    # Pass 1
    command_pass1 = [
        "ffmpeg",
        "-y",
        "-i", input_file,
        "-c:v", "libx265",
        "-preset", "veryslow",
        "-b:v", f"{bitrate}k",
        "-pass", "1",
        "-c:a", "aac",
        "-b:a", "192k",
        "temp_pass1.mp4"
    ]
    log_message("Running Pass 1 (CPU):")
    total_duration = get_duration(input_file)
    start_time = time.time()
    proc1 = subprocess.Popen(command_pass1, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    while True:
        line = proc1.stdout.readline()
        if not line:
            break
        print(line, end="")
    proc1.wait()
    log_message(f"Time taken for Pass 1: {time.time()-start_time:.2f} seconds")

    # Pass 2
    command_pass2 = [
        "ffmpeg",
        "-y",
        "-i", input_file,
        "-c:v", "libx265",
        "-preset", "verslow",
        "-b:v", f"{bitrate}k",
        "-pass", "2",
        "-c:a", "aac",
        "-b:a", "192k",
        output_file
    ]
    log_message("\nRunning Pass 2 (CPU):")
    start_time = time.time()
    proc2 = subprocess.Popen(command_pass2, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    total_duration = get_duration(input_file)
    pass2_lines, last_qp = monitor_ffmpeg_output(proc2, total_duration, start_time)
    proc2.wait()
    log_message(f"Time taken for Pass 2: {time.time()-start_time:.2f} seconds")
    output_text = ''.join(pass2_lines)
    final_qp = extract_avg_qp(output_text)
    if final_qp is None and last_qp is not None:
        try:
            final_qp = float(last_qp)
        except ValueError:
            final_qp = None
    if final_qp is not None:
        log_message(f"\nFinal encoded Average QP (CPU): {final_qp}")
    else:
        log_message("\nFinal encoded Average QP could not be extracted.")
    return final_qp

def run_one_pass_encoding_qsv(input_file, bitrate, output_file, encoder="h264_qsv"):
    """
    Runs one-pass QSV encoding (QSV does not support two-pass).
    Uses a progress bar and copies audio.
    """
    command = [
        "ffmpeg",
        "-init_hw_device", "qsv=hw",
        "-filter_hw_device", "hw",
        "-y",
        "-i", input_file,
        "-c:v", encoder,
        "-preset:v", "veryslow",
        "-b:v", f"{bitrate}k",
        "-c:a", "copy",
        output_file
    ]
    log_message(f"\nRunning one-pass QSV encoding with {encoder} at bitrate {bitrate}k")
    total_duration = get_duration(input_file)
    start_time = time.time()
    proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    output_lines, last_qp = monitor_ffmpeg_output(proc, total_duration, start_time)
    proc.wait()
    log_message(f"\nTime taken for QSV one-pass encoding: {time.time()-start_time:.2f} seconds")
    output_text = ''.join(output_lines)
    final_qp = extract_avg_qp(output_text)
    if final_qp is None and last_qp is not None:
        try:
            final_qp = float(last_qp)
        except ValueError:
            final_qp = None
    log_message(f"One-pass QSV encoding measured QP: {final_qp}")
    return final_qp

def main():
    global USE_QSV
    parser = argparse.ArgumentParser(description='Intelligent bitrate search and encoding with optional QSV support.')
    parser.add_argument('input_file', help='Path to the source input file (e.g., video.mp4)')
    parser.add_argument('--encoder', choices=["cpu", "qsv"], default="cpu",
                        help="Select encoder: 'cpu' (default, using libx265 two-pass) or 'qsv' (one-pass QSV encoding with QSV search if available).")
    args = parser.parse_args()

    input_file = args.input_file
    base, ext = os.path.splitext(input_file)
    output_file = f"{base}-crf{ext}"

    if args.encoder == "qsv" and test_qsv():
        log_message("QSV acceleration is available. Will use QSV for search and final encoding.")
        USE_QSV = True
    else:
        if args.encoder == "qsv":
            log_message("QSV acceleration is NOT available. Falling back to CPU encoding.")
        USE_QSV = False

    overall_start = time.time()
    log_message(f"\nStarting intelligent bitrate search for target QP = {TARGET_QP}")
    search_start = time.time()
    candidate_bitrate, candidate_qp, debug_info = intelligent_bitrate_search(input_file)
    search_time = time.time() - search_start
    log_message(f"\nSelected candidate bitrate: {candidate_bitrate}k (measured QP: {candidate_qp})")
    log_message(f"Time taken for bitrate search: {search_time:.2f} seconds")

    if USE_QSV:
        final_qp = run_one_pass_encoding_qsv(input_file, candidate_bitrate, output_file, encoder="h264_qsv")
        if final_qp is not None and abs(final_qp - TARGET_QP) <= QP_TOL:
            log_message("Final QSV encoding QP is within tolerance. Encoding complete.")
        else:
            log_message("Final QSV encoding QP is not within tolerance. Please adjust parameters and retry.")
            sys.exit(1)
    else:
        final_qp = run_two_pass_encoding(input_file, candidate_bitrate, output_file)

    total_time = time.time() - overall_start
    log_message(f"\nTotal process time: {total_time:.2f} seconds")

    log_message("\n--- Debug Information for Interpolation ---")
    for info in debug_info:
        log_message(f"Iteration {info['iteration']}:")
        log_message(f"  Lower bound: {info['lower_bitrate']}k (QP: {info['qp_lower']})")
        log_message(f"  Upper bound: {info['upper_bitrate']}k (QP: {info['qp_upper']})")
        log_message(f"  Slope: {info['slope']:.4f}, Intercept: {info['intercept']:.4f}")
        log_message(f"  Candidate bitrate: {info['candidate_bitrate']}k, Candidate QP: {info['candidate_qp']}\n")

if __name__ == "__main__":
    main()
