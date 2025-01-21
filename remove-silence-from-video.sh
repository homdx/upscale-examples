#!/bin/bash

#Remove silence -50db for 0.05 sec
input="source.mp4"
output="output.mp4"
stats_file="silence_stats.txt"

# Step 1: Detect silences and save to stats file
ffmpeg -i "$input" -af silencedetect=n=-50dB:d=0.05 -f null - 2> "$stats_file"

# Step 2: Parse silence_stats.txt to extract silent segments
silent_segments=()
while read -r line; do
    if [[ $line == *"silence_start"* ]]; then
        start_time=$(echo "$line" | grep -oP '(?<=silence_start: )[^ ]+')
    elif [[ $line == *"silence_end"* ]]; then
        end_time=$(echo "$line" | grep -oP '(?<=silence_end: )[^ ]+')
        silent_segments+=("$start_time,$end_time")
    fi
done < "$stats_file"

# Step 3: Generate trim filter to remove silent segments
filter_complex=""
prev_end=0
segment_id=0
for segment in "${silent_segments[@]}"; do
    IFS=',' read -r start end <<< "$segment"
    if (( $(echo "$start > $prev_end" | bc -l) )); then
        filter_complex+="[0:v]trim=start=$prev_end:end=$start,setpts=PTS-STARTPTS[v$segment_id];"
        filter_complex+="[0:a]atrim=start=$prev_end:end=$start,asetpts=PTS-STARTPTS[a$segment_id];"
        segment_id=$((segment_id + 1))
    fi
    prev_end=$end
done

# Add the last segment
duration=$(ffprobe -i "$input" -show_entries format=duration -v quiet -of csv="p=0")
if (( $(echo "$prev_end < $duration" | bc -l) )); then
    filter_complex+="[0:v]trim=start=$prev_end:end=$duration,setpts=PTS-STARTPTS[v$segment_id];"
    filter_complex+="[0:a]atrim=start=$prev_end:end=$duration,asetpts=PTS-STARTPTS[a$segment_id];"
    segment_id=$((segment_id + 1))
fi

# Combine all trimmed segments
map_filter=""
for i in $(seq 0 $((segment_id - 1))); do
    map_filter+="[v$i][a$i]"
done

# Final FFmpeg command
ffmpeg -i "$input" -filter_complex "$filter_complex${map_filter}concat=n=$segment_id:v=1:a=1[outv][outa]" -map "[outv]" -map "[outa]" "$output"
