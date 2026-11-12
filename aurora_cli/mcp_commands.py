"""``jobia mcp``: local registry (import, search, install) and bridge servers.

A server name found in the local registry (project ``.mcp.json``, the user
registry or ``~/.aurora/mcp_config.json``) runs on this computer; any other
name is forwarded to the connected Aurora bridge. ``--local`` / ``--remote``
force the side.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import click


def _pairs(values, label):
    result = {}
    for item in values:
        if "=" not in item:
            raise click.ClickException(f"{label} invalide '{item}' (attendu CLÉ=VALEUR).")
        key, value = item.split("=", 1)
        result[key.strip()] = value
    return result


def _json_arguments(json_args, kv_args):
    arguments = {}
    if json_args:
        try:
            parsed = json.loads(json_args)
        except ValueError as exc:
            raise click.ClickException(f"Arguments JSON invalides : {exc}") from exc
        if not isinstance(parsed, dict):
            raise click.ClickException("Arguments JSON invalides : un objet est attendu")
        arguments.update(parsed)
    for item in kv_args:
        if "=" not in item:
            raise click.ClickException(f"Argument invalide '{item}' (attendu clé=valeur).")
        key, value = item.split("=", 1)
        try:
            arguments[key] = json.loads(value)
        except ValueError:
            arguments[key] = value
    return arguments


def register_mcp_commands(group, client_factory, require_ok):
    from aurora_cli import config, display, mcp_registry as reg

    def local_server(name, force_local, force_remote):
        if force_remote:
            return None
        found = reg.servers(include_disabled=True).get(name)
        if found is None and force_local:
            raise click.ClickException(f"Serveur MCP local inconnu : {name} (jobia mcp list)")
        if found is not None and found.get("disabled"):
            raise click.ClickException(f"Le serveur {name} est désactivé (jobia mcp enable {name}).")
        return found

    def remote(method, *args, **kwargs):
        client = client_factory()
        try:
            return require_ok(getattr(client, method)(*args, **kwargs))
        finally:
            client.close()

    def emit(value):
        click.echo(json.dumps(value, ensure_ascii=False, indent=2))

    def local_call(cfg, method, params, timeout):
        response, error = reg.call(cfg, method, params, timeout=timeout)
        if error:
            raise click.ClickException(error)
        return response["result"]

    side = [click.option("--local", "force_local", is_flag=True, help="Exécuter sur cet ordinateur."),
            click.option("--remote", "force_remote", is_flag=True, help="Exécuter sur le PC Aurora (bridge).")]

    def with_side(func):
        for option in reversed(side):
            func = option(func)
        return func

    @group.command(name="list")
    @with_side
    @click.option("--json", "as_json", is_flag=True)
    def list_servers(force_local, force_remote, as_json):
        """Serveurs MCP de cet ordinateur et du PC Aurora."""
        local = {} if force_remote else reg.servers(include_disabled=True)
        cache = reg.load_cache()
        remote_servers, remote_tools, remote_error = [], [], ""
        if not force_local and (force_remote or config.is_configured()):
            client = client_factory()
            try:
                listed = client.mcp_list()
                tools = client.mcp_tools() if listed.get("ok") else {}
                if listed.get("ok"):
                    remote_servers, remote_tools = listed.get("servers", []), tools.get("tools", [])
                else:
                    remote_error = listed.get("error") or "bridge indisponible"
            finally:
                client.close()
            if remote_error and force_remote:
                raise click.ClickException(remote_error)
        if as_json:
            emit({"local": [{"name": n, **{k: v for k, v in c.items() if k != "env"},
                             "tools": len(cache.get(n, {}).get("tools", []))} for n, c in local.items()],
                  "remote": remote_servers, "remote_tools": remote_tools,
                  **({"remote_error": remote_error} if remote_error else {})})
            return
        if not force_remote:
            rows = [(name, ("désactivé · " if cfg.get("disabled") else "") + cfg.get("scope", ""),
                     f"{len(cache.get(name, {}).get('tools', []))} outils" if name in cache else "catalogue à lire",
                     reg.describe(cfg)) for name, cfg in local.items()]
            if rows:
                display.print(display.view.key_value_table(
                    "MCP · CET ORDINATEUR", [(n, f"{scope} · {tools} · {detail}") for n, scope, tools, detail in rows]))
            else:
                display.hint("Aucun serveur MCP local. jobia mcp import récupère ceux de Claude, Cursor, VS Code…"
                             " · jobia mcp search TEXTE --registry pour en trouver.")
        if remote_servers or remote_tools:
            display.mcp_table(remote_servers, remote_tools)
        elif remote_error and not force_local:
            display.hint(f"PC Aurora : {remote_error}")

    @group.command(name="add", context_settings={"ignore_unknown_options": True})
    @click.argument("name")
    @click.argument("command", nargs=-1, type=click.UNPROCESSED)
    @click.option("--url", default="", help="Point d'accès MCP distant (HTTP Streamable).")
    @click.option("--sse", is_flag=True, help="Le point d'accès utilise l'ancien transport SSE.")
    @click.option("--header", "headers", multiple=True, help="En-tête HTTP CLÉ=VALEUR (répétable).")
    @click.option("--env", "env", multiple=True, help="Variable d'environnement CLÉ=VALEUR (répétable).")
    @click.option("--cwd", default="", help="Répertoire de travail du serveur.")
    @click.option("--persistent", is_flag=True, help="Garder le processus entre les appels.")
    @click.option("--description", default="")
    @click.option("--force", is_flag=True, help="Remplacer un serveur du même nom.")
    def add(name, command, url, sse, headers, env, cwd, persistent, description, force):
        """Ajouter un serveur : jobia mcp add NOM -- npx -y paquet  ou  --url https://…"""
        if url:
            cfg = {"url": url, "type": "sse" if sse else "http", "headers": _pairs(headers, "En-tête")}
        elif command:
            cfg = {"command": command[0], "args": list(command[1:]), "env": _pairs(env, "Variable")}
            if cwd:
                cfg["cwd"] = cwd
        else:
            raise click.ClickException("Indiquez une commande après -- ou une adresse --url.")
        if persistent:
            cfg["persistent"] = True
        if description:
            cfg["description"] = description
        try:
            entry = reg.add(name, cfg, overwrite=force)
        except ValueError as exc:
            raise click.ClickException(str(exc)) from exc
        display.success(f"Serveur MCP ajouté : {name} · {reg.describe(entry)}")
        if entry.get("command") and shutil.which(entry["command"]) is None:
            display.warning(f"Commande {entry['command']} introuvable dans le PATH de cet ordinateur.")
        missing = reg.missing_values(entry)
        if missing:
            display.hint("Valeurs à fournir : " + ", ".join(missing))

    @group.command(name="remove")
    @click.argument("name")
    def remove(name):
        """Retirer un serveur du registre local."""
        if not reg.remove(name):
            raise click.ClickException(f"{name} n'est pas dans le registre local ({reg.registry_path()}).")
        display.success(f"Serveur MCP retiré : {name}")

    @group.command(name="enable")
    @click.argument("name")
    def enable(name):
        """Réactiver un serveur."""
        if not reg.set_enabled(name, True):
            raise click.ClickException(f"Serveur MCP inconnu : {name}")
        display.success(f"Serveur MCP activé : {name}")

    @group.command(name="disable")
    @click.argument("name")
    def disable(name):
        """Désactiver un serveur sans le supprimer."""
        if not reg.set_enabled(name, False):
            raise click.ClickException(f"Serveur MCP inconnu : {name}")
        display.success(f"Serveur MCP désactivé : {name}")

    @group.command(name="import")
    @click.argument("source", required=False, default="")
    @click.option("--name", "names", multiple=True, help="Importer seulement ce serveur (répétable).")
    @click.option("--force", is_flag=True, help="Remplacer les serveurs du même nom.")
    @click.option("--list", "list_only", is_flag=True, help="Afficher les candidats sans importer.")
    @click.option("--json", "as_json", is_flag=True)
    def import_cmd(source, names, force, list_only, as_json):
        """Importer depuis Claude Desktop/Code, Cursor, VS Code, Windsurf, Gemini, Codex, un fichier ou une URL.

        Sans SOURCE, toutes les configurations connues de cet ordinateur sont lues.
        SOURCE : claude-desktop, claude-code, cursor, vscode, windsurf, gemini,
        codex, aurora, un chemin .json/.toml ou une adresse https://.
        """
        if source:
            try:
                found = reg.load_source(source)
            except Exception as exc:
                raise click.ClickException(str(exc)) from exc
            rows = [{"name": n, "config": c, "label": source, "status": "nouveau",
                     "missing": reg.missing_values(c)} for n, c in found.items()]
        else:
            rows = reg.scan_clients()
            found = {}
            for row in rows:
                found.setdefault(row["name"], row["config"])
        if as_json and list_only:
            emit([{k: v for k, v in r.items() if k != "config"} | {"describe": reg.describe(r["config"])} for r in rows])
            return
        if not rows:
            display.hint("Aucune configuration MCP trouvée (Claude Desktop, Claude Code, Cursor, VS Code, Windsurf, Gemini, Codex).")
            return
        if list_only:
            display.print(display.view.key_value_table("CANDIDATS MCP", [
                (r["name"], f"{r.get('label', '')} · {r.get('status', '')} · {reg.describe(r['config'])}") for r in rows]))
            return
        imported, skipped = reg.import_servers(found, names=set(names) or None, overwrite=force,
                                               origin=source or "import")
        if as_json:
            emit({"imported": imported, "skipped": skipped})
            return
        if imported:
            display.success(f"{len(imported)} serveur(s) importé(s) : {', '.join(imported)}")
        else:
            display.info("Rien de nouveau à importer.")
        if skipped:
            display.warning(f"Déjà présents avec une autre configuration : {', '.join(skipped)} (--force pour remplacer)")
        for row in rows:
            if row["name"] in imported and row.get("missing"):
                display.hint(f"{row['name']} : valeurs à fournir ({', '.join(row['missing'])})")
        if imported:
            display.hint("jobia mcp refresh lit leurs outils ; jobia mcp search TEXTE les retrouve.")

    @group.command(name="refresh")
    @click.argument("names", nargs=-1)
    @click.option("--timeout", type=click.FloatRange(1, 180), default=25.0, show_default=True)
    def refresh(names, timeout):
        """Lire les catalogues réels (outils, prompts, ressources) et les garder pour la recherche."""
        cache = reg.refresh(set(names) or None, timeout=timeout)
        visible = reg.servers()
        for name in (names or visible):
            record = cache.get(name, {})
            if record.get("error"):
                display.warning(f"{name} : {record['error']}")
            elif name in visible:
                display.success(f"{name} : {len(record.get('tools', []))} outils · {len(record.get('prompts', []))} prompts"
                                f" · {len(record.get('resources', []))} ressources")

    @group.command(name="search")
    @click.argument("query", nargs=-1, required=True)
    @click.option("--live", is_flag=True, help="Relire les catalogues des serveurs avant de chercher.")
    @click.option("--registry", "use_registry", is_flag=True, help="Chercher aussi dans le registre officiel MCP.")
    @click.option("--limit", type=click.IntRange(1, 100), default=20, show_default=True)
    @click.option("--json", "as_json", is_flag=True)
    def search(query, live, use_registry, limit, as_json):
        """Chercher un serveur, un outil, un prompt ou une ressource (local + registre officiel)."""
        text = " ".join(query)
        hits = reg.search(text, refresh_live=live, limit=limit)
        catalog, registry_error = [], ""
        if use_registry or not hits:
            try:
                catalog = reg.registry_search(text, limit=limit)
            except Exception as exc:
                registry_error = str(exc)
        if as_json:
            emit({"local": hits, "registry": catalog, **({"registry_error": registry_error} if registry_error else {})})
            return
        if hits:
            display.print(display.view.key_value_table(f"MCP · « {text} »", [
                (f"{h['server']}:{h['name']}" if h["kind"] != "serveur" else h["name"],
                 f"{h['kind']} · {h['description'][:120]}") for h in hits]))
        if catalog:
            display.print(display.view.key_value_table("REGISTRE OFFICIEL MCP", [
                (row["name"], f"{row['version']} · {row['description'][:110]}") for row in catalog]))
            display.hint("Installer : jobia mcp install NOM [--env CLÉ=VALEUR]")
        if not hits and not catalog:
            display.hint("Aucun résultat." + (f" Registre : {registry_error}" if registry_error else
                                             " jobia mcp refresh met à jour les catalogues locaux."))

    @group.command(name="install")
    @click.argument("registry_name")
    @click.option("--as", "alias", default="", help="Nom local (défaut : fin du nom du registre).")
    @click.option("--env", "env", multiple=True, help="Variable CLÉ=VALEUR transmise au serveur.")
    @click.option("--remote-endpoint", is_flag=True, help="Préférer le point d'accès distant au paquet local.")
    @click.option("--force", is_flag=True)
    def install(registry_name, alias, env, remote_endpoint, force):
        """Installer un serveur du registre officiel MCP (npm, PyPI, Docker ou distant)."""
        try:
            rows = reg.registry_search(registry_name, limit=30)
        except Exception as exc:
            raise click.ClickException(f"Registre MCP injoignable : {exc}") from exc
        entry = next((r for r in rows if r["name"] == registry_name), None) or (rows[0] if len(rows) == 1 else None)
        if entry is None:
            names = ", ".join(r["name"] for r in rows[:8]) or "aucun"
            raise click.ClickException(f"Nom exact introuvable. Proches : {names}")
        try:
            cfg = reg.install_config(entry, env=_pairs(env, "Variable"), prefer="remote" if remote_endpoint else "")
            name = alias or reg.default_alias(entry["name"])
            saved = reg.add(name, cfg, overwrite=force)
        except ValueError as exc:
            raise click.ClickException(str(exc)) from exc
        display.success(f"{entry['name']} installé sous le nom {name} · {reg.describe(saved)}")
        runner = saved.get("command")
        if runner and shutil.which(runner) is None:
            display.warning(f"{runner} est nécessaire sur cet ordinateur pour démarrer ce serveur.")
        missing = reg.missing_values(saved)
        if missing:
            display.hint("À renseigner avant usage : " + ", ".join(missing) + " (variables d'environnement ou --env)")

    @group.command(name="tools")
    @click.argument("server", required=False, default="")
    @with_side
    @click.option("--workspace", default="", help="Espace de travail du bridge pour ses configurations MCP.")
    @click.option("--json", "as_json", is_flag=True)
    def tools(server, force_local, force_remote, workspace, as_json):
        """Lister les outils réels d'un serveur (ou de tous)."""
        visible = {} if force_remote else reg.servers()
        targets = {server: visible[server]} if server in visible else ({} if server else visible)
        local_rows, errors = [], []
        if not force_remote and (targets or force_local):
            for name, cfg in targets.items():
                try:
                    for tool in reg.all_items(cfg, "tools"):
                        local_rows.append({"server": name, "name": tool.get("name", ""),
                                           "description": tool.get("description", ""),
                                           "inputSchema": tool.get("inputSchema", {}), "side": "local"})
                except Exception as exc:
                    errors.append({"server": name, "error": str(exc)})
        remote_rows = []
        if not force_local and not (server and server in visible) and (force_remote or config.is_configured() or not visible):
            result = remote("mcp_tools", workspace=workspace)
            remote_rows = [dict(t, side="remote") for t in result.get("tools", []) if not server or t.get("server") == server]
            errors += result.get("errors", [])
        rows = local_rows + remote_rows
        if as_json:
            emit({"ok": True, "tools": rows, "errors": errors})
            return
        if not rows:
            display.hint("Aucun outil MCP disponible.")
        for tool in rows:
            display.kv(f"{tool.get('server', '?')}:{tool.get('name', '?')}", tool.get("description", ""))
        for err in errors:
            display.warning(f"{err.get('server', '?')} : {err.get('error', 'erreur')}")

    @group.command(name="call")
    @click.argument("server")
    @click.argument("tool")
    @click.option("--arg", "kv_args", multiple=True, help="Argument clé=valeur (répétable).")
    @click.option("--json-args", default="", help="Objet JSON d'arguments.")
    @click.option("--workspace", default="", help="Espace de travail du bridge.")
    @click.option("--timeout", "timeout_seconds", type=click.FloatRange(1, 180), default=None,
                  help="Budget d'exécution, jusqu'à 180 secondes.")
    @with_side
    def call(server, tool, kv_args, json_args, workspace, timeout_seconds, force_local, force_remote):
        """Appeler un outil MCP et afficher sa réponse réelle."""
        arguments = _json_arguments(json_args, kv_args)
        cfg = local_server(server, force_local, force_remote)
        if cfg is not None:
            emit(local_call(cfg, "tools/call", {"name": tool, "arguments": arguments}, timeout_seconds or 60.0))
            return
        options = {"timeout_seconds": timeout_seconds} if timeout_seconds is not None else {}
        result = remote("mcp_call", server, tool, arguments=arguments, workspace=workspace, **options)
        emit(result.get("result", result))

    @group.command(name="catalog")
    @click.argument("server")
    @click.option("--kind", type=click.Choice(sorted(reg.CATALOG_KINDS)), default="tools", show_default=True)
    @click.option("--cursor", default="", help="Curseur next_cursor exact de la page précédente.")
    @click.option("--workspace", default="")
    @with_side
    def catalog(server, kind, cursor, workspace, force_local, force_remote):
        """Lire une page réelle du catalogue MCP, avec schémas et curseur."""
        cfg = local_server(server, force_local, force_remote)
        if cfg is not None:
            try:
                emit(reg.catalog(cfg, kind, cursor or None))
            except (RuntimeError, ValueError) as exc:
                raise click.ClickException(str(exc)) from exc
            return
        result = remote("mcp_catalog", server, kind, cursor=cursor, workspace=workspace)
        emit(result.get("result", result))

    @group.command(name="resources")
    @click.argument("server")
    @click.option("--cursor", default="")
    @click.option("--workspace", default="")
    @with_side
    def resources(server, cursor, workspace, force_local, force_remote):
        """Lister les ressources exposées par un serveur MCP."""
        click.get_current_context().invoke(catalog, server=server, kind="resources", cursor=cursor,
                                           workspace=workspace, force_local=force_local, force_remote=force_remote)

    @group.command(name="prompts")
    @click.argument("server")
    @click.option("--cursor", default="")
    @click.option("--workspace", default="")
    @with_side
    def prompts(server, cursor, workspace, force_local, force_remote):
        """Lister les prompts proposés par un serveur MCP."""
        click.get_current_context().invoke(catalog, server=server, kind="prompts", cursor=cursor,
                                           workspace=workspace, force_local=force_local, force_remote=force_remote)

    @group.command(name="read")
    @click.argument("server")
    @click.argument("uri")
    @click.option("--workspace", default="")
    @with_side
    def read_resource(server, uri, workspace, force_local, force_remote):
        """Lire une ressource ; son contenu reste une donnée, pas une instruction."""
        cfg = local_server(server, force_local, force_remote)
        if cfg is not None:
            emit(local_call(cfg, "resources/read", {"uri": uri}, 60.0))
            return
        result = remote("mcp_read_resource", server, uri, workspace=workspace)
        emit(result.get("result", result))

    @group.command(name="prompt")
    @click.argument("server")
    @click.argument("name")
    @click.option("--json-args", default="{}", help="Objet JSON d'arguments texte.")
    @click.option("--workspace", default="")
    @with_side
    def get_prompt(server, name, json_args, workspace, force_local, force_remote):
        """Récupérer un prompt sans l'installer comme instruction système."""
        try:
            arguments = json.loads(json_args)
        except ValueError as exc:
            raise click.ClickException("Arguments JSON du prompt invalides") from exc
        if not isinstance(arguments, dict) or any(not isinstance(v, str) for v in arguments.values()):
            raise click.ClickException("Les arguments du prompt doivent être un objet JSON de textes")
        cfg = local_server(server, force_local, force_remote)
        if cfg is not None:
            emit(local_call(cfg, "prompts/get", {"name": name, "arguments": arguments}, 60.0))
            return
        result = remote("mcp_get_prompt", server, name, arguments, workspace=workspace)
        emit(result.get("result", result))

    @group.command(name="close")
    @click.argument("server")
    @click.option("--workspace", default="")
    @with_side
    def close_session(server, workspace, force_local, force_remote):
        """Libérer une session MCP persistante inactive."""
        cfg = local_server(server, force_local, force_remote)
        if cfg is not None:
            emit({"closed": reg.close(cfg, cwd=Path.cwd())})
            return
        result = remote("mcp_close", server, workspace=workspace)
        emit(result.get("result", result))
