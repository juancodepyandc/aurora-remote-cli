"""Read-only, checkpoint-backed verification of a JOBIA 3D delivery.

Not an independent certification or a guarantee about unobserved surfaces.
Missing evidence is never promoted to a successful check.
"""
from __future__ import annotations

import json
from pathlib import Path

from .glb_validation import inspect_glb


def audit_delivery(asset: Path) -> dict:
    from .pipeline import _digest, _visual_contract

    asset = asset.expanduser().resolve()
    checks = {}

    def check(name, action):
        try:
            detail = action()
            checks[name] = dict(status='passed', detail=detail)
        except (OSError, ValueError, TypeError, KeyError, IndexError, AttributeError) as exc:
            checks[name] = dict(status='failed', detail=str(exc))

    def require(condition, message):
        if not condition:
            raise ValueError(message)

    def scoped_file(directory, relative):
        require(isinstance(relative, str) and bool(relative), 'Chemin de preuve absent')
        path = (directory / relative).resolve()
        require(path.is_relative_to(directory.resolve()), 'Preuve hors du dossier de travail')
        require(path.is_file(), f'Preuve absente : {path.name}')
        return path

    def structure():
        require(asset.suffix.casefold() == '.glb', 'Cette vérification concerne les fichiers GLB')
        return inspect_glb(asset, require_textures=True)

    check('structure_et_uv', structure)
    state = checkpoint = None
    for candidate in sorted((asset.parent / '.jobia').glob('*/job.json')):
        try:
            data = json.loads(candidate.read_text(encoding='utf-8'))
            delivered = data.get('delivered') or {}
            matches = (asset.parent / delivered.get('filename', '')).resolve() == asset
        except (OSError, ValueError, TypeError, AttributeError):
            # An unrelated interrupted/corrupt job does not invalidate this
            # asset's evidence. Without attribution it cannot certify it either.
            continue
        if matches:
            if checkpoint is not None:
                checks['checkpoint'] = dict(status='failed', detail='Plusieurs checkpoints revendiquent ce fichier')
            else:
                checkpoint, state = candidate, data
    if state is None or checks.get('checkpoint', {}).get('status') == 'failed':
        for name in ('integrite', 'reference', 'textures_decodees', 'rendus', 'controle_visuel'):
            checks[name] = dict(status='not_run', detail='Aucune preuve JOBIA attribuable à ce fichier')
    else:
        job_dir = checkpoint.parent
        delivered = state['delivered']
        measured = None

        def integrity():
            nonlocal measured
            require(state.get('status') == 'completed', 'Travail non terminé')
            require(_digest(asset) == delivered.get('sha256'), 'Fichier livré modifié')
            require(delivered.get('visual_contract') == _visual_contract(),
                    'Contrôles anciens : réévaluation nécessaire avec le code actuel')
            proof = scoped_file(job_dir, delivered.get('visual_report'))
            require(_digest(proof) == delivered.get('visual_report_sha256'), 'Rapport de contrôle modifié')
            measured = json.loads(proof.read_text(encoding='utf-8'))
            require(measured.get('mesh_sha256') == delivered.get('sha256'),
                    'Rapport non lié au fichier livré')
            return {'asset_sha256': delivered['sha256'], 'report': str(proof)}

        check('integrite', integrity)
        if checks['integrite']['status'] != 'passed':
            for name in ('reference', 'textures_decodees', 'rendus', 'controle_visuel'):
                checks[name] = dict(status='not_run', detail='Intégrité des preuves non établie')
        else:
            def reference():
                accepted = state.get('accepted_reference') or {}
                path = scoped_file(job_dir, accepted.get('filename'))
                require(_digest(path) == accepted.get('sha256') == measured.get('reference_sha256'),
                        'Référence de livraison modifiée ou différente')
                conditioning = scoped_file(job_dir, measured.get('measured_reference'))
                require(_digest(conditioning) == measured.get('measured_reference_sha256'),
                        'Image de conditionnement modifiée')
                return {'source_sha256': accepted['sha256'], 'kind': accepted.get('kind', 'generated_reference')}

            def textures():
                info = measured.get('asset_validation') or {}
                materials = info.get('materials') or []
                require(info.get('pixels_decoded') is True and materials,
                        'Décodage des cartes PBR non établi')
                for material in materials:
                    maps = material.get('maps') or {}
                    require('base_colour' in maps, 'Texture couleur absente')
                    require(all(value.get('decoded') is True for value in maps.values()),
                            'Carte PBR non décodée')
                placement = measured.get('texture_placement') or {}
                require(placement.get('verdict') == 'placed', 'Placement des couleurs non accepté')
                return {'materials': materials, 'placement': placement,
                        'limit': 'Cartes connectées et décodées ; rendu de contrôle albédo, pas une mesure BRDF.'}

            def renders():
                require(measured.get('verdict') == 'plausible', 'Géométrie ou silhouette refusée')
                views, previews = measured.get('views') or [], measured.get('previews') or []
                require(len(views) >= 2 and len(views) == len(previews), 'Vues de contrôle incomplètes')
                hashes = measured.get('previews_sha256') or []
                require(len(hashes) == len(previews), 'Empreintes des rendus absentes')
                for filename, expected in zip(previews, hashes):
                    path = scoped_file(job_dir, filename)
                    require(_digest(path) == expected, 'Rendu de contrôle modifié')
                return {'views': len(views), 'render_mode': measured.get('render_mode')}

            def semantic():
                review = measured.get('semantic_review') or {}
                verdict = review.get('verdict') or {}
                sheet = scoped_file(job_dir, measured.get('contact_sheet'))
                require(_digest(sheet) == review.get('preview_sha256'), 'Planche de contrôle modifiée')
                observations = verdict.get('checks') or []
                require(review.get('status') == 'done' and verdict.get('match') is True
                        and verdict.get('issues') == [] and len(observations) >= 3
                        and all(c.get('passed') is True and c.get('evidence') for c in observations),
                        'Contrôle visuel absent, incomplet ou défavorable')
                inputs = verdict.get('review_input') or {}
                require(inputs.get('source_sha256') == review.get('preview_sha256')
                        and inputs.get('comparison_reference_sha256') == measured.get('reference_sha256'),
                        'Avis visuel non lié aux rendus et à la référence')
                return {'provider': review.get('provider'), 'observations': observations}

            for name, action in (('reference', reference), ('textures_decodees', textures),
                                 ('rendus', renders), ('controle_visuel', semantic)):
                check(name, action)
    return dict(asset=str(asset), checkpoint=str(checkpoint) if checkpoint else None,
                verified=all(c['status'] == 'passed' for c in checks.values()), checks=checks,
                scope='3d_recorded_delivery_checks',
                limits='Vérification locale des preuves conservées, pas une certification indépendante '
                       'ni une garantie de perfection. Aucun autre module n’est certifié par ce rapport.')
