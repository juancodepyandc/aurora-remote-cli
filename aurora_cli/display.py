"""Terminal rendering.

Every function here takes its colours and glyphs from the active theme and
its intensity from the detected capabilities, so no call site decides what
the terminal can do. Nothing in this module knows the product name; it asks
:mod:`aurora_cli.brand`.

The public names used by the rest of the package are kept stable
(``success``, ``error``, ``banner``, ``code_diff``, ...) so the mission and
interactive layers did not have to be rewritten to use the theme system.
"""
from __future__ import annotations

import dataclasses
import time
from typing import Any, Iterable, Sequence
from rich import box as rich_box
from rich.console import Console, Group, RenderableType
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from aurora_cli import brand, themes
from aurora_cli.core import animation, capabilities
from aurora_cli.core.providers import ModelInfo, ProviderInfo, human_size

#: Boxes available to themes, resolved from a theme's short name.
BOXES: dict[str, Any] = {
    "rounded": rich_box.ROUNDED,
    "round": rich_box.ROUNDED,
    "heavy": rich_box.HEAVY,
    "double": rich_box.DOUBLE,
    "square": rich_box.SQUARE,
    "minimal": rich_box.MINIMAL,
    "ascii": rich_box.ASCII,
}



class Display:
    """Bound renderer: a console plus the theme and capabilities in force.

    Exposed as a module-level singleton :data:`view`, and as a class so tests
    can build one against a fake terminal.
    """

    def __init__(self, console_: "Console | None" = None,
                 theme: Theme | None = None,
                 caps: Capabilities | None = None):
        self.caps = caps or capabilities.current()
        self.console = console_ or _default_console(self.caps)
        self.theme = theme or themes.auto_select(self.caps)
        self.animator = animation.Animator(self.console, self.caps, self.theme)

    # --- context switching ----------------------------------------------

    def use_theme(self, name: str) -> tuple[str, str]:
        """Switch theme, returning the new theme and where it came from."""
        from aurora_cli import config

        chosen, origin = themes.resolve(name, self.caps, config.get("theme", ""))
        self.theme = chosen
        self.animator = animation.Animator(self.console, self.caps, chosen)
        return chosen.id, origin

    def refresh_capabilities(self) -> None:
        self.caps = capabilities.current(refresh=True)
        self.animator = animation.Animator(self.console, self.caps, self.theme)

    # --- primitives ------------------------------------------------------

    def style(self, slot: str, **flags) -> str:
        """Rich style string for a palette slot, adapted to the terminal."""
        return self.theme.style(slot, self.caps, **flags)

    def mark(self, slot: str, text: str, **flags) -> str:
        """Wrap text in Rich markup using an adapted palette slot."""
        return self.theme.markup(slot, self.caps, text, **flags)

    def glyph(self, name: str) -> str:
        return self.theme.glyph(name, self.caps)

    def box(self) -> Any:
        return BOXES.get(self.theme.box_for(self.caps), rich_box.ROUNDED)

    def panel(self, renderable: RenderableType, *, title: str = "",
              slot: str = "border", padding: tuple[int, int] = (0, 1),
              title_align: str | None = None) -> Panel:
        return Panel(
            renderable,
            title=f"[{self.style('primary')}]{title}[/]" if title else None,
            title_align=title_align or self.theme.title_align,
            border_style=self.style(slot),
            box=self.box(),
            padding=padding,
        )

    def print(self, renderable: RenderableType = "", **kwargs) -> None:
        self.console.print(renderable, **kwargs)

    def clear(self) -> None:
        self.console.clear()

    def rule(self, title: str = "") -> None:
        self.console.rule(f"[{self.style('muted')}]{title}[/]" if title else "")

    # --- status messages -------------------------------------------------

    def success(self, msg: str) -> None:
        self.console.print(f"{self.mark('success', self.glyph('ok'), bold=True)} {msg}")

    def error(self, msg: str) -> None:
        self.console.print(f"{self.mark('error', self.glyph('fail'), bold=True)} {msg}")

    def warning(self, msg: str) -> None:
        self.console.print(f"{self.mark('warning', self.glyph('warn'), bold=True)} {msg}")

    def info(self, msg: str) -> None:
        self.console.print(f"{self.mark('info', self.glyph('info'), bold=True)} {msg}")

    def hint(self, msg: str) -> None:
        self.console.print(f"[{self.style('muted', italic=True)}]{msg}[/]")

    def kv(self, key: str, value: str, *, slot: str = "text") -> None:
        self.console.print(
            f"  [{self.style('muted')}]{key}[/]  [{self.style(slot)}]{value}[/]"
        )

    # --- banner ----------------------------------------------------------

    def banner(self, status: dict | None = None, *, animate: bool = True,
               theme: "Theme | None" = None, caption: bool = False) -> None:
        """Startup banner, followed by whatever context is known.

        ``theme`` previews a theme without rebinding the session; ``caption``
        prints the tagline under the art, which the local REPL wants and
        ``--help`` does not.
        """
        target = theme if theme is not None else self.theme
        if caption and not target.banner.caption:
            target = dataclasses.replace(
                target, banner=dataclasses.replace(target.banner,
                                                   caption=brand.APP_TAGLINE))
        animator = (self.animator if target is self.theme
                    else animation.Animator(self.console, self.caps, target))
        if animate:
            animator.play_banner()
        else:
            self.console.print(animator.render_banner())
        self.console.print()
        if status is not None:
            self.system_panel(status)

    def system_panel(self, status: dict) -> None:
        """Connection and hardware summary shown under the banner."""
        hardware = status.get("hardware") or {}
        gpu = str(hardware.get("gpu", "n/d"))
        vram = hardware.get("vram_total_gb")
        if vram:
            gpu = f"{gpu} ({vram} Go)"
        table = Table(box=None, show_header=False, pad_edge=False, padding=(0, 2))
        table.add_column(style=self.style("muted"), justify="right", no_wrap=True)
        table.add_column(style=self.style("text"))
        table.add_row("Connexion", self.mark("success", "établie", bold=True))
        table.add_row("Tunnel", str(status.get("tunnel_url", "local")))
        table.add_row("GPU", gpu)
        table.add_row(
            "CPU",
            f"{hardware.get('cpu_cores', '?')} cœurs · RAM {hardware.get('ram_total_gb', '?')} Go",
        )
        self.console.print(self.panel(
            table, title=brand.APP_NAME.upper(), slot="primary", padding=(1, 2),
        ))
        self.console.print()

    # --- tables ----------------------------------------------------------

    def table(self, title: str, columns: Sequence[tuple[str, str]], rows: Iterable[Sequence[Any]],
              *, expand: bool = True) -> Table:
        """Build a themed table. ``columns`` is ``[(header, style_slot)]``."""
        grid = Table(title=f"[{self.style('primary')}]{title}[/]" if title else None,
                     box=self.box(), expand=expand, show_header=True,
                     header_style=self.style("primary", bold=True),
                     border_style=self.style("border"), title_style=self.style("accent", bold=True))
        for header, slot in columns:
            grid.add_column(header, style=self.style(slot))
        for row in rows:
            grid.add_row(*["" if cell is None else str(cell) for cell in row])
        return grid

    def key_value_table(self, title: str, pairs: Iterable[tuple[str, Any]]) -> Table:
        grid = Table(title=f"[{self.style('primary')}]{title}[/]" if title else None,
                     box=self.box(), show_header=False, expand=True,
                     border_style=self.style("border"), title_style=self.style("accent", bold=True))
        grid.add_column(style=self.style("muted"), justify="right", no_wrap=True)
        grid.add_column(style=self.style("text"))
        for key, value in pairs:
            grid.add_row(str(key), "" if value is None else str(value))
        return grid

    # --- model and provider listings ------------------------------------

    def models_table(self, models: Sequence[ModelInfo], *, title: str = "Modèles") -> None:
        if not models:
            self.hint("Aucun modèle détecté.")
            return
        rows = []
        for model in models:
            details = " · ".join(
                part for part in (model.params_label, model.quantization,
                                  model.weight_format, model.family) if part
            )
            if model.capability:
                details = " · ".join(part for part in (model.capability, details) if part)
            rows.append((
                model.name,
                model.provider or model.source,
                details or "—",
                model.size_label or "—",
            ))
        self.console.print(self.table(
            title,
            [("Modèle", "text"), ("Source", "info"), ("Détails", "muted"), ("Taille", "muted")],
            rows,
        ))

    def machine_table(self, machine) -> None:
        """What was measured on this host, and what it means for a download."""
        rows = [
            ("Système", f"{machine.os_name} {machine.arch}"),
            ("RAM totale", f"{machine.total_ram_gb:.0f} Go"),
            ("RAM libre", f"{machine.free_ram_gb:.0f} Go "
                          f"({machine.ram_pressure:.0%})"),
            ("Accélérateur", machine.accelerator),
            ("Disque libre", f"{machine.free_disk_gb:.0f} Go"),
            ("Cœurs CPU", str(machine.cpu_cores)),
            ("Catégorie", machine.size_class),
        ]
        if machine.vram_gb:
            rows.insert(3, ("VRAM", f"{machine.vram_gb:.0f} Go"))
        self.console.print(self.table(
            "Cette machine",
            [("Mesure", "text"), ("Valeur", "info")],
            rows,
        ))
        for note in machine.notes:
            self.hint(note)

    def agents_capability_table(self, agents, machine) -> None:
        """Roles JOBIA can delegate, marked with what this machine can run."""
        rows = []
        for agent in agents:
            if machine.usable and not machine.under_pressure and agent.can_run_here(machine):
                verdict = "local possible"
            elif agent.runtimes == ("remote",):
                verdict = "distant"
            elif agent.can_run_here(machine):
                verdict = "local si la mémoire se libère"
            else:
                verdict = "trop juste ici"
            rows.append((
                agent.label,
                agent.capability,
                f"{agent.ram_floor_gb:.0f} Go",
                verdict,
            ))
        self.console.print(self.table(
            "Agents",
            [("Agent", "text"), ("Capacité", "info"), ("Mémoire", "muted"),
             ("Sur cette machine", "muted")],
            rows,
        ))

    def providers_table(self, providers: Sequence[ProviderInfo]) -> None:
        if not providers:
            self.hint("Aucun fournisseur local détecté.")
            return
        rows = []
        for provider in providers:
            icon = (self.mark("success", self.glyph("ok")) if provider.healthy
                    else self.mark("muted", self.glyph("fail")))
            detail = provider.base_url or provider.binary_path or provider.detail
            rows.append((
                provider.label,
                icon,
                provider.status_label,
                f"{len(provider.models)} modèle(s)",
                detail[:48] if isinstance(detail, str) else detail,
            ))
        self.console.print(self.table(
            "Fournisseurs locaux",
            [("Nom", "text"), ("État", "text"), ("Statut", "muted"),
             ("Modèles", "muted"), ("Où", "muted")],
            rows,
        ))

    def permissions_table(self, levels: dict, current: str = "") -> None:
        rows = []
        for name, perms in levels.items():
            if isinstance(perms, dict):
                enabled = [key for key, value in perms.items() if value]
            else:
                enabled = []
            active = name == current
            label = self.mark("success", name, bold=True) if active else name
            if active:
                label += " " + self.glyph("arrow")
            rows.append((label, ", ".join(enabled[:6]) or "—",
                         "…" if len(enabled) > 6 else ""))
        self.console.print(self.table(
            "Niveaux de permission",
            [("Niveau", "text"), ("Capacités", "muted"), ("", "muted")],
            rows,
        ))

    def skills_table(self, skills: Sequence[dict]) -> None:
        if not skills:
            self.hint("Aucune compétence chargée.")
            return
        rows = [
            (s.get("name", "?"), s.get("level", ""),
             s.get("description", "") or "",
             ", ".join((s.get("triggers") or [])[:3]))
            for s in skills
        ]
        self.console.print(self.table(
            "Compétences",
            [("Nom", "text"), ("Niveau", "info"), ("Description", "muted"),
             ("Déclencheurs", "muted")],
            rows,
        ))

    def sessions_table(self, sessions: Sequence[dict]) -> None:
        if not sessions:
            self.hint("Aucune session.")
            return
        rows = [
            (s.get("id", "?"), (s.get("created_at") or "")[:19],
             str(s.get("message_count", 0)), s.get("permissions", ""))
            for s in sessions
        ]
        self.console.print(self.table(
            "Sessions",
            [("Identifiant", "text"), ("Créée", "muted"), ("Messages", "muted"),
             ("Permissions", "muted")],
            rows,
        ))

    def agents_table(self, official: Sequence[dict], dynamic: Sequence[dict]) -> None:
        self.console.print(
            f"\n[{self.style('primary', bold=True)}]Agents officiels "
            f"({len(official)})[/]")
        for agent in official:
            icon = (self.mark("success", self.glyph("ok")) if agent.get("enabled", True)
                    else self.mark("error", self.glyph("fail")))
            self.console.print(
                f"  {icon} [{self.style('text', bold=True)}]{agent.get('name', '?')}[/]"
                f"  [{self.style('muted')}]{agent.get('description', '')}[/]"
            )
        if dynamic:
            self.console.print(
                f"\n[{self.style('secondary', bold=True)}]Agents dynamiques "
                f"({len(dynamic)})[/]")
            for agent in dynamic:
                self.console.print(
                    f"  [{self.style('secondary')}]{self.glyph('bullet')}[/]"
                    f" [{self.style('text', bold=True)}]{agent.get('name', '?')}[/]"
                    f"  [{self.style('muted')}]{agent.get('role', '')}[/]"
                )
        self.console.print()

    def connections_table(self, connections: Sequence[dict]) -> None:
        if not connections:
            self.hint("Aucune connexion déclarée.")
            return
        rows = [
            (c.get("name") or c.get("service", ""),
             self.mark("success", "actif") if c.get("active") else self.mark("muted", "inactif"),
             ", ".join(c.get("capabilities") or []) or "—")
            for c in connections
        ]
        self.console.print(self.table(
            "Connexions",
            [("Service", "text"), ("État", "text"), ("Capacités", "muted")],
            rows,
        ))

    #: Check states mapped to the palette slot and glyph used for them.
    _CHECK_STATES = {
        "ok": ("success", "ok"),
        "warn": ("warning", "warn"),
        "error": ("error", "fail"),
        "fail": ("error", "fail"),
    }

    def doctor_results(self, checks: Sequence[dict]) -> None:
        self.console.print()
        for check in checks:
            state = str(check.get("status", "")).lower()
            if not state:
                state = "ok" if check.get("ok") else "error"
            slot, glyph_key = self._CHECK_STATES.get(state, ("muted", "info"))
            detail = check.get("detail") or check.get("error") or check.get("message") or ""
            self.console.print(
                f"  {self.mark(slot, self.glyph(glyph_key))} "
                f"[{self.style('text')}]{check.get('name', '?')}[/]"
                + (f"  [{self.style('muted')}]{detail}[/]" if detail else "")
            )
        self.console.print()

    # --- content ---------------------------------------------------------

    def markdown(self, text: str, *, title: str = "", slot: str = "primary") -> Panel:
        return self.panel(
            Markdown(text, justify="left", code_theme=self.theme.scan_style),
            title=title, slot=slot, padding=(1, 2),
        )

    def mission_summary(self, event: dict) -> None:
        result = str(event.get("result", ""))
        self.console.print(self.panel(
            Text(result, style=self.style("success")),
            title=f"{self.glyph('ok')} Mission terminée", slot="success", padding=(1, 2),
        ))

    def code_diff(self, filename: str, diff_lines: Sequence[str]) -> None:
        """Render a unified diff with line numbers and per-sign colouring."""
        self.console.print()
        self.console.print(
            f"[{self.style('secondary', bold=True)}]{self.glyph('bullet')} "
            f"{filename}[/]"
        )
        grid = Table(box=None, show_header=False, padding=(0, 1), pad_edge=False)
        grid.add_column(justify="right", style=self.style("muted"), width=4)
        grid.add_column()
        line_number = 0
        for raw in diff_lines:
            line = raw.rstrip("\n")
            if line.startswith("@@"):
                line_number = 0
                grid.add_row("", f"[{self.style('info')}]{line}[/]")
                continue
            marker = line[:1]
            body = line[1:] if marker in ("+", "-", " ") else line
            line_number += 1
            if marker == "+":
                grid.add_row(str(line_number),
                             f"[{self.style('success')}]{self.theme.diff_added} {body}[/]")
            elif marker == "-":
                grid.add_row(str(line_number),
                             f"[{self.style('error')}]{self.theme.diff_removed} {body}[/]")
            else:
                grid.add_row(str(line_number),
                             f"[{self.style('muted')}] {body}[/]")
        self.console.print(self.panel(grid, slot="secondary"))
        self.console.print()

    def files_received(self, entries: Sequence[dict]) -> None:
        """Table of artifacts downloaded to this machine."""
        if not entries:
            return
        rows = []
        for entry in entries:
            rows.append((
                entry.get("filename", ""),
                human_size(float(entry.get("size", 0) or 0)) or "—",
                entry.get("path", ""),
            ))
        self.console.print(self.table(
            "Fichiers reçus",
            [("Fichier", "text"), ("Taille", "muted"), ("Emplacement", "muted")],
            rows,
        ))

    # --- motion ----------------------------------------------------------

    def thinking(self, label: str, started: float) -> Live:
        """A live panel that breathes while work is in flight."""
        if not self.caps.animate:
            return _NullLive()
        frames = self.animator.frames_for("dots")
        return _ThinkingLive(self, label, frames, started)

    def typing_effect(self, text: str, *, title: str = "", speed: float = 0.012) -> None:
        """Reveal text progressively, degrading to an instant print."""
        if not self.caps.animate or not self.theme.motion.typewriter_help:
            self.console.print(self.markdown(text, title=title))
            return
        shown = ""
        with Live(console=self.console, auto_refresh=False, transient=True) as live:
            for char in text:
                shown += char
                live.update(self.panel(
                    Markdown(shown, justify="left", code_theme=self.theme.scan_style),
                    title=title, slot="primary", padding=(0, 2),
                ), refresh=True)
                time.sleep(speed)
                if char == "\n":
                    time.sleep(speed * 4)

    def ask_password(self, prompt: str) -> str:
        """Prompt for a secret without echoing it."""
        from rich.prompt import Prompt

        self.console.print()
        self.console.print(
            f"[{self.style('warning', bold=True)}]🔒 {prompt}[/]")
        return Prompt.ask(f"[{self.style('primary')}]{prompt}[/]", password=True)


# --- Live helpers ---------------------------------------------------------


class _NullLive:
    """Stands in for :class:`rich.live.Live` when animation is off."""

    def __enter__(self) -> "_NullLive":
        return self

    def __exit__(self, *exc) -> None:
        return None

    def update(self, *args, **kwargs) -> None:
        return None

    def start(self) -> None:
        return None

    def stop(self) -> None:
        return None


class _ThinkingLive:
    """Spinning status panel with a live elapsed clock."""

    def __init__(self, view: Display, label: str, frames: Sequence[str], started: float):
        self.view = view
        self.label = label
        self.frames = list(frames)
        self.started = started
        self._frame = 0
        self._live: Live | None = None

    def _renderable(self) -> RenderableType:
        spinner = self.frames[self._frame % len(self.frames)]
        elapsed = int(time.time() - self.started)
        clock = f"{elapsed // 60:02d}:{elapsed % 60:02d}"
        text = Text.assemble(
            (f"{spinner} ", self.view.style("primary", bold=True)),
            (self.label, self.view.style("text")),
            (f"  {clock}", self.view.style("muted")),
        )
        return self.view.panel(text, title=brand.APP_NAME, slot="primary")

    def __enter__(self) -> "_ThinkingLive":
        self._live = Live(self._renderable(), console=self.view.console,
                          refresh_per_second=12, transient=True)
        self._live.start()
        return self

    def __exit__(self, *exc) -> None:
        if self._live is not None:
            self._live.stop()
            self._live = None

    def set_label(self, label: str) -> None:
        self.label = label
        if self._live is not None:
            self._live.update(self._renderable())

    def update(self, renderable: RenderableType | None = None) -> None:
        self._frame += 1
        if self._live is not None:
            self._live.update(renderable if renderable is not None else self._renderable())


# --- Module-level facade --------------------------------------------------

def _default_console(caps: Capabilities) -> "Console":
    """Apply the selected output policy when the renderer is bound."""
    systems = {"ansi": "standard", "color8": "standard",
               "color256": "256", "truecolor": "truecolor"}
    return Console(force_terminal=caps.color,
                   color_system=systems.get(caps.color_depth),
                   no_color=not caps.color, width=caps.width)


view = Display()


def bind(console_: "Console | None" = None,
         theme: Theme | None = None,
         caps: Capabilities | None = None) -> Display:
    """Rebind the facade, used by the CLI once options are parsed."""
    global view
    view = Display(console_, theme, caps)
    return view


# Backwards-compatible function surface. Keeping these means the mission and
# interactive layers keep working while their internals get replaced.

def clear() -> None:
    view.clear()


def banner(status_data: dict | None = None, *, animate: bool = True,
           theme: "Theme | None" = None, caption: bool = False) -> None:
    view.banner(status_data, animate=animate, theme=theme, caption=caption)


def header(title: str, subtitle: str = "") -> None:
    text = view.mark("text", title, bold=True)
    if subtitle:
        text += " " + view.glyph("bullet") + " " + view.mark("muted", subtitle)
    view.console.print(view.panel(text, slot="primary"))


def success(msg: str) -> None:
    view.success(msg)


def error(msg: str) -> None:
    view.error(msg)


def warning(msg: str) -> None:
    view.warning(msg)


def info(msg: str) -> None:
    view.info(msg)


def hint(msg: str) -> None:
    view.hint(msg)


def mark(slot: str, text: str, **flags) -> str:
    return view.mark(slot, text, **flags)


def style(slot: str, **flags) -> str:
    return view.style(slot, **flags)


def glyph(name: str) -> str:
    return view.glyph(name)


def kv(key: str, value: str) -> None:
    view.kv(key, value)


def status_display(data: dict) -> None:
    hardware = data.get("hardware") or {}
    bridge = data.get("bridge")
    ollama = data.get("ollama")
    rows = [
        ("Pont", view.mark("success", "actif") if bridge else view.mark("error", "inactif"),
         "port 3001"),
        ("Ollama", view.mark("success", "actif") if ollama else view.mark("error", "inactif"),
         f"{data.get('models_count', 0)} modèle(s)"),
        ("ComfyUI", view.mark("success", "actif") if data.get("comfyui")
         else view.mark("warning", "veille"), ""),
        ("GPU", str(hardware.get("gpu", "n/d")),
         f"{hardware.get('vram_free_gb', 0)}/{hardware.get('vram_total_gb', 0)} Go libres"),
        ("RAM", f"{hardware.get('ram_gb', 0)} Go", str(hardware.get("os", ""))),
    ]
    view.console.print(view.table(
        "État du serveur",
        [("Composant", "text"), ("État", "text"), ("Détail", "muted")],
        rows,
    ))


def models_table(models: Sequence[ModelInfo]) -> None:
    view.models_table(models)


def print(renderable: RenderableType = "", **kwargs) -> None:
    """Print through the themed view, so ad-hoc lines match the theme."""
    view.print(renderable, **kwargs)


def machine_table(machine) -> None:
    view.machine_table(machine)


def agents_capability_table(agents, machine) -> None:
    view.agents_capability_table(agents, machine)


def providers_table(providers: Sequence[ProviderInfo]) -> None:
    view.providers_table(providers)


def tools_table(tools: Sequence[dict]) -> None:
    rows = [
        (t.get("name", ""), t.get("desc") or t.get("description", ""),
         t.get("source") or "intégré")
        for t in tools
    ]
    view.console.print(view.table(
        "Outils",
        [("Nom", "text"), ("Description", "muted"), ("Source", "muted")],
        rows,
    ))


def agents_table(official: Sequence[dict], dynamic: Sequence[dict]) -> None:
    view.agents_table(official, dynamic)


def sessions_table(sessions: Sequence[dict]) -> None:
    view.sessions_table(sessions)


def permissions_display(levels: dict, current: str = "") -> None:
    view.permissions_table(levels, current)


def skills_table(skills: Sequence[dict]) -> None:
    view.skills_table(skills)


def mcp_table(servers: Sequence[dict], tools: Sequence[dict] | None = None) -> None:
    if servers:
        rows = [(s.get("name", ""), s.get("command", ""), s.get("source", ""))
                for s in servers]
        view.console.print(view.table(
            "Serveurs MCP",
            [("Serveur", "text"), ("Commande", "muted"), ("Source", "muted")],
            rows,
        ))
    if tools:
        rows = [(t.get("server", ""), t.get("name", ""),
                 t.get("description", "") or "") for t in tools]
        view.console.print(view.table(
            "Outils MCP",
            [("Serveur", "muted"), ("Outil", "text"), ("Description", "muted")],
            rows,
        ))


def connections_table(connections: Sequence[dict]) -> None:
    view.connections_table(connections)


def doctor_results(checks: Sequence[dict]) -> None:
    view.doctor_results(checks)


def code_diff(filename: str, diff_lines: Sequence[str]) -> None:
    view.code_diff(filename, diff_lines)


def mission_summary(event: dict) -> None:
    view.mission_summary(event)


def files_received(entries: Sequence[dict]) -> None:
    view.files_received(entries)


def ask_password(prompt: str) -> str:
    return view.ask_password(prompt)


def typing_effect(text: str, *, title: str = "", speed: float = 0.012) -> None:
    view.typing_effect(text, title=title, speed=speed)


def thinking(label: str, started: float):
    return view.thinking(label, started)


def model_rows(models: Iterable[ModelInfo]) -> list[dict[str, str]]:
    return [m.as_row() for m in models]


def group(*renderables: RenderableType) -> Group:
    return Group(*renderables)
