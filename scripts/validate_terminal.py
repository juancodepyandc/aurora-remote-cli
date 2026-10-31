"""Exercise the shipped CLI in a real PTY against a disposable loopback bridge.

Linux/macOS only; needs test tooling pyte and psutil. No model, credential or
user configuration is accessed. The bridge drops an incomplete SSE response.
"""
from __future__ import annotations

import argparse
import codecs
import fcntl
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import pty
import select
import socket
import struct
import subprocess
import sys
import tempfile
import termios
import threading
import time

import psutil
import pyte

ROOT = Path(__file__).resolve().parents[1]


def scenario(renderer, depth, output, *, extended=False, launcher=None, restored=False):
    requests, cursors = [], []
    stopped = threading.Event()
    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'
        def log_message(self,*args):
            pass
        def reply(self,payload):
            data = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header('Content-Type','application/json')
            self.send_header('Content-Length',str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers.get('Content-Length',0))) or '{}')
            requests.append((self.path,payload))
            if self.path.endswith('/stop'):
                stopped.set()
                return self.reply({'ok':True})
            self.reply({'ok':True,'mission_id':'mis_pty','cursor':0})
        def do_GET(self):
            if self.path.endswith('/stream'):
                cursor = self.headers.get('Last-Event-ID')
                cursors.append(cursor)
                if extended and len([p for p,_ in requests if p.endswith('/start')])>1:
                    self.send_response(200)
                    self.send_header('Content-Type','text/event-stream')
                    self.send_header('Connection','close')
                    self.end_headers()
                    self.wfile.write(b'id: 1\ndata: {"type":"token","content":"MISSION EN ATTENTE"}\n\n')
                    self.wfile.flush()
                    if stopped.wait(12):
                        self.wfile.write(b'id: 2\ndata: {"type":"mission_complete","status":"stopped","result":"ARRET CONFIRME"}\n\n')
                        self.wfile.flush()
                    self.close_connection = True
                    return
                events = [(1,{'type':'token','content':'Partie reçue'}),
                          (2,{'type':'mission_complete','status':'completed','result':'CONTROLE TERMINE'})]
                data = ''.join(f'id: {i}\ndata: '+json.dumps(e)+'\n\n' for i,e in events
                               if i>int(cursor or 0) and (len(cursors)>1 or i==1)).encode()
                self.send_response(200)
                self.send_header('Content-Type','text/event-stream')
                self.send_header('Content-Length',str(len(data)+(100 if len(cursors)==1 else 0)))
                self.end_headers()
                self.wfile.write(data)
                self.wfile.flush()
                if len(cursors)==1:
                    self.connection.shutdown(socket.SHUT_RDWR)
                    self.connection.close()
                return
            if self.path.endswith('/status'):
                return self.reply({'ok':True,'id':'mis_pty','status':'running','steps':2,
                                   'plan':['Analyser la demande','Générer le modèle'],
                                   'criteria':['Fichier livré'],'verified':[],'evidence':[]})
            self.reply({'ok':True,'ready':True,'models':[],'missions':[],'checks':[]})
    server = ThreadingHTTPServer(('127.0.0.1',0),Handler)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    with tempfile.TemporaryDirectory(prefix='aurora-terminal-') as tmp:
        temp = Path(tmp)
        cfg = temp/'config';cfg.mkdir()
        settings = {'mode':'remote','server_url':f'http://127.0.0.1:{server.server_port}',
                    'api_key':'fixture-only','default_permissions':'SAFE'}
        (cfg/'config.json').write_text(json.dumps(settings)) # Prevent legacy migration.
        env = {k:v for k,v in os.environ.items() if not k.startswith(('JOBIA_','AURORA_')) and k!='NO_COLOR'}
        env.update(JOBIA_CONFIG_DIR=str(cfg),JOBIA_DATA_DIR=str(temp/'data'),JOBIA_COLOR=depth,
                   JOBIA_ANIM='full',TERM='xterm-256color',COLORTERM='truecolor',LANG='C.UTF-8',
                   PYTHONPATH=str(ROOT),JOBIA_NATIVE_BIN=str(ROOT/'native/target/release/aurora-native'))
        if depth=='none':
            env.update(NO_COLOR='1',JOBIA_UNICODE='0')
        if restored:
            # Use the real persistence API in the same interpreter/package as the
            # launched UI, with a disposable project and no real configuration.
            subprocess.run([sys.executable,'-c',
                "from aurora_cli.core.conversation import Conversation; "
                "h=Conversation.open(); h.append({'role':'user','content':'ANCIENNE DEMANDE CONSERVEE'}); "
                "h.state.update(active_mission_id='mis_saved',pending_request={'request':'Brouillon conserve'}); "
                "h.save()"],cwd=temp,env=env,check=True)
            saved={p:p.read_bytes() for p in (temp/'data').rglob('*.json')}
        master,slave = pty.openpty()
        fcntl.ioctl(slave,termios.TIOCSWINSZ,struct.pack('HHHH',32,80,0,0))
        command = [launcher] if launcher else [sys.executable,'-m','aurora_cli','ui','--renderer',renderer]
        began = time.monotonic()
        proc = subprocess.Popen(command,stdin=slave,stdout=slave,stderr=slave,cwd=temp,env=env,start_new_session=True)
        os.close(slave)
        screen = pyte.Screen(80,32)
        stream = pyte.Stream(screen)
        decoder = codecs.getincrementaldecoder('utf-8')('replace')
        raw = bytearray()
        ps = psutil.Process(proc.pid)
        rss_peak, first = 0, None
        def resources():
            processes = [ps,*ps.children(recursive=True)]
            rss,cpu = 0,0
            for child in processes:
                try:
                    rss+=child.memory_info().rss
                    cpu+=sum(child.cpu_times()[:2])
                except psutil.NoSuchProcess:
                    pass
            return rss,cpu
        def pump(seconds):
            nonlocal rss_peak,first
            until = time.monotonic()+seconds
            while time.monotonic()<until:
                ready,_,_ = select.select([master],[],[],.01)
                if ready:
                    try: data = os.read(master,65536)
                    except OSError: break
                    raw.extend(data)
                    if b'\x1b[6n' in data:
                        os.write(master,b'\x1b[1;1R')
                    stream.feed(decoder.decode(data))
                    if first is None and 'AURORA' in '\n'.join(screen.display).upper():
                        first = (time.monotonic()-began)*1000
                try: rss_peak=max(rss_peak,resources()[0])
                except psutil.NoSuchProcess: pass
        def send(value):
            os.write(master,value)
        def until(predicate,timeout=5):
            limit = time.monotonic()+timeout
            while time.monotonic()<limit:
                pump(.04)
                if predicate():return
            raise AssertionError('Terminal state timed out: '+'\n'.join(screen.display))
        def visible():
            return '\n'.join(screen.display)
        try:
            until(lambda:'connect' in visible())
            pump(1)
            if restored:
                assert '/modules' in visible() and '/theme' in visible(),visible()
                assert 'Mission conserv' in visible() and 'Reprise incompl' in visible(),visible()
                assert 'ANCIENNE DEMANDE' not in visible(),visible()
                assert not requests,requests  # No automatic start, resume or stop.
                assert all(p.read_bytes()==content for p,content in saved.items())
                output.mkdir(parents=True,exist_ok=True)
                (output/f'{renderer}-{depth}-startup.txt').write_text(visible()+'\n')
            start_cpu = resources()[1];start_bytes=len(raw);start_idle=time.monotonic()
            pump(1.5)
            idle_cpu = 100*(resources()[1]-start_cpu)/(time.monotonic()-start_idle)
            idle_bytes = len(raw)-start_bytes
            # Live preview then cancellation must not persist.
            send(b'\x1bOQ');pump(.12);send(b'\x1b[B');pump(.2)
            assert json.loads((cfg/'config.json').read_text()).get('theme','otter')=='otter'
            send(b'\x1b');pump(.3)
            # Explicit command persists; F2 works even during a running mission.
            send(b'/theme glacier\r')
            until(lambda:json.loads((cfg/'config.json').read_text()).get('theme')=='glacier')
            fcntl.ioctl(master,termios.TIOCSWINSZ,struct.pack('HHHH',24,60,0,0))
            screen.resize(24,60);pump(.2)
            send('Vérifie le contrat\r'.encode())
            until(lambda:'CONTROLE TERMINE' in visible(),timeout=8)
            assert len(requests)==1 and requests[0][1]['request']=='Vérifie le contrat'
            assert cursors==['0','1'],cursors
            if extended:
                # Busy presentation commands must preserve the original mission.
                send('Garde cette mission ouverte\r'.encode())
                until(lambda:'MISSION EN ATTENTE' in visible())
                send(b'/theme brutal\r')
                until(lambda:json.loads((cfg/'config.json').read_text()).get('theme')=='brutal')
                send(b'\x1bOQ')
                until(lambda:'imm' in visible())
                send(b'\x03')
                until(stopped.is_set)
                send(b'\x1b')
                until(lambda:'ARRET CONFIRME' in visible())
                assert len([p for p,_ in requests if p.endswith('/start')])==2
                assert len([p for p,_ in requests if p.endswith('/stop')])==1
            text = visible()
            assert 'Session' in text and 'Mission mis_pty' in text
            if depth=='none':
                assert text.isascii()
                assert b'\x1b[38;' not in raw and b'\x1b[48;' not in raw
            if depth=='ansi':
                assert b'38;5;' not in raw and b'48;5;' not in raw
                assert b'38;2;' not in raw and b'48;2;' not in raw
            send(b'\x11')
            until(lambda:proc.poll() is not None)
            assert proc.returncode==0
            result = dict(renderer=renderer,depth=depth,first_frame_ms=round(first or 0,3),
                          idle_cpu_percent=round(idle_cpu,3),idle_output_bytes=idle_bytes,
                          peak_tree_rss_mib=round(rss_peak/1048576,3),exit=proc.returncode,
                          sse_cursors=cursors,mission_posts=sum(p.endswith('/start') for p,_ in requests),
                          stop_posts=sum(p.endswith('/stop') for p,_ in requests),
                          resize=True,theme_preview=True,busy_controls=extended,
                          restored_startup=restored,bare_launcher=bool(launcher))
            output.mkdir(parents=True,exist_ok=True)
            name=f'{renderer}-{depth}'
            (output/(name+'.json')).write_text(json.dumps(result,indent=2)+'\n')
            (output/(name+'.txt')).write_text(text+'\n')
            return result
        finally:
            if proc.poll() is None:
                proc.terminate()
                try:proc.wait(timeout=3)
                except subprocess.TimeoutExpired:proc.kill();proc.wait()
            os.close(master)
            server.shutdown();server.server_close()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--renderer',default='native',choices=['native','python'])
    parser.add_argument('--depth',default='truecolor',choices=['truecolor','color256','ansi','none'])
    parser.add_argument('--output',type=Path,default=ROOT/'docs/validation/terminal')
    parser.add_argument('--source',type=Path,default=ROOT,
                        help='Import root; use an empty directory to exercise the installed wheel and managed binary.')
    parser.add_argument('--extended',action='store_true',help='Also verify theme command and Ctrl+C inside a picker during a mission.')
    parser.add_argument('--launcher',help='Run this installed command without arguments, e.g. jobia.')
    parser.add_argument('--restored',action='store_true',help='Seed a real saved conversation, retained mission and incomplete request before startup.')
    args=parser.parse_args()
    ROOT=args.source.resolve()
    print(json.dumps(scenario(args.renderer,args.depth,args.output,extended=args.extended,
                              launcher=args.launcher,restored=args.restored),ensure_ascii=False))
