"""Responsive welcome composition; no timers, configuration or network reads.

The native renderer mirrors these cell regions in welcome::geometry. Geometry
is intentionally small and is validated at the same compact/full breakpoints.
"""
from __future__ import annotations

from dataclasses import dataclass

from .terminal import safe_text
from .tui_design import fit


@dataclass
class Region:
    x: int
    y: int
    width: int
    height: int


def geometry(width, height, mode):
    w = min(width, 132)
    offset = (width-w)//2
    def rect(x,y,ww,hh):
        return Region(x+offset,y,min(ww,max(0,w-x)),min(hh,max(0,height-y)))
    side = 18 if height<18 else min(38,max(24,w//3))
    art = rect(1,2,side,height-3)
    body = rect(side+3,2,w-side-4,height-2)
    brand = rect(side+3,3,w-side-4,0)
    ornament = rect(1,height-2,side,0)
    eyebrow = rect(1,0,w-2,1)
    if mode in {'reverse','editorial'}:
        art.x = offset+w-side-1
        body.x = brand.x = offset+1
    if mode=="console":
        eyebrow=rect(3,1,w-6,1)
    if height>=18:
        body.y,body.height = 7,height-7
        brand.height,ornament.height = 3,3
        ornament.y=height-3
        if mode in {'center','gallery'}:
            art = rect((w-16)//2,0,16,8)
            eyebrow = rect((w-min(w,42))//2,9,min(w,42),1)
            body = rect((w-min(w,42))//2,10,min(w,42),height-10)
            brand = rect(2,3,(w-20)//2,3)
            ornament = rect((w+20)//2,3,(w-24)//2,4)
        elif mode=='horizon':
            brand = rect(2,2,max(w//2,24),3)
            ornament = rect(w//2+2,1,w//2-4,4)
            art = rect(w-side-1,7,side,height-7)
            body = rect(2,7,w-side-5,height-7)
        elif mode=='editorial':
            brand=rect(1,2,w-2,2)
            body.y,body.height=6,height-6
            ornament=rect(1,4,w-2,1)
            art.y,art.height=7,height-8
        elif mode=='console':
            brand=rect(3,3,w-6,2)
            body=rect(3,6,w-side-7,height-7)
            art=rect(w-side-3,5,side,height-7)
            ornament.height=0
        elif mode=='poster':
            brand=rect(2,2,w-4,4)
            body=rect(2,8,w-side-6,height-8)
            art=rect(w-side-2,8,side,height-9)
            ornament=rect(1,6,w-2,1)
    return dict(art=art,body=body,brand=brand,ornament=ornament,eyebrow=eyebrow)


def render_welcome(width, height, theme, caps):
    """Return the same plain text / lexer fragments contract as render_document."""
    width,height=max(40,width),max(11,height)
    regions=geometry(width,height,theme.components.composition)
    cells=[[('', ' ') for _ in range(width)] for _ in range(height)]
    def style(slot,bold=False):
        return ('fg:'+theme.palette.slot(slot)+' ' if caps.color else '')+('bold' if bold else '')
    def put(region, rows, slot='text', bold=False, gradient=False):
        for dy,line in enumerate(rows[:region.height]):
            # Theme art contains only single-cell glyphs; fit also transliterates ASCII.
            text=fit(line,region.width,caps.unicode)
            ramp = theme.palette.ramp(max(1,len(text))) if gradient and caps.color else []
            for dx,char in enumerate(text):
                x,y=region.x+dx,region.y+dy
                if 0<=x<width and 0<=y<height:
                    cells[y][x]=(('fg:'+ramp[dx]+' bold') if gradient and caps.color else style(slot,bold),char)
    if theme.components.composition=='console':
        horizontal,vertical,corner=('─','│','┌') if caps.unicode else ('-','|','+')
        put(Region(0,0,width,1),[corner+horizontal*(width-2)+('┐' if caps.unicode else '+')],'border')
        put(Region(0,height-1,width,1),[('└' if caps.unicode else '+')+horizontal*(width-2)+('┘' if caps.unicode else '+')],'border')
        for y in range(1,height-1):
            put(Region(0,y,1,1),[vertical],'border')
            put(Region(width-1,y,1,1),[vertical],'border')
    put(regions['eyebrow'],[theme.components.eyebrow],'muted')
    brand=list(theme.components.wordmark)
    if any(len(safe_text(row,unicode=caps.unicode))>regions['brand'].width for row in brand):
        brand=['A U R O R A']
    put(regions['brand'],brand,'primary',True,True)
    put(regions['ornament'],list(theme.components.ornament),'secondary')
    art,palette=theme.components.pixel_art,theme.components.pixel_palette
    region=regions['art']
    source_width=max(map(len,art),default=0)
    if source_width and region.width and region.height:
        ww=min(region.width,region.height*2,38)
        hh=min(region.height,(ww*len(art)+source_width*2-1)//(source_width*2))
        xx=region.x+(region.width-ww)//2
        def pixel(x,y):
            return int(art[y][x],16) if y<len(art) and x<len(art[y]) else 0
        for y in range(hh):
            for x in range(ww):
                a=pixel(x*source_width//ww,y*len(art)//hh)
                b=pixel(x*source_width//ww,(y*2+1)*len(art)//(hh*2))
                if a==b==0:
                    continue
                glyph=('#' if a==b else '+') if not caps.unicode else '▀' if caps.color or b==0 else '▄' if a==0 else '█'
                pixel_style=(f'fg:{palette[a]} bg:{palette[b]}' if caps.color and max(a,b)<len(palette) else '')
                cells[region.y+y][xx+x]=(pixel_style,glyph)
    region=regions['body']
    semantic=[(theme.components.headline,'primary',True),('', 'text',False),
              ('Décrivez le résultat attendu.','text',False),('', 'text',False),
              ('/modules   Explorer','accent',False),('/missions  Reprendre','accent',False),
              ('/theme     Changer d’univers','accent',False),('/help      Commandes','accent',False)]
    # Wrap semantic text instead of truncating it; terminal controls are removed.
    import textwrap
    y=region.y
    for text,slot,bold in semantic:
        for line in textwrap.wrap(safe_text(text,unicode=caps.unicode),width=max(1,region.width),replace_whitespace=False) or ['']:
            put(Region(region.x,y,region.width,1),[line],slot,bold)
            y+=1
    lines=[]
    for row in cells:
        while row and row[-1][1]==' ':
            row.pop()
        fragments=[]
        for st,ch in row:
            if fragments and fragments[-1][0]==st:
                fragments[-1]=(st,fragments[-1][1]+ch)
            else:
                fragments.append((st,ch))
        lines.append(fragments)
    while lines and not lines[-1]:
        lines.pop()
    return '\n'.join(''.join(text for _,text in row) for row in lines),lines
