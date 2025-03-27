#!/bin/bash

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
increment=6
increment=1
end=4899
counter=1
upscale="/home/user/Project/squashfs-root/resources"
#Extract AppImage with command line: --appimage-extract
sourcesfolder="/mnt/srcsfolder"
resultsfolder="/mnt/resultsfolder"
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
#    runcmd="$upscale/bin/upscayl-bin  -i $sourcesfolder/$f -o $resultsfolder/$f  -s 4 -m $upscale/models -n realesrgan-x4plus  -f png -g 0"
#  Now is supporting the 2.15 version
    runcmd="$upscale/bin/upscayl-bin  -i $sourcesfolder/$f -o $resultsfolder/$f  -s 4 -m $upscale/models -n high-fidelity-4x  -f png -g 0"
    #echo $runcmd >$numfile-tmp.txt
    #runcmd="go run test6.go --num $numfile"
    time $runcmd
    if [ $? -eq 0 ]; then
    numsave=$(echo $i | sed 's/^0*//')
    echo $numsave >$num
    echo sequesfull done $i / $end and saved $numsave from $start
    date
    else
        exit 1
    fi
    echo $runcmd
    if ((counter % 6 == 0)); then
        start=$((start + increment))
    fi

    counter=$((counter + 1))
done
