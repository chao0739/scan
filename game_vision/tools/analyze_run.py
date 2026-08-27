"""真机日志分析：趟数/用时、STUCK 位置、LOST、按键、检出、P10 事件。用法: python tools/analyze_run.py logs/run_xxx.jsonl"""
import json, sys, collections, statistics as s
L=[json.loads(l) for l in open(sys.argv[1])]
if not L: print('空日志'); sys.exit()
dur=L[-1]['timestamp']-L[0]['timestamp']; fps=len(L)/max(dur,1e-6)
c=collections.Counter(x['ctl'] for x in L)
print('%s: %d 帧 / %.0f s (%.1f fps)  状态=%s'%(sys.argv[1].split('/')[-1],len(L),dur,fps,dict(c)))
print('  LOST %.1f%%  单帧中位 %.1f ms  player_score 中位 %.2f  检出帧 %d (%.0f%%)'%(100*c.get('LOST',0)/len(L),s.median(x['latency_ms'] for x in L),s.median(x['player_score'] for x in L),sum(x['detected'] for x in L),100*sum(x['detected'] for x in L)/len(L)))
xs=[x['player'][0] for x in L if x['player'] and x['ctl']!='LOST']
if xs: print('  玩家 x 范围 %d~%d  (x<450 占 %.0f%%, 500~800 占 %.0f%%, >800 占 %.0f%%)'%(min(xs),max(xs),100*sum(1 for v in xs if v<450)/len(xs),100*sum(1 for v in xs if 500<=v<=800)/len(xs),100*sum(1 for v in xs if v>800)/len(xs)))
# 趟
flips=[]; prev=None
for x in L:
    t=x.get('patrol_target')
    if t and prev and t!=prev: flips.append((x['frame'],x['timestamp'],prev,t,x['player'][0] if x['player'] else None))
    if t: prev=t
print('  掉头 %d 次 -> %d 个来回'%(len(flips),len(flips)//2))
for i,(f,t,a,b,px) in enumerate(flips):
    dt = t-flips[i-1][1] if i else t-L[0]['timestamp']
    print('    帧%5d  %5s->%-5s x=%4s  这趟用时 %5.1fs'%(f,a,b,px,dt))
# stuck
eps=[]; st=None
for i,x in enumerate(L+[None]):
    on = x is not None and x.get('stuck')
    if on and st is None: st=i
    if not on and st is not None:
        eps.append((L[st]['frame'], (i-st)/max(fps,1), L[st]['held'], L[st]['player'][0] if L[st]['player'] else None)); st=None
print('  STUCK %d 次:'%len(eps), ' '.join('f%d(%.1fs %s x=%s)'%e for e in eps[:15]))
hc=collections.Counter(x['held'] for x in L); print('  held 分布', dict(hc))
if any('cmds' in x for x in L): pass
# P10 事件：RECOVER(跳) / GIVEUP(掉头)，以及每次跳跃前后的 x/y（看有没有上台阶或掉下去）
ev=[]
for i,x in enumerate(L):
    if x['ctl'] in ('RECOVER','GIVEUP') and (i==0 or L[i-1]['ctl']!=x['ctl']):
        after=L[min(i+30,len(L)-1)]
        ev.append('%s@f%d x=%s,y=%s -> 1s后 x=%s,y=%s'%(x['ctl'],x['frame'],
                  x['player'][0] if x['player'] else None, x['player'][1] if x['player'] else None,
                  after['player'][0] if after['player'] else None, after['player'][1] if after['player'] else None))
print('  P10 事件 %d 个:'%len(ev)); [print('    '+e) for e in ev[:20]]
ys=[x['player'][1] for x in L if x['player']]
if ys: print('  玩家 y 范围 %d~%d（层变化看这里）'%(min(ys),max(ys)))
