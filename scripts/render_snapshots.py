"""Capture real prompt-toolkit cell layouts and the native renderer's input state.

No model, network, real configuration or history access. PNGs are terminal-cell
rasterizations for inspection, not claims of physical macOS terminal capture.
Run: python scripts/render_snapshots.py --output docs/snapshots/python
"""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import replace
import io
import json
from pathlib import Path
import sys
import subprocess
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def rasterize(cells, path, width, height, background='#0b111b'):
    from PIL import Image, ImageDraw, ImageFont
    font_path = '/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf'
    font = ImageFont.truetype(font_path, 17) if Path(font_path).exists() else ImageFont.load_default()
    cell_width, cell_height = 11, 23
    image = Image.new('RGB', (width*cell_width+32, height*cell_height+32), background)
    draw = ImageDraw.Draw(image)
    for row in cells:
        for cell in row:
            x, y = 16+cell['x']*cell_width, 16+cell['y']*cell_height
            bg = cell.get('bg') or background
            fg = cell.get('fg') or '#e5eef5'
            draw.rectangle((x, y, x+cell_width, y+cell_height), fill=bg)
            draw.text((x, y), cell.get('char', ' '), fill=fg, font=font)
    image.save(path)


async def capture(theme, width, height, scenario, output, *, ascii_only=False, no_color=False):
    from prompt_toolkit.data_structures import Size
    from prompt_toolkit.input.defaults import create_pipe_input
    from prompt_toolkit.output import DummyOutput
    from aurora_cli import config, display, ui
    from aurora_cli.core.capabilities import Capabilities

    class SizedOutput(DummyOutput):
        def get_size(self):
            return Size(rows=height, columns=width)

    class History(list):
        session_id = 'demo-42'

    cfg = {'mode':'remote','default_model':'modele-observe','default_permissions':'SAFE','server_url':'https://pc-maitre.example'}
    caps = Capabilities('linux', True, 'none' if no_color else 'truecolor', not ascii_only,
                        'none', width, height, not no_color, False)
    with patch.object(config, 'get', side_effect=lambda key, default=None:cfg.get(key, default)), \
         patch.object(config, 'resolve_server_url', return_value=cfg['server_url']), \
         patch.object(config, 'is_configured', return_value=True), create_pipe_input() as pipe:
        display.bind(theme=theme, caps=caps)
        app = ui.WorkspaceApp(history=History(), input=pipe, output=SizedOutput())
        async def discover():
            pass
        app.discover = discover
        app._active_target = 'pc-maitre.example'
        app.connection, app.latency_ms, app.health = 'connecté', 24, 'Diagnostic de démonstration'
        if scenario != 'welcome':
            app.state = ui.MissionView(id='mis_demo', goal='Inspecter le projet puis vérifier les tests', status='running', started=time.monotonic())
            app.event({'type':'plan','steps':['Inspecter le projet','Exécuter les tests'],'criteria':['Tests réussis']})
            app.event({'type':'tool_start','tool':'read_file'})
            app.transcript = '## Vous\nInspecte ce projet et vérifie les tests.\n\n## Aurora\nLe plan est prêt. Je commence par le fichier d’entrée.\n\n```python\ndef ready():\n    return True\n```\n'
            app.partial = 'Lecture de la configuration en cours…'
            if scenario == 'offline':
                app.event({'type':'reconnecting','attempt':2,'message':'Réseau interrompu ; curseur conservé'})
            elif scenario == 'error':
                app.event({'type':'error','message':'Accès refusé : autorisation nécessaire'})
            elif scenario == 'permission':
                app.event({'type':'permission_request','message':'Écrire un fichier : confirmation explicite nécessaire.'})
            elif scenario == 'complete':
                app.event({'type':'mission_complete','status':'completed','result':'## Vérification terminée\nLes tests demandés ont réussi.','verification':{'verified':['Tests réussis']}})
        app.prompt.text = '' if scenario == 'welcome' else 'Explique le résultat'
        app.refresh_body()
        running = asyncio.create_task(app.run())
        await asyncio.sleep(.075)
        screen = app.app.renderer._last_screen
        cells = []
        for y in range(height):
            row = []
            for x in range(width):
                cell = screen.data_buffer[y][x]
                attrs = app.app._merged_style.get_attrs_for_style_str(cell.style)
                fg = '#'+attrs.color if len(attrs.color or '') == 6 else None
                bg = '#'+attrs.bgcolor if len(attrs.bgcolor or '') == 6 else None
                if attrs.reverse:
                    fg, bg = bg, fg
                row.append({'x':x,'y':y,'char':cell.char,'fg':fg,'bg':bg})
            cells.append(row)
        name = f'{theme.id}-{width}-{scenario}' + ('-ascii' if ascii_only else '') + ('-no-color' if no_color else '')
        output.mkdir(parents=True, exist_ok=True)
        (output/(name+'.txt')).write_text('\n'.join(''.join(c['char'] for c in row).rstrip() for row in cells)+'\n',encoding='utf-8')
        rasterize(cells,output/(name+'.png'),width,height,getattr(theme.palette,'surface','#0b111b'))
        try:
            from aurora_cli.render_server import snapshot
            (output/(name+'.json')).write_text(json.dumps(snapshot(app),ensure_ascii=False,separators=(',',':'))+'\n')
        except ImportError:
            pass
        app.app.exit()
        await running
        if getattr(app,'_refresh_handle',None):
            app._refresh_handle.cancel()


async def main(args):
    sys.path.insert(0,str(args.source))
    from aurora_cli import themes
    selected = [themes.get(args.theme)] if args.theme else themes.all_themes()
    for theme in selected:
        for width in [60,80,120,200]:
            await capture(theme,width,32,'streaming',args.output)
        for scenario in ['welcome','offline','error','permission','complete']:
            await capture(theme,80,32,scenario,args.output)
        await capture(theme,60,24,'welcome',args.output)
        await capture(theme,60,24,'streaming',args.output,ascii_only=True,no_color=True)
    if args.native:
        native_output = args.output/'native'
        native_output.mkdir(exist_ok=True)
        for source in sorted(args.output.glob('*.json')):
            width = int(source.stem.split('-')[1])
            height = 24 if '-ascii' in source.stem or '-60-welcome' in source.stem else 32
            cells_path = native_output/(source.stem+'.cells.json')
            captured = subprocess.run([str(args.native.resolve()),'--snapshot',str(source),
                                       str(width),str(height),'--cells',str(cells_path)],
                                      check=True,capture_output=True,text=True)
            (native_output/(source.stem+'.txt')).write_text(captured.stdout,encoding='utf-8')
            raw = json.loads(cells_path.read_text())
            state = json.loads(source.read_text())['state']
            background = state['theme']['palette']['surface']
            def color(value, default):
                return value if value.startswith('#') else default
            cells = [[{'x':x,'y':y,'char':cell['symbol'],
                       'fg':color(cell['fg'],'#e5eef5'),'bg':color(cell['bg'],background)}
                      for x,cell in enumerate(row)] for y,row in enumerate(raw['rows'])]
            rasterize(cells,native_output/(source.stem+'.png'),width,height,background)
            cells_path.unlink()  # Reproducible, large intermediate cell arrays.
    links = ''.join(f'<figure><figcaption>{p.stem}</figcaption><img loading="lazy" src="{p.name}"></figure>' for p in sorted(args.output.glob('*.png')))
    (args.output/'index.html').write_text('<!doctype html><meta charset="utf-8"><title>Aurora · snapshots</title><style>body{background:#080e16;color:#e5eef5;font:16px system-ui;margin:32px}img{max-width:100%;height:auto}figure{margin:24px 0 48px}figcaption{margin-bottom:12px}</style><h1>Aurora · cellules terminal</h1><p>Snapshots du renderer, données de démonstration. Aucun moteur IA contacté.</p>'+links)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,default=ROOT)
    parser.add_argument('--output',type=Path,default=ROOT/'docs/snapshots/python')
    parser.add_argument('--theme')
    parser.add_argument('--native',type=Path,help='Built aurora-native binary; capture both renderers.')
    asyncio.run(main(parser.parse_args()))
