"""Main CLI entrypoint for Aurora Remote CLI."""
import click

from aurora_cli import config
from aurora_cli.client import AuroraClient
from aurora_cli import display
from aurora_cli.interactive import run_interactive
from aurora_cli.mission import run_mission


@click.group(invoke_without_command=True)
@click.pass_context
def main(ctx):
    """Aurora AI Agent — Remote CLI Client."""
    if ctx.invoked_subcommand is None:
        client = AuroraClient()
        if not config.is_configured() or not client.ping():
            display.error("Client non configuré ou serveur injoignable.")
            display.info("Veuillez lancer: aurora connect")
            return
        run_interactive(client)


@main.command()
def connect():
    """Connect to Aurora server automatically (zero friction)."""
    import uuid
    import socket
    import httpx
    import json
    
    display.console.print("\n[bold cyan]🔗 Connexion automatique au serveur Aurora[/bold cyan]")
    
    try:
        # Fetch dynamic URL from the permanent Gist
        display.console.print("Recherche du serveur en cours...")
        # On utilise l'API GitHub pour contourner le cache agressif des CDN (qui cause des erreurs de DNS avec des vieux liens)
        gist_api_url = "https://api.github.com/gists/4510a5d538cef3e262ec38b6acc5bde0"
        
        r_url = httpx.get(gist_api_url, timeout=10.0)
        r_url.raise_for_status()
        
        gist_data = r_url.json()
        url = gist_data.get("files", {}).get("tunnel_sync.txt", {}).get("content", "").strip().rstrip("/")
        
        if not url or "trycloudflare" not in url:
            raise ValueError(f"URL de tunnel invalide reçue : {url}")
            
        display.console.print(f"Serveur localisé : [green]{url}[/green]")
        
        # Generate a unique identity for this client
        client_key = config.get("api_key")
        if not client_key:
            client_key = "aurora_cli_" + str(uuid.uuid4()).replace("-", "")
            
        device_name = socket.gethostname()
        
        display.console.print("Vérification et enregistrement...")
        r = httpx.post(f"{url}/api/cli/register", json={
            "device_name": device_name,
            "client_key": client_key
        }, timeout=10.0)
        
        if r.status_code == 200:
            try:
                data = r.json()
                if data.get("ok"):
                    config.set_key("server_url", url)
                    config.set_key("api_key", client_key)
                    display.success(f"Connexion établie avec succès ! (Appareil : {device_name})")
                    
                    # Lancement magique et immédiat de l'interface !
                    display.console.print("\n[bold green]🚀 Démarrage de l'interface interactive...[/bold green]")
                    import time
                    time.sleep(1)
                    from aurora_cli.interactive import run_interactive
                    from aurora_cli.client import AuroraClient
                    
                    # Instantiate client with newly configured keys
                    client = AuroraClient(server_url=url, api_key=client_key, timeout=10.0)
                    run_interactive(client)
                else:
                    display.error(f"Refus du serveur: {data.get('error', 'Inconnue')}")
            except Exception:
                display.error("Le serveur a répondu avec un format invalide.")
        else:
            if "<html" in r.text.lower() or "cloudflare" in r.text.lower():
                display.error(f"Le serveur distant (Linux) est hors-ligne ou inaccessible (Erreur {r.status_code}).\nVeuillez vous assurer qu'Aurora est bien lancé sur la machine principale.")
            else:
                display.error(f"Erreur HTTP {r.status_code}: {r.text[:200]}")
    except Exception as e:
        display.error(f"Impossible de se connecter automatiquement. ({e})")


@main.command()
def status():
    """Show server status."""
    try:
        display.status_display(AuroraClient().status())
    except Exception as e:
        display.error(str(e))


@main.command()
def doctor():
    """Run diagnostics."""
    try:
        display.doctor_results(AuroraClient().doctor().get("checks", []))
    except Exception as e:
        display.error(str(e))


@main.command()
@click.argument('level', required=False)
def permissions(level):
    """Show or set permissions."""
    client = AuroraClient()
    try:
        if level:
            res = client.permissions_set(level)
            if res.get("ok"):
                display.success(f"Niveau global par défaut défini sur : {level}")
                config.set_key("default_permissions", level.upper())
            else:
                display.error(res.get("error"))
        else:
            display.permissions_display(client.permissions_get().get("levels", {}),
                                      config.get("default_permissions", "AUTONOMOUS"))
    except Exception as e:
        display.error(str(e))


@main.command()
@click.argument('request', required=True)
def run(request):
    """Démarrer une mission autonome en une commande."""
    client = AuroraClient()
    if not client.ping():
        display.error("Serveur injoignable.")
        return
    import os
    run_mission(client, request, workspace=os.getcwd(), permissions=config.get("default_permissions", "AUTONOMOUS"))


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

@agents.command(name="disable")
@click.argument("name")
def agents_disable(name):
    try:
        AuroraClient().agent_disable(name)
        display.success(f"Agent {name} désactivé.")
    except Exception as e:
        display.error(str(e))


# --- MCP Group ---
@main.group()
def mcp():
    """Manage MCP servers and tools."""
    pass

@mcp.command(name="list")
def mcp_list():
    try:
        client = AuroraClient()
        display.mcp_table(client.mcp_list().get("servers", []), client.mcp_tools().get("tools", []))
    except Exception as e:
        display.error(str(e))


# --- Skills Group ---
@main.group()
def skills():
    """Manage Aurora Skills."""
    pass

@skills.command(name="list")
def skills_list():
    try:
        display.skills_table(AuroraClient().skills_list().get("skills", []))
    except Exception as e:
        display.error(str(e))


if __name__ == '__main__':
    main()
