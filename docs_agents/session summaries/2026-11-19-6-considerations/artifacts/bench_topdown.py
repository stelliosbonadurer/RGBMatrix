import time, numpy as np
W=H=64; L=5
rng=np.random.default_rng(0)
cov=[rng.random((H,W))<0.12 for _ in range(L)]          # ~12% coverage per layer (bars-like)
col=[rng.integers(0,255,(H,W,3),dtype=np.uint8) for _ in range(L)]
calls=0
def set_pixel(x,y,r,g,b):   # stand-in for canvas.SetPixel (real one is Cython, heavier)
    global calls; calls+=1
# Python per-pixel top-down, layers pre-converted to nested lists (best case for pure Python)
covl=[c.tolist() for c in cov]; coll=[c.tolist() for c in col]
def per_pixel():
    for y in range(H):
        for x in range(W):
            for i in range(L):
                if covl[i][y][x]:
                    r,g,b=coll[i][y][x]; set_pixel(x,y,r,g,b); break
            else:
                set_pixel(x,y,0,0,0)
frame=np.zeros((H,W,3),np.uint8); occ=np.zeros((H,W),bool); vis=np.empty((H,W),bool)
def vectorized():
    global occ
    frame.fill(0); occ.fill(False)
    for i in range(L):
        np.logical_and(cov[i], ~occ, out=vis)
        frame[vis]=col[i][vis]
        np.logical_or(occ, vis, out=occ)
def t(f,n):
    f(); s=time.perf_counter()
    for _ in range(n): f()
    return (time.perf_counter()-s)/n*1e3
print(f"python per-pixel top-down ({L} layers, 4096 px): {t(per_pixel,50):.2f} ms/frame")
print(f"numpy top-down mask compose ({L} layers):        {t(vectorized,500):.3f} ms/frame")
