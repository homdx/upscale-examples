#!/usr/bin/python

import subprocess
import re
import time

TARGET_QP = 22.0    # Desired average QP value
QP_TOL = 0.5        # Acceptable tolerance for the target QP
MAX_ITER = 10       # Maximum iterations for the intelligent search

def run_ffmpeg_pass1(bitrate, preset="veryfast", input_file="Dr-var2.avi", output_file="Dr-var2_temp.mp4"):
    """
    Runs a first pass of ffmpeg with the given bitrate and preset.
    Streams output in real time and returns the full output text.
    Note: A temporary output file is created and overwritten.
    """
    command = [
        "ffmpeg",
        "-y",  # Overwrite output
        "-i", input_file,
        "-c:v", "libx265",
        "-preset", preset,
        "-b:v", f"{bitrate}k",
        "-pass", "1",
        "-c:a", "aac",
        "-b:a", "192k",
        output_file
    ]
    print(f"\nRunning first pass at bitrate: {bitrate}k, preset: {preset}")
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    output_lines = []
    while True:
        line = process.stdout.readline()
        if not line:
            break
        print(line, end='')  # Stream to console
        output_lines.append(line)
    process.wait()
    return ''.join(output_lines)

def extract_avg_qp(ffmpeg_output):
    """
    Extracts the 'Avg QP' value from ffmpeg's output using regex.
    Returns the value as a float, or None if not found.
    """
    match = re.search(r"Avg QP:\s*([\d\.]+)", ffmpeg_output)
    if match:
        try:
            return float(match.group(1))
        except ValueError:
            return None
    return None

def measure_qp(bitrate):
    """
    Runs the first pass encoding at the given bitrate and returns the measured Avg QP.
    """
    output = run_ffmpeg_pass1(bitrate)
    qp = extract_avg_qp(output)
    if qp is None:
        print(f"Could not extract QP for bitrate {bitrate}k. Assuming high QP.")
        qp = 999  # Fallback to an arbitrarily high value
    print(f"Measured Avg QP at {bitrate}k: {qp}")
    return qp

def intelligent_bitrate_search(target_qp=TARGET_QP, tol=QP_TOL, max_iter=MAX_ITER):
    """
    Uses an iterative approach with linear interpolation to find a candidate bitrate
    that achieves a QP near the target.
    
    The algorithm:
      1. Measure QP at 4000k (lower bound) and 15000k (initial upper bound).
         (Note: The upper bound is increased if necessary.)
      2. Compute the slope and intercept for linear interpolation.
      3. Predict a candidate bitrate that should yield the target QP.
      4. Measure QP at the candidate bitrate.
      5. Update the search bounds based on whether the candidate's QP is above or below target.
      6. Repeat until the measured QP is within tolerance or maximum iterations are reached.
    
    Returns a tuple (candidate_bitrate, candidate_qp, debug_info) where debug_info
    is a list of dictionaries capturing the state at each iteration.
    """
    debug_info = []
    # Initial measurements
    lower_bitrate = 4000
    qp_lower = measure_qp(lower_bitrate)
    
    # Initialize upper bound
    upper_bitrate = 15000
    qp_upper = measure_qp(upper_bitrate)
    iter_count = 0

    while qp_upper > target_qp and iter_count < max_iter:
        upper_bitrate += 500
        qp_upper = measure_qp(upper_bitrate)
        iter_count += 1
        if iter_count == max_iter and qp_upper > target_qp:
            print("Reached maximum iterations while searching for an upper bound; continuing with current values.")
            break

    print(f"\nInitial bounds:")
    print(f"  Lower: {lower_bitrate}k -> QP: {qp_lower}")
    print(f"  Upper: {upper_bitrate}k -> QP: {qp_upper}")

    candidate_bitrate = None
    candidate_qp = None
    iter_count = 0  # Reset iteration counter for interpolation search

    while iter_count < max_iter:
        # Compute linear interpolation slope between the two bounds.
        if upper_bitrate == lower_bitrate:
            candidate_bitrate = lower_bitrate
            candidate_qp = qp_lower
            break
        
        slope = (qp_upper - qp_lower) / (upper_bitrate - lower_bitrate)
        intercept = qp_lower - slope * lower_bitrate
        
        # Predict candidate bitrate for target QP:
        candidate_bitrate = int((target_qp - intercept) / slope)
        # Clamp candidate bitrate within bounds (with some margin)
        if candidate_bitrate <= lower_bitrate:
            candidate_bitrate = lower_bitrate + 100
        elif candidate_bitrate >= upper_bitrate:
            candidate_bitrate = upper_bitrate - 100
        
        print(f"\nIteration {iter_count+1}:")
        print(f"  Slope: {slope:.4f}, Intercept: {intercept:.4f}")
        print(f"  Predicted bitrate for target QP ({target_qp}) is {candidate_bitrate}k")
        
        candidate_qp = measure_qp(candidate_bitrate)
        # Save debug details for this iteration.
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
            print(f"Candidate bitrate {candidate_bitrate}k achieved QP {candidate_qp} within tolerance ±{tol}.")
            break
        
        if candidate_qp > target_qp:
            # QP too high; quality lower than desired. Increase bitrate.
            lower_bitrate = candidate_bitrate
            qp_lower = candidate_qp
            print(f"QP too high at {candidate_bitrate}k. New lower bound: {lower_bitrate}k (QP: {qp_lower}).")
        else:
            # QP too low; quality is higher than needed. Lower bitrate.
            upper_bitrate = candidate_bitrate
            qp_upper = candidate_qp
            print(f"QP too low at {candidate_bitrate}k. New upper bound: {upper_bitrate}k (QP: {qp_upper}).")
    
    print(f"\nIntelligent search complete. Final candidate bitrate: {candidate_bitrate}k with QP: {candidate_qp}")
    return candidate_bitrate, candidate_qp, debug_info

def run_two_pass_encoding(bitrate, input_file="Dr-var2.avi", output_file="Dr-var2.mp4"):
    """
    Runs a two-pass encoding using the found bitrate.
    First pass uses 'veryslow' for improved quality.
    Second pass uses 'verslow'.
    Streams output to the console and extracts the final Avg QP.
    """
    print("\nStarting two-pass encoding with final preset 'verslow' for Pass 2...\n")
    
    # First pass using 'veryslow'
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
        "Dr-var2_temp.mp4"
    ]
    print("Running Pass 1:")
    process1 = subprocess.Popen(command_pass1, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    while True:
        line = process1.stdout.readline()
        if not line:
            break
        print(line, end='')
    process1.wait()
    
    # Second pass using 'verslow'
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
    print("\nRunning Pass 2:")
    process2 = subprocess.Popen(command_pass2, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    pass2_output_lines = []
    while True:
        line = process2.stdout.readline()
        if not line:
            break
        print(line, end='')
        pass2_output_lines.append(line)
    process2.wait()
    
    pass2_output = ''.join(pass2_output_lines)
    final_avg_qp = extract_avg_qp(pass2_output)
    if final_avg_qp is not None:
        print(f"\nFinal encoded Average QP: {final_avg_qp}")
    else:
        print("\nFinal encoded Average QP could not be extracted.")
    
    return final_avg_qp

def main():
    overall_start = time.time()
    
    print("Starting intelligent bitrate search for target QP =", TARGET_QP)
    search_start = time.time()
    candidate_bitrate, candidate_qp, debug_info = intelligent_bitrate_search()
    search_end = time.time()
    search_time = search_end - search_start
    
    print(f"\nSelected bitrate for final encoding: {candidate_bitrate}k (measured QP: {candidate_qp})")
    print(f"Time taken for bitrate search: {search_time:.2f} seconds")
    
    final_start = time.time()
    final_avg_qp = run_two_pass_encoding(candidate_bitrate)
    final_end = time.time()
    final_time = final_end - final_start
    
    print(f"\nTime taken for final two-pass encoding: {final_time:.2f} seconds")
    overall_end = time.time()
    overall_time = overall_end - overall_start
    print(f"\nTotal process time: {overall_time:.2f} seconds")
    
    # Debug information summary for future improvements
    print("\n--- Debug Information for Interpolation ---")
    for info in debug_info:
        print(f"Iteration {info['iteration']}:")
        print(f"  Lower bound: {info['lower_bitrate']}k (QP: {info['qp_lower']})")
        print(f"  Upper bound: {info['upper_bitrate']}k (QP: {info['qp_upper']})")
        print(f"  Slope: {info['slope']:.4f}, Intercept: {info['intercept']:.4f}")
        print(f"  Candidate bitrate: {info['candidate_bitrate']}k, Candidate QP: {info['candidate_qp']}\n")
    
if __name__ == "__main__":
    main()
