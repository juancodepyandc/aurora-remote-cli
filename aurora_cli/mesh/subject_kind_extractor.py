#!/usr/bin/env python
"""Aurora 3D subject-kind extractor — derives the canonical kind label
(`character`, `humanoid`, `quadruped`, `creature`, `pc_tower`, `case`,
`computer`, `vehicle`, `product`, `gadget`, `architecture`, `sphere`,
`generic`) from a free-form 3D prompt.

Used by mesh_quality_score.py to know which canonical aspect ratio to
compare against, and by auto_validate_mesh.py to make autonomous retry
decisions without humans needing to pre-classify.

Pure Python regex matching — fast, deterministic, no LLM call. Mirrors
intent classification in `application/src/services/threeDIntent.ts`
(checked by tests).

Usage:
    python subject_kind_extractor.py "<prompt>"
"""

from __future__ import annotations

import argparse
import json
import re
import sys


# Order matters — first match wins. More specific patterns first.
KIND_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("vehicle", re.compile(
        r"\b(voiture|car|truck|camion|moto|motorcycle|bike|v[ée]lo|tank|char|"
        r"bus|avion|airplane|jet|h[ée]licopt[èe]re|helicopter|bateau|boat|"
        r"navire|ship|sous[\s-]?marin|submarine|fus[ée]e|rocket|train|"
        r"locomotive|tramway|skateboard|scooter|tractor|tracteur|"
        r"tesla|porsche|ferrari|lamborghini|bugatti|bmw|audi|mercedes|"
        r"toyota|honda|mazda|nissan|peugeot|renault|citroen|jeep|"
        r"model[\s-]?s|model[\s-]?3|model[\s-]?x|model[\s-]?y)\b",
        re.IGNORECASE,
    )),
    ("pc_tower", re.compile(
        r"\b(boitier|bo[iî]tier|pc tower|pc tour|pc complet|computer case|"
        r"desktop case|tour pc|chassis pc|mid[\s-]?tower|full[\s-]?tower|"
        r"mini[\s-]?tower|gaming pc)\b",
        re.IGNORECASE,
    )),
    # iter24.fix: motherboards / mainboards / specific brand SKUs. Detected
    # before "computer" so "ASUS X870E" doesn't fall into the generic
    # computer bucket. multi-view recommended (see MULTIVIEW_RECOMMENDED_KINDS
    # in aurora_3d_pipeline.py); FLUX prompt enrichment via BRAND_VISUAL_CUES
    # appends component-specific cues so the diffusion model has a stronger
    # signal on PCB color, branded heatsinks, OLED placement, AURA RGB zones.
    ("motherboard", re.compile(
        r"\b(motherboard|mainboard|carte\s+m[èe]re|"
        r"x870e|x670e|x670|b850|b650|z890|z790|z690|"
        r"rog\s+strix|rog\s+crosshair|rog\s+maximus|rog\s+(?:c|h)ero|"
        r"tuf\s+gaming|prime\s+(?:x|z|b)\d|"
        r"msi\s+(?:meg|mpg|mag)|gigabyte\s+aorus|asrock\s+(?:taichi|phantom))\b",
        re.IGNORECASE,
    )),
    ("computer", re.compile(
        r"\b(ordinateur|computer|laptop|desktop|workstation|server|serveur)\b",
        re.IGNORECASE,
    )),
    ("humanoid", re.compile(
        r"\b(humano[iï]de|humanoid|elf|elfe|guerrier|warrior|knight|chevalier|"
        r"princess|princesse|wizard|mage|sorcier|sorci[èe]re|witch|paladin|"
        r"samurai|samoura[iï]|ninja|cyborg|androide|android|robot humanoid|"
        r"viking|gladiateur|gladiator|homme|femme|man|woman|monsieur|madame|"
        r"gar[cç]on|fille|enfant|child|boy|girl|personne|person|humain|human|"
        r"adulte|adult|vieillard|soldat|soldier|policier|pompier|docteur|"
        r"m[ée]decin|infirmi[èe]re|chef|cuisinier|danseur|danseuse|athl[èe]te)\b",
        re.IGNORECASE,
    )),
    ("character", re.compile(
        r"\b(character|personnage|hero|heroine|h[ée]ros|h[ée]ro[iï]ne|"
        r"protagonist|protagoniste|figurine|action figure|chibi|avatar|"
        r"persona|player character|pc character)\b",
        re.IGNORECASE,
    )),
    ("quadruped", re.compile(
        r"\b(cheval|horse|chien|dog|chat|cat|loup|wolf|lion|tigre|tiger|"
        r"ours|bear|cerf|deer|vache|cow|taureau|bull|biche|panda|renard|fox|"
        r"renne|reindeer|girafe|giraffe|zebre|zebra|[ée]l[ée]phant|elephant|"
        r"rhinoc[ée]ros|rhinoceros|hippopotame|hippo|mouton|sheep|ch[èe]vre|"
        r"goat|cochon|pig|sanglier|boar|tortue|turtle|crocodile|lezard|lizard)\b",
        re.IGNORECASE,
    )),
    ("creature", re.compile(
        # v80t — exclude "alien artifact|object|relic|fragment|debris" via
        # negative lookahead on the standalone "alien" / "extraterrestre" so
        # those map to generic/product (open silhouette) instead of being
        # forced into creature aspect (tall body) which fails reshape.
        r"\b(dragon|wyvern|drake|hydra|phoenix|griffon|griffin|kraken|"
        r"pieuvre|octopus|m[ée]duse|jellyfish|squid|calmar|"
        r"creature|cr[ée]ature|monstre|monster|beast|b[êe]te|"
        r"chimera|chim[èe]re|gargouille|gargoyle|"
        r"(?:alien|extraterrestre)(?!\s+(?:artifact|artefact|object|"
        r"objet|relic|relique|fragment|debris|wreckage|crystal|crystalline|"
        r"lattice|orb|sphere|sph[èe]re)))\b",
        re.IGNORECASE,
    )),
    ("architecture", re.compile(
        r"\b(building|b[âa]timent|maison|house|tower fortress|chateau|"
        r"ch[âa]teau|castle|temple|church|[ée]glise|cathedral|cath[ée]drale|"
        r"mosque|mosqu[ée]e|tour eiffel|eiffel tower|bridge|pont|skyscraper|"
        r"gratte[\s-]?ciel|pyramid|pyramide|colis[ée]e|colosseum)\b",
        re.IGNORECASE,
    )),
    ("sphere", re.compile(
        r"\b(sphere|sph[èe]re|ball|ballon|globe|orb|planete|planet|atom|"
        r"atome|bubble|bulle)\b",
        re.IGNORECASE,
    )),
    ("product", re.compile(
        r"\b(product|produit|packshot|consumer product|item|object commercial|"
        r"mockup|maquette|presentation produit)\b",
        re.IGNORECASE,
    )),
    ("gadget", re.compile(
        r"\b(gadget|device|appareil|montre|watch|smartphone|phone|t[ée]l[ée]phone|"
        r"tablet|tablette|casque|headphone|earbud|smartwatch|console|"
        r"iphone|ipad|airpods|galaxy|pixel|macbook|ipod|kindle|"
        r"playstation|ps[1-9]|xbox|switch|nintendo)\b",
        re.IGNORECASE,
    )),
]


def extract_kind(prompt: str) -> dict:
    """Return {kind, confidence, matched_pattern, alternatives} for a prompt."""
    if not prompt:
        return {"kind": "generic", "confidence": 0.0, "matched_pattern": None,
                "alternatives": []}
    matches: list[tuple[str, str]] = []
    for kind, pattern in KIND_PATTERNS:
        m = pattern.search(prompt)
        if m:
            matches.append((kind, m.group(0)))
    if not matches:
        return {
            "kind": "generic",
            "confidence": 0.0,
            "matched_pattern": None,
            "alternatives": [],
        }
    primary_kind, primary_match = matches[0]
    # Confidence: 1.0 for sole match, drops with each alternative.
    confidence = round(1.0 / len(matches), 2)
    return {
        "kind": primary_kind,
        "confidence": confidence,
        "matched_pattern": primary_match,
        "alternatives": [k for k, _ in matches[1:]],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Aurora 3D subject-kind extractor")
    parser.add_argument("prompt", nargs="+", help="3D prompt to classify")
    args = parser.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except (AttributeError, ValueError):
        pass
    prompt = " ".join(args.prompt).strip()
    out = extract_kind(prompt)
    out["prompt"] = prompt
    sys.stdout.write(json.dumps(out, indent=2, ensure_ascii=True) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
