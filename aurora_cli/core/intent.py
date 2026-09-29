"""Read a request, look at the machine, and say what should be done.

This is the part that makes the CLI feel intelligent rather than a catalogue
browser. The user types what they want in their own words; JOBIA works out
which jobs that implies, checks what the machine already has, and returns a
plan with a reason for every step.

Four things make the difference between a useful recommendation and a guess:

**Compound requests are normal.** "Fais une image d'une chaise puis un modèle
3D" is two jobs, not one. Splitting on conjunctions and matching each clause
separately is what stops the second half of the sentence from being ignored.

**Shared dependencies are counted once.** Texturing a mesh needs both the paint
pipeline and the vision conditioner; asking for "3D" and "texture" must not
propose downloading the conditioner twice, and the plan has to say so.

**The recommendation follows the machine, not the wish.** A 16 GB laptop and a
128 GB workstation are offered different things for the same sentence, and the
reason is stated rather than assumed.

**Nothing is done here.** This module returns a plan. Installing is a separate,
explicit step, so a recommendation can never cost gigabytes by accident.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import agents as agents_mod
from . import catalog
from .agents import Agent

#: Words that split a request into separate clauses.
_CONJUNCTIONS = (
    " puis ", " ensuite ", " apres ", "après ", " and then ", " after ",
    " et ensuite ", " puis faire ", " and ", " et ", " also ", " aussi ",
)

#: Requests that need a prior step to be worth anything, and the agent that
#: provides it. A 3D mesh from an image needs the image read first, and a user
#: asking only for a 3D model without a picture gets told the input is missing
#: rather than handed a download that cannot run.
#:
#: Only genuinely dependent jobs are listed. Adding a reader as a
#: prerequisite of every job that mentions the word "image" was wrong twice
#: over: asking to *generate* an image proposed a second 6 GB vision model
#: nobody needs, and the plan then proposed 12 GB for a single picture.
_PREREQUISITES = {
    "3d": ("vision",),
    "3d-texture": ("3d",),
}


@dataclass
class Step:
    """One job, resolved against the machine and the scan."""

    agent: Agent
    #: The model to use, when the catalogue can supply one for this machine.
    artifact: object | None = None
    tier: str = ""
    #: Models already on disk that cover this job.
    satisfied_by: list = field(default_factory=list)
    #: True when the machine can host the job at all.
    local_possible: bool = True
    #: Why this step is in the plan, in words meant to be read.
    because: str = ""
    #: Why this quality tier, given what is free right now.
    tier_reason: str = ""
    blocked_reason: str = ""
    #: Extra refs this step needs alongside its own artefact.
    extra_refs: tuple[str, ...] = ()

    @property
    def satisfied(self) -> bool:
        return bool(self.satisfied_by)

    @property
    def needs_download(self) -> bool:
        return not self.satisfied and self.artifact is not None


@dataclass
class Recommendation:
    """The answer to a request, ready to be shown and to be acted on."""

    request: str
    steps: list[Step]
    machine: object
    #: True when the wording matched nothing the catalogue knows about.
    unknown: bool = False
    note: str = ""

    @property
    def pending(self) -> list[Step]:
        return [s for s in self.steps if s.needs_download]

    @property
    def already_covered(self) -> list[Step]:
        return [s for s in self.steps if s.satisfied]

    @property
    def local_impossible(self) -> list[Step]:
        return [s for s in self.steps if not s.local_possible]

    def planned_downloads(self) -> list:
        """Artefacts to fetch, in order, without duplicates.

        A ref shared by two steps appears once: two agents needing the same
        conditioner is one download, and the plan says so rather than
        double-counting the disk it will use.
        """
        out: list = []
        for step in self.steps:
            if step.satisfied:
                continue
            for artifact in [step.artifact] + [
                catalog.Artifact(agent_id=step.agent.id, tier=step.tier,
                                 runtime="huggingface", ref=ref, label=ref)
                for ref in step.extra_refs
            ]:
                if artifact is not None:
                    out.append(artifact)
        return catalog.merge_downloads(out)

    def headline(self) -> str:
        if self.unknown:
            return "Je n'ai pas su deviner l'agent pour cette demande."
        done = len(self.already_covered)
        todo = len(self.pending)
        if done and not todo:
            return "Tout ce qu'il faut est déjà sur cette machine."
        if done and todo:
            return f"{done} agent(s) déjà prêts, {todo} à installer."
        if todo:
            return f"{todo} agent(s) à installer."
        return "Aucun agent nécessaire."


def _normalise(text: str) -> str:
    lowered = (text or "").lower()
    # Strip accents so "génère" and "genere" match the same keyword.
    return "".join(ch for ch in _strip_accents(lowered))


def _strip_accents(text: str) -> str:
    import unicodedata
    return "".join(
        ch for ch in unicodedata.normalize("NFD", text)
        if unicodedata.category(ch) != "Mn")


def _clauses(request: str) -> list[str]:
    """Split a request into the clauses that can each name a job."""
    normalised = f" {_normalise(request)} "
    parts = [normalised]
    for conjunction in _CONJUNCTIONS:
        expanded = [part for chunk in parts for part in chunk.split(conjunction)]
        parts = expanded
    return [part.strip() for part in parts if part.strip()]


def _match(agent: Agent, text: str) -> tuple[bool, str]:
    """Whether a clause asks for this agent, and the word that says so.

    Longest keyword wins so "modele 3d" beats "modele", which is the whole
    reason the keyword list is ordered by length rather than by position.
    """
    best = ""
    for keyword in agent.keywords:
        if keyword and re.search(r"(?<!\w)" + re.escape(_normalise(keyword)) + r"(?!\w)", text) and len(keyword) > len(best):
            best = keyword
    return bool(best), best


def match_agents(request: str) -> list[tuple[Agent, str]]:
    """Agents implied by a request, most specific match first.

    A noun naming a *result* must not also read as a request to work on one.
    In "genere une image de chaise" the image is the output, so the reader
    agent, whose job is looking at images, is not part of the request. The
    approach is to prefer agents that match an action, and only fall back to
    the bare noun when nothing else matched: the image agent is reached
    through "genere une image", which is a phrase, and the reader only through
    the word "image". Matching the action first, then dropping the noun, is
    what keeps a single picture request at one model instead of two.
    """
    found: dict[str, tuple[Agent, str]] = {}
    for clause in _clauses(request):
        text = _normalise(clause)
        direct: dict[str, tuple[Agent, str]] = {}
        indirect: dict[str, tuple[Agent, str]] = {}
        for agent in agents_mod.AGENTS:
            hit, keyword = _match(agent, text)
            if not hit:
                continue
            bucket = direct if _is_action(keyword) else indirect
            if agent.id not in bucket or len(keyword) > len(bucket[agent.id][1]):
                bucket[agent.id] = (agent, keyword)
        # An action wins over a bare noun. Only if the clause names nothing
        # but outputs does the noun alone decide the agent.
        chosen = direct or indirect
        for agent_id, pair in chosen.items():
            keyword = pair[1]
            if agent_id not in found or len(keyword) > len(found[agent_id][1]):
                found[agent_id] = pair
    return sorted(found.values(), key=lambda pair: -len(pair[1]))


#: Keywords that name a produced thing on their own. Reaching an agent through
#: one of these means the user is talking about the result, not about working
#: on an input.
_RESULT_NOUNS = (
    "image", "photo", "dessin", "modele 3d", "mesh", "video", "vidéo", "musique",
    "texte", "document", "son", "3d", "picture", "video", "music", "drawing",
)


def _is_action(keyword: str) -> bool:
    """Whether a keyword describes doing something rather than a thing."""
    return keyword not in _RESULT_NOUNS


def _covers(agent: Agent, model) -> bool:
    """Whether a scanned model can serve this agent.

    Capability is the primary test, and it has to be strict. An earlier version
    accepted any model with no declared capability for a text agent, on the
    theory that an unnamed GGUF is probably a language model. The scan proved
    that wrong in a way that matters: ``facebook/dinov2-giant`` was reported as
    "prêt" for the summarising agent, so the plan claimed everything was
    installed while also proposing a 15 GB download for the same request.

    The cost is asymmetric. Declaring a text model ready when it is not wastes
    one download; declaring a vision encoder ready for a writing task produces
    an agent that cannot do the job, and a plan that contradicts itself. So a
    match is required, and an unknown capability only counts when the artefact
    is recognisably a language model.
    """
    if not agent.capability:
        return False
    if model.capability == agent.capability:
        return True
    # Texturing and mesh generation are both 3D, and the scan now tells them
    # apart: a paint pipeline reports ``3d-texture`` and a shape pipeline
    # ``3d``. Each therefore satisfies only its own half. The name check is
    # kept for models that predate that distinction, where the directory is
    # all there is to go on.
    # A vision model can be asked to look at a video by sampling frames, so it
    # covers the video-reading agent; the reverse never holds.
    if agent.capability == "video" and model.capability == "vision":
        return True
    # An embedding model cannot read a picture and cannot write text. It is a
    # lookup index, not an agent, and pretending otherwise would offer an
    # indexer where a reader is needed.
    if agent.capability in ("embedding",):
        return False
    # A text agent can be served by a vision-language model, which reads and
    # writes; the reverse is false, so this stays one-directional.
    if agent.capability == "llm" and model.capability == "vision":
        return _is_vision_language(model)
    return False


def _is_vision_language(model) -> bool:
    """Whether a vision model is also a language model.

    A VLM such as qwen-vl can read a screenshot and answer about it in text; a
    bare encoder such as dinov2 or CLIP can only produce embeddings and cannot
    answer at all. Conflating the two is what made a summarising agent look
    installed because an image encoder was on disk.
    """
    name = model.name.lower()
    return any(marker in name for marker in ("vl", "-vlm", "llava", "vision-language",
                                             "qwen-vl", "qwen3-vl", "minicpm-v", "internvl"))


def recommend(request: str, machine, models) -> Recommendation:
    """Build the plan for a request against a measured machine and a scan."""
    matches = match_agents(request)
    if not matches:
        return Recommendation(request=request, steps=[], machine=machine,
                              unknown=True,
                              note=("Cette demande ne mentionne pas de tâche "
                                    "connue. Essaie par exemple « génère une "
                                    "image d'une chaise », « fais un modèle 3D », "
                                    "« résume ce document »."))

    steps: list[Step] = []
    for agent, keyword in matches:
        covered = [m for m in models if _covers(agent, m)]
        tiers = catalog.available_tiers(agent, machine)
        local_possible = bool(tiers)
        artifact = catalog.resolve(agent, machine, catalog.recommended_tier(agent, machine)) \
            if local_possible else None
        because = f"« {keyword} »"
        blocked = ""
        if not local_possible:
            blocked = (f"{machine.total_ram_gb:.0f} Go de RAM, il en faut "
                       f"{agent.ram_floor_gb:.0f} pour cet agent")
        steps.append(Step(
            agent=agent, artifact=artifact,
            tier=artifact.tier if artifact else "",
            satisfied_by=covered,
            local_possible=local_possible,
            because=because, blocked_reason=blocked,
            tier_reason=catalog.explain_tier(agent, machine, artifact.tier) if artifact else "",
            extra_refs=artifact.required_with if artifact else (),
        ))

    # A 3D job with no image and no text source is missing its input. Saying so
    # is more useful than handing over a download that cannot run.
    _flag_missing_input(request, steps)

    # Prerequisites, added only for steps that will actually have to run.
    # A step already covered by an installed model needs no preparation, and
    # adding one anyway is what produced a plan that said both "everything is
    # ready" and "install 15 GB" for the same sentence.
    for step in list(steps):
        if step.satisfied:
            continue
        for prerequisite in _PREREQUISITES.get(step.agent.id, ()):
            if not _mentions_source(request, "image"):
                continue
            if any(s.agent.id == prerequisite for s in steps):
                continue
            sub = agents_mod.get(prerequisite)
            if sub is None:
                continue
            covered_sub = [m for m in models if _covers(sub, m)]
            steps.append(Step(
                agent=sub,
                artifact=None if covered_sub else catalog.resolve(
                    sub, machine, catalog.recommended_tier(sub, machine)),
                tier=catalog.recommended_tier(sub, machine),
                local_possible=bool(catalog.available_tiers(sub, machine)),
                because=f"prérequis de {step.agent.label}",
                satisfied_by=covered_sub,
                tier_reason=catalog.explain_tier(
                    sub, machine, catalog.recommended_tier(sub, machine)),
            ))

    return Recommendation(request=request, steps=steps, machine=machine)


def _mentions_source(request: str, kind: str) -> bool:
    """Whether the request carries an actual *input* to work from.

    "Génère une image de chaise" mentions an image but supplies none: the
    image is the output. Treating the word as an input made a reader agent
    look required for every generation request, which is how a single picture
    came to need 12 GB across two models.
    """
    text = _normalise(request)
    if kind == "image":
        # Words that point at something supplied: a file, a photo already
        # taken, a reference. The verb matters, not the noun.
        supply = ("cette image", "ce dessin", "cette photo", "ce schema",
                  "ce plan", "ma photo", "mon image", "mon dessin", "la photo",
                  "le dessin", "l'image", "l'image donnee", "de cette image",
                  "du cette image", "de ce dessin", "de cette photo",
                  "a partir de cette image", "a partir de ce dessin",
                  "from this image", "from this photo", "this image",
                  "cette capture", "ce screenshot", "attachment",
                  "ce fichier", "ce document", "fichier joint")
        return any(word in text for word in supply)
    return any(w in text for w in ("texte", "text", "description", "prompt",
                                   "phrase", "consigne"))


def _flag_missing_input(request: str, steps: list[Step]) -> None:
    for step in steps:
        if step.agent.id == "3d" and not step.satisfied_by:
            if not _mentions_source(request, "image") and "texte" not in _normalise(request):
                step.because += " — aucune image ni description fournie : " \
                                "précise la source, sinon le résultat sera vide"
