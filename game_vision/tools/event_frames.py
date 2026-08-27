"""把日志里的事件（STUCK 起点 / RECOVER / GIVEUP / 掉头 / 指定帧）对应的录像帧矫正后拼成一张图，带标注。
用法: event_frames.py <log.jsonl> <rec.mp4> <out.jpg> [--calib file] [--frames 100,200] [--max 12]"""
import sys, os, json, argparse
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, HERE); os.chdir(HERE)
import cv2, numpy as np
from calibration import Rectifier, load_corners
ap = argparse.ArgumentParser(); ap.add_argument('log'); ap.add_argument('rec'); ap.add_argument('out')
ap.add_argument('--calib', default='calibration/homography.json'); ap.add_argument('--frames', default='')
ap.add_argument('--max', type=int, default=12); a = ap.parse_args()
L = [json.loads(l) for l in open(a.log)]
rect = Rectifier(load_corners(a.calib), 1280, 720)
want = []
if a.frames:
    want = [(int(f), 'frame') for f in a.frames.split(',')]
else:
    prev = None
    for i, x in enumerate(L):
        tag = None
        if x.get('stuck') and not (L[i-1].get('stuck') if i else False): tag = 'STUCK'
        if x['ctl'] in ('RECOVER', 'GIVEUP') and (i == 0 or L[i-1]['ctl'] != x['ctl']): tag = x['ctl']
        t = x.get('patrol_target')
        if t and prev and t != prev: tag = f'FLIP->{t}'
        if t: prev = t
        if tag: want.append((x['frame'], tag))
    want = want[:a.max]
cap = cv2.VideoCapture(a.rec); tiles = []
for f, tag in want:
    cap.set(cv2.CAP_PROP_POS_FRAMES, f); ok, fr = cap.read()
    if not ok: continue
    g = rect(fr); x = L[f] if f < len(L) else {}
    if x.get('player'):
        px, py = map(int, x['player']); cv2.drawMarker(g, (px, py), (255, 0, 255), cv2.MARKER_CROSS, 30, 3)
    if x.get('roi'):
        x1, y1, x2, y2 = x['roi']; cv2.rectangle(g, (x1, y1), (x2, y2), (255, 255, 0), 2)
    txt = f"f{f} {tag} ctl={x.get('ctl')} held={x.get('held')} p={x.get('player')} s={x.get('player_score')} bg={x.get('bg_dx')}"
    cv2.rectangle(g, (0, 0), (1280, 30), (0, 0, 0), -1); cv2.putText(g, txt, (6, 22), 0, 0.7, (0, 255, 255), 2)
    tiles.append(cv2.resize(g, (640, 360)))
if not tiles: print('no frames'); sys.exit()
cols = 2; rows = (len(tiles) + cols - 1) // cols
sheet = np.zeros((rows * 360, cols * 640, 3), np.uint8)
for i, t in enumerate(tiles): sheet[(i//cols)*360:(i//cols+1)*360, (i%cols)*640:(i%cols+1)*640] = t
cv2.imwrite(a.out, sheet); print('wrote', a.out, len(tiles), 'tiles:', [w for w in want[:len(tiles)]])
