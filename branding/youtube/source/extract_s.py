from PIL import Image
import numpy as np
im=np.asarray(Image.open('source/reference_screenshot.png').convert('RGB')).astype(float)
r,g,b=im[...,0],im[...,1],im[...,2]
red=r-np.maximum(g,b)
mask=red>40
mask[:900]=False; mask[1700:]=False
ys,xs=np.where(mask); print(xs.min(),xs.max(),ys.min(),ys.max())
x0,x1,y0,y1=xs.min()-6,xs.max()+7,ys.min()-6,ys.max()+7
c=im[y0:y1,x0:x1]; rc=red[y0:y1,x0:x1]
a=np.clip((rc-12)/50,0,1)
# unblend from navy bg (approx 12,16,26)
bg=np.array([12,16,26.])
col=np.where(a[...,None]>0.02,(c-bg*(1-a[...,None]))/np.maximum(a[...,None],0.02),0)
out=np.dstack([np.clip(col,0,255),a*255]).astype(np.uint8)
Image.fromarray(out,'RGBA').save('S_master.png'); print(out.shape)
