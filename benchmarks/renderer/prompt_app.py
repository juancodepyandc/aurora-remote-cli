import asyncio, json, os, time
from prompt_toolkit.application import Application
from prompt_toolkit.layout import Layout, HSplit, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.widgets import TextArea, Frame
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.styles import Style

count=0
times=[]
begin=0
def banner():
    return [('class:banner',f'AURORA   {["◌","◔","◑","◕"][count%4]}   REMOTE STUDIO\nMachine demo.local · Session spike · Latence non mesurée')]
body=TextArea(read_only=True, scrollbar=False, focusable=False)
prompt=TextArea(multiline=False)
kb=KeyBindings()
@kb.add('c-q')
def quit(event): event.app.exit()
def before(app):
    global begin
    begin=time.perf_counter()
def after(app): times.append((time.perf_counter()-begin)*1000)
app=Application(layout=Layout(HSplit([Window(FormattedTextControl(banner),height=4),Frame(body,title=' Conversation '),Frame(prompt,title=' Votre demande ',height=3),Window(FormattedTextControl(lambda:f'FRAME {count:04}   Entrée : envoyer · Ctrl+Q : quitter'),height=1)]),focused_element=prompt),full_screen=True,key_bindings=kb,style=Style.from_dict({'banner':'#40e0c2'}),before_render=before,after_render=after,min_redraw_interval=0)
async def drive():
    global count
    await asyncio.sleep(3)
    start=time.monotonic()
    deadline=start
    while time.monotonic()-start<3:
        count+=1
        body.text='\n'.join(f'{i:03}  Aurora > Vérification du projet, observation reçue.' for i in range(max(0,count-20),count))
        app.invalidate()
        deadline+=1/60
        await asyncio.sleep(max(0,deadline-time.monotonic()))
    await asyncio.sleep(3)
    app.exit()
async def run():
    task=asyncio.create_task(drive())
    await app.run_async()
    await task
asyncio.run(run())
with open(os.environ['SPIKE_METRICS'],'w') as f: json.dump({'frame_ms':times,'frames':len(times),'input':prompt.text},f)
