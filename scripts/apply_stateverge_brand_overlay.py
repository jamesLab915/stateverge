#!/usr/bin/env python3
import argparse, subprocess, shlex
from pathlib import Path

def run(cmd):
    print("RUN:", " ".join(shlex.quote(str(x)) for x in cmd))
    subprocess.check_call(cmd)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--location", default="HOBOKEN")
    ap.add_argument("--subtitle", default="NEW YORK HARBOR")
    args = ap.parse_args()

    inp = Path(args.input)
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)

    draw = (
        "drawbox=x=30:y=30:w=230:h=64:color=black@0.35:t=fill,"
        "drawbox=x=30:y=30:w=58:h=58:color=white@0.25:t=2,"
        "drawtext=text='SV':x=42:y=36:fontsize=30:fontcolor=white:font='Montserrat':borderw=1,"
        "drawtext=text='NYC':x=39:y=67:fontsize=13:fontcolor=red:font='Montserrat',"
        "drawtext=text='STATEVERGE':x=100:y=38:fontsize=20:fontcolor=white:font='Montserrat',"
        "drawtext=text='NYC':x=100:y=62:fontsize=18:fontcolor=white:font='Montserrat',"
        "drawtext=text='LIVE AMBIENT':x=w-260:y=42:fontsize=20:fontcolor=white:font='Montserrat',"
        "drawbox=x=w-36:y=45:w=12:h=12:color=red@1:t=fill,"
        "drawbox=x=40:y=h-120:w=470:h=72:color=black@0.42:t=fill,"
        "drawbox=x=60:y=h-98:w=84:h=38:color=red@0.92:t=fill,"
        "drawtext=text='LIVE':x=78:y=h-91:fontsize=24:fontcolor=white:font='Montserrat',"
        f"drawtext=text='{args.location} → MANHATTAN':x=165:y=h-100:fontsize=22:fontcolor=white:font='Montserrat',"
        f"drawtext=text='{args.subtitle}':x=165:y=h-72:fontsize=18:fontcolor=gray:font='Montserrat'"
    )

    cmd = [
        "ffmpeg", "-y",
        "-i", str(inp),
        "-vf", draw,
        "-c:v", "libx264",
        "-preset", "medium",
        "-crf", "18",
        "-c:a", "copy",
        "-movflags", "+faststart",
        str(out)
    ]
    run(cmd)

if __name__ == "__main__":
    main()
