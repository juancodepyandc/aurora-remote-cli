"""Prepare an existing mesh through the authenticated bridge, no inference."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time

import click

from .bridge import Bridge
from .assembly_viewer import launch_viewer


def prepare_export(client, source, options, output, *, wait_seconds=210):
    source, output = Path(source), Path(output)
    if source.suffix.lower() not in {'.glb', '.stl', '.obj'} or not source.is_file():
        raise ValueError('Fichier GLB, STL ou OBJ requis.')
    if not 0 < source.stat().st_size <= 120*1024*1024:
        raise ValueError('Maillage vide ou supérieur à 120 Mo.')
    if output.exists():
        raise ValueError('Le fichier de sortie existe déjà ; choisir un autre nom.')
    # A pre-dispatch diagnostic can recover an expired saved tunnel. No
    # mission/POST has been accepted yet, so no earlier effect is replayed.
    client.doctor()
    with source.open('rb') as mesh:
        response = client._client.post('/api/3d/engineering', files={'mesh': (source.name, mesh)},
                                       data={'settings': json.dumps(options)}, timeout=60)
    receipt = response.json()
    if not response.is_success or not receipt.get('ok'):
        raise RuntimeError(receipt.get('error', 'Export refusé.'))
    job = receipt['job_id']
    deadline = time.monotonic()+wait_seconds
    while True:
        state = client.get('/api/3d/engineering/'+job)
        if not state.get('ok') or state.get('state') == 'error':
            raise RuntimeError(state.get('error', 'Export échoué.'))
        if state.get('state') == 'done':
            break
        if time.monotonic() >= deadline:
            raise RuntimeError('Suivi interrompu pour '+job+' ; aucun nouvel export lancé automatiquement.')
        time.sleep(0.5)
    path = '/api/3d/engineering/'+job+'/download'
    expected = state.get('archive_sha256')
    if not isinstance(expected, str) or len(expected) != 64:
        raise RuntimeError('Empreinte de l’archive absente ; mettre à jour le bridge.')
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.aurora-export-', dir=output.parent)
    digest = hashlib.sha256()
    try:
        with os.fdopen(fd, 'wb') as stream, client._client.stream('GET', path, timeout=60) as response:
            response.raise_for_status()
            for chunk in response.iter_bytes():
                digest.update(chunk); stream.write(chunk)
            stream.flush(); os.fsync(stream.fileno())
        if digest.hexdigest() != expected:
            raise RuntimeError('L’archive reçue ne correspond pas à son empreinte serveur.')
        # Exclusive destination creation protects an existing user file.
        os.link(temporary, output)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return state.get('report', {})


@click.command('export3d')
@click.argument('mesh', type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option('--variant', type=click.Choice(['geometry','textured','assembly']), default='geometry')
@click.option('--size-mm', type=click.FloatRange(1,2000), default=200, show_default=True,
              help='Plus grande dimension finale ; variante texturée conserve les unités originales.')
@click.option('--profile', type=click.Path(exists=True, dir_okay=False, path_type=Path),
              help='Profil imprimante JSON (name, process, bed_mm et paramètres de raccord).')
@click.option('--axis', type=click.Choice(['auto','x','y','z']), default='auto')
@click.option('--cut-mm', multiple=True, type=click.FloatRange(min=0.01), help='Plan de coupe, répétable.')
@click.option('--output', type=click.Path(dir_okay=False, path_type=Path), default='aurora-3d.zip')
@click.option('--constraints', type=click.Path(exists=True, dir_okay=False, path_type=Path),
              help='JSON : protected_zones_mm et/ou connector_centers_mm, dans les coordonnées finales du modèle.')
@click.option('--viewer', type=click.Choice(['colors','textured']), default='colors',
              help='Aspect du viewer d’assemblage ; textured commence en vue éclatée.')
@click.option('--open/--no-open', 'open_browser', default=True, help='Ouvrir le navigateur après un assemblage ; sinon afficher son URL locale.')
def export3d(mesh, variant, size_mm, profile, axis, cut_mm, output, constraints, viewer, open_browser):
    """Exporter géométrie, texture ou pièces d'assemblage via le bridge."""
    try:
        options = dict(mode=variant, size_mm=size_mm, axis=axis, cuts_mm=list(cut_mm))
        if constraints:
            rules = json.loads(constraints.read_text(encoding='utf-8'))
            if not isinstance(rules, dict) or set(rules)-{'protected_zones_mm','connector_centers_mm'}:
                raise ValueError('Contraintes : protected_zones_mm et connector_centers_mm uniquement.')
            options.update(rules)
        if variant == 'assembly':
            if profile is None:
                raise ValueError('Assemblage : fournir --profile avec le profil imprimante JSON.')
            options['profile'] = json.loads(profile.read_text(encoding='utf-8'))
        with Bridge() as client:
            report = prepare_export(client, mesh, options, output)
        click.echo(f'Archive vérifiée : {output}')
        if variant == 'assembly':
            click.echo(f"{report['piece_count']} pièce(s), {report['pin_count']} pion(s). Ajustement physique à calibrer.")
            url, opened = launch_viewer(output, open_browser=open_browser, appearance=viewer, exploded=viewer=='textured')
            click.echo(f'Viewer : {url}')
            if open_browser and not opened:
                click.echo('Navigateur indisponible : ouvrir cette URL sur cette machine.')
    except Exception as exc:
        raise click.ClickException(str(exc)) from exc


@click.command('view3d')
@click.argument('archive', type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option('--viewer', type=click.Choice(['colors','textured']), default='colors')
@click.option('--exploded', is_flag=True, help='Commencer en vue éclatée.')
@click.option('--open/--no-open', 'open_browser', default=True)
def view3d(archive, viewer, exploded, open_browser):
    """Rouvrir le viewer d'une archive livrée, sans relancer son export."""
    try:
        url, opened = launch_viewer(archive, open_browser=open_browser, appearance=viewer, exploded=exploded or viewer=='textured')
        click.echo(f'Viewer : {url}')
        if open_browser and not opened:
            click.echo('Navigateur indisponible : ouvrir cette URL sur cette machine.')
    except Exception as exc:
        raise click.ClickException(str(exc)) from exc
