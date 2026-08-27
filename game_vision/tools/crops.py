"""围绕玩家抠 260x170 小图，按帧序列拼条带，看角色在做什么（走/站/被打闪白/攻击）。
用法: python tools/crops.py <log.jsonl> <rec.mp4> <out.jpg> <起始帧> <结束帧> <步长>  [环境变量 CALIB=标定文件，默认 calibration/homography.json]
录像需用 app.py --record 录制（录像第 n 帧 = 日志 frame n）。"""
import sys, os, json
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, HERE); os.chdir(HERE)
import cv2, numpy as np
from calibration import Rectifier, load_corners
log, rec, out, f0, f1, step = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4]), int(sys.argv[5]), int(sys.argv[6])
L=[json.loads(l) for l in open(log)]; rect=Rectifier(load_corners(os.environ.get('CALIB','calibration/homography.json')),1280,720)
cap=cv2.VideoCapture(rec); tiles=[]
for f in range(f0, f1, step):
    cap.set(cv2.CAP_PROP_POS_FRAMES, f); ok, fr = cap.read()
    if not ok: break
    g=rect(fr); x=L[f]; p=x['player'] or (640,360); px,py=int(p[0]),int(p[1])
    x0,y0=max(0,px-130),max(0,py-120); crop=g[y0:y0+170, x0:x0+260].copy()
    crop=cv2.copyMakeBorder(crop,0,170-crop.shape[0],0,260-crop.shape[1],cv2.BORDER_CONSTANT)
    cv2.putText(crop, f"f{f} {x['ctl']}/{x['held']} x={px} bg={x['bg_dx']}", (3,14), 0, 0.42, (0,255,255), 1)
    tiles.append(cv2.resize(crop,(520,340)))
cols=4; rows=(len(tiles)+cols-1)//cols; sheet=np.zeros((rows*340, cols*520,3),np.uint8)
for i,t in enumerate(tiles): sheet[(i//cols)*340:(i//cols+1)*340,(i%cols)*520:(i%cols+1)*520]=t
cv2.imwrite(out, sheet); print('wrote', out, len(tiles))
