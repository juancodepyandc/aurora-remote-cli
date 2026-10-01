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


def _remember_result(history, request, result):
    from .core.conversation import Conversation
    history.extend([{'role': 'user', 'content': request}, {'role': 'assistant', 'content': result}])
    if isinstance(history, Conversation):
        history.save()


def _remote_mission(request, history):
    from .bridge import Bridge
    from .mission import run_mission
    return run_mission(Bridge(), request, workspace=str(Path.cwd()),
                       permissions=config.get('default_permissions', 'SAFE'),
                       # A local archive UUID is not a session created by the
                       # remote server. Never send a fabricated bridge session.
                       session_id=getattr(history, 'state', {}).get('remote_session_id', ''))


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
        from .core.pipeline import run_pipeline
        from .core.request_spec import parse_creation_request
        spec = parse_creation_request(request)
        # A configured bridge is the fixed workstation requested by the user.
        # Try it first for complex 3D work; local execution remains an
        # automatic fallback if the bridge is temporarily unreachable.
        if spec.input_image is not None and mode != 'local' and config.is_configured():
            # The bridge currently transports text, not local attachments. A
            # path on this computer is not evidence the remote host can read it.
            display.hint("Image locale fournie : exécution locale ; le pont ne transfère pas encore les références.")
        if spec.input_image is None and mode != "local" and config.is_configured():
            display.info("Demande 3D complexe : utilisation automatique du PC fixe via AuroraIA.")
            if _remote_mission(request, history):
                return
            display.warning("Le PC fixe ne répond pas ; reprise automatique en local.")
        display.info("Image fournie → forme 3D → texture PBR → contrôles → livraison."
                     if spec.input_image is not None
                     else "Préparation → référence vérifiée → forme 3D → texture PBR → livraison.")
        if spec.input_image is not None:
            display.hint(f"Image d’entrée : {spec.input_image}")
        display.hint(f"Sujet : {spec.subject} · Destination : {spec.output_dir}")
        result_3d = run_pipeline(request, spec.output_dir, texture=True, progress=show_pipeline_stage)
        if result_3d.success:
            display.success(f"Fichier 3D livré, géométrie et textures contrôlées : {result_3d.final_output}")
        else:
            display.warning("Travail conservé ; le résultat demandé n'est pas encore livré.")
            if result_3d.checkpoint:
                display.hint(f"État et diagnostics : {result_3d.checkpoint}")
        remaining = [step.agent.label for step in plan.steps
                     if step.agent.id not in {'3d', '3d-texture', 'image', 'vision'}]
        pending = ''
        if remaining:
            pending = 'Autres étapes non exécutées par ce pipeline 3D local : ' + ', '.join(remaining)
            display.warning(pending)
        _remember_result(history, request, f"3D : {'livrée' if result_3d.success else 'non livrée'}. "
                         f"Fichier : {result_3d.final_output}. Diagnostics : {result_3d.checkpoint}. {pending}")
        return
    if media and mode != "remote" and all(s.agent.id == "image" for s in plan.steps):
        from aurora_cli.core.images import generate
        output = generate(request, result)
        if output:
            _remember_result(history, request, f'Image livrée après contrôle visuel : {output}')
        else:
            _remember_result(history, request, 'Image non livrée : le contrôle visuel a échoué ; diagnostics conservés.')
        return
    if media or mode == "remote":
        if mode != "local" and config.is_configured():
            display.info("Cette tâche utilise les outils du PC fixe via le pont.")
            _remote_mission(request, history)
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
        _remote_mission(request, history)
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
    role = next((s.agent.id for s in plan.steps if s.agent.capability in {'llm', 'code'}), 'resume')
    chosen = router.pick_model(route=route, role=role)
    if not chosen:
        display.warning('Aucun modèle servi ne tient dans le budget mémoire disponible pour cette tâche.')
        display.hint('/apps montre les ressources occupées. La conversation reste conservée.')
        return
    display.info(f"Local · {route.target} · {chosen}")
    from .core.conversation import respond_with_fallback
    try:
        for text in respond_with_fallback(route.runtime, chosen, history, request, models=route.models, role=role):
            display.view.console.print(text, end="", markup=False, highlight=False)
    finally:
        display.view.console.print()


def show_pipeline_stage(stage):
    if stage.status == "running":
        display.info(stage.log)
    elif stage.status == "failed":
        display.warning(f"{stage.name} : {stage.log[-800:]}")
    elif stage.status == "done":
        display.hint(f"{stage.name} : terminé ({stage.duration_s:.1f}s)")


def run_workspace():
    from prompt_toolkit import PromptSession
    from prompt_toolkit.completion import WordCompleter
    from prompt_toolkit.styles import Style

    result = scan(deep=True)
    dashboard(result)
    commands = ["/theme", "/models", "/apps", "/close", "/prepare", "/clear", "/new", "/context", "/help", "/quit"]
    session = PromptSession(completer=WordCompleter(commands))
    from .core.conversation import Conversation
    history = Conversation.open()
    if history:
        display.hint(f"Conversation reprise : {len(history) // 2} échange(s). /new pour une nouvelle conversation.")
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
                view.console.clear()
                dashboard(result)
            elif raw == "/new":
                history = history.new()
                display.hint("Nouvelle conversation ; la précédente reste archivée localement.")
            elif raw == "/context":
                for entry in history:
                    display.kv(entry['role'], entry['content'])
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
