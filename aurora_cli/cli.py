"""Main CLI entrypoint for Aurora Remote CLI."""

import ipaddress
import socket

import httpx

_orig_getaddrinfo = socket.getaddrinfo


def _patched_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    """Use system DNS first; recover failed tunnel lookups through DNS over HTTPS."""
    try:
        return _orig_getaddrinfo(host, port, family, type, proto, flags)
    except socket.gaierror:
        name = host.decode("ascii", errors="replace") if isinstance(host, bytes) else host
        if not isinstance(name, str) or not name.endswith(".trycloudflare.com"):
            raise
        try:
            response = httpx.get(
                "https://cloudflare-dns.com/dns-query",
                params={"name": name, "type": "AAAA" if family == socket.AF_INET6 else "A"},
                headers={"accept": "application/dns-json"}, timeout=5.0,
            )
            response.raise_for_status()
            for answer in response.json().get("Answer", []):
                if answer.get("type") in (1, 28):
                    address = str(ipaddress.ip_address(answer["data"]))
                    return _orig_getaddrinfo(address, port, family, type, proto, flags)
        except (httpx.HTTPError, ValueError, KeyError, socket.gaierror):
            # Preserve the original DNS exception for tunnel rediscovery.
            raise socket.gaierror(socket.EAI_NONAME, "Tunnel DNS resolution failed") from None
        raise


socket.getaddrinfo = _patched_getaddrinfo

import click
from rich.traceback import install as install_rich_traceback

# Install expert traceback handler
install_rich_traceback(show_locals=False, theme="monokai")

from aurora_cli import config
from aurora_cli.client import AuroraClient
from aurora_cli import display
from aurora_cli.interactive import run_interactive
from aurora_cli.mission import run_mission
from aurora_cli.core.paths import init_app_dirs

# Initialisation de l'architecture cachée
init_app_dirs()



@click.group(invoke_without_command=True)
@click.pass_context
def main(ctx):
    """Aurora AI Agent — Remote CLI Client."""
    if ctx.invoked_subcommand is None:
        client = AuroraClient()
        try:
            if not config.is_configured() or not client.ping():
                display.error("Client non configuré ou serveur injoignable.")
                display.info("Veuillez lancer: jobia connect")
                return
            run_interactive(client)
        finally:
            client.close()


@main.command()
@click.option("--server", default="", help="Adresse du bridge ; sinon découverte du tunnel.")
@click.option("--api-key", envvar="AURORA_API_KEY", default="", help="Clé déjà autorisée par le bridge.")
def connect(server, api_key):
    """Connect with an existing bridge key and remember this device."""
    import base64
    import time
    from urllib.parse import urlsplit

    def discover():
        response = httpx.get(
            "https://api.github.com/repos/juancodepyandc/aurora-live/contents/tunnel.txt",
            params={"_t": str(int(time.time()))},
            headers={"Cache-Control": "no-cache", "Accept": "application/vnd.github.v3+json"},
            timeout=10.0,
        )
        response.raise_for_status()
        metadata = response.json()
        if metadata.get("encoding") != "base64":
            raise ValueError("Réponse de découverte du serveur invalide")
        discovered = base64.b64decode(metadata["content"]).decode("utf-8").strip().rstrip("/")
        if not discovered:
            raise ValueError("Le serveur distant semble éteint ou son tunnel n'est pas encore publié sur GitHub.")
        parsed = urlsplit(discovered)
        if (parsed.scheme != "https" or not parsed.hostname
                or not parsed.hostname.endswith(".trycloudflare.com")
                or parsed.username or parsed.password or parsed.port not in (None, 443)
                or parsed.path or parsed.query or parsed.fragment):
            raise ValueError("Adresse de tunnel publiée invalide")
        return discovered

    def register(url, key):
        return httpx.post(
            f"{url}/api/cli/register",
            headers={"Authorization": f"Bearer {key}"},
            json={"device_name": socket.gethostname(), "client_key": key},
            timeout=10.0,
        )

    try:
        url = (server or config.resolve_server_url() or discover()).rstrip("/")
        parsed = urlsplit(url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("Adresse du bridge invalide")
        client_key = api_key or config.get("api_key")
        if not client_key:
            client_key = click.prompt("Clé autorisée par l'administrateur du bridge", hide_input=True)
        import os
        refresh_allowed = (not server and not os.environ.get("AURORA_SERVER_URL")
                           and parsed.hostname.endswith(".trycloudflare.com"))
        try:
            response = register(url, client_key)
            if refresh_allowed and response.status_code in (502, 503, 504, 530):
                response.raise_for_status()
        except (httpx.ConnectError, httpx.TimeoutException, httpx.HTTPStatusError):
            if not refresh_allowed:
                raise
            display.warning("Le tunnel enregistré ne répond plus. Recherche de l’adresse actuelle...")
            updated = discover()
            if updated == url:
                raise
            url = updated
            response = register(url, client_key)
        if response.status_code == 401:
            display.error("Clé absente, invalide ou révoquée. Fournissez une clé déjà autorisée par le bridge.")
            return
        response.raise_for_status()
        if not response.json().get("ok"):
            display.error("Le bridge a refusé l'enregistrement de cet appareil.")
            return
        config.set_key("server_url", url)
        config.set_key("api_key", client_key)
        display.success("Connexion établie.")
        client = AuroraClient(server_url=url, api_key=client_key, timeout=30.0)
        try:
            run_interactive(client)
        finally:
            client.close()
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        display.error(f"Connexion impossible : {exc}")


@main.command()
def status():
    """Show server status."""
    client = AuroraClient()
    try:
        display.banner(client.status())
    except Exception as e:
        display.error(str(e))
    finally:
        client.close()


@main.command()
def doctor():
    """Run diagnostics."""
    client = AuroraClient()
    try:
        display.doctor_results(client.doctor().get("checks", []))
    except Exception as e:
        display.error(str(e))
    finally:
        client.close()


@main.command()
@click.argument('level', required=False)
def permissions(level):
    """Show or set permissions."""
    if level:
        level = level.upper()
        if level not in config.PERMISSION_LEVELS:
            display.error(f"Niveau inconnu : {level}. Niveaux autorisés : {', '.join(config.PERMISSION_LEVELS)}")
            return
    client = AuroraClient()
    try:
        if level:
            res = client.permissions_set(level)
            if res.get("ok"):
                display.success(f"Niveau global par défaut défini sur : {level}")
                config.set_key("default_permissions", level)
            else:
                display.error(res.get("error"))
        else:
            display.permissions_display(client.permissions_get().get("levels", {}),
                                      config.get("default_permissions", "AUTONOMOUS"))
    except Exception as e:
        display.error(str(e))
    finally:
        client.close()


@main.command()
@click.argument('request', required=True)
@click.option('--model', default='', help='Modèle Ollama du serveur à utiliser.')
@click.option('--server-workspace', default=None, help='Répertoire de travail sur le serveur Aurora.')
def run(request, model, server_workspace):
    """Démarrer une mission autonome en une commande."""
    client = AuroraClient()
    import os
    try:
        if not client.ping():
            raise click.ClickException("Serveur injoignable.")
        if not run_mission(client, request, workspace=os.getcwd(), model=model,
                           permissions=config.get("default_permissions", "AUTONOMOUS"),
                           server_workspace=server_workspace or config.get("default_workspace") or None):
            raise click.ClickException("La mission ne s'est pas terminée avec succès.")
    finally:
        client.close()


# --- Agents Group ---
@main.group()
def agents():
    """Manage Aurora agents."""
    pass

@agents.command(name="list")
def agents_list():
    """List all agents."""
    client = AuroraClient()
    try:
        off = client.agents_official().get("agents", [])
        dyn = client.agents_dynamic().get("agents", [])
        display.agents_table(off, dyn)
    except Exception as e:
        display.error(str(e))
    finally:
        client.close()

@agents.command(name="disable")
@click.argument("name")
def agents_disable(name):
    client = AuroraClient()
    try:
        res = client.agent_disable(name)
        if res.get("ok"):
            display.success(f"Agent {name} désactivé.")
        else:
            display.error(res.get("error", "Agent non désactivé."))
    except Exception as e:
        display.error(str(e))
    finally:
        client.close()


# --- MCP Group ---
@main.group()
def mcp():
    """Manage MCP servers and tools."""
    pass

@mcp.command(name="list")
def mcp_list():
    client = AuroraClient()
    try:
        servers = client.mcp_list().get("servers", [])
        tools = client.mcp_tools().get("tools", [])
        display.mcp_table(servers, tools)
    except Exception as e:
        display.error(str(e))
    finally:
        client.close()


# --- Skills Group ---
@main.group()
def skills():
    """Manage Aurora Skills."""
    pass

@skills.command(name="list")
def skills_list():
    client = AuroraClient()
    try:
        display.skills_table(client.skills_list().get("skills", []))
    except Exception as e:
        display.error(str(e))
    finally:
        client.close()


if __name__ == '__main__':
    main()
