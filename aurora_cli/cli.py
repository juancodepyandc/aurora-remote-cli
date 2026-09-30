"""Command line entry point.

Two families of commands live side by side here. The local ones read and
write this machine only and work with no bridge configured; the remote ones
reuse :class:`aurora_cli.bridge.Bridge` and require a running bridge.

Global options are applied before the subcommand runs so that every command,
including ``--help``, sees the same theme and the same detected terminal
capabilities.
"""
from __future__ import annotations

import ipaddress
import json
import platform
import shutil
import socket
from pathlib import Path

import click


def _patched_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    """Resolve ``*.trycloudflare.com`` through DNS-over-HTTPS.

    Short lived tunnel hostnames sometimes sit in a resolver cache that has
    not picked up the new record yet, which makes a freshly opened tunnel look
    like a dead server. Falling back to a public DoH resolver fixes that
    without touching the resolution of any other host.
    """
    try:
        return _ORIGINAL_GETADDRINFO(host, port, family, type, proto, flags)
    except socket.gaierror:
        if not host or "trycloudflare" not in host:
            raise
        import httpx

        for endpoint in (
            "https://1.1.1.1/dns-query",
            "https://dns.google/resolve",
        ):
            try:
                response = httpx.get(
                    endpoint,
                    params={"name": host, "type": "A"},
                    headers={"accept": "application/dns-json"},
                    timeout=5.0,
                    verify=True,
                )
                answers = [
                    item["data"]
                    for item in response.json().get("Answer", [])
                    if item.get("type") in (1, 5)
                ]
            except Exception:
                continue
            if answers:
                return _ORIGINAL_GETADDRINFO(answers[0], port, family, type, proto, flags)
        raise


_ORIGINAL_GETADDRINFO = socket.getaddrinfo
socket.getaddrinfo = _patched_getaddrinfo


import click  # noqa: E402,F811  (re-imported after the resolver shim on purpose)

from rich.traceback import install as install_rich_traceback  # noqa: E402

from aurora_cli import __version__, brand, config, display, themes  # noqa: E402
from aurora_cli.bridge import Bridge  # noqa: E402
from aurora_cli.core import capabilities, locations  # noqa: E402
from aurora_cli.core.providers import human_size  # noqa: E402

install_rich_traceback(console=display.view.console, extra_lines=1)


def _client(**kwargs) -> Bridge:
    return Bridge(**kwargs)


def _apply_overrides(ctx) -> None:
    """Resolve the theme and capabilities once, before any subcommand runs."""
    parent = ctx.obj or {}
    caps = capabilities.detect()
    for key, value in (("color", parent.get("color")),
                       ("animation", parent.get("animation"))):
        if value:
            import os

            os.environ[brand.env(key)] = value
    caps = capabilities.detect()
    theme, source = themes.resolve(parent.get("theme", ""),
                                   caps, config.get("theme", ""))
    display.view = display.bind(theme=theme, caps=caps)
    ctx.obj = {
        "caps": caps,
        "theme": theme,
        "theme_source": source,
        "overrides": parent,
    }


@click.group(invoke_without_command=True, context_settings={"help_option_names": ["-h", "--help"]})
@click.option("--theme", "theme_opt", default="", help="Force a theme for this run only.")
@click.option(
    "--color",
    type=click.Choice(["auto", "always", "never"]),
    default="auto",
    help="Colour output policy.",
)
@click.option(
    "--animation",
    "anim_opt",
    type=click.Choice(["auto", "full", "reduced", "none"]),
    default="auto",
    help="Animation budget.",
)
@click.version_option(__version__, prog_name=brand.APP_NAME.lower())
@click.pass_context
def main(ctx, theme_opt, color, anim_opt):
    """Control a local model or a remote bridge from one terminal."""
    ctx.ensure_object(dict)
    overrides = {"theme": theme_opt}
    if color != "auto":
        overrides["color"] = color
    if anim_opt != "auto":
        overrides["animation"] = anim_opt
    ctx.obj.update(overrides)
    _apply_overrides(ctx)

    if ctx.invoked_subcommand is None:
        import sys
        if sys.stdin.isatty() and sys.stdout.isatty():
            from aurora_cli.workspace import run_workspace
            run_workspace()
            return
        from aurora_cli.workspace import dashboard
        dashboard()
        display.hint(f"Commandes : {', '.join(sorted(main.commands))}")


@main.command()
@click.option("--server", default="", help="Bridge address; otherwise discover the tunnel.")
@click.option("--api-key", envvar=brand.env_legacy("api_key"), default="", help="Key already allowed by the bridge.")
def connect(server, api_key):
    """Pair this machine with a remote bridge."""
    from aurora_cli.connect import connect as do_connect

    do_connect(server_url=server, api_key=api_key)


@main.command()
def status():
    """Show the bridge status and the local model situation."""
    from aurora_cli.core.discovery import scan
    from aurora_cli.core.router import Router

    data = config.load()
    display.kv(brand.APP_NAME, data.get("server_url") or "(non configuré)")
    display.kv("Mode", data.get("mode", "auto"))
    display.kv("Pont", "configuré" if config.is_configured() else "non configuré")

    try:
        result = scan(deep=True)
    except Exception as error:  # a failing scan must not hide the bridge state
        display.warning(f"Scan local incomplet : {error}")
        return

    every = [m for p in result.providers for m in p.models] + result.loose_models
    if every:
        display.hint("")
        display.models_table(every)
    display.hint("")

    router = Router(mode=data.get("mode", "auto"), prefer=data.get("provider", ""),
                    result=result)
    router.note_remote(config.is_configured())
    route = router.route()
    if not route.ok:
        display.warning(
            "Aucun modèle joignable. Démarrez un runtime local ou configurez un pont distant."
        )
        display.hint(route.reason)
    elif route.kind == "local":
        display.success(f"Routage : local · {route.target} · {router.pick_model(route=route)}")
    else:
        display.success(f"Routage : distant · {route.target}")


@main.command()
def doctor():
    """Check the terminal, the configuration and the local runtimes."""
    from aurora_cli.core.discovery import scan

    ctx = click.get_current_context().find_root()
    caps = ctx.obj["caps"]
    path = locations.config_file()
    console = display.view.console

    checks = [
        {"name": "Système", "status": "ok", "detail": f"{caps.os_name} / python {platform.python_version()}"},
        {"name": "Terminal", "status": "ok" if caps.is_tty else "warn", "detail": f"tty={caps.is_tty} {caps.width}x{caps.height}"},
        {"name": "Couleur", "status": "ok", "detail": caps.color_depth},
        {"name": "Unicode", "status": "ok" if caps.unicode else "warn", "detail": str(caps.unicode)},
        {"name": "Animation", "status": "ok", "detail": caps.animation},
        {"name": "Thème", "status": "ok", "detail": f"{ctx.obj['theme'].id} ({ctx.obj['theme_source']})"},
        {"name": "Configuration", "status": "ok" if path.exists() else "warn", "detail": str(path)},
    ]
    display.doctor_results(checks)

    display.hint("")
    result = scan()
    display.providers_table(result.providers)


@main.command()
@click.argument("level", required=False)
def permissions(level):
    """Show or set the bridge permission level."""
    if level:
        config.set("permission_level", level)
        display.success(f"Niveau de permission : {level}")
        return
    display.kv("Niveau", config.get("permission_level", "interactif"))
    display.hint("Niveaux : auto | full | ask | read | off")


@main.command()
@click.argument("request", required=True)
@click.option("--model", default="", help="Server model to use.")
@click.option("--server-workspace", default=None, help="Working directory on the bridge host.")
def run(request, model, server_workspace):
    """Send one request to the remote bridge."""
    from aurora_cli.mission import run_mission

    run_mission(request, model=model, server_workspace=server_workspace)


# --- Local commands -------------------------------------------------------

@main.command(name="theme")
@click.argument("name", required=False)
def theme_cmd(name):
    """List themes, preview one, or make it the default."""
    ctx = click.get_current_context().find_root()
    caps = ctx.obj["caps"]

    if not name:
        display.header("Thèmes disponibles")
        for candidate in themes.all_themes():
            marker = " (défaut)" if candidate.id == themes.DEFAULT_THEME_ID else ""
            display.kv(candidate.id, f"{candidate.label} — {candidate.description}{marker}")
        display.hint("")
        display.kv("Actuel", f"{ctx.obj['theme'].id} ({ctx.obj['theme_source']})")
        return

    if not themes.is_known(name):
        display.error(f"Thème inconnu : {name}")
        display.hint(f"Disponibles : {', '.join(themes.theme_ids())}")
        raise SystemExit(2)
    resolved, source = themes.resolve(name, caps)

    display.banner(theme=resolved)
    if click.confirm(f"Définir « {resolved.id} » comme thème par défaut ?", default=True):
        config.set("theme", resolved.id)
        display.success(f"Thème enregistré : {resolved.id} (dans {locations.config_file()})")
    else:
        display.hint(f"Thème prévisualisé uniquement ({source})")


@main.command()
@click.option("--shallow", is_flag=True, help="Skip the filesystem walk (faster, misses on-disk models).")
@click.option("--provider", default="", help="Only report providers matching this name.")
def models(shallow, provider):
    """List every model found on this machine."""
    from aurora_cli.core.discovery import scan

    display.header("Modèles détectés")
    result = scan(deep=not shallow)
    everything = [m for p in result.providers for m in p.models] + result.loose_models
    selected = everything
    if provider:
        needle = provider.lower()
        selected = [m for m in selected if needle in m.name.lower() or needle in m.provider.lower()]
    if not selected:
        display.warning("Aucun modèle trouvé.")
        display.hint(
            "Essayez --deep, ou lancez un runtime (Ollama, LM Studio, llama.cpp) "
            "puis relancez jobia models."
        )
        return
    display.models_table(selected)
    total = sum(m.size_bytes for m in selected if m.size_bytes)
    if total:
        display.hint(f"Total : {human_size(total)}")


@main.command()
@click.argument("request", required=False)
@click.option("--tier", default=None, type=click.Choice(["light", "balanced", "max"]),
              help="Forcer un niveau de qualité au lieu du conseil automatique.")
@click.option("--json", "as_json", is_flag=True, help="Sort machine readable.")
@click.option("--verbose", is_flag=True, help="Show technical model references.")
def ask(request, tier, as_json, verbose):
    """Read a request and say what should be done about it.

    Matches the request against the agents, checks what this machine already
    has, and proposes a plan. Downloads nothing: acting on the plan is a
    separate, explicit step.
    """
    from aurora_cli.core.discovery import scan
    from aurora_cli.core.intent import recommend
    from aurora_cli.core.machine import profile

    if not request:
        display.hint("Décris ce que tu veux faire, par exemple :")
        display.hint('  jobia ask "génère une image de chaise en bois"')
        display.hint('  jobia ask "résume ce document puis fais un modèle 3D"')
        return

    prof = profile()
    result = scan(deep=True)
    found = [m for p in result.providers for m in p.models] + result.loose_models

    plan = recommend(request, prof, found)
    if as_json:
        # Roles by default, the same rule the human view follows, so the
        # machine-readable form cannot become a back door for the names the
        # user asked not to see.
        click.echo(json.dumps({
            "request": request,
            "headline": plan.headline(),
            "unknown": plan.unknown,
            "machine": prof.to_dict(),
            "steps": [{
                "agent": s.agent.id,
                "label": s.agent.label,
                "because": s.because,
                "satisfied": s.satisfied,
                "local_possible": s.local_possible,
                "tier": s.tier,
                "already": [m.name if verbose else "installé" for m in s.satisfied_by],
                "would_download": (s.artifact.ref if verbose else
                                   (s.artifact.label if s.artifact else "")),
                "extra": list(s.extra_refs) if verbose else
                        [f"{len(s.extra_refs)} dépendance(s)" for _ in s.extra_refs],
                "blocked": s.blocked_reason,
            } for s in plan.steps],
            "downloads": ([a.ref for a in plan.planned_downloads()] if verbose else
                          [a.label for a in plan.planned_downloads()]),
        }, ensure_ascii=False, indent=2))
        return

    display.header(f"Demande : {request}")
    display.hint(plan.headline())
    if plan.unknown:
        display.warning(plan.note)
        return

    for step in plan.steps:
        tag = "PRÊT" if step.satisfied else ("LOCAL" if step.local_possible else "DISTANT")
        display.print(f"  [{tag}] {step.agent.label} — {step.because}")
        if step.satisfied:
            for model in step.satisfied_by:
                size = f" ({human_size(model.size_bytes)})" if model.size_bytes else ""
                name = model.name if verbose else "déjà installé"
                display.print(f"      déjà là : {name}{size}")
        elif step.local_possible and step.artifact:
            display.print(f"      à installer : {step.artifact.label} "
                          f"[{step.tier}] ~{human_size(step.artifact.bytes or 0)}, "
                          f"~{step.artifact.ram_gb:.0f} Go RAM")
            if step.tier_reason:
                display.hint(f"      {step.tier_reason}")
            if step.extra_refs:
                shown = (", ".join(step.extra_refs) if verbose
                         else f"{len(step.extra_refs)} dépendance(s)")
                display.print(f"      + {shown}")
        elif not step.local_possible:
            display.hint(f"      {step.blocked_reason}")

    downloads = plan.planned_downloads()
    if downloads:
        total = sum(a.bytes or 0 for a in downloads)
        display.hint(f"Total à télécharger : {human_size(total)} "
                     f"({len(downloads)} modèle(s), sans doublon).")
        first = downloads[0]
        forced = f" --tier {tier}" if tier else ""
        display.hint(f"Pour commencer : jobia provision-install {first.agent_id}{forced}")
    else:
        display.success("Rien à télécharger pour cette demande.")

    # If a 3D engine is available locally, offer to run it.
    from aurora_cli.core.engine3d import find_hunyuan3d
    engine = find_hunyuan3d()
    if engine and engine.available:
        display.hint(f"Moteur 3D local détecté : {engine.name} — "
                     f"jobia generate-3d --image <fichier> --output <fichier.glb>")


@main.command(name="create-3d")
@click.argument("character")
@click.option("--output", default=None, help="Output directory (default: JOBIA data outputs)")
@click.option("--prompt", default=None, help="Override the image generation prompt")
def create_3d(character, output, prompt):
    """Full autonomous pipeline: generate image → VLM check → 3D mesh.

    Example: jobia create-3d "Natsu version combattant"
    """
    from aurora_cli.core.pipeline import run_pipeline

    from aurora_cli.core.locations import data_dir
    output_dir = Path(output).expanduser() if output else data_dir() / "outputs" / "3d" / character.replace(" ", "_")

    display.header(f"Pipeline 3D : {character}")
    display.hint(f"Sortie : {output_dir}")
    display.hint("Étapes : génération image → vérification VLM → mesh 3D")

    result = run_pipeline(character, output_dir, image_prompt=prompt)

    if result.success:
        display.success(f"Mesh 3D généré : {result.final_output}")
        display.hint(f"Image de référence : {result.stages[0].output if result.stages else 'N/A'}")
    else:
        display.error("Le pipeline a échoué.")
        display.hint(result.log[-500:])


@main.command(name="generate-3d")
@click.option("--image", required=True, help="Source image (PNG/JPG)")
@click.option("--output", required=True, help="Output .glb path")
@click.option("--paint", is_flag=True, help="Apply PBR texturing")
def generate_3d(image, output, paint):
    """Generate a 3D mesh from an image using the local Hunyuan3D pipeline."""
    from aurora_cli.core.engine3d import find_hunyuan3d, generate_mesh

    engine = find_hunyuan3d()
    if not engine or not engine.available:
        display.error("Hunyuan3D non trouvé localement.")
        display.hint("JOBIA ne trouve pas encore le moteur 3D adapté ; utilise create-3d pour le préparer automatiquement.")
        return

    image_path = Path(image).expanduser().resolve()
    output_path = Path(output).expanduser().resolve()

    if not image_path.exists():
        display.error(f"Image introuvable : {image_path}")
        return

    display.header(f"Génération 3D : {image_path.name}")
    display.hint(f"Moteur : {engine.name}")
    display.hint(f"Sortie : {output_path}")
    display.hint("Génération en cours... (peut prendre plusieurs minutes)")

    success, log = generate_mesh(engine, image_path, output_path, paint=paint)

    if success:
        display.success(f"Mesh généré : {output_path}")
    else:
        display.error("La génération a échoué.")
        display.hint(log[-500:])


@main.command()
def machine():
    """Measure what this machine can run, right now.

    Read live, never cached: the verdict changes when you close a browser, and
    provisioning against a stale reading is how a machine gets OOM-killed
    mid-download.
    """
    from aurora_cli.core.machine import profile

    prof = profile()
    display.machine_table(prof)
    display.hint(prof.reason())


@main.command(name="agents-capabilities")
def agents_capabilities_cmd():
    """List the agents JOBIA can delegate, and whether this machine can host them.

    Roles, not model names: which model satisfies a job is decided later, from
    what the scan found and what this machine can actually run.
    """
    from aurora_cli.core.agents import AGENTS
    from aurora_cli.core.machine import profile

    prof = profile()
    display.agents_capability_table(AGENTS, prof)
    display.hint(prof.reason())


@main.command()
@click.argument("agent_id", required=False)
def provision(agent_id):
    """Show what an agent needs, what is already here, and what is missing.

    Never downloads anything on its own. Read-only by design: installing is a
    separate, explicit step so a status check can never cost gigabytes.
    """
    from aurora_cli.core import agents as agents_mod
    from aurora_cli.core.discovery import scan
    from aurora_cli.core.machine import profile
    from aurora_cli.core.planner import missing_for
    from aurora_cli.core import catalog
    from aurora_cli.core import provision as prov

    prof = profile()
    result = scan(deep=True)
    found = [m for p in result.providers for m in p.models] + result.loose_models

    if agent_id:
        agent = agents_mod.get(agent_id)
        if agent is None:
            display.error(f"Agent inconnu : {agent_id}")
            display.hint("Jobia agents-capabilities pour la liste.")
            raise SystemExit(2)
        selected = [agent]
    else:
        selected = list(agents_mod.AGENTS)

    for agent in selected:
        display.header(agent.label)
        display.print(f"  {agent.note}")
        already = missing_for(agent, found)
        if already:
            display.success(f"Déjà disponible sur cette machine :")
            for model in already:
                size = f" — {human_size(model.size_bytes)}" if model.size_bytes else ""
                display.print(f"    • {model.name} ({model.capability}){size}")
            prov.record_existing([m.path for m in already if m.path], agent=agent.id)
            display.hint("Aucun téléchargement nécessaire pour cet agent.")
            continue
        display.warning("Aucun modèle pour cet agent sur cette machine.")
        if not agent.can_run_here(prof):
            display.hint(
                f"Cette machine a {prof.total_ram_gb:.0f} Go de RAM : "
                f"l'agent {_agent_label(agent)} ne pourra pas tourner en local."
            )
            continue
        tiers = catalog.available_tiers(agent, prof)
        if not tiers:
            display.hint("Cette machine ne peut pas l'exécuter en local : "
                         "l'agent passera par le service distant.")
            continue
        display.print("  Choix possibles, adaptés à cette machine :")
        for tier_name in tiers:
            artifact = catalog.resolve(agent, prof, tier_name)
            if artifact is None:
                continue
            size = f"~{human_size(artifact.bytes or 0)}" if artifact.bytes else "distant"
            display.print(f"    • {artifact.label} — {size}, "
                          f"~{artifact.ram_gb:.0f} Go de RAM [{tier_name}]")
        display.hint(
            f"Aucun téléchargement lancé. Pour installer : "
            f"jobia provision-install {agent.id} --tier {catalog.recommended_tier(agent, prof)}"
        )


def _agent_label(agent) -> str:
    return agent.label


@main.command(name="provision-install")
@click.argument("agent_id")
@click.option("--tier", default=None, type=click.Choice(["light", "balanced", "max"]),
              help="Qualité voulue. Par défaut, la meilleure que la machine tienne.")
@click.option("--agent-job", default="", help="Intitulé de la tâche, pour le registre.")
@click.option("--yes", is_flag=True, help="Skip the confirmation prompt.")
@click.option("--dry-run", is_flag=True, help="Show what would be done, fetch nothing.")
@click.option("--verbose", is_flag=True, help="Show technical model references.")
@click.option("--strategy", default="auto",
              type=click.Choice(["auto", "now", "wait", "report"]),
              help="now: un modèle plus petit tout de suite. wait: tenir "
                   "jusqu'à ce que la mémoire suffise. report: rien changer, "
                   "expliquer ce qui occupe la mémoire.")
@click.option("--wait", "wait", default=0, type=int, metavar="SECONDES",
              help="With --strategy wait, how long to hold the request.")
def provision_install(agent_id, tier, agent_job, yes, dry_run, verbose, strategy, wait):
    """Install what an agent needs, after asking, and record its provenance.

    Never touches a model that was already on the machine: the scan decides
    first, and the ledger records only what this command fetched.
    """
    from aurora_cli.core import agents as agents_mod
    from aurora_cli.core import catalog, fetcher, governor
    from aurora_cli.core.discovery import scan
    from aurora_cli.core.machine import profile
    from aurora_cli.core.planner import missing_for

    agent = agents_mod.get(agent_id)
    if agent is None:
        display.error(f"Agent inconnu : {agent_id}")
        display.hint("Jobia agents-capabilities pour la liste.")
        raise SystemExit(2)

    prof = profile()
    result = scan(deep=True)
    found = [m for p in result.providers for m in p.models] + result.loose_models

    already = missing_for(agent, found)
    if already:
        display.header(agent.label)
        display.success("Un modèle est déjà présent, rien à installer :")
        for model in already:
            size = f" — {human_size(model.size_bytes)}" if model.size_bytes else ""
            display.print(f"  • {model.name} ({model.capability}){size}")
        return

    tiers = catalog.available_tiers(agent, prof)
    if not tiers:
        display.header(agent.label)
        display.warning("Cette machine ne peut pas exécuter cet agent en local.")
        display.hint(f"RAM : {prof.total_ram_gb:.0f} Go au total, "
                     f"{prof.free_ram_gb:.0f} Go libres. Utilise le service distant.")
        return

    # The governor turns the measurement into a decision. With a tier asked for
    # explicitly it will wait rather than quietly hand over a smaller model,
    # because "the most powerful one" and "whatever fits right now" are
    # different requests and only the second one is served by stepping down.
    decision = governor.decide(agent, prof, want=tier or "",
                               strategy=strategy)

    if decision.strategy == "report":
        # Asked to change nothing: explain and stop. Continuing here would
        # have started a download the user explicitly said not to start.
        display.header(f"{agent.label} — état de la mémoire")
        for reason in decision.reasons:
            display.hint(reason)
        if decision.held_by:
            display.print("  Ce qui occupe la mémoire :")
            for name, gb in decision.held_by:
                display.print(f"    {name} : {gb:.1f} Go")
            display.hint("Fermer une de ces applications libère la mémoire ; "
                         "JOBIA ne ferme rien à ta place.")
        if tiers:
            display.hint(f"Niveaux possibles ici : {', '.join(tiers)}.")
        return

    if decision.strategy == "wait":
        display.header(f"{agent.label} — en attente de mémoire")
        for reason in decision.reasons:
            display.hint(reason)
        for name, gb in decision.held_by:
            display.print(f"    {name} occupe {gb:.1f} Go")
        if decision.fallback_tier:
            display.hint(f"Ou alors : --strategy now installe le niveau "
                         f"« {decision.fallback_tier} » tout de suite.")
        if not wait or dry_run:
            display.hint("Relance avec --wait pour tenir jusqu'à ce que la "
                         "mémoire se libère.")
            return

        display.hint(f"Attente {max(0, wait)} s… (Ctrl-C pour arrêter)")

        def _update(free_gb, remaining):
            click.echo(f"  {free_gb:.1f} Go libres, encore {int(remaining)}s")

        if not governor.wait_until(agent, decision.tier, machine=prof,
                                   seconds=wait, on_update=_update):
            display.warning(f"Pas assez de mémoire après {wait}s.")
            for name, gb in decision.held_by:
                display.print(f"    {name} occupe {gb:.1f} Go")
            display.hint("Ferme une application, ou accepte un modèle plus "
                         "léger avec --strategy now.")
            return
        display.success("Mémoire libérée, le niveau demandé est possible.")
        prof = profile()

    chosen = decision.tier or decision.fallback_tier or catalog.recommended_tier(agent, prof)
    artifact = decision.artifact or catalog.resolve(agent, prof, chosen)
    if artifact is None:
        display.error(f"Aucun artefact pour le niveau {chosen} sur cette machine.")
        raise SystemExit(2)
    for reason in decision.reasons:
        display.hint(reason)

    ok, detail = fetcher.check_runtime(artifact)
    display.header(f"{agent.label} — {artifact.label}")
    # The technical ref is deliberately withheld. The user asked to be told
    # which role is being prepared, not which weights are being written, and a
    # ref in the confirmation prompt is also a ref in the scrollback, the
    # screenshot and the shell history.
    if verbose:
        display.print(f"  Modèle     : {artifact.ref}")
    else:
        display.hint("  (le détail technique est disponible avec --verbose)")
    display.print(f"  Runtime    : {artifact.runtime}")
    display.print(f"  Mémoire    : ~{artifact.ram_gb:.0f} Go")
    display.print(f"  Télécharg. : ~{human_size(artifact.bytes or 0)}")
    if artifact.note:
        display.print(f"  {artifact.note}")
    if artifact.required_with:
        display.hint(f"Dépend aussi d'un conditionneur ; --verbose pour le détail.")
    if not ok:
        display.warning(detail)
        if not dry_run:
            raise SystemExit(3)

    if not tiers or chosen != tiers[-1]:
        display.hint(f"Niveaux possibles ici : {', '.join(tiers)}.")

    if dry_run:
        try:
            plan = fetcher.preflight(artifact, prof)
        except fetcher.ProvisionError as exc:
            display.error(str(exc))
            raise SystemExit(3)
        display.hint(f"Destination : {plan.target}")
        for command in plan.commands:
            display.print(f"  $ {command[0]} …" if not verbose else f"  $ {' '.join(command)}")
        display.success("Simulation terminée, rien n'a été téléchargé.")
        return

    if prof.under_pressure:
        display.warning(
            f"{prof.free_ram_gb:.0f} Go de RAM libres. Télécharger maintenant "
            f"risque l'OOM pendant l'exécution.")

    if not yes and not click.confirm(
            f"Installer {agent.label} — {artifact.label} "
            f"(~{human_size(artifact.bytes or 0)}) ?",
            default=False):
        display.hint("Annulé. Aucun téléchargement.")
        return

    try:
        plan, size = fetcher.install(
            artifact, prof, agent=agent.id, job=agent_job,
            progress=lambda msg: display.hint(msg))
    except fetcher.ProvisionError as exc:
        display.error(str(exc))
        raise SystemExit(3)
    except KeyboardInterrupt:
        display.warning("Téléchargement interrompu.")
        display.hint("Relance la même commande : les fichiers déjà téléchargés "
                     "seront conservés.")
        raise SystemExit(130)

    display.success(f"{artifact.label} installé ({human_size(size)}).")
    display.hint("Enregistré au registre : jobia provision-clean pourra le "
                 "supprimer, et lui seul.")


@main.command(name="provision-clean")
@click.option("--yes", is_flag=True, help="Skip the confirmation prompt.")
def provision_clean(yes):
    """Remove what JOBIA installed. Never touches a model you already had.

    Only paths recorded in the ledger with owned=True are candidates, so a
    pre-existing model (a TRELLIS, a Hunyuan) is structurally unreachable from
    this command.
    """
    from aurora_cli.core import provision as prov

    records = prov.removable()
    if not records:
        display.success("Rien à supprimer : JOBIA n'a installé aucun modèle ici.")
        return

    display.header("Installé par JOBIA, suppression possible")
    total = 0
    for record in records:
        size = prov.record_size(record)
        total += size
        when = record.installed_at_label or "date inconnue"
        label = (record.path if record.kind == "ollama-model"
                 else Path(record.path).name)
        display.print(f"  • {label} — {prov.human(size)} ({when})")
    display.hint(f"Espace récupérable : {prov.human(total)}")

    if not yes and not click.confirm("Supprimer ces modèles ?", default=False):
        display.hint("Annulé. Rien n'a été supprimé.")
        return

    removed: list[str] = []
    for record in records:
        if record.kind == "ollama-model":
            tag = record.path.split(":", 1)[1] if ":" in record.path else record.path
            ok, detail = _ollama_remove(tag)
            if ok:
                removed.append(record.path)
                display.success(f"Désinstallé (Ollama) : {tag}")
            else:
                display.error(f"Impossible de désinstaller {tag} : {detail}")
            continue
        if not _safe_to_remove(record):
            display.warning(
                f"Refusé : {record.path} n'est pas un emplacement de modèle "
                f"reconnu. Rien n'a été touché.")
            continue
        try:
            path = Path(record.path)
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink(missing_ok=True)
            removed.append(record.path)
            display.success(f"Supprimé : {record.path}")
        except OSError as exc:
            display.error(f"Impossible de supprimer {record.path} : {exc}")

    if removed:
        prov.save_ledger([r for r in prov.load_ledger()
                          if r.path not in set(removed)])
    if not removed:
        display.warning("Rien n'a été supprimé.")


def _ollama_remove(tag: str) -> tuple[bool, str]:
    """Uninstall one Ollama tag, without touching the rest of the store.

    ``ollama rm <tag>`` removes exactly that model. Never a directory: the
    store is shared, and deleting it would take every model the user has.
    """
    if not shutil.which("ollama"):
        return False, "Ollama absent."
    try:
        import subprocess
        result = subprocess.run(["ollama", "rm", tag], capture_output=True,
                                text=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, str(exc)
    if result.returncode != 0:
        return False, (result.stderr or result.stdout or "").strip()[:200]
    return True, ""


def _safe_to_remove(record) -> bool:
    """Final guard before a delete: refuse anything outside a model location.

    The ledger is the primary guard; this is the backstop for a hand-edited
    or corrupted ledger. Refusing a path that is not under a model root means
    a bad record costs a warning, not a filesystem.
    """
    path = Path(record.path).expanduser()
    if not path.exists():
        return False
    root = (locations.data_dir() / "models").resolve()
    return not path.is_symlink() and path.resolve() != root and path.resolve().is_relative_to(root)


@main.command()
def providers():
    """List every local runtime found on this machine."""
    from aurora_cli.core.discovery import scan

    result = scan(deep=True)
    if not result.providers:
        display.warning("Aucun runtime local détecté.")
        display.hint(
            "Le CLI cherche ollama, llama-server, lms, vllm, localai, koboldcpp, "
            "jan et llamafile dans le PATH et les emplacements habituels."
        )
        return
    display.providers_table(result.providers)


@main.command()
def scan_cmd():
    """Full machine scan: binaries, open ports, model files."""
    from aurora_cli.core.discovery import scan

    result = scan(deep=True)
    live = sum(1 for p in result.providers if p.healthy)
    display.hint(f"Fournisseurs : {len(result.providers)} ({live} joignable(s)) · "
                 f"Modèles : {result.total_models} · "
                 f"{result.scanned_files} fichier(s) sur {result.scanned_roots} racine(s) · "
                 f"{result.duration_ms:.0f} ms")
    display.hint("")
    display.providers_table(result.providers)
    if result.loose_models:
        display.hint("")
        display.models_table(result.loose_models)


@main.command()
@click.argument("key", required=False)
@click.argument("value", required=False)
def config_cmd(key, value):
    """Show the configuration, or set one key."""
    path = locations.config_file()
    if not key:
        data = config.load()
        for name in sorted(data):
            shown = "********" if config.is_secret(name) else data[name]
            display.kv(name, shown)
        display.hint("")
        display.hint(f"Fichier : {path}")
        return
    if value is None:
        current = config.get(key, None)
        if current is None:
            display.error(f"Clé inconnue : {key}")
        else:
            shown = "********" if config.is_secret(key) else current
            display.kv(key, shown)
        return
    config.set(key, value)
    if config.is_secret(key):
        display.success(f"{key} enregistré (masqué).")
    else:
        display.success(f"{key} = {value}")


@main.command()
@click.argument("model", required=False)
def chat(model):
    """Chat with a local model. Never contacts the bridge."""
    from aurora_cli.chat import run_local_chat

    # Exit non-zero when no local model could be served, so scripts can branch.
    if run_local_chat(model=model) != 0:
        raise SystemExit(1)


@main.command(name="env")
def env_cmd():
    """Show what the CLI detected about this terminal."""
    ctx = click.get_current_context().find_root()
    caps = ctx.obj["caps"]
    import dataclasses

    for name, value in sorted(dataclasses.asdict(caps).items()):
        display.kv(name, value)
    display.hint("")
    display.kv("Thème actif", ctx.obj["theme"].id)


@main.command()
@click.argument("name", required=True)
def preview(name):
    """Play the banner of a theme without saving it."""
    if not themes.is_known(name):
        display.error(f"Thème inconnu : {name}")
        display.hint(f"Disponibles : {', '.join(themes.theme_ids())}")
        raise SystemExit(2)
    caps = capabilities.detect()
    resolved, _ = themes.resolve(name, caps)
    display.bind(theme=resolved, caps=caps).banner(caption=True)


# --- Remote surface kept from the original CLI ----------------------------

@main.group()
def agents():
    """Manage the bridge's agent registry."""


@agents.command(name="list")
def agents_list():
    """List agents known to the bridge."""
    display.agents_table([], _client().list_agents())


@agents.command(name="disable")
@click.argument("name")
def agents_disable(name):
    """Disable one agent on the bridge."""
    _client().disable_agent(name)
    display.success(f"Agent désactivé : {name}")


@main.group()
def mcp():
    """Manage the bridge's MCP servers."""


@mcp.command(name="list")
def mcp_list():
    """List MCP servers and their tools."""
    client = _client()
    display.mcp_table(client.list_mcp_servers(), client.list_mcp_tools())


@main.group()
def skills():
    """Manage the bridge's skills."""


@skills.command(name="list")
def skills_list():
    """List skills exposed by the bridge."""
    display.skills_table(_client().list_skills())


@main.command(name="prepare")
@click.argument("request")
@click.option("--yes", is_flag=True, help="Autoriser les téléchargements du plan.")
def prepare_cmd(request, yes):
    """Détecter puis installer les modèles manquants pour une demande."""
    from aurora_cli.workspace import prepare
    if not prepare(request, yes=yes):
        raise click.ClickException("Préparation incomplète ; voir le diagnostic ci-dessus.")


@main.command(name="source")
@click.argument("path")
def source_cmd(path):
    """Récupérer un seul fichier de référence du PC fixe, sans l'exécuter."""
    from aurora_cli.core.upstream import fetch_source
    try:
        target = fetch_source(path)
    except Exception as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(str(target))


@main.command(name="apps")
def apps_cmd():
    """Lister les applications et leur consommation mémoire."""
    from aurora_cli.core.apps import show_apps
    show_apps()


@main.command(name="close")
@click.argument("pid", type=int)
def close_cmd(pid):
    """Demander l'arrêt d'un processus précis, après confirmation."""
    from aurora_cli.core.apps import close_app
    try:
        close_app(pid)
    except Exception as exc:
        raise click.ClickException(str(exc)) from exc


if __name__ == "__main__":
    main()
