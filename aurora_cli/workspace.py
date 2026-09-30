"""Interactive terminal workspace; discovered capabilities drive execution."""
from __future__ import annotations

from pathlib import Path

import click
from rich import box
from rich.columns import Columns
from rich.panel import Panel
from rich.text import Text

from aurora_cli import config, display, themes
from aurora_cli.core import catalog, fetcher, governor
from aurora_cli.core.agents import get
from aurora_cli.core.discovery import scan
from aurora_cli.core.intent import recommend
from aurora_cli.core.machine import profile
from aurora_cli.core.router import Router


def dashboard(result=None):
    view = display.view
    theme, caps = view.theme, view.caps
    view.banner(caption=True)
    cards = [
        ("CRÉER", "Images · personnages · modèles 3D\nDécris ton résultat, connu ou inventé."),
        ("TRAVAILLER", "Code · rédaction · conversation\nModèles locaux et missions sur ton PC fixe."),
        ("PRÉPARER", "Détection · mémoire · installation\n/models  /apps  /prepare ta demande"),
    ]
    panels = [Panel(Text(body, style=theme.style("text", caps)),
                    title=Text(title, style=theme.style("accent", caps, bold=True)),
                    border_style=theme.style("primary", caps),
                    box=box.ROUNDED if caps.unicode else box.ASCII, padding=(1, 2))
              for title, body in cards]
    view.console.print(Columns(panels, equal=True, expand=True))
    if result is not None:
        active = sum(p.healthy for p in result.providers)
        view.console.print(Text(
            f"  {active} moteur(s) actif(s)  ·  {result.total_models} modèle(s) détecté(s)"
            f"  ·  thème {theme.id}  ·  mode {config.get('mode', 'auto')}",
            style=theme.style("secondary", caps)))
    display.hint("/theme otter · /models · /apps · /help · /quit")


def prepare(request: str, *, yes=False):
    """Reuse local files, choose a fitting tier and install missing artifacts."""
    result = scan(deep=True)
    machine = profile()
    models = [m for p in result.providers for m in p.models] + result.loose_models
    plan = recommend(request, machine, models)
    if plan.unknown:
        plan = recommend("discussion : " + request, machine, models)
    downloads = []
    for step in plan.steps:
        if step.satisfied:
            display.info(f"{step.agent.label} : fichiers détectés, moteur à vérifier.")
            continue
        decision = governor.decide(step.agent, machine, strategy="now")
        if not decision.artifact:
            display.warning(decision.explain())
            display.hint("/apps pour identifier les applications à fermer, puis réessaie.")
            return False
        artifact = decision.artifact
        display.info(f"{step.agent.label} : {artifact.ref}, ~{(artifact.bytes or 0)/1024**3:.1f} Go")
        downloads.append(artifact)
        for ref in artifact.required_with:
            downloads.append(catalog.Artifact(
                agent_id=step.agent.id, tier=artifact.tier, runtime="huggingface",
                ref=ref, label=ref))
    if not downloads:
        return True
    display.hint("Les tailles sont estimées ; certaines dépendances ont une taille inconnue.")
    if not yes and not click.confirm("Installer ces modèles pour poursuivre ?", default=False):
        return False
    for artifact in catalog.merge_downloads(downloads):
        from aurora_cli.core.bootstrap import ensure_hf, start_ollama
        if artifact.runtime == "huggingface":
            ensure_hf()
        elif artifact.runtime == "ollama":
            start_ollama()
        ok, detail = fetcher.check_runtime(artifact)
        if not ok:
            display.warning(detail)
            return False
        try:
            fetcher.install(artifact, profile(), job=request, progress=display.hint)
        except fetcher.ProvisionError as exc:
            display.error(str(exc))
            return False
    return True


def execute(request: str, history: list, *, local_only=False):
    result = scan(deep=True)
    plan = recommend(request, profile(),
                     [m for p in result.providers for m in p.models] + result.loose_models)
    media = any(s.agent.capability not in {"llm", "code"} for s in plan.steps)
    mode = "local" if local_only else config.get("mode", "auto")
    if any(s.agent.id in {"3d", "3d-texture"} for s in plan.steps):
        # A configured bridge is the fixed workstation requested by the user.
        # Try it first for complex 3D work; local execution remains an
        # automatic fallback if the bridge is temporarily unreachable.
        if mode != "local" and config.is_configured():
            from .bridge import Bridge
            from .mission import run_mission
            display.info("Demande 3D complexe : utilisation automatique du PC fixe via AuroraIA.")
            if run_mission(Bridge(), request, permissions="AUTONOMOUS"):
                return
            display.warning("Le PC fixe ne répond pas ; reprise automatique en local.")
        from .core import locations
        from .core.pipeline import run_pipeline
        # Honour a natural destination such as "dans le dossier Documents"
        # without forcing a flag or a machine-specific absolute path.
        destination = locations.data_dir() / "outputs" / "3d" / "autonomous"
        request_lower = request.lower()
        if "documents" in request_lower or "document" in request_lower:
            destination = Path.home() / "Documents" / "JOBIA" / "3d"
        output_dir = destination
        display.info("Pipeline autonome : image → contrôle → forme 3D → texture PBR → validation.")
        result_3d = run_pipeline(request, output_dir, texture=True)
        if result_3d.success:
            display.success(f"Résultat 3D validé : {result_3d.final_output}")
        else:
            display.error("Le pipeline 3D n'a pas atteint son gate qualité.")
            display.hint(result_3d.log[-800:])
        return
    if media and mode != "remote" and all(s.agent.id == "image" for s in plan.steps):
        from aurora_cli.core.images import generate
        if generate(request, result):
            return
    if media or mode == "remote":
        if mode != "local" and config.is_configured():
            display.info("Cette tâche utilise les outils du PC fixe via le pont.")
            from aurora_cli.mission import run_mission
            run_mission(request)
            return
        display.hint("Analyse des modèles nécessaires à cette création…")
        for step in plan.steps:
            display.info(f"{step.agent.label} : {len(step.satisfied_by)} modèle(s) local(aux) détecté(s).")
        display.hint("/prepare suivi de ta demande permet de télécharger les poids manquants.")
        display.warning("Les poids seuls ne constituent pas un moteur de génération.")
        display.hint("Pour exécuter cette création avec les pipelines du PC fixe : jobia connect, puis réessaie.")
        return
    router = Router(mode=mode, prefer=config.get("provider", ""), result=result)
    router.note_remote(config.is_configured() and not local_only)
    route = router.route()
    if route.kind == "remote":
        from aurora_cli.mission import run_mission
        run_mission(request)
        return
    if not route.ok or not route.models:
        if not prepare("discussion : " + request, yes=True):
            return
        router.rescan(deep=True)
        route = router.route()
    if not route.ok or not route.models:
        display.warning("Modèle préparé, mais aucun moteur ne le sert encore.")
        display.hint("Démarre Ollama (ollama serve) ou charge le modèle dans LM Studio, puis réessaie ici.")
        return
    chosen = router.pick_model(route=route)
    display.info(f"Local · {route.target} · {chosen}")
    messages = history[-20:] + [{"role": "user", "content": request}]
    answer = []
    try:
        for chunk in route.runtime.stream(chosen, messages):
            if chunk.error:
                raise RuntimeError(chunk.error)
            if chunk.text:
                display.view.console.print(chunk.text, end="", markup=False, highlight=False)
                answer.append(chunk.text)
    finally:
        display.view.console.print()
    if answer:
        history.extend([messages[-1], {"role": "assistant", "content": "".join(answer)}])
    else:
        display.warning("Le moteur a renvoyé une réponse vide ; /models pour changer de moteur.")


def run_workspace():
    from prompt_toolkit import PromptSession
    from prompt_toolkit.completion import WordCompleter
    from prompt_toolkit.styles import Style

    result = scan(deep=True)
    dashboard(result)
    commands = ["/theme", "/models", "/apps", "/close", "/prepare", "/clear", "/help", "/quit"]
    session = PromptSession(completer=WordCompleter(commands))
    history = []
    while True:
        try:
            view = display.view
            raw = session.prompt(
                [("class:prompt", view.theme.prompt_text(view.caps))],
                style=Style.from_dict(view.theme.prompt_toolkit_style(view.caps)),
                bottom_toolbar=f" JOBIA  /  {view.theme.id}  /  /help ").strip()
            if not raw:
                continue
            if raw in {"/quit", "/exit"}:
                return
            if raw in themes.theme_ids() or raw.startswith("/theme "):
                name = raw.split(maxsplit=1)[-1]
                if not themes.is_known(name):
                    display.warning("Thème inconnu : " + name)
                    continue
                view.use_theme(name)
                config.set("theme", name)
                view.console.clear()
                dashboard(result)
            elif raw == "/models":
                result = scan(deep=True)
                display.providers_table(result.providers)
                display.models_table([m for p in result.providers for m in p.models] + result.loose_models)
            elif raw == "/apps":
                from aurora_cli.core.apps import show_apps
                show_apps()
            elif raw.startswith("/close "):
                from aurora_cli.core.apps import close_app
                close_app(int(raw.split()[1]))
            elif raw.startswith("/prepare "):
                prepare(raw.split(maxsplit=1)[1])
            elif raw == "/clear":
                history.clear()
                view.console.clear()
                dashboard(result)
            elif raw.startswith("/"):
                display.hint(" · ".join(commands))
                display.hint("/close PID : demander la fermeture d'une application précise.")
            else:
                execute(raw, history)
        except EOFError:
            return
        except KeyboardInterrupt:
            display.warning("Interrompu. La session reste ouverte ; /quit pour sortir.")
        except Exception as exc:
            display.error(str(exc))
