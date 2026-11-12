"""``jobia skills``: local library (import, search, create) beside bridge skills."""
from __future__ import annotations

import json
from pathlib import Path

import click


def register_skills_commands(group, client_factory, require_ok):
    from aurora_cli import config, display, skills as lib

    def emit(value):
        click.echo(json.dumps(value, ensure_ascii=False, indent=2))

    def remote_call(method, *args, **kwargs):
        client = client_factory()
        try:
            return require_ok(getattr(client, method)(*args, **kwargs))
        finally:
            client.close()

    def remote_skills(force_remote):
        if not (force_remote or config.is_configured()):
            return [], ""
        client = client_factory()
        try:
            result = client.skills_list()
        finally:
            client.close()
        if not result.get("ok"):
            if force_remote:
                raise click.ClickException(result.get("error") or "Le bridge n'a pas répondu.")
            return [], result.get("error") or "bridge indisponible"
        return result.get("skills", []), ""

    def show(rows, title):
        if rows:
            display.print(display.view.key_value_table(title, rows))

    @group.command(name="list")
    @click.option("--local", "force_local", is_flag=True, help="Seulement cet ordinateur.")
    @click.option("--remote", "force_remote", is_flag=True, help="Seulement le PC Aurora.")
    @click.option("--json", "as_json", is_flag=True)
    def list_skills(force_local, force_remote, as_json):
        """Skills de cet ordinateur et du PC Aurora."""
        local = [] if force_remote else lib.discover(include_disabled=True)
        remote, error = ([], "") if force_local else remote_skills(force_remote)
        if as_json:
            emit({"local": [s.to_dict() for s in local], "remote": remote, **({"remote_error": error} if error else {})})
            return
        if not force_remote:
            if local:
                show([(s.name, ("désactivé · " if not s.enabled else "") + f"{s.level} · {s.description[:120]}")
                      for s in local], "SKILLS · CET ORDINATEUR")
            else:
                display.hint("Aucun skill local. jobia skills import anthropics/skills · jobia skills search TEXTE --catalog")
        if remote:
            display.skills_table(remote)
        elif error and not force_local:
            display.hint(f"PC Aurora : {error}")

    @group.command(name="search")
    @click.argument("query", nargs=-1, required=True)
    @click.option("--catalog", "use_catalog", is_flag=True, help="Chercher aussi dans les catalogues (anthropics/skills…).")
    @click.option("--refresh", is_flag=True, help="Retélécharger les catalogues.")
    @click.option("--limit", type=click.IntRange(1, 100), default=15, show_default=True)
    @click.option("--json", "as_json", is_flag=True)
    def search(query, use_catalog, refresh, limit, as_json):
        """Chercher par nom, description, déclencheurs et contenu (accents et casse ignorés)."""
        text = " ".join(query)
        local = lib.search(text, limit=limit)
        remote, _ = remote_skills(False)
        from aurora_cli import fuzzy
        remote_hits = fuzzy.rank(text, remote, lambda r: [(str(r.get("name", "")), 3.0),
                                                          (str(r.get("description", "")), 1.8)], limit=limit)
        catalog_hits, errors = [], []
        if use_catalog or (not local and not remote_hits):
            catalog_hits, errors = lib.catalog_search(text, refresh=refresh, limit=limit)
        if as_json:
            emit({"local": [dict(s.to_dict(), score=round(sc, 2)) for sc, s in local],
                  "remote": [dict(r, score=round(sc, 2)) for sc, r in remote_hits],
                  "catalog": [dict(s.to_dict(), score=round(sc, 2)) for sc, s in catalog_hits],
                  **({"errors": errors} if errors else {})})
            return
        show([(s.name, f"{s.level} · {s.description[:120]}") for _, s in local], f"SKILLS · « {text} »")
        show([(str(r.get("name")), "PC Aurora · " + str(r.get("description", ""))[:110]) for _, r in remote_hits],
             "SKILLS · PC AURORA")
        show([(s.name, f"{s.source} · {s.description[:110]}") for _, s in catalog_hits], "CATALOGUES")
        if catalog_hits:
            first = catalog_hits[0][1]
            display.hint(f"Importer : jobia skills import {first.source} --name {first.name}")
        for error in errors:
            display.warning(error)
        if not (local or remote_hits or catalog_hits):
            display.hint("Aucun skill correspondant. --catalog cherche dans les catalogues publics.")

    @group.command(name="read")
    @click.argument("name")
    @click.option("--workspace", default="", help="Espace de travail du bridge.")
    @click.option("--remote", "force_remote", is_flag=True)
    def read(name, workspace, force_remote):
        """Afficher les instructions complètes d'un skill."""
        content = ""
        if not force_remote:
            try:
                _, content = lib.read(name)
            except ValueError:
                content = ""
        if not content:
            content = remote_call("skills_read", name, workspace=workspace).get("content", "")
        if display.view.caps.is_tty:
            display.print(display.view.markdown(content, title=name))
        else:
            display.view.stream(content + "\n")

    @group.command(name="import")
    @click.argument("source")
    @click.option("--name", "names", multiple=True, help="Importer seulement ce skill (répétable).")
    @click.option("--project", is_flag=True, help="Dans ./.aurora/skills plutôt que la bibliothèque utilisateur.")
    @click.option("--force", is_flag=True, help="Remplacer un skill du même nom.")
    @click.option("--list", "list_only", is_flag=True, help="Afficher les skills de la source sans importer.")
    def import_cmd(source, names, project, force, list_only):
        """Importer depuis un dossier, une archive .zip/.skill, une URL ou un dépôt GitHub (owner/repo)."""
        import time
        try:
            with display.thinking("Lecture de " + source, time.monotonic()):
                report = lib.import_skills(source, names=names, project=project, overwrite=force, list_only=list_only)
        except Exception as exc:
            raise click.ClickException(str(exc)) from exc
        if list_only:
            show([(name, "disponible") for name in report.available], f"SKILLS · {source}")
            return
        if report.imported:
            display.success(f"{len(report.imported)} skill(s) importé(s) dans {report.target} : {', '.join(report.imported)}")
        else:
            display.info("Aucun nouveau skill importé.")
        if report.skipped:
            display.warning(f"Déjà présents : {', '.join(report.skipped)} (--force pour remplacer)")
        if names and not report.imported and not report.skipped:
            display.hint("Disponibles : " + ", ".join(report.available[:30]))

    @group.command(name="create")
    @click.argument("name")
    @click.option("--description", required=True, help="Quand utiliser ce skill (sert à le retrouver).")
    @click.option("--instructions", default="", help="Corps Markdown du skill.")
    @click.option("--from-file", "from_file", type=click.Path(exists=True, dir_okay=False, path_type=Path), default=None)
    @click.option("--trigger", "triggers", multiple=True, help="Mot déclencheur (répétable).")
    @click.option("--level", default="user", type=click.Choice(["user", "project"]))
    @click.option("--workspace", default="", help="Espace de travail cible sur le bridge (--remote).")
    @click.option("--remote", "force_remote", is_flag=True, help="Créer sur le PC Aurora plutôt qu'ici.")
    @click.option("--force", is_flag=True)
    def create(name, description, instructions, from_file, triggers, level, workspace, force_remote, force):
        """Créer un skill réutilisable (ici par défaut, sur le PC avec --remote)."""
        body = from_file.read_text("utf-8") if from_file else instructions
        if force_remote:
            result = remote_call("skills_create", name, description=description, level=level,
                                 triggers=list(triggers), instructions=body, workspace=workspace)
            display.success(f"Skill créé sur le PC Aurora : {result.get('file', name)}")
            return
        try:
            skill = lib.create(name, description, body, triggers=triggers, project=level == "project", overwrite=force)
        except ValueError as exc:
            raise click.ClickException(str(exc)) from exc
        display.success(f"Skill créé : {skill.file}")

    @group.command(name="remove")
    @click.argument("name")
    def remove(name):
        """Retirer un skill de la bibliothèque (une sauvegarde datée est gardée)."""
        try:
            backup = lib.remove(name)
        except ValueError as exc:
            raise click.ClickException(str(exc)) from exc
        display.success(f"Skill retiré : {name}")
        display.hint(f"Sauvegarde : {backup}")

    @group.command(name="enable")
    @click.argument("name")
    def enable(name):
        """Réactiver un skill."""
        if not lib.set_enabled(name, True):
            raise click.ClickException(f"Skill introuvable : {name}")
        display.success(f"Skill activé : {name}")

    @group.command(name="disable")
    @click.argument("name")
    def disable(name):
        """Désactiver un skill sans le supprimer."""
        if not lib.set_enabled(name, False):
            raise click.ClickException(f"Skill introuvable : {name}")
        display.success(f"Skill désactivé : {name}")

    @group.command(name="discover")
    @click.option("--workspace", default="", help="Espace de travail du bridge à parcourir.")
    @click.option("--json", "as_json", is_flag=True)
    def discover(workspace, as_json):
        """Découvrir les skills du PC Aurora (projet, utilisateur, global)."""
        result = remote_call("skills_discover", workspace=workspace)
        if as_json:
            emit(result)
            return
        display.skills_table(result.get("skills", []))
