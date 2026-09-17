import re

file_path = "/home/juan/aurora-remote-cli/aurora_cli/interactive.py"
with open(file_path, "r") as f:
    code = f.read()

# Add lexer import and definition
lexer_code = """
    from prompt_toolkit.styles import Style
    from prompt_toolkit.lexers import PygmentsLexer
    from pygments.lexer import RegexLexer, bygroups
    from pygments.token import Keyword, String, Text
    
    class AuroraLexer(RegexLexer):
        tokens = {
            'root': [
                (r'(^/\w+)(\s+)(.*)$', bygroups(Keyword, Text, String)),
                (r'(^/\w+)$', Keyword),
                (r'.', Text),
            ]
        }
    
    # Style cyberpunk / moderne pour le menu déroulant (autocomplétion)
    custom_style = Style.from_dict({
        'completion-menu': 'bg:#1e1e1e #00ffff',
        'completion-menu.completion.current': 'bg:#00ffff #000000 bold',
        'completion-menu.completion': 'bg:#1e1e1e #00aaaa',
        'scrollbar.background': 'bg:#222222',
        'scrollbar.button': 'bg:#00ffff',
        'prompt': '#00ffff bold',
        'keyword': '#ff00ff bold', # Magenta pour /commande
        'string': '#ffff00',       # Jaune pour les arguments
    })

    prompt_session: PromptSession = PromptSession(
        history=FileHistory(history_file),
        completer=COMMAND_COMPLETER,
        style=custom_style,
        lexer=PygmentsLexer(AuroraLexer),
        complete_while_typing=True
    )
"""

old_style_block = """
    from prompt_toolkit.styles import Style
    
    # Style cyberpunk / moderne pour le menu déroulant (autocomplétion)
    custom_style = Style.from_dict({
        'completion-menu': 'bg:#1e1e1e #00ffff',
        'completion-menu.completion.current': 'bg:#00ffff #000000 bold',
        'completion-menu.completion': 'bg:#1e1e1e #00aaaa',
        'scrollbar.background': 'bg:#222222',
        'scrollbar.button': 'bg:#00ffff',
        'prompt': '#00ffff bold',
    })

    prompt_session: PromptSession = PromptSession(
        history=FileHistory(history_file),
        completer=COMMAND_COMPLETER,
        style=custom_style,
        complete_while_typing=True
    )
"""

code = code.replace(old_style_block.strip(), lexer_code.strip())

with open(file_path, "w") as f:
    f.write(code)

print("Prompt patched")
