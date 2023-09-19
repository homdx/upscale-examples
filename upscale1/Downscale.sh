#!/bin/bash
 
 mkdir converted && time for f in *.avi; do ffmpeg -i "$f"  -sn -c:a libmp3lame -ar 48000 -ab 128k -ac 2 -c:v libxvid      -vtag DIVX -vf "scale=320:-1"  -mbd rd -flags +mv4+aic     -trellis 2 -cmp 2 -subcmp 2 -g 30 -vb 1500k -pass 1 -y "converted/${f%.mp4}.avi"; ffmpeg -i "$f"  -sn -c:a libmp3lame -ar 48000 -ab 128k -ac 2 -c:v libxvid      -vtag DIVX -vf "scale=320:-1"  -mbd rd -flags +mv4+aic     -trellis 2 -cmp 2 -subcmp 2 -g 30 -vb 1500k -pass 2 -y "converted/${f%.mp4}.avi"; done
