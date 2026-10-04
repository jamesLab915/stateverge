# StateVerge YouTube branding (all channels)

Unified watermark for **every** StateVerge channel: the red 3D ribbon S from the NYC videos, on a transparent background, bottom-right.

| File | Use |
|---|---|
| `stateverge_channel_watermark_800.png` | 800×800 watermark (API: `set_youtube_long_channel_watermark.py --position bottom_right`) |
| `watermark_S_150.png` | 150×150 watermark for manual upload in YouTube Studio (recommended) |
| `watermark_S_150_transparent.png` | same, S itself at 65% opacity |
| `avatar_800.png` | channel avatar 800×800 |
| `banner_2560x1440.png` | channel banner; content stays inside the 1546×423 safe area |
| `channel_description.txt` | channel description (replace `[your email]`) |
| `source/S_master_transparent.png` | the S itself, transparent — master for everything above |
| `source/make_branding.py`, `source/extract_s.py` | scripts that produced these (Pillow + numpy, Montserrat font) |
| `previews/` | previews only, not for upload |

Colours: red `#E50914`, white `#FFFFFF`, grey `#B3B3B3`, dark `#0A0A0A` / navy. Type: Montserrat (STATEVERGE semibold, AMBIENT CINEMA light, wide tracking).

Studio: Customization → Branding → Video watermark → "Entire video". YouTube always places it bottom-right.
