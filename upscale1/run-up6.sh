#!/bin/bash

#Export to png images from video: ffmpeg -i test.mp4 thumb%04d.png

export numfile="6"
export num="$numfile.txt"

start1=$(cat $num)
#Removing leading zeroes from a variable
#start=${start1##+(0)}
#echo Removing leading zeroes from a variable $start1 is now is $start
# sed removes leading zeroes from stdin
start=$(echo $start1 | sed 's/^0*//')
echo Removing leading zeroes from a variable $start1 is now is $start

#echo $start is
#exit
#start=318
echo will be start from $start file name
increment=1
end=4899
counter=1
upscale="/home/user/Project/squashfs-root/resources"
# Windows: upscale="/c/upscale20024/resources"
modelmode="ultrasharp-4x"
# ultramix-balanced-4x
#Extract AppImage with command line: --appimage-extract
sourcesfolder="/mnt/srcsfolder"
# ultramix-balanced-4x
resultsfolder="/mnt/resultsfolder"
# Windows: sourcesfolder="c:\\srcfolder"
# Windows: resultsfolder="c:\\resultsfolder"
mkdir -pv $resultsfolder
#param1=$1
#if [ -z "$param1" ]; then
#    echo "No command-line argument provided."
#    export start=1
#else
#    echo "The value of the first command-line argument is: $param1"
#    export start=$param
#fi
# Number of the zeroes in seq
for i in $(seq -f "%04g" $start $increment $end); do
#    echo $i
    f=thumb$i.png
    echo $f
    runcmd="$upscale/bin/upscayl-bin  -i $f -o $resultsfolder/$f -m $upscale/models -n $modelmode -f png -g 0"
    # Windows: runcmd="$upscale/bin/upscayl-bin.exe  -i $sourcesfolder\\$f -o $resultsfolder\\$f -m $upscale\\models -n $modelmode -f png -g 0"
    #echo $runcmd >$numfile-tmp.txt
    #runcmd="go run test6.go --num $numfile"
    time $runcmd
    echo $runcmd >6-tmp.txt
    if [ $? -eq 0 ]; then
    numsave=$(echo $i | sed 's/^0*//')
    echo $numsave >$num
    echo sequesfull done $i and saved $numsave from $start
    date
    else
        exit 1
    fi

    echo $runcmd
    date
    if ((counter % 6 == 0)); then
        start=$((start + increment))
    fi

    counter=$((counter + 1))
done
