#!/usr/bin/env python
"""Aurora 3D faithful-scene prompt composer.

Problem this solves
--------------------
The CLI / tunnel / extension 3D path (aurora_3d_pipeline.py -> enhance_flux_prompt
-> flux_reference_synth.synth_multiview) historically fed FLUX the *raw* user
prompt plus a couple of hardcoded brand cues. For a genuinely COMPOUND request —
a named celebrity + a decor + a mechanical apparatus + fluids + luminous effects
+ motion — nothing guaranteed the secondary elements survived into the reference
image. The diffusion model would happily collapse the scene to its dominant noun
(the person) and silently drop the alley, the steam, the neon, the turbine. The
result honored the request only "approximately" (the user's exact complaint).

Meanwhile the React UI path (ModelView.tsx -> buildFluxVisualDescription in
threeDIntent.ts) builds a much richer prompt. So UI and CLI/tunnel were NOT
aligned: same request, very different fidelity.

What this module does
----------------------
A single, deterministic, dependency-free scene decomposer. It scans the prompt
(bilingual FR/EN) and detects every requested FACET:

    identity (named real person / known character) | secondary subjects |
    decor / environment | mechanical apparatus | fluids | luminous-emissive |
    motion (body + mechanical) | materials

When the request is genuinely COMPOUND (>= 2 distinct facet families present), it
appends an explicit "MULTI-ELEMENT FIDELITY CONTRACT": one MUST-render line per
facet so the diffusion model cannot quietly simplify the scene. The user's
verbatim prompt always stays at the front; we never replace intent, we only make
the *implicit* requirement explicit.

For simple / single-facet prompts the composer is a no-op (returns the prompt
unchanged) so existing single-subject behavior is untouched — zero regression.

This is intentionally pure-stdlib so node-free unittest can exercise it and the
orchestrator can import it without any heavy dependency. It mirrors the facet
vocabulary used by buildCompoundSceneContract() in threeDIntent.ts so the UI and
CLI/tunnel paths stay aligned.

Schema: aurora.faithful_scene.v1
"""

from __future__ import annotations

import argparse
import json
import re
import sys


# Each facet family: a friendly label + a compiled bilingual detector.
# Detection is keyword-level on purpose: we are not trying to fully parse the
# sentence, only to prove a requested element exists so we can force it into the
# contract. Order is cosmetic (it only affects contract line ordering).
_FACET_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("decor", re.compile(
        r"\b(decor|d[ée]cor|sc[èe]ne|scene|environnement|environment|"
        r"background|arri[èe]re[\s-]?plan|paysage|landscape|cityscape|"
        r"ruelle|all[ée]e|alley|rue|street|ville|city|cit[ée]|"
        r"n[ée]o[\s-]?tokyo|cyberpunk\s+city|rooftop|toit|"
        r"for[êe]t|forest|d[ée]sert|desert|jungle|"
        r"int[ée]rieur|interior|pi[èe]ce|room|chambre|atelier|workshop|"
        r"temple|ru[ie]nes?|ruins?|grotte|cave|cavern|"
        r"sous\s+la\s+pluie|in\s+the\s+rain|night\s+city|ville\s+nocturne)\b",
        re.IGNORECASE,
    )),
    ("mechanical", re.compile(
        r"\b(m[ée]canique|mechanical|m[ée]canisme|mechanism|machine|machinery|"
        r"moteur|engine|turbine|h[ée]lice|propeller|r[ée]acteur|reactor|thruster|"
        r"bras\s+m[ée]canique|bras\s+articul[ée]|mechanical\s+arm|robotic\s+arm|"
        r"engrenage|gear|pignon|piston|v[ée]rin|actuator|hydraulic|hydraulique|"
        r"pneumatic|pneumatique|rouage|cog|servo|articulation|joint\s+m[ée]canique|"
        r"exosquelette|exoskeleton|cybern[ée]tique|cybernetic|prosth[èe]se|prosthetic|"
        r"rotor|crankshaft|vilebrequin|courroie|belt\s+drive|transmission)\b",
        re.IGNORECASE,
    )),
    ("fluids", re.compile(
        r"\b(eau|water|liquide|liquid|fluide|fluid|"
        r"pluie|rain|rainfall|averse|drizzle|"
        r"vapeur|steam|fum[ée]e|smoke|brume|brouillard|fog|mist|haze|"
        r"lave|lava|magma|"
        r"flaques?|puddles?|ruissel|ruissell|drip|dripping|gouttes?|droplets?|"
        r"[ée]claboussures?|splash|spray|jet\s+d[\s']?eau|water\s+jet|"
        r"cascades?|waterfalls?|vagues?|waves?|fontaines?|fountains?|"
        r"sang|blood|huile|oil|encre|ink|miel|honey|"
        r"[ée]coulement|flow|coul[ae]nt|streaming\s+water)\b",
        re.IGNORECASE,
    )),
    ("luminous", re.compile(
        # Stem + bounded inflection so French plurals AND conjugations survive
        # the closing \b (néons, étincelles, bioluminescents, éclairent, brillent)
        # — a literal list + trailing \b otherwise silently dropped the whole
        # luminous facet, the exact "à peu près" bug this contract prevents.
        r"\b(n[ée]ons?|neons?|lumineux|lumineuse[s]?|luminous|lumi[èe]res?|light\s+source|"
        r"glow(?:s|ing|ed)?|lueurs?|halos?|aura\s+lumineuse|"
        r"bioluminescen[a-zà-ÿ]{0,4}|luminescen[a-zà-ÿ]{0,4}|fluorescent[a-zà-ÿ]{0,2}|phosphorescent[a-zà-ÿ]{0,2}|"
        r"leds?|dels?|[ée]tincel[a-zà-ÿ]{0,5}|sparkl?[a-z]{0,4}|sparks?|"
        r"[ée]clats?|[ée]clair[a-zà-ÿ]{0,5}|illumin[a-zà-ÿ]{0,6}|scintill[a-zà-ÿ]{0,5}|brill[a-zà-ÿ]{0,5}|"
        r"flash(?:es|ing)?|lasers?|hologram(?:s|me|mes)?|holographi[a-zà-ÿ]{0,4}|"
        r"[ée]missif[s]?|emissive|incandescen[a-zà-ÿ]{0,3}|braises?|embers?|"
        r"refl[èe]t[a-zà-ÿ]{0,5}|reflet[\s]+lumineux|light\s+reflection|"
        r"enseignes?\s+lumineuses?|illuminated\s+sign|backlit|r[ée]tro[\s-]?[ée]clair[a-zà-ÿ]{0,5})\b",
        re.IGNORECASE,
    )),
    ("motion", re.compile(
        # Stem + bounded inflection: "tournent", "oscille", "jaillissent",
        # "brandissent" must not be lost the way a literal "tourne|tourner" list
        # loses 3rd-person plural and other conjugations. Bounds keep stems from
        # over-matching unrelated words.
        r"\b(tourn[a-zà-ÿ]{0,5}|spin(?:s|ning|ned)?|rotat(?:e|es|ed|ing|ion|ional)?|whirl(?:s|ing|ed)?|"
        r"roul[a-zà-ÿ]{0,5}|roll(?:s|ing|ed)?|avanc[a-zà-ÿ]{0,5}|"
        r"en\s+mouvement|in\s+motion|mov(?:e|es|ed|ing|ement)?|"
        r"vol[ae][a-zà-ÿ]{0,4}|fly|flies|flying|hover(?:s|ing|ed)?|"
        r"march[ae][a-zà-ÿ]{0,4}|walk(?:s|ing|ed)?|cour(?:s|t|ent|ir|ait|aient|u|ant)|run(?:s|ning)?|"
        r"dans[ae][a-zà-ÿ]{0,3}|danc(?:e|es|ing|ed)?|saut[ae][a-zà-ÿ]{0,3}|jump(?:s|ing|ed)?|"
        r"l[èe]v[a-zà-ÿ]{0,4}|rais(?:e|es|ing|ed)?|brandi[a-zà-ÿ]{0,5}|"
        r"salu[a-zà-ÿ]{0,4}|wav(?:e|es|ing|ed)?|frapp[a-zà-ÿ]{0,4}|punch(?:es|ing|ed)?|kick(?:s|ing|ed)?|"
        r"jaill[a-zà-ÿ]{0,6}|gush(?:es|ing|ed)?|erupt(?:s|ing|ed)?|spew(?:s|ing|ed)?|"
        r"swing(?:s|ing)?|balanc[a-zà-ÿ]{0,5}|oscill[a-zà-ÿ]{0,5}|vibr[a-zà-ÿ]{0,5}|puls[a-zà-ÿ]{0,4}|"
        r"battement[s]?|battant[s]?|se\s+d[ée]plac[a-zà-ÿ]{0,4}|mouvement[s]?|movement[s]?|articul[a-zà-ÿ]{0,5})\b",
        re.IGNORECASE,
    )),
    ("materials", re.compile(
        r"\b(m[ée]tal|metal|chrome|acier|steel|fer|iron|cuivre|copper|"
        r"or\b|gold|argent|silver|aluminium|aluminum|titane|titanium|"
        r"cuir|leather|verre|glass|cristal|crystal|plastique|plastic|"
        r"bois|wood|pierre|stone|marbre|marble|b[ée]ton|concrete|"
        r"tissu|fabric|soie|silk|fourrure|fur|[ée]cailles?|scales?|"
        r"caoutchouc|rubber|c[ée]ramique|ceramic|carbone|carbon\s+fiber)\b",
        re.IGNORECASE,
    )),
]

# A "celebrity / known character" signal — a named real person or a fictional
# identity that the diffusion model must preserve exactly, not genericize. This
# mirrors hasKnownCharacterSignal() in threeDIntent.ts. We keep it deliberately
# conservative: a Proper Name (two capitalized tokens) OR a fiction context.
_PROPER_NAME_RE = re.compile(r"\b([A-ZÀ-Ý][\wÀ-ÿ'’._-]{2,}\s+[A-ZÀ-Ý][\wÀ-ÿ'’._-]{2,})\b")
_FICTION_CONTEXT_RE = re.compile(
    r"\b(comic|comics|bd|manga|anime|s[ée]rie|series|film|movie|game|jeu|"
    r"franchise|marvel|dc\b|pokemon|zelda|final\s*fantasy|star\s*wars|"
    r"acteur|actrice|actor|actress|c[ée]l[èe]bre|celebrity|chanteur|singer|"
    r"rappeur|rapper|footballeur|joueur|player|personnage\s+de)\b",
    re.IGNORECASE,
)
# Common lowercase tokens that look like a Proper Name when title-cased at the
# start of a sentence but are not identities (avoids false "identity" matches).
_NAME_STOPWORDS = {
    "le", "la", "les", "un", "une", "des", "the", "a", "an",
    "render", "create", "generate", "make", "fais", "crée", "genere",
}


def _detect_identity(prompt: str) -> dict | None:
    """Return {name, basis} when a named real person / known character is
    requested, else None. Never fabricates an appearance — it only flags that
    the identity must be preserved, leaving the look to the diffusion model /
    reference research."""
    fiction = bool(_FICTION_CONTEXT_RE.search(prompt))
    name = None
    for m in _PROPER_NAME_RE.finditer(prompt):
        candidate = m.group(1).strip()
        first = candidate.split()[0].lower()
        if first in _NAME_STOPWORDS:
            continue
        name = candidate
        break
    if name and (fiction or True):
        # A two-token capitalized name in a 3D-character prompt is almost always
        # an identity to preserve (celebrity, historical figure, named hero).
        return {"name": name, "basis": "named_identity"}
    if fiction:
        return {"name": None, "basis": "fiction_context"}
    return None


def detect_facets(prompt: str, motion_prompt: str | None = None) -> dict:
    """Decompose a prompt into requested facets.

    Returns a dict:
        {
          "identity": {name, basis} | None,
          "facets": { family: [matched snippets...] },
          "families": [family, ...],            # families that fired, in order
          "compound": bool,                     # >= 2 families -> compound scene
        }
    """
    text = prompt or ""
    if motion_prompt:
        text = f"{text} {motion_prompt}"

    identity = _detect_identity(prompt or "")

    facets: dict[str, list[str]] = {}
    for family, pattern in _FACET_PATTERNS:
        seen: list[str] = []
        for m in pattern.finditer(text):
            snippet = m.group(0).strip().lower()
            if snippet not in seen:
                seen.append(snippet)
        if seen:
            facets[family] = seen

    families = [fam for fam, _ in _FACET_PATTERNS if fam in facets]

    # "compound" decides whether we inject the contract. Identity counts as a
    # family for this purpose because "celebrity + decor" must be protected even
    # if only one _FACET_PATTERNS family fired.
    family_count = len(families) + (1 if identity else 0)
    compound = family_count >= 2

    return {
        "schema": "aurora.faithful_scene.v1",
        "identity": identity,
        "facets": facets,
        "families": families,
        "compound": compound,
    }


# Human-readable instruction per facet family. {snips} is replaced by the
# comma-joined matched snippets so the model sees its own words echoed back.
#
# Two instruction sets resolve a conflict that ONLY surfaces when you actually
# run the pipeline: a riggable character is reconstructed from an ISOLATED
# reference (white background, A-pose) so the mesh is clean and animatable. That
# directly contradicts "render the full decor scene behind the subject". A single
# Hunyuan3D mesh cannot be both a rigged celebrity AND a separate spinning turbine
# AND volumetric rain. So:
#   * SCENE mode (non-isolated subjects: machines, dioramas, products) — keep the
#     decor/fluids as real background geometry.
#   * ISOLATED-CHARACTER mode (character/humanoid to be rigged) — express the same
#     scene facets as atmosphere ON the subject (rain-wet leather, neon rim-light,
#     steam hugging the figure). The mood is honored without breaking the rig.
_FACET_INSTRUCTION_SCENE = {
    "decor": "the surrounding decor / environment ({snips}) must be present and "
             "readable behind and around the subject, not replaced by a plain studio backdrop",
    "mechanical": "the mechanical apparatus ({snips}) must be modeled as real "
                  "functional hard-surface geometry with visible parts, not hinted or omitted",
    "fluids": "the fluid / atmospheric elements ({snips}) must be visibly rendered "
              "as volumetric water/steam/smoke/spray, never dropped",
    "luminous": "the luminous / emissive elements ({snips}) must glow with strong "
                "saturated emissive color and cast visible light, not flat paint",
    "motion": "the motion ({snips}) must be expressed through pose and part "
              "orientation so the action reads clearly in the still reference",
    "materials": "the named materials ({snips}) must read as distinct PBR surfaces "
                 "with correct metalness/roughness, each material separable",
}
_FACET_INSTRUCTION_ISOLATED = {
    "decor": "the scene setting ({snips}) must appear ONLY as coloured rim-light and "
             "atmospheric tint ON the subject against a PLAIN white studio background — "
             "NO street, buildings, walls or environment scenery behind the subject "
             "(a clean isolated subject is mandatory for 3D reconstruction)",
    "mechanical": "any mechanical apparatus ({snips}) worn by, held by or attached to "
                  "the subject must be real geometry; standalone machinery in the scene "
                  "is conveyed only as reflection/lighting, not as separate parts",
    "fluids": "the fluid / atmospheric elements ({snips}) must appear ON and AROUND the "
              "subject — rain-soaked surfaces, dripping water, steam haze hugging the "
              "figure — not as a separate volumetric background",
    "luminous": "the luminous / emissive elements ({snips}) must LIGHT the subject — "
                "coloured rim-light and glowing reflections on the materials — emissive "
                "on the figure, not a detached neon sign",
    "motion": "the motion ({snips}) must be expressed through pose and part orientation "
              "so the action reads clearly",
    "materials": "the named materials ({snips}) must read as distinct PBR surfaces with "
                 "correct metalness/roughness, each material separable",
}

# Kinds reconstructed from an isolated reference (rig-ready). Compound scenes with
# these subjects switch to ON-SUBJECT atmosphere phrasing.
_ISOLATED_KINDS = {"character", "humanoid", "creature"}


def compose_faithful_prompt(prompt: str, *, subject_kind: str | None = None,
                            motion_prompt: str | None = None) -> dict:
    """Build a faithful, fidelity-locked FLUX prompt from a compound request.

    Returns:
        {
          "prompt": <verbatim prompt> [+ MULTI-ELEMENT FIDELITY CONTRACT],
          "applied": bool,             # True only when a contract was appended
          "analysis": <detect_facets(...) result>,
          "contract_lines": [str, ...],
        }

    No-op (applied=False, prompt returned unchanged) for simple/single-facet
    requests so single-subject generation behavior is unchanged.
    """
    base = (prompt or "").strip()
    analysis = detect_facets(prompt, motion_prompt)

    if not analysis["compound"]:
        return {
            "prompt": base,
            "applied": False,
            "analysis": analysis,
            "mode": "scene",
            "contract_lines": [],
        }

    # ISOLATED-CHARACTER mode resolves the rig-vs-scene conflict: a riggable
    # character is reconstructed from an isolated reference, so scene facets are
    # reframed as atmosphere ON the subject instead of separate background
    # geometry. SCENE mode (machines, dioramas, products) keeps real backgrounds.
    isolated = (subject_kind or "").lower() in _ISOLATED_KINDS
    instructions = _FACET_INSTRUCTION_ISOLATED if isolated else _FACET_INSTRUCTION_SCENE
    mode = "isolated_character" if isolated else "scene"

    lines: list[str] = []

    identity = analysis["identity"]
    if identity and identity.get("name"):
        lines.append(
            f"the requested identity ({identity['name']}) must be preserved exactly "
            "— recognizable face and silhouette, no generic look-alike, no gender swap"
        )
    elif identity:
        lines.append(
            "the requested named identity must be preserved exactly, not genericized"
        )

    # In isolated mode the subject MUST sit on a plain background or neither the
    # turnaround audit (white = background) nor Hunyuan3D can segment it — that is
    # exactly what blocked the celebrity reference gate. The atmosphere survives
    # as on-subject lighting, set by the per-facet lines below.
    if isolated:
        lines.append(
            "ISOLATED FULL-BODY SUBJECT centered on a PLAIN WHITE studio background, "
            "the whole figure visible head to feet with clear white margin all around, "
            "no environment scenery behind the subject"
        )

    for family in analysis["families"]:
        snips = ", ".join(analysis["facets"][family][:4])
        template = instructions.get(family)
        if template:
            lines.append(template.format(snips=snips))

    if not lines:
        return {
            "prompt": base,
            "applied": False,
            "analysis": analysis,
            "mode": mode,
            "contract_lines": [],
        }

    # Honest trade-off note when a riggable subject is asked to carry standalone
    # scene props — a single mesh can't be both a rigged figure and separate
    # animated machinery / volumetric fluids.
    has_standalone = any(f in analysis["facets"] for f in ("decor", "mechanical", "fluids"))
    if isolated and has_standalone:
        lines.append(
            "single riggable subject: standalone scene props and separate machinery are "
            "conveyed as styling, lighting and reflection on the subject, not as separate "
            "animated geometry"
        )

    header = (
        "MULTI-ELEMENT FIDELITY CONTRACT (render EVERY element described with "
        "precise detail; do NOT simplify the scene to a single subject, do NOT "
        "omit, merge or approximate any element listed):"
    )
    contract_block = header + " " + "; ".join(lines) + "."
    composed = base.rstrip(" ,.") + "\n\n" + contract_block

    return {
        "prompt": composed,
        "applied": True,
        "analysis": analysis,
        "mode": mode,
        "contract_lines": lines,
    }


# Human/person lexical signal — distinguishes a named *human* identity (a
# celebrity, a character to rig) from a named *product* brand (Lian Li Strimer).
# Required before we nudge the subject kind to character, so a branded cable is
# never turned into a humanoid.
_HUMAN_SIGNAL_RE = re.compile(
    r"\b(personnage|person|personne|humain|human|homme|femme|man|woman|"
    r"gar[çc]on|fille|boy|girl|acteur|actrice|actor|actress|celebrity|"
    r"c[ée]l[èe]bre|chanteur|chanteuse|singer|rappeur|rapper|"
    r"footballeur|joueur|player|h[ée]ros|h[ée]ro[iï]ne|hero|heroine|"
    r"motard|biker|guerrier|warrior|soldat|soldier|danseur|dancer|"
    r"visage|face|silhouette|tenue|outfit|costume|veste|blouson|"
    r"\bil\b|\belle\b|\bhe\b|\bshe\b|\bsa\b|\bson\b|his\b|her\b)\b",
    re.IGNORECASE,
)

# Non-human kinds that a named-human prompt should override. We keep the
# original kind for anything already organic (character/humanoid/creature/
# quadruped) — only rescue prompts misrouted to an object kind.
_OVERRIDABLE_KINDS = {
    "vehicle", "product", "gadget", "generic", "sphere", "architecture",
    "motherboard", "computer", "pc_tower", "case",
}


def refine_subject_kind(prompt: str, kind: str | None,
                        motion_prompt: str | None = None) -> dict:
    """Rescue the effective subject kind for a compound prompt whose dominant
    reconstruction subject is a *person*.

    The single-bucket extract_kind() is first-match-wins: "Keanu Reeves on a
    motorcycle" matches "moto" first and returns `vehicle`, which then strips the
    human-fidelity / rigging contracts. When a named human identity plus a human
    lexical signal are present and the current kind is an object kind, we nudge it
    to `character`. The scene's vehicle/decor stay protected by the faithful
    contract — only the *primary* kind changes.

    Returns {kind, changed, reason}.
    """
    original = (kind or "generic").lower()
    identity = _detect_identity(prompt or "")
    text = f"{prompt or ''} {motion_prompt or ''}"
    has_human_signal = bool(_HUMAN_SIGNAL_RE.search(text))
    if (identity and identity.get("name")
            and has_human_signal
            and original in _OVERRIDABLE_KINDS):
        return {
            "kind": "character",
            "changed": True,
            "reason": (f"named human identity '{identity['name']}' + human signal "
                       f"present; rescued kind {original} -> character"),
        }
    return {"kind": original, "changed": False, "reason": None}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Aurora 3D faithful-scene prompt composer")
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--subject-kind", default=None, dest="subject_kind")
    parser.add_argument("--motion-prompt", default=None, dest="motion_prompt")
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except (AttributeError, ValueError):
        pass

    out = compose_faithful_prompt(
        args.prompt, subject_kind=args.subject_kind,
        motion_prompt=args.motion_prompt,
    )
    if args.pretty:
        a = out["analysis"]
        sys.stdout.write(f"compound:   {a['compound']}\n")
        sys.stdout.write(f"identity:   {a['identity']}\n")
        sys.stdout.write(f"families:   {a['families']}\n")
        sys.stdout.write(f"applied:    {out['applied']}\n\n")
        sys.stdout.write("FLUX prompt:\n" + out["prompt"] + "\n")
    else:
        sys.stdout.write(json.dumps(out, indent=2, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
