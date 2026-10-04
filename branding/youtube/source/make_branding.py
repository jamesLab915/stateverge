from PIL import Image, ImageDraw, ImageFont, ImageFilter
import random
S=Image.open('S_master.png')
def font(w,size):
    f=ImageFont.truetype('Montserrat.ttf',size); f.set_variation_by_axes([w]); return f
def bg(W,H,city=True,seed=7):
    im=Image.new('RGB',(W,H)); d=ImageDraw.Draw(im)
    for y in range(H):  # navy → slightly lighter, red tint at bottom
        t=y/H; c=(int(10+8*t+18*max(0,t-.8)/.2),int(12+12*t),int(20+18*t))
        d.line([(0,y),(W,y)],fill=c)
    if city:
        rnd=random.Random(seed); lay=Image.new('RGBA',(W,H)); ld=ImageDraw.Draw(lay)
        x=int(W*.05)
        while x<W*.95:
            bw=rnd.randint(int(W*.03),int(W*.07)); bh=rnd.randint(int(H*.12),int(H*.38))
            ld.rectangle([x,H-bh,x+bw,H],fill=(32,38,56,150))
            for wy in range(H-bh+8,H,10): ld.point((x+bw//2,wy),fill=(70,80,100,120))
            x+=bw+rnd.randint(4,int(W*.02))
        im.paste(lay,(0,0),lay)
    return im
def put_S(im,h,cx,cy):
    s=S.resize((round(S.width*h/S.height),h),Image.LANCZOS)
    glow=Image.new('RGBA',im.size); g=Image.new('RGBA',s.size,(229,9,20,90)); glow.paste(g,(cx-s.width//2,cy-h//2),s)
    glow=glow.filter(ImageFilter.GaussianBlur(h//10)); im.paste(glow,(0,0),glow)
    im.paste(s,(cx-s.width//2,cy-h//2),s); return s.width
def text_c(d,cx,y,txt,f,fill,spacing=0):
    w=sum(d.textlength(ch,font=f) for ch in txt)+spacing*(len(txt)-1); x=cx-w/2
    for ch in txt: d.text((x,y),ch,font=f,fill=fill); x+=d.textlength(ch,font=f)+spacing
# avatar 800: S only (circle crop), small wordmark would be illegible
av=bg(800,800,city=False); put_S(av,470,400,395); av.save('avatar_800.png')
# banner 2560x1440, content inside 1546x423 safe area (centre)
bn=bg(2560,1440,seed=3); cx,cy=1280,720
sw=put_S(bn,360,0,0) if False else None
d=ImageDraw.Draw(bn)
f1,f2=font(600,150),font(300,52)
w1=d.textlength('STATEVERGE',font=f1); Sh=330; Sw=round(S.width*Sh/S.height); gap=70
total=Sw+gap+w1; x0=cx-total/2
put_S(bn,Sh,int(x0+Sw/2),cy)
d=ImageDraw.Draw(bn)
d.text((x0+Sw+gap,cy-120),'STATEVERGE',font=f1,fill='#FFFFFF')
text_c(d,x0+Sw+gap+w1/2,cy+65,'AMBIENT CINEMA',f2,'#B3B3B3',spacing=14)
bn.save('banner_2560x1440.png')
# watermark 150 transparent: red S, full + semi-transparent
for name,op in [('watermark_S_150.png',1.0),('watermark_S_150_transparent.png',0.65)]:
    wm=Image.new('RGBA',(150,150)); s=S.resize((round(S.width*144/S.height),144),Image.LANCZOS)
    if op<1: a=s.getchannel('A').point(lambda v:int(v*op)); s.putalpha(a)
    wm.paste(s,((150-s.width)//2,3),s); wm.save(name)
Image.open('S_master.png').save('S_master_transparent.png')
# previews
p=bn.copy(); pd=ImageDraw.Draw(p); pd.rectangle([507,508,2053,931],outline='#00FF88',width=4); p.resize((1280,720)).save('preview_banner_safe_area.png')
m=Image.new('RGB',(800,800),'#888'); c=Image.new('L',(800,800)); ImageDraw.Draw(c).ellipse([0,0,799,799],fill=255); m.paste(av,(0,0),c); m.save('preview_avatar_circle.png')
fr=bg(1280,720,seed=11).convert('RGBA'); w=Image.open('watermark_S_150_transparent.png').resize((90,90)); fr.paste(w,(1280-90-24,720-90-24),w); fr.save('preview_watermark.png')
