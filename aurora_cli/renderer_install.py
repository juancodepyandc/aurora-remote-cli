"""Install the matching release renderer, without Cargo or connection config.

Only the public, version-pinned project release is used. Downloads are bounded,
SHA-256 checked, probed, then atomically installed inside this Python environment.
Nothing here runs during normal CLI startup.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
import tempfile

import httpx

from . import __version__

REPOSITORY = 'juancodepyandc/aurora-remote-cli'
MAX_BINARY_BYTES = 32 * 1024 * 1024


def target_triple(system=None, machine=None):
    system = system or platform.system()
    machine = (machine or platform.machine()).lower()
    arch = {'arm64':'aarch64','aarch64':'aarch64','amd64':'x86_64','x86_64':'x86_64'}.get(machine)
    targets = {('Darwin','aarch64'):'aarch64-apple-darwin',
               ('Darwin','x86_64'):'x86_64-apple-darwin',
               ('Linux','x86_64'):'x86_64-unknown-linux-gnu',
               ('Windows','x86_64'):'x86_64-pc-windows-msvc'}
    try:
        return targets[(system,arch)]
    except KeyError:
        raise RuntimeError(f'Binaire natif non distribué pour {system}/{machine}. Interface Python disponible.') from None


def managed_binary(prefix=None):
    name = 'aurora-native.exe' if os.name=='nt' else 'aurora-native'
    return Path(prefix or sys.prefix)/'libexec'/'jobia'/__version__/name


def asset_name(target):
    suffix = '.exe' if target.endswith('windows-msvc') else ''
    return f'aurora-native-v{__version__}-{target}{suffix}'


def probe(path):
    try:
        result = subprocess.run([str(path),'--version'],capture_output=True,text=True,timeout=5,check=False)
    except (OSError,subprocess.SubprocessError) as exc:
        raise RuntimeError('Le renderer téléchargé ne peut pas démarrer sur ce système.') from exc
    if result.returncode or result.stdout.strip()!=f'aurora-native {__version__} protocol 1':
        raise RuntimeError('La version du renderer ne correspond pas au CLI installé.')


def _download(client, url, limit):
    with client.stream('GET',url) as response:
        response.raise_for_status()
        content=bytearray()
        for chunk in response.iter_bytes():
            content.extend(chunk)
            if len(content)>limit:
                raise RuntimeError('Téléchargement du renderer trop volumineux.')
        return bytes(content)


def install_native(*, client=None, destination=None, target=None, verify=probe):
    target = target or target_triple()
    name = asset_name(target)
    destination = Path(destination or managed_binary())
    receipt = destination.with_name(destination.name+'.sha256')
    if destination.is_file() and receipt.is_file():
        expected = receipt.read_text(encoding='ascii').strip()
        if re.fullmatch('[0-9a-f]{64}',expected) and hashlib.sha256(destination.read_bytes()).hexdigest()==expected:
            verify(destination)
            return destination
    if client is None:
        with httpx.Client(timeout=30,follow_redirects=True,headers={'User-Agent':f'jobia/{__version__}'}) as session:
            return install_native(client=session,destination=destination,target=target,verify=verify)
    base = f'https://github.com/{REPOSITORY}/releases/download/v{__version__}/'
    try:
        checksum = _download(client,base+name+'.sha256',256).decode('ascii').strip().split()
        if len(checksum)!=2 or not re.fullmatch('[0-9a-f]{64}',checksum[0]) or checksum[1]!=name:
            raise RuntimeError('Empreinte de distribution du renderer invalide.')
        payload = _download(client,base+name,MAX_BINARY_BYTES)
        if hashlib.sha256(payload).hexdigest()!=checksum[0]:
            raise RuntimeError('Empreinte SHA-256 du renderer incorrecte ; installation refusée.')
    except (httpx.HTTPError,UnicodeError) as exc:
        raise RuntimeError(f'Binaire natif v{__version__} indisponible pour {target}. '
                           'Vérifiez le réseau puis relancez jobia renderer install ; interface Python disponible.') from exc
    destination.parent.mkdir(parents=True,exist_ok=True)
    temporary=None
    try:
        with tempfile.NamedTemporaryFile(dir=destination.parent,prefix='.aurora-',suffix=destination.suffix,delete=False) as handle:
            temporary=Path(handle.name)
            handle.write(payload)
        temporary.chmod(0o755)
        verify(temporary)
        temporary.replace(destination)
        receipt.write_text(checksum[0]+'\n',encoding='ascii')
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return destination


def main():
    try:
        print(install_native())
    except (OSError,RuntimeError) as exc:
        print(str(exc),file=sys.stderr)
        return 1
    return 0


if __name__=='__main__':
    raise SystemExit(main())
