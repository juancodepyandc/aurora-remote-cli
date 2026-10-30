"""Package the tested host binary and SHA-256 sidecar for a versioned release."""
from pathlib import Path
import hashlib
import os
import shutil
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from aurora_cli import __version__  # noqa: E402


def main():
    target=next(line.partition(': ')[2] for line in subprocess.check_output(['rustc','-vV'],text=True).splitlines() if line.startswith('host: '))
    suffix='.exe' if os.name=='nt' else ''
    source=ROOT/'native/target/release'/('aurora-native'+suffix)
    actual=subprocess.check_output([str(source),'--version'],text=True).strip()
    if actual!=f'aurora-native {__version__} protocol 1':
        raise RuntimeError('Python/native version mismatch: '+actual)
    output=ROOT/'native/dist';output.mkdir(exist_ok=True)
    name=f'aurora-native-v{__version__}-{target}{suffix}'
    shutil.copy2(source,output/name)
    digest=hashlib.sha256(source.read_bytes()).hexdigest()
    (output/(name+'.sha256')).write_text(f'{digest}  {name}\n',encoding='ascii')
    print(name)


if __name__=='__main__':
    main()
