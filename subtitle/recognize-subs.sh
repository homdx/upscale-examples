#!/bin/bash

#time ffmpeg -i mini_it2.mkv  -vf fps=1,crop=1280:200:680:in_h %04d.png
start=1
increment=1
end=3978
counter=1
subnum=1
odltext=""

for i in $(seq -f "%04g" $start $increment $end); do
#for VARABLE in {0000..4081} ;
VARIABLE=$i
time easyocr -l en -f "$VARIABLE.png" --detail=0 >1.txt

#time tesseract "$VARIABLE.png" output -l eng
#c=$(cat output.txt)
echo --- file $i `cat 1.txt`

#echo $i >>result.srt
secs=$start
#printf '%dh:%dm:%d,000' $((secs/3600)) $((secs%3600/60)) $((secs%60))
a=$(printf '%02d:%02d:%02d\n' $((secs/3600)) $((secs%3600/60)) $((secs%60)))
echo "--- result line: $prev,000 --> $a,000"

b=$(cat 1.txt)


if [ -z "$b" ]
then
      echo "[ - ] var \$b is empty"
      # check if old text not empty
  if [ -z "$oldtext" ]
  then
     echo empty string
  else
    secs=$start
    ((secs--))
     a=$(printf '%02d:%02d:%02d\n' $((secs/3600)) $((secs%3600/60)) $((secs%60)))
     echo "[1> ] flush subtitle $prev -> $a"
     echo "[1> ] $oldtext"
     echo $subnum >>result.txt
     echo "$prev,000 --> $a,000">>result.txt
     echo "$oldtext" >>result.txt
     echo "">>result.txt
     oldtext=""
     ((subnum++))
  fi

else
      echo "[REC] var \$b is NOT empty $b"
#      echo "     [ $b"
#      echo "     [ $c"

#echo $subnum >>result.txt
#echo "$prev,000 --> $a,000">>result.txt
#echo "$b" >>result.txt
#echo "">>result.txt

  if [ -z "$oldtext" ]
  then
    echo empty oldtext $oldtext
    secs=$start
    ((secs--))
    #printf '%dh:%dm:%d,000' $((secs/3600)) $((secs%3600/60)) $((secs%60))
    a=$(printf '%02d:%02d:%02d\n' $((secs/3600)) $((secs%3600/60)) $((secs%60)))
    prev=$a
    oldtext=$b
    echo "[ i ] $a first position"
  else
    echo not empty old text $oldtext

if [ "$b" = "$oldtext" ]; then
    echo "Strings are equal. $oldtext"
else
    echo "[new] Strings are not equal. $b abd $oldtext"
    secs=$start
    ((secs--))
     a=$(printf '%02d:%02d:%02d\n' $((secs/3600)) $((secs%3600/60)) $((secs%60)))
     echo "[2> ] flush subtitle $prev -> $a"
     echo "[2> ] $oldtext"
     echo $subnum >> result.txt
     echo "$prev,000 --> $a,000">>result.txt
     echo "$oldtext" >>result.txt
     echo "">>result.txt
     oldtext=$b
     prev=$a
     ((subnum++))
fi


  fi

#oldtext=$b
#((subnum++))
#prev=$a


fi

#secs=$start
#a=$(printf '%02d:%02d:%02d\n' $((secs/3600)) $((secs%3600/60)) $((secs%60)))
#prev=$a


date
# ~/bin/old-ffmpeg -i ../vga2/thumb$VARIABLE.png  -filter_complex "drawtext=text='${VARIABLE%.*}':x=(w-text_w)/2:y=(h-text_h)/2-(h-text_h)/320:box=1:boxborderw=2:fontsize=140"  thumb2-$VARIABLE.png -y

    echo $i
    if ((counter % increment == 0)); then
        start=$((start + increment))
    fi


#Make file
#1
#00:00:00,000 --> 00:00:02,000
#C'è, benissimo!

    counter=$((counter + 1))
done

    echo last flush
    secs=$start
    ((secs--))
     a=$(printf '%02d:%02d:%02d\n' $((secs/3600)) $((secs%3600/60)) $((secs%60)))
     echo "[2> ] flush subtitle $prev -> $a"
     echo "[2> ] $oldtext"
     echo $subnum >>result.txt
     echo "$prev,000 --> $a,000">>result.txt
     echo "$oldtext" >>result.txt
     echo "">>result.txt
