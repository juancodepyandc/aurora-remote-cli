"""Short-code device approval: the credential is received and saved, never displayed."""
import socket
import time
from urllib.parse import urlsplit
import httpx
from aurora_cli import display


def pair_device(url, *, client_factory=httpx.Client, clock=time.monotonic, sleep=time.sleep):
    parsed=urlsplit(url)
    if parsed.scheme!='https' and parsed.hostname not in {'localhost','127.0.0.1','::1'}:
        raise ValueError('La connexion distante nécessite HTTPS pour protéger son autorisation.')
    with client_factory(timeout=10,follow_redirects=False) as client:
        response=client.post(url+'/api/devices/pair',json={'name':('CLI '+socket.gethostname())[:80],'transport':'cli'})
        response.raise_for_status();pending=response.json()
        if not isinstance(pending,dict) or not isinstance(pending.get('claim'),str) or not isinstance(pending.get('code'),str) or len(pending['code'])!=6 or not pending['code'].isdigit():raise ValueError('Réponse de connexion invalide.')
        deadline=clock()+min(300,max(1,float(pending.get('expires_in',300))))
        display.header('AUTORISATION EN ATTENTE', 'Sur le PC Aurora, saisissez ce code dans la fenêtre de connexion.')
        display.print(display.view.key_value_table('CODE DE CONNEXION', [
            ('Code', pending['code']), ('Validité maximale', f'{int(deadline-clock())} s'),
        ]))
        display.hint('Aucune clé à chercher. La demande peut être refusée sur le PC.')
        disconnected = False
        while clock()<deadline:
            try:
                response=client.post(url+'/api/devices/claim',json={'claim':pending['claim']})
                response.raise_for_status();data=response.json()
            except (httpx.ConnectError,httpx.TimeoutException):
                if not disconnected:
                    display.warning('Connexion interrompue · attente du PC avec le même code.')
                    disconnected = True
                sleep(1);continue  # The same challenge is retained; no second approval request.
            if disconnected:
                display.info('Connexion rétablie · attente de l’autorisation.')
                disconnected = False
            if data.get('connected'):
                credential=data.get('credential')
                if not isinstance(credential,str) or not credential.startswith('aurora_device:') or len(credential)<40:raise ValueError('Autorisation reçue invalide.')
                display.success('Appareil autorisé par le PC.')
                return credential
            sleep(1)
        raise ValueError('Demande expirée ou non validée sur le PC. Relancez la connexion lorsque le PC est disponible.')
