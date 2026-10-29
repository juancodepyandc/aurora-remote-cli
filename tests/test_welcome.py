"""Content outranks artwork at every responsive breakpoint and capability."""
from dataclasses import replace
import pytest
from prompt_toolkit.utils import get_cwidth
from aurora_cli import themes
from aurora_cli.core.capabilities import Capabilities
from aurora_cli.welcome import geometry, render_welcome


@pytest.mark.parametrize('theme',themes.all_themes(),ids=lambda t:t.id)
@pytest.mark.parametrize('width,height',[(56,11),(76,18),(116,18),(196,18),(76,20),(56,20)])
@pytest.mark.parametrize('unicode,color',[(True,True),(True,False),(False,False)])
def test_every_action_survives_compact_full_and_degraded_layouts(theme,width,height,unicode,color):
    caps=Capabilities('linux',True,'truecolor' if color else 'none',unicode,'none',width,height,color,False)
    text,lines=render_welcome(width,height,theme,caps)
    for command in ['/modules','/missions','/theme','/help']:
        assert text.count(command)==1,(theme.id,width,height,command,text)
    assert len(lines)<=height
    assert all(get_cwidth(line)<=width for line in text.splitlines())
    if not unicode:assert text.isascii()
    if not color:assert all('fg:' not in style and 'bg:' not in style for row in lines for style,_ in row)


def test_compositions_change_geometry_with_identical_text_and_palette():
    plans=[geometry(116,20,t.components.composition) for t in themes.all_themes()]
    # Center/gallery share geometry but different art; other compositions vary.
    assert len({repr(p) for p in plans})>=7
    theme=themes.get('otter')
    foreign=replace(theme,id='third-party',components=replace(theme.components,composition='editorial'))
    caps=Capabilities('linux',True,'none',False,'none',80,32,False,False)
    assert render_welcome(76,20,theme,caps)[0] != render_welcome(76,20,foreign,caps)[0]
