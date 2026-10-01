"""Pure local chat session.

This module never touches the bridge. It resolves a model from the machine
scan, takes the runtime the router already built, and streams the answer.
Everything it renders comes from the active theme and the detected terminal
capabilities, so it behaves the same on a dumb pipe and on a full colour TTY.
"""
from __future__ import annotations

import sys
import time

from aurora_cli import config, display, themes
from aurora_cli.core import capabilities
from aurora_cli.core.discovery import scan
from aurora_cli.core.router import Mode, Router

HELP = (
    ("/model", "lister les modèles joignables"),
    ("/provider", "lister les sources locales"),
    ("/context", "relire l'historique"),
    ("/clear", "effacer l'écran sans perdre le contexte"),
    ("/new", "nouvelle conversation, ancienne archivée"),
    ("/theme", "changer de thème pour cette session"),
    ("/quit", "quitter"),
)


def _pick_route(router: Router, model: str):
    """Route in local mode and report the concrete failure if it cannot."""
    route = router.route(model)
    if not route.ok:
        display.error("Aucun runtime local actif.")
        display.hint(route.reason or "Démarrez Ollama, LM Studio, llama-server, vLLM…")
        return None
    if not route.models:
        display.warning(
            f"{route.target} répond mais n'expose aucun modèle."
        )
        display.hint("Chargez un modèle, puis relancez la commande.")
        return None
    if model and not any(m.name == model or model in m.name for m in route.models):
        available = ", ".join(sorted(m.name for m in route.models))
        display.error(f"Modèle introuvable : {model}")
        display.hint(f"Disponibles : {available}")
        return None
    return route


def _print_help() -> None:
    display.header("Commandes")
    for command, description in HELP:
        display.kv(command, description)


def run_local_chat(model: str = "") -> int:
    """Start a REPL bound to a local model. Returns a process exit code."""
    caps = capabilities.detect()
    theme, origin = themes.resolve(config.get("theme", ""), caps)
    view = display.bind(theme=theme, caps=caps)
    view.banner(caption=True)

    display.hint("Recherche des modèles locaux...")
    router = Router(mode=Mode.LOCAL.value, result=scan(deep=True))
    route = _pick_route(router, model)
    if route is None:
        return 1

    chosen = router.pick_model(model, route=route)
    display.success(f"Session locale · {route.target} · {chosen}")
    display.hint(f"Thème {theme.id} ({origin}) · /? pour l'aide, /quit pour sortir.")

    from .core.conversation import Conversation, respond_with_fallback
    history = Conversation.open()
    if history:
        display.hint(f"Conversation reprise : {len(history) // 2} échange(s).")

    while True:
        try:
            # Rich's input() renders the themed prompt and keeps the streamed
            # answer above it on screen; plain input() would drop the colour.
            raw = view.console.input(view.mark("accent", theme.prompt_text(caps)))
        except (EOFError, KeyboardInterrupt):
            display.hint("")
            display.info("À bientôt.")
            return 0

        message = raw.strip()
        if not message:
            continue

        if message in ("/quit", "/exit", "/q"):
            display.info("À bientôt.")
            return 0
        if message in ("/help", "/?"):
            _print_help()
            continue
        if message == "/clear":
            view.console.clear()
            continue
        if message == "/new":
            history = history.new()
            display.success("Nouvelle conversation ; précédente archivée.")
            continue
        if message == "/context":
            if not history:
                display.hint("Historique vide.")
            for entry in history:
                display.kv(entry["role"], entry["content"])
            continue
        if message == "/provider":
            display.providers_table(router.result.providers)
            continue
        if message == "/model":
            for candidate in route.models:
                details = " · ".join(
                    part for part in (candidate.params_label, candidate.size_label) if part
                )
                display.kv(candidate.name, details or candidate.provider)
            continue
        if message.startswith("/theme ") or message in themes.theme_ids():
            name = message.split(maxsplit=1)[-1]
            if not themes.is_known(name):
                display.warning("Thème inconnu : " + name)
                continue
            view.use_theme(name)
            theme = view.theme
            view.console.clear()
            view.banner(caption=True)
            continue
        if message == "/theme":
            display.hint(themes.describe())
            display.hint("Relancez avec --theme <id> pour en choisir un.")
            continue

        started = time.monotonic()
        chunks: list[str] = []
        try:
            with display.thinking("génération", started):
                for text in respond_with_fallback(route.runtime, chosen, history, message, models=route.models):
                    chunks.append(text)
                    sys.stdout.write(text)
                    sys.stdout.flush()
        except KeyboardInterrupt:
            display.hint("")
            display.warning("Interrompu.")
            continue
        except Exception as exc:
            display.hint("")
            display.error(f"Échec de la génération : {exc}")
            display.hint(f"Source : {route.target}")
            continue

        print()
        answer = "".join(chunks).strip()
        if answer:
            display.hint(f"{len(chunks)} fragment(s) · {len(answer)} caractères")
        else:
            display.warning("Réponse vide.")
