#!/bin/bash

export numfile="7"
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
end=4081
counter=1
upscale="/home/user/squashfs-root/resources"
#param1=$1


#!/bin/bash

for i in {4000..9999}; do
    filename="vga2/thumb$i.png"
    if [[ -e $filename ]]; then
        if [[ -s $filename && $(stat -c %s "$filename") -eq 0 ]]; then
            echo "File $filename exists and its size is less than zero bytes"
        fi
    else
        echo the $filename does not exist
        #echo $filename >$numfile

    f=thumb$i.png
    echo $f
    runcmd="$upscale/bin/upscayl-realesrgan  -i /mnt/vga-up/vga/$f -o /mnt/vga-up/vga2/$f  -s 4 -m $upscale/models -n realesrgan-x4plus  -f png -g 0"
    echo $runcmd >$numfile-tmp.txt
    runcmd="go run test6.go --num $numfile"
    time $runcmd
    if [ $? -eq 0 ]; then
    numsave=$(echo $i | sed 's/^0*//')
    echo $numsave >$num
    echo sequesfull done $i and saved $numsave from $start
    date
    #    return 0
    else
        exit 1
    fi
    echo $runcmd

fi
done
