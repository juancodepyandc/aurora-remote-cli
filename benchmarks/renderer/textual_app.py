import json, os, time
from textual.app import App, ComposeResult
from textual.widgets import Static, Input
from textual.screen import Screen

_original_update=Screen._on_timer_update
def measured_update(self):
    start=time.perf_counter()
    result=_original_update(self)
    if hasattr(self.app,"times"):
        self.app.times.append((time.perf_counter()-start)*1000)
    return result
Screen._on_timer_update=measured_update

class Spike(App):
    CSS='''Screen {background: #101218; color: #e1e8ee;} #banner {height:4; color:#40e0c2;} #body {height:1fr; border:solid #556777;} Input {height:3; border:solid #556777;} #foot {height:1;}'''
    BINDINGS=[('ctrl+q','quit','Quit')]
    def __init__(self):
        super().__init__(); self.count=0; self.times=[]; self.input_value=""
    def compose(self)->ComposeResult:
        yield Static('AURORA   ◌   REMOTE STUDIO\nMachine demo.local · Session spike · Latence non mesurée',id='banner')
        yield Static('',id='body')
        yield Input(placeholder='Votre demande',id='prompt')
        yield Static('FRAME 0000   Entrée : envoyer · Ctrl+Q : quitter',id='foot')
    def on_mount(self):
        self.query_one('#prompt',Input).focus()
        self.start=time.monotonic()
        self.set_timer(3,self.stream_start)
        self.set_timer(9,self.exit)
    def stream_start(self):
        self.stream_time=time.monotonic()
        self.timer=self.set_interval(1/60,self.tick)
    def tick(self):
        if time.monotonic()-self.stream_time>=3:
            self.timer.stop(); return
        self.count+=1
        self.query_one('#banner',Static).update(f'AURORA   {["◌","◔","◑","◕"][self.count%4]}   REMOTE STUDIO\nMachine demo.local · Session spike · Latence non mesurée')
        self.query_one('#body',Static).update('\n'.join(f'{i:03}  Aurora > Vérification du projet, observation reçue.' for i in range(max(0,self.count-20),self.count)))
        self.query_one('#foot',Static).update(f'FRAME {self.count:04}   Entrée : envoyer · Ctrl+Q : quitter')
    def on_input_changed(self,event):
        self.input_value=event.value

app=Spike()
app.run()
with open(os.environ['SPIKE_METRICS'],'w') as f: json.dump({'frame_ms':app.times,'frames':len(app.times),'input':app.input_value},f)
