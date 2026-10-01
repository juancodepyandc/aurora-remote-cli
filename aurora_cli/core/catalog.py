"""Concrete installable artefacts, per agent, per machine class.

The catalogue of *roles* (``agents.py``) deliberately names no model. This
module is the other half: the point where a role plus a measured machine plus
a requested quality tier resolves to something a runtime can actually fetch.

Every entry below was read out of the Linux project's own scripts rather than
invented, because an install list is a factual claim: a wrong repo id fails
after a multi-gigabyte download, which is the most expensive possible way to
discover a typo.

The three quality tiers map onto real differences, not on a scale invented to
look thorough:

* ``light``     a quantised small model, runs on a laptop under pressure.
* ``balanced``  the default: best quality per gigabyte, which is what a
                normal machine should get.
* ``max``       the largest variant the machine can hold. Only offered when the
                machine is genuinely idle, because it is the tier that turns a
                quiet desktop into an unresponsive one.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .agents import Agent

#: Quality tiers, cheapest first. The order is the fallback order.
TIERS = ("light", "balanced", "max")


@dataclass(frozen=True)
class Artifact:
    """One thing that can be fetched for one agent."""

    agent_id: str
    tier: str
    #: How to obtain it.
    runtime: str
    #: The reference the runtime understands: an Ollama tag or an HF repo id.
    ref: str
    #: What it is called to a human, and why it is the right pick.
    label: str
    #: Approximate download size; None when the provider must be asked.
    bytes: int | None = None
    #: RAM the model needs resident to be useful.
    ram_gb: float = 0.0
    #: Files inside a Hugging Face repo, when only part of it is wanted.
    include: tuple[str, ...] = ()
    #: Set when a second artefact is genuinely required for the job. A Hunyuan
    #: shape run without its VAE produces a broken mesh, so "two models" is a
    #: property of the job, not a preference.
    required_with: tuple[str, ...] = ()
    note: str = ""
    gated: bool = False
    tags: tuple[str, ...] = field(default_factory=tuple)
    revision: str = ''


_GO = 1024 ** 3

#: Ollama-backed agents. Tags are those the Linux project actually installs.
_OLLAMA: tuple[Artifact, ...] = (
    # --- Code / text ---
    Artifact("code", "light", "ollama", "qwen2.5-coder:7b", "Version légère",
             4_700_000_000, 6.0, note="Rapide, tient dans un portable chargé."),
    Artifact("code", "balanced", "ollama", "qwen2.5-coder:14b", "Équilibré",
             9_000_000_000, 12.0, note="Le meilleur rapport qualité/volume du catalogue."),
    Artifact("code", "max", "ollama", "qwen3-coder:30b", "Qualité maximale",
             18_000_000_000, 24.0, note="Machine idle requise."),
    Artifact("resume", "light", "ollama", "qwen2.5:7b", "Version légère",
             4_700_000_000, 6.0),
    Artifact("resume", "balanced", "ollama", "qwen3:14b", "Équilibré",
             9_000_000_000, 12.0),
    Artifact("resume", "max", "ollama", "qwen3.6:27b", "Qualité maximale",
             17_000_000_000, 22.0),
)

#: Vision agents read images; they never answer questions, so their model is
#: chosen for the encoder rather than for prose quality.
_VISION: tuple[Artifact, ...] = (
    Artifact("vision", "light", "ollama", "qwen2.5-vl:7b", "Version légère",
             6_000_000_000, 8.0, note="Lit images et texte."),
    Artifact("vision", "balanced", "ollama", "qwen3-vl:8b", "Équilibré",
             6_500_000_000, 9.0),
    Artifact("vision", "max", "ollama", "qwen3-vl:30b", "Qualité maximale",
             19_000_000_000, 24.0),
)

#: Image generation is a diffusion model, not a language model: it is fetched
#: from Hugging Face into a ComfyUI-style tree, never through Ollama.
#:
#: The three levels are three different models, not three settings of one. An
#: earlier version pointed "balanced" and "max" at the same weights and called
#: the extra 8 GB of RAM "quality settings", which made the top level a lie:
#: the user asking for the most powerful model would have been handed the same
#: one as the middle level. Quality that can be tuned is set at run time; only
#: a different model is a different level.
_IMAGE: tuple[Artifact, ...] = (
    Artifact('image', 'balanced', 'huggingface', 'black-forest-labs/FLUX.2-klein-4B',
             'FLUX.2 klein 4B', 16_000_000_000, 16.0, tags=('flux', 'flux2'),
             note='Génération et édition ; compatibilité Diffusers vérifiée avant utilisation.'),
    Artifact("image", "light", "huggingface", "stabilityai/sdxl-turbo",
             "Version légère", 7_000_000_000, 8.0,
             note="Few steps, works on any recent machine.", tags=("sd", "turbo")),
    Artifact("image", "balanced", "huggingface", "stabilityai/stable-diffusion-xl-base-1.0",
             "SDXL détaillé", 7_000_000_000, 10.0,
             include=("model_index.json", "scheduler/*", "tokenizer/*", "tokenizer_2/*",
                      "text_encoder/config.json", "text_encoder/*.fp16.safetensors",
                      "text_encoder_2/config.json", "text_encoder_2/*.fp16.safetensors",
                      "unet/config.json", "unet/*.fp16.safetensors",
                      "vae/config.json", "vae/*.fp16.safetensors"),
             note="Référence 1024 px, 30 étapes ; réutilisation des poids locaux.", tags=("sdxl",)),
    Artifact("image", "balanced", "huggingface", "black-forest-labs/FLUX.1-schnell",
             "Équilibré", 23_800_000_000, 16.0,
             note="Much better detail, 4 steps suffice.", tags=("flux",)),
    Artifact("image", "max", "huggingface", "black-forest-labs/FLUX.1-dev",
             "Qualité maximale", 33_000_000_000, 24.0,
             note="Reference image quality, 20 to 30 steps. Needs a licence "
                  "accepted on Hugging Face.", tags=("flux",)),
    Artifact("image-edition", "light", "huggingface", "stabilityai/sd-turbo",
             "Retouche légère", 7_000_000_000, 8.0,
             note="Upscale and background removal at low cost.",
             tags=("sd", "turbo")),
    Artifact("image-edition", "balanced", "huggingface",
             "stabilityai/stable-diffusion-xl-refiner-1.0",
             "Retouche équilibrée", 6_000_000_000, 10.0,
             note="Refines an existing image rather than generating one.",
             tags=("sd", "refiner")),
)

#: 3D. A shape run needs the DiT *and* the VAE; texturing needs the paint
#: pipeline. Each of those is a set, and the ledger must record every file so
#: cleanup can reclaim the whole thing.
_3D: tuple[Artifact, ...] = (
    Artifact("3d", "balanced", "huggingface", "tencent/Hunyuan3D-2.1",
             "Générateur 3D (forme + VAE)", 7_500_000_000, 16.0,
             include=("hunyuan3d-dit-v2-1/*", "hunyuan3d-vae-v2-1/*"),
             tags=("3d", "hunyuan")),
    Artifact("3d", "max", "huggingface", "microsoft/TRELLIS.2-4B",
             "Reconstruction 3D haute qualité", 16_000_000_000, 24.0,
             note="Un modèle, géométrie cohérente + PBR depuis une seule image.",
             tags=("3d", "trellis")),
    Artifact("3d-texture", "light", "huggingface", "tencent/Hunyuan3D-2.1",
             "Texture PBR", 6_400_000_000, 16.0,
             include=("hunyuan3d-paintpbr-v2-1/*",), tags=("3d", "hunyuan")),
    Artifact("3d-texture", "balanced", "huggingface", "tencent/Hunyuan3D-2.1",
             "Texture PBR + conditionneur", 11_000_000_000, 24.0,
             include=("hunyuan3d-paintpbr-v2-1/*",),
             required_with=("facebook/dinov2-giant",),
             note="Le paint-UNet a besoin de dinov2-giant comme conditionneur ; "
                  "sans lui le premier texturage se fige plusieurs minutes sans "
                  "rien afficher.", tags=("3d", "hunyuan")),
)

_AUDIO: tuple[Artifact, ...] = (
    Artifact("audio", "light", "huggingface", "openai/whisper-tiny",
             "Version légère", 150_000_000, 2.0),
    Artifact("audio", "balanced", "huggingface", "openai/whisper-large-v3",
             "Équilibré", 3_100_000_000, 8.0),
    Artifact("audio", "max", "huggingface", "openai/whisper-large-v3",
             "Qualité maximale", 3_100_000_000, 10.0),
)

_SPEECH = (Artifact('speech', 'light', 'huggingface', 'facebook/mms-tts-fra',
    'Narration française VITS', 300_000_000, 2.0,
    include=('config.json', 'model.safetensors', 'tokenizer_config.json', 'vocab.json', 'special_tokens_map.json'),
    note='Français, CPU ; licence CC-BY-NC-4.0 (usage non commercial). Pas de clonage.'),)

ARTIFACTS: tuple[Artifact, ...] = _OLLAMA + _VISION + _IMAGE + _3D + _AUDIO + _SPEECH


def for_agent(agent: Agent | str) -> list[Artifact]:
    agent_id = agent if isinstance(agent, str) else agent.id
    exact = [a for a in ARTIFACTS if a.agent_id == agent_id]
    if exact:
        return exact
    from dataclasses import replace
    from .agents import get
    role = get(agent_id) if isinstance(agent, str) else agent
    if role is None:
        return []
    base = {"llm": "resume", "code": "code", "vision": "vision"}.get(role.capability)
    return [replace(a, agent_id=agent_id) for a in ARTIFACTS if a.agent_id == base]


def resolve(agent: Agent, machine, tier: str = "balanced") -> Artifact | None:
    """The artefact for one agent on one machine at one quality tier.

    Capability is judged on *total* RAM, because total RAM is what the machine
    owns. Free RAM decides the recommended tier and the warning, not whether
    local is possible at all: a machine with 128 GB and 1 GB free can still
    run a 30 GB model, it will just swap and take a while, and that is the
    user's call to make rather than a silent downgrade to "remote".
    """
    options = for_agent(agent)
    if not options:
        return None
    for artifact in options:
        if artifact.tier == tier and artifact.ram_gb <= machine.total_ram_gb and supported(artifact, machine):
            return artifact
    return None


def supported(artifact, machine):
    """Do not advertise a CUDA-only engine as runnable from host RAM alone."""
    from aurora_cli.adapters import AdapterRegistry
    from .agents import get
    role = get(artifact.agent_id)
    runner, _ = AdapterRegistry().resolve(role.capability if role else artifact.agent_id, artifact.ref)
    return not runner or not runner.incompatibility(machine)


#: Memory left over for the model itself once the desktop keeps its share. Used
#: as the budget for a *default* recommendation, not as a hard limit: the user
#: can always override the tier.
_HEADROOM_GB = 2.0


def usable_ram_gb(machine) -> float:
    """RAM a model may safely count on *right now*.

    Total RAM is the wrong number to plan against. A 24 GB model does not fit
    in 12 GB of free RAM; it starts swapping and the machine crawls. The
    earlier version only looked at free RAM once the machine was already
    flagged as under pressure, so a machine sitting at 51% free happily had a
    24 GB / 23.8 GB download proposed to it, which is advice nobody should
    follow at that moment.
    """
    return max(0.0, machine.free_ram_gb - _HEADROOM_GB)


def recommended_tier(agent: Agent, machine) -> str:
    """The best tier this machine should *default* to right now.

    On a machine with room this is the largest it can hold. As free memory
    falls the choice steps down, because a model that fits in total RAM but
    not in what is currently free will thrash. This is a recommendation, not
    a refusal: ``--tier`` still overrides it.
    """
    tiers = available_tiers(agent, machine)
    if not tiers:
        return ""
    budget = usable_ram_gb(machine)
    # Walk from the largest tier down and take the first that fits. Walking
    # upwards and returning the first fit made every machine recommend the
    # smallest tier, which is how a 64 GB workstation got told to settle for
    # the 6 GB model.
    for tier in reversed(tiers):
        for artifact in for_agent(agent):
            if artifact.tier == tier and artifact.ram_gb <= budget and supported(artifact, machine):
                return tier
    # Nothing fits the current free memory. Offer the smallest tier anyway,
    # which is what a user asking for this job on a busy machine wants, and
    # say in the plan that it will be slow.
    return tiers[0]


def explain_tier(agent: Agent, machine, tier: str) -> str:
    """A sentence saying why this tier, so the choice is never a black box."""
    tiers = available_tiers(agent, machine)
    if not tier or not tiers:
        return ""
    artifacts = [a for a in for_agent(agent) if a.tier == tier]
    if not artifacts:
        return ""
    ram = max(a.ram_gb for a in artifacts)
    # The tier being compared against is the largest available, not the last
    # artefact of the chosen one. Reading it from this filtered list made the
    # sentence say "max would need 8 GB" while quoting the 8 GB light model.
    top_ram = max((a.ram_gb for a in for_agent(agent) if a.tier == tiers[-1]),
                  default=ram)
    if tier == tiers[-1]:
        if ram <= usable_ram_gb(machine):
            return (f"{machine.free_ram_gb:.0f} Go libres, la qualité maximale "
                    f"({ram:.0f} Go) tient sans swap.")
        return (f"Qualité maximale possible ({ram:.0f} Go), mais seulement "
                f"{machine.free_ram_gb:.0f} Go sont libres : prévoir du swap.")
    return (f"{machine.free_ram_gb:.0f} Go libres sur {machine.total_ram_gb:.0f} Go : "
            f"le niveau « {tier} » ({ram:.0f} Go) est le dernier à tenir "
            f"sans swap, « {tiers[-1]} » demanderait {top_ram:.0f} Go.")


def available_tiers(agent: Agent, machine) -> list[str]:
    """Which tiers this machine can hold at all, cheapest first."""
    out = []
    for tier in TIERS:
        for artifact in for_agent(agent):
            if artifact.tier == tier and artifact.ram_gb <= machine.total_ram_gb and supported(artifact, machine):
                out.append(tier)
                break
    return out


def merge_downloads(artifacts):
    """Merge component subsets of the same repository without dropping weights."""
    from dataclasses import replace
    merged = {}
    for artifact in artifacts:
        key = (artifact.runtime, artifact.ref)
        previous = merged.get(key)
        if previous is None:
            merged[key] = artifact
        else:
            includes = (tuple(dict.fromkeys(previous.include + artifact.include))
                        if previous.include and artifact.include else ())
            merged[key] = replace(previous, include=includes,
                                  bytes=max(previous.bytes or 0, artifact.bytes or 0),
                                  required_with=tuple(dict.fromkeys(previous.required_with + artifact.required_with)))
    return list(merged.values())
