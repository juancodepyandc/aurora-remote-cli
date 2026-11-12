"""Short-code device approval: the credential is received and saved, never displayed.

The PC shows a window; the user types the six digits displayed here. The same
flow serves the terminal command (``jobia connect``) and the full-screen
interface (``/connect``), which passes ``on_code``/``on_status`` callbacks and
a ``cancel`` event instead of printing.
"""
from __future__ import annotations

import ipaddress
import socket
import time
from urllib.parse import urlsplit

import httpx

from aurora_cli import display


def _plaintext_allowed(host: str) -> bool:
    """Loopback and private networks may pair over HTTP; the internet may not."""
    if host in {'localhost', '127.0.0.1', '::1'} or host.endswith('.local'):
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.is_private or address.is_loopback or address.is_link_local


def _terminal_code(code: str, seconds: int) -> None:
    display.header('AUTORISATION EN ATTENTE', 'Sur le PC Aurora, saisissez ce code dans la fenêtre de connexion.')
    display.print(display.view.key_value_table('CODE DE CONNEXION', [
        ('Code', code), ('Validité maximale', f'{seconds} s'),
    ]))
    display.hint('Aucune clé à chercher. La demande peut être refusée sur le PC.')


def _terminal_status(kind: str, message: str) -> None:
    {'warning': display.warning, 'success': display.success}.get(kind, display.info)(message)


def pair_device(url, *, client_factory=httpx.Client, clock=time.monotonic, sleep=time.sleep,
                on_code=None, on_status=None, cancel=None):
    """Request approval on the PC and return the credential it grants."""
    parsed = urlsplit(url)
    if parsed.scheme != 'https' and not _plaintext_allowed((parsed.hostname or '').lower()):
        raise ValueError('La connexion distante nécessite HTTPS pour protéger son autorisation.')
    show_code = on_code or _terminal_code
    status = on_status or _terminal_status
    with client_factory(timeout=10, follow_redirects=False) as client:
        response = client.post(url + '/api/devices/pair',
                               json={'name': ('CLI ' + socket.gethostname())[:80], 'transport': 'cli'})
        response.raise_for_status()
        pending = response.json()
        if (not isinstance(pending, dict) or not isinstance(pending.get('claim'), str)
                or not isinstance(pending.get('code'), str) or len(pending['code']) != 6
                or not pending['code'].isdigit()):
            raise ValueError('Réponse de connexion invalide.')
        deadline = clock() + min(300, max(1, float(pending.get('expires_in', 300))))
        show_code(pending['code'], int(deadline - clock()))
        disconnected = False
        while clock() < deadline:
            if cancel is not None and cancel.is_set():
                raise ValueError('Appairage annulé.')
            try:
                response = client.post(url + '/api/devices/claim', json={'claim': pending['claim']})
                response.raise_for_status()
                data = response.json()
            except (httpx.ConnectError, httpx.TimeoutException):
                if not disconnected:
                    status('warning', 'Connexion interrompue · attente du PC avec le même code.')
                    disconnected = True
                sleep(1)
                continue  # The same challenge is retained; no second approval request.
            if disconnected:
                status('info', 'Connexion rétablie · attente de l’autorisation.')
                disconnected = False
            if data.get('connected'):
                credential = data.get('credential')
                if not isinstance(credential, str) or not credential.startswith('aurora_device:') or len(credential) < 40:
                    raise ValueError('Autorisation reçue invalide.')
                status('success', 'Appareil autorisé par le PC.')
                return credential
            sleep(1)
        raise ValueError('Demande expirée ou non validée sur le PC. Relancez la connexion lorsque le PC est disponible.')
