"""Serve a delivered assembly ZIP on loopback, without extracting its files."""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import os
from pathlib import Path, PurePosixPath
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlsplit, unquote
import webbrowser
import zipfile


def validate_archive(path):
    with zipfile.ZipFile(path) as archive:
        members = archive.infolist()
        if len(members) > 256 or sum(m.file_size for m in members) > 512*1024*1024:
            raise ValueError('Archive trop volumineuse pour le viewer.')
        names = [m.filename for m in members]
        if len(set(names)) != len(names) or any(PurePosixPath(n).is_absolute() or '..' in PurePosixPath(n).parts
                                                or '\\' in n for n in names):
            raise ValueError('Chemins de l’archive invalides.')
        required = {'viewer.html','assembly.json','assembled.glb'}
        if not required <= set(names):
            raise ValueError('Archive sans viewer d’assemblage ; refaire l’export avec le bridge à jour.')
        report = json.loads(archive.read('assembly.json'))
        if report.get('mode') != 'assembly':
            raise ValueError('Archive d’assemblage requise.')
        if archive.testzip() is not None:
            raise ValueError('Archive endommagée.')
        return report


def make_server(path):
    path = Path(path).resolve()
    validate_archive(path)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_GET(self):
            # Only the local origin and known render assets are served; never
            # expose arbitrary user files or bind to the external network.
            allowed_hosts = {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}
            if self.headers.get('Host') not in allowed_hosts:
                self.send_error(403); return
            name = unquote(urlsplit(self.path).path).lstrip('/') or 'viewer.html'
            allowed = name in {'viewer.html','assembly.json','assembled.glb','assembled_textured.glb'} or (
                name.startswith('viewer_assets/') and name.endswith(('.js', '/LICENSE')))
            if not allowed or '..' in PurePosixPath(name).parts or '\\' in name:
                self.send_error(404); return
            try:
                with zipfile.ZipFile(path) as archive:
                    data = archive.read(name)
            except (KeyError, OSError, zipfile.BadZipFile):
                self.send_error(404); return
            self.server.last_request = time.monotonic()
            self.send_response(200)
            self.send_header('Content-Type', 'text/javascript' if name.endswith('.js') else mimetypes.guess_type(name)[0] or 'application/octet-stream')
            self.send_header('Content-Length', str(len(data)))
            self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Cache-Control','no-store')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' blob: data:; connect-src 'self' blob: data:; object-src 'none'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(data)
    server = ThreadingHTTPServer(('127.0.0.1',0), Handler)
    server.daemon_threads = True
    server.last_request = time.monotonic()
    return server


def launch_viewer(archive, *, open_browser=True, appearance='colors', exploded=False):
    report = validate_archive(archive)
    if appearance == 'textured' and not report.get('textured_assembly_available'):
        raise ValueError('Ce maillage ne contient pas de textures ; utiliser la vue par couleurs.')
    with tempfile.TemporaryDirectory(prefix='aurora-viewer-') as temporary:
        ready = Path(temporary)/'ready.json'
        kwargs = {'start_new_session':True} if os.name != 'nt' else {'creationflags':subprocess.CREATE_NEW_PROCESS_GROUP}
        process = subprocess.Popen([sys.executable,'-m','aurora_cli.assembly_viewer',str(Path(archive).resolve()),'--ready',str(ready)],
                                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kwargs)
        deadline = time.monotonic()+15
        while not ready.exists():
            if process.poll() is not None or time.monotonic() > deadline:
                process.terminate()
                raise RuntimeError('Le serveur local du viewer n’a pas démarré.')
            time.sleep(.05)
        url = json.loads(ready.read_text(encoding='utf-8'))['url']
    url += f'?appearance={appearance}&exploded={int(exploded)}'
    opened = False
    if open_browser:
        try:
            opened = webbrowser.open(url)
        except webbrowser.Error:
            pass
    return url, opened


def serve():
    parser = argparse.ArgumentParser()
    parser.add_argument('archive'); parser.add_argument('--ready',required=True)
    args = parser.parse_args()
    server = make_server(args.archive); server.timeout=1
    Path(args.ready).write_text(json.dumps({'url':f'http://127.0.0.1:{server.server_port}/viewer.html'}),encoding='utf-8')
    try:
        # Browser tabs maintain the listener while visible; otherwise expire
        # after 30 idle minutes. Reopen at any time with jobia view3d.
        while time.monotonic()-server.last_request < 1800:
            server.handle_request()
    finally:
        server.server_close()


if __name__ == '__main__':
    serve()
