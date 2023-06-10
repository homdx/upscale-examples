#!/bin/bash

start=1
increment=4
end=4081
counter=1

for i in $(seq -f "%04g" $start $increment $end); do
#for VARABLE in {0000..4081} ;

VARIABLE=$i
 ~/bin/old-ffmpeg -i ../vga2/thumb$VARIABLE.png  -filter_complex "drawtext=text='${VARIABLE%.*}':x=(w-text_w)/2:y=(h-text_h)/2-(h-text_h)/320:box=1:boxborderw=2:fontsize=140"  thumb2-$VARIABLE.png -y
    echo $i
    if ((counter % increment == 0)); then
        start=$((start + increment))
    fi

    counter=$((counter + 1))
done
