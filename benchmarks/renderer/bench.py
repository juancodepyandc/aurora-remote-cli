"""PTY probe, no model/server contact. Run from this directory with venv Python."""
import codecs,fcntl,json,os,pty,select,statistics,struct,subprocess,sys,termios,time
from pathlib import Path
import psutil,pyte
ROOT=Path(__file__).resolve().parent
COMMANDS={'prompt_toolkit':[sys.executable,str(ROOT/'prompt_app.py')], 'textual':[sys.executable,str(ROOT/'textual_app.py')], 'ratatui':[str(ROOT/'rust/target/release/aurora-render-spike')]}
def percentile(items,p):
    return sorted(items)[min(len(items)-1,round((len(items)-1)*p))] if items else None
def run(name,command,rep):
    out=ROOT/'results';out.mkdir(exist_ok=True)
    master,slave=pty.openpty();fcntl.ioctl(slave,termios.TIOCSWINSZ,struct.pack('HHHH',32,120,0,0))
    metric=out/f'{name}-{rep}-frames.json'
    env={**os.environ,'TERM':'xterm-256color','COLORTERM':'truecolor','LANG':'C.UTF-8','SPIKE_METRICS':str(metric),'PYTHONUNBUFFERED':'1'}
    env.pop('NO_COLOR',None)
    began=time.perf_counter()
    proc=subprocess.Popen(command,stdin=slave,stdout=slave,stderr=slave,cwd=ROOT,env=env,start_new_session=True);os.close(slave)
    ps=psutil.Process(proc.pid);screen=pyte.Screen(120,32);stream=pyte.Stream(screen);decoder=codecs.getincrementaldecoder('utf-8')('replace')
    raw=bytearray();first=None;sent=False;rss=0;cpu={};snapped=False;last_count=None;frame_times=[]
    while proc.poll() is None and time.perf_counter()-began<14:
        now=time.perf_counter()-began
        try:
            rss=max(rss,ps.memory_info().rss)
            for label,t in [('a',1.4),('b',2.8),('c',7.4),('d',8.8),('e',3.6),('f',5.8)]:
                if now>=t and label not in cpu: cpu[label]=(now,sum(ps.cpu_times()[:2]))
        except psutil.NoSuchProcess: pass
        if now>1 and not sent: os.write(master,b'Inspecte ce projet');sent=True
        ready,_,_=select.select([master],[],[],.005)
        if ready:
            try: chunk=os.read(master,65536)
            except OSError:break
            if not chunk:break
            raw.extend(chunk)
            if b'\x1b[6n' in chunk: os.write(master,b'\x1b[1;1R')
            stream.feed(decoder.decode(chunk))
            visible='\n'.join(screen.display)
            if first is None and 'AURORA' in visible: first=(time.perf_counter()-began)*1000
            import re
            m=re.search(r'FRAME (\d+)',visible)
            if m and int(m[1])!=last_count:
                last_count=int(m[1]);frame_times.append((last_count,time.perf_counter()-began))
        if now>7 and not snapped:
            (out/f'{name}-{rep}.txt').write_text('\n'.join(screen.display));snapped=True
    if proc.poll() is None:proc.terminate()
    proc.wait(timeout=2);os.close(master)
    (out/f'{name}-{rep}.ansi').write_bytes(raw)
    own=json.loads(metric.read_text()) if metric.exists() else {}
    idle=[]
    for a,b in [('a','b'),('c','d')]:
        if a in cpu and b in cpu:idle.append(100*(cpu[b][1]-cpu[a][1])/(cpu[b][0]-cpu[a][0]))
    periods=[1000*(b[1]-a[1]) for a,b in zip(frame_times,frame_times[1:]) if b[0]==a[0]+1 and a[0]>0]
    return {'name':name,'rep':rep,'exit':proc.returncode,'first_frame_ms':first,'rss_peak_mib':rss/1048576,'idle_cpu_percent':statistics.mean(idle) if idle else None,'active_cpu_percent':100*(cpu['f'][1]-cpu['e'][1])/(cpu['f'][0]-cpu['e'][0]) if 'e' in cpu and 'f' in cpu else None,'output_bytes':len(raw),'render_p50_ms':percentile(own.get('frame_ms',[]),.5),'render_p95_ms':percentile(own.get('frame_ms',[]),.95),'visible_period_p50_ms':percentile(periods,.5),'visible_period_p95_ms':percentile(periods,.95),'visible_frames':len(frame_times),'input_ok':own.get('input')=='Inspecte ce projet'}
if __name__=='__main__':
    selected=sys.argv[1:] or list(COMMANDS)
    results=[]
    for rep in range(3):
        for name in selected:
            r=run(name,COMMANDS[name],rep);results.append(r);print(json.dumps(r),flush=True)
            (ROOT/'results/metrics.json').write_text(json.dumps(results,indent=2))
