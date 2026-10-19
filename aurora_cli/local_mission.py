"""Portable file missions with durable observations and explicit completion gates.

The Python runner executes trusted project code with the user's permissions;
workspace path checks are not an operating-system sandbox.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import uuid

from .core import locations
from .core.security import is_forbidden


import shlex


def needs_actions(request):
    if not isinstance(request, str) or not request.strip():
        return False
    return bool(re.search(
        r'\b(?:fichiers?|files?|dossiers?|r[ée]pertoires?|folders?|directories|scripts?'
        r'|corrige\w*|fix\w*|bugs?|impl[ée]ment\w*|test(?:e|er|s)?|pytest|v[ée]rifi\w*'
        r'|analys\w*|refactor\w*|modifi\w*|cr[ée](?:e|er|ation)\w*|ajout\w*|fonctions?|classes?'
        r'|modules?|d[ée]p[ôo]ts?|repos?|workspaces?|git|code\w*|programm\w*|d[ée]velopp\w*'
        r'|optimi[sz]\w*|am[ée]lior\w*|inspect\w*|lis|lire|read|[ée]cri(?:s|re|t)|write|[ée]dit\w*'
        r'|ex[ée]cut\w*|run|lance\w*|build\w*|compil\w*|debug\w*|d[ée]bogu\w*|diagnosti\w*'
        r'|architect\w*|syst[èe]m\w*|pipelin\w*|rout(?:e|es|ing)|api|serveurs?|servers?|cli'
        r'|tunnels?|bridges?|agents?|t[âa]ches?|tasks?|skills?|mcp|outils?|tools?'
        r'|projets?|projects?|packages?|configs?|configurations?|sch[ée]mas?|schemas?'
        r'|bases?|databases?|sql|migrations?|audits?|auditer|nettoy\w*|clean\w*|cherch\w*'
        r'|search\w*|find|trouv\w*|remplac\w*|replace\w*|patch\w*|mets|mettre)\b'
        r'|\.(?:py|json|csv|txt|js|ts|tsx|jsx|html|css|md|toml|ya?ml|sh|sql|rs|go|c|cpp|h|glb|obj|stl)\b'
        r'|(?:^|\s)[.~]?/[a-zA-Z0-9_.-]+|`[^`]+`',
        request, re.I,
    ))


def _digest(data):
    return hashlib.sha256(data).hexdigest()


def _atomic(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.jobia-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def list_local_missions() -> list[dict]:
    directory = locations.data_dir() / 'local-missions'
    if not directory.is_dir():
        return []
    items = []
    for path in sorted(directory.glob('loc_*.json'), key=lambda p: p.stat().st_mtime, reverse=True)[:50]:
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
            if not isinstance(data, dict):
                continue
            criteria = data.get('criteria', [])
            verified = data.get('verified', {})
            items.append({
                'id': data.get('id', path.stem),
                'status': data.get('status', 'unknown'),
                'request': data.get('request', ''),
                'workspace': data.get('workspace', ''),
                'permissions': data.get('permissions', 'SAFE'),
                'criteria_count': len(criteria) if isinstance(criteria, list) else 0,
                'verified_count': len(verified) if isinstance(verified, dict) else 0,
                'path': str(path),
            })
        except (OSError, ValueError):
            continue
    return items


BUILTIN_MODULE_AGENTS = {
    # 0. Orchestrateur Suprême
    'aurora-orchestrator': {'module': 'orchestration', 'role_type': 'orchestrator', 'role': 'Architecte et coordinateur principal multi-modules', 'can_create_subagents': True},
    # 1. Conversation / Chat (Maître + 5 sous-agents)
    'conversation-lead': {'module': 'conversation', 'role_type': 'master', 'role': 'Agent Maître du module Conversation & Synthèse (Lyra)', 'can_create_subagents': True},
    'chat-lead': {'module': 'chat', 'role_type': 'master', 'role': 'Agent principal du module conversationnel et synthèse', 'can_create_subagents': True},
    'conversation-intent-router': {'module': 'conversation', 'role_type': 'subagent', 'parent_master': 'conversation-lead', 'feature': 'intent_routing', 'role': 'Détection d’intention et routage inter-modules sans faux positifs', 'can_create_subagents': False},
    'conversation-pipeline-tuner': {'module': 'conversation', 'role_type': 'subagent', 'parent_master': 'conversation-lead', 'feature': 'sse_streaming_fallback', 'role': 'Pipeline SSE, nettoyage <think>, sélection modèle et fallback local', 'can_create_subagents': False},
    'conversation-web-factchecker': {'module': 'conversation', 'role_type': 'subagent', 'parent_master': 'conversation-lead', 'feature': 'web_rag_verification', 'role': 'Recherche web temps réel, extraction URL et anti-hallucination factuelle', 'can_create_subagents': False},
    'conversation-memory-keeper': {'module': 'conversation', 'role_type': 'subagent', 'parent_master': 'conversation-lead', 'feature': 'session_persistence', 'role': 'Persistance des fils de discussion, reprise multi-surface et profil', 'can_create_subagents': False},
    'conversation-vision-multimodal': {'module': 'conversation', 'role_type': 'subagent', 'parent_master': 'conversation-lead', 'feature': 'multimodal_attachments', 'role': 'Analyse d’images/PDF joints dans le chat via Qwen-VL / OCR', 'can_create_subagents': False},
    # 2. Code (Maître + 6 sous-agents)
    'code-lead': {'module': 'code', 'role_type': 'master', 'role': 'Agent Maître du module Code & Architecture Logicielle (Glyph)', 'can_create_subagents': True},
    'code-architecture-planner': {'module': 'code', 'role_type': 'subagent', 'parent_master': 'code-lead', 'feature': 'architecture_planning', 'role': 'Étape 1 Architecte : planification multi-fichiers et contrats de types', 'can_create_subagents': False},
    'code-design-architect': {'module': 'code', 'role_type': 'subagent', 'parent_master': 'code-lead', 'feature': 'motion_ui_design_aaa', 'role': 'Directeur Design UI/UX AAA, Motion Design 60fps, Shaders WebGL & Anti-Slop', 'can_create_subagents': False},
    'code-backend-performance': {'module': 'code', 'role_type': 'subagent', 'parent_master': 'code-lead', 'feature': 'backend_perf_security', 'role': 'Architecture Backend Haute Performance, SQL/Index, Concurrence & Sécurité OWASP', 'can_create_subagents': False},
    'code-fidelity-auditor': {'module': 'code', 'role_type': 'subagent', 'parent_master': 'code-lead', 'feature': 'ast_fidelity_audit', 'role': 'Étape 3 Auditeur : scoring de fidélité 0-100, détection TODO/stubs et boucle auto-fix', 'can_create_subagents': False},
    'code-sandbox-runner': {'module': 'code', 'role_type': 'subagent', 'parent_master': 'code-lead', 'feature': 'live_preview_sandbox', 'role': 'Compilation Babel/TSX live, iframe preview isolée et capture erreurs runtime', 'can_create_subagents': False},
    'code-delivery-packager': {'module': 'code', 'role_type': 'subagent', 'parent_master': 'code-lead', 'feature': 'workspace_export', 'role': 'Écriture disque atomique, export ZIP et persistance workspace_projects/', 'can_create_subagents': False},
    # 3. Image (Maître + 6 sous-agents)
    'image-lead': {'module': 'image', 'role_type': 'master', 'role': 'Agent Maître du module Image & Direction Artistique (Iris)', 'can_create_subagents': True},
    'image-prompt-director': {'module': 'image', 'role_type': 'subagent', 'parent_master': 'image-lead', 'feature': 'adaptive_prompt_expansion', 'role': 'Expansion adaptative (fidélité sujet vs enrichissement créatif) sans mots-clés figés', 'can_create_subagents': False},
    'image-flux-stylist': {'module': 'image', 'role_type': 'subagent', 'parent_master': 'image-lead', 'feature': 'flux2_txt2img_img2img', 'role': 'Exécution des workflows FLUX.2-dev / FLUX.1-dev (txt2img, img2img, inpainting)', 'can_create_subagents': False},
    'image-reference-researcher': {'module': 'image', 'role_type': 'subagent', 'parent_master': 'image-lead', 'feature': 'ground_truth_reference', 'role': 'Recherche de vérité terrain (Wikimedia/marques/personnages) et garde-fou anti-hallucination', 'can_create_subagents': False},
    'image-upscale-16k-pbr': {'module': 'image', 'role_type': 'subagent', 'parent_master': 'image-lead', 'feature': 'upscale_16k_32bit_pbr', 'role': 'Super-résolution tuilée 8K/16K, export EXR/PNG 16-bit/32-bit et maps PBR', 'can_create_subagents': False},
    'image-job-cancellation': {'module': 'image', 'role_type': 'subagent', 'parent_master': 'image-lead', 'feature': 'job_stop_and_step_telemetry', 'role': 'Interruption immédiate (Stop) AbortController + /interrupt ComfyUI et étapes live', 'can_create_subagents': False},
    'image-output-organizer': {'module': 'image', 'role_type': 'subagent', 'parent_master': 'image-lead', 'feature': 'creations_modifications_storage', 'role': 'Rangement strict dans creations/ vs modifications/ et galerie d’historique', 'can_create_subagents': False},
    # 4. Drawing & Character Forge (Maître + 4 sous-agents)
    'drawing-lead': {'module': 'drawing', 'role_type': 'master', 'role': 'Agent Maître du module Dessin, Croquis & Character Forge (Sumi)', 'can_create_subagents': True},
    'drawing-canvas-stroke': {'module': 'drawing', 'role_type': 'subagent', 'parent_master': 'drawing-lead', 'feature': 'canvas_brush_layers', 'role': 'Moteur de pinceaux, pression stylet, lissage Bézier, calques et undo/redo', 'can_create_subagents': False},
    'drawing-sketch-interpreter': {'module': 'drawing', 'role_type': 'subagent', 'parent_master': 'drawing-lead', 'feature': 'sketch_gesture_analysis', 'role': 'Analyse du trait (axe d’énergie, rythme, espace négatif) sans surcharge', 'can_create_subagents': False},
    'drawing-controlnet-renderer': {'module': 'drawing', 'role_type': 'subagent', 'parent_master': 'drawing-lead', 'feature': 'scribble_canny_img2img', 'role': 'Transformation sketch-to-render via ControlNet Scribble / Canny / Lineart', 'can_create_subagents': False},
    'drawing-character-forge': {'module': 'drawing', 'role_type': 'subagent', 'parent_master': 'drawing-lead', 'feature': 'character_sheet_turnaround', 'role': 'Génération de Character Sheets cohérentes (face, profil, dos, expressions)', 'can_create_subagents': False},
    # 5. 3D, CAO & Assemblage (Maître + 11 sous-agents)
    '3d-lead': {'module': '3d', 'role_type': 'master', 'role': 'Agent Maître du module 3D, CAO & Assemblage (Atlas)', 'can_create_subagents': True},
    '3d-pipeline-router': {'module': '3d', 'role_type': 'subagent', 'parent_master': '3d-lead', 'feature': 'mode_classification_routing', 'role': 'Classification automatique Neural Mesh vs CAD B-Rep vs Assemblage vs Scène', 'can_create_subagents': False},
    '3d-neural-generator': {'module': '3d', 'role_type': 'subagent', 'parent_master': '3d-lead', 'feature': 'hunyuan3d_trellis_inference', 'role': 'Inférence Hunyuan3D-2.1 / Trellis et adaptateurs LoRA renforcés', 'can_create_subagents': False},
    '3d-mesh-postprocessor': {'module': '3d', 'role_type': 'subagent', 'parent_master': '3d-lead', 'feature': 'topology_manifold_repair', 'role': 'Post-processing géométrique, réparation watertight/manifold, normales et LOD', 'can_create_subagents': False},
    '3d-texture-lead': {'module': '3d-texture', 'role_type': 'subagent', 'parent_master': '3d-lead', 'feature': 'pbr_uv_material_baking', 'role': 'Spécialiste textures PBR, UV et matériaux multi-zones', 'can_create_subagents': True},
    '3d-quality-rescuer': {'module': '3d', 'role_type': 'subagent', 'parent_master': '3d-lead', 'feature': 'validation_5axes_autorescue', 'role': 'Spécialiste auto-correction géométrique et réparation 3D (score 5 axes)', 'can_create_subagents': True},
    '3d-motion-rigger': {'module': '3d-motion', 'role_type': 'subagent', 'parent_master': '3d-lead', 'feature': 'skeleton_rigging_skinning', 'role': 'Auto-rigging squelettique, skinning LBS et clips procéduraux', 'can_create_subagents': False},
    '3d-motion-lead': {'module': '3d-motion', 'role_type': 'subagent', 'parent_master': '3d-lead', 'feature': 'semantic_3d_animation', 'role': 'Spécialiste rigging squelettique et animation sémantique 3D', 'can_create_subagents': True},
    '3d-cad-engineering': {'module': '3d', 'role_type': 'subagent', 'parent_master': '3d-lead', 'feature': 'parametric_brep_step_stl', 'role': 'CAO paramétrique B-Rep exacte (CadQuery/Build123d/OpenSCAD), STEP/STL ISO', 'can_create_subagents': False},
    '3d-assembly-kinematics': {'module': '3d', 'role_type': 'subagent', 'parent_master': '3d-lead', 'feature': 'multi_part_exploded_view', 'role': 'Assemblage multi-pièces, liaisons cinématiques, zéro collision BVH et vue éclatée', 'can_create_subagents': False},
    '3d-splat-telemetry': {'module': '3d', 'role_type': 'subagent', 'parent_master': '3d-lead', 'feature': 'gaussian_splatting_telemetry', 'role': 'Rendu 3D Gaussian Splatting (.splat/.ply) et télémétrie matérielle 3D', 'can_create_subagents': False},
    '3d-viewer-lead': {'module': '3d-viewer', 'role_type': 'subagent', 'parent_master': '3d-lead', 'feature': 'webgl_threejs_viewer', 'role': 'Spécialiste rendu viewer 3D, Draco/Meshopt et transport GLB', 'can_create_subagents': True},
    # 6. Vidéo & Cinéma Temporel (Maître + 4 sous-agents)
    'video-lead': {'module': 'video', 'role_type': 'master', 'role': 'Agent Maître du module Vidéo & Réalisation Cinéma (Cinéma)', 'can_create_subagents': True},
    'video-storyboard-director': {'module': 'video', 'role_type': 'subagent', 'parent_master': 'video-lead', 'feature': 'multi_shot_storyboard', 'role': 'Découpage en plans numérotés, cadrage, mouvements de caméra et raccords', 'can_create_subagents': False},
    'video-motion-director': {'module': 'video', 'role_type': 'subagent', 'parent_master': 'video-lead', 'feature': 'ltx_wan_video_diffusion', 'role': 'Inférence LTX-Video 2B / Wan2.1 14B, cohérence temporelle et assemblage FFmpeg', 'can_create_subagents': False},
    'video-hymotion-kinematics': {'module': 'video', 'role_type': 'subagent', 'parent_master': 'video-lead', 'feature': 'hymotion_3d_trajectory', 'role': 'Synthèse de mouvements humains HY-Motion-1.0 et trajectoires caméra', 'can_create_subagents': False},
    'video-talking-head': {'module': 'video', 'role_type': 'subagent', 'parent_master': 'video-lead', 'feature': 'portrait_video_animation', 'role': 'Animation de portraits parlants synchronisés (MuseTalk / LivePortrait)', 'can_create_subagents': False},
    # 7. Voix & Audio Temps Réel (Maître + 4 sous-agents)
    'voice-lead': {'module': 'voice', 'role_type': 'master', 'role': 'Agent Maître du module Voix, Audio & Duplex Temps Réel', 'can_create_subagents': True},
    'voice-stt-transcriber': {'module': 'voice', 'role_type': 'subagent', 'parent_master': 'voice-lead', 'feature': 'whisper_asr_vad', 'role': 'Transcription speech-to-text Faster-Whisper / Canary, VAD et diarisation', 'can_create_subagents': False},
    'voice-tts-stt-tuner': {'module': 'voice', 'role_type': 'subagent', 'parent_master': 'voice-lead', 'feature': 'kokoro_f5_tts_synthesis', 'role': 'Synthèse vocale Kokoro-82M / F5-TTS / Piper, prosodie et clonage vocal', 'can_create_subagents': False},
    'voice-lipsync': {'module': 'voice', 'role_type': 'subagent', 'parent_master': 'voice-lead', 'feature': 'viseme_avatar_sync', 'role': 'Extraction d’enveloppe/visèmes temps réel et synchronisation labiale avatar', 'can_create_subagents': False},
    'voice-live-duplex': {'module': 'voice', 'role_type': 'subagent', 'parent_master': 'voice-lead', 'feature': 'full_duplex_barge_in', 'role': 'Conversation vocale mains-libres en continu, détection barge-in', 'can_create_subagents': False},
    # 8. Academy & Sciences Formelles (Maître + 4 sous-agents)
    'learning-lead': {'module': 'learning', 'role_type': 'master', 'role': 'Agent Maître du module Academy & Sciences Formelles (Sage)', 'can_create_subagents': True},
    'learning-formal-oracle': {'module': 'learning', 'role_type': 'subagent', 'parent_master': 'learning-lead', 'feature': 'sympy_z3_lean4_verification', 'role': 'Vérification formelle des preuves et calculs via SymPy, Z3 et Lean 4', 'can_create_subagents': False},
    'learning-bac-curator': {'module': 'learning', 'role_type': 'subagent', 'parent_master': 'learning-lead', 'feature': 'curriculum_courses_lessons', 'role': 'Structuration des cours, fiches de révision, théorèmes et annales', 'can_create_subagents': False},
    'learning-quiz-verifier': {'module': 'learning', 'role_type': 'subagent', 'parent_master': 'learning-lead', 'feature': 'interactive_exercises_grading', 'role': 'Génération de quiz/exercices gradués, indices progressifs et correction', 'can_create_subagents': False},
    'learning-lab-integrator': {'module': 'learning', 'role_type': 'subagent', 'parent_master': 'learning-lead', 'feature': 'theory_to_simulator_bridge', 'role': 'Passerelle directe entre une formule théorique et le Simulateur Scientifique', 'can_create_subagents': False},
    # 9. Simulateur Scientifique (Maître + 4 sous-agents)
    'simulator-lead': {'module': 'simulator', 'role_type': 'master', 'role': 'Agent Maître du Simulateur Scientifique Multi-Physique', 'can_create_subagents': True},
    'simulator-physics-engine': {'module': 'simulator', 'role_type': 'subagent', 'parent_master': 'simulator-lead', 'feature': 'rigid_body_rapier_rk4', 'role': 'Intégration numérique RK4/Verlet, collisions Rapier3D, gravité et contraintes', 'can_create_subagents': False},
    'simulator-fluid-particles': {'module': 'simulator', 'role_type': 'subagent', 'parent_master': 'simulator-lead', 'feature': 'sph_fluids_fields_waves', 'role': 'Fluides SPH, champs électromagnétiques, optique et mécanique ondulatoire', 'can_create_subagents': False},
    'simulator-chemistry-electronics': {'module': 'simulator', 'role_type': 'subagent', 'parent_master': 'simulator-lead', 'feature': 'molecular_circuits_kinetics', 'role': 'Dynamique moléculaire, cinétique chimique et simulation de circuits', 'can_create_subagents': False},
    'simulator-scene-io': {'module': 'simulator', 'role_type': 'subagent', 'parent_master': 'simulator-lead', 'feature': 'preset_telemetry_csv', 'role': 'Import/export de scènes JSON, presets physiques et télémétrie CSV', 'can_create_subagents': False},
    # 10. Cybersécurité (Maître + 5 sous-agents)
    'cyber-lead': {'module': 'cyber', 'role_type': 'master', 'role': 'Agent Maître du module Cybersécurité & Audit (Phantom)', 'can_create_subagents': True},
    'cyber-symbolic-zeroday': {'module': 'cyber', 'role_type': 'subagent', 'parent_master': 'cyber-lead', 'feature': 'ast_taint_symbolic_audit', 'role': 'Analyse statique AST, taint tracking, exécution symbolique Z3 et CWE/CVE', 'can_create_subagents': False},
    'cyber-closedloop-patcher': {'module': 'cyber', 'role_type': 'subagent', 'parent_master': 'cyber-lead', 'feature': 'auto_remediation_diff_verify', 'role': 'Génération de correctifs de sécurité (diffs minimaux) et re-vérification fermée', 'can_create_subagents': False},
    'cyber-lab-builder': {'module': 'cyber', 'role_type': 'subagent', 'parent_master': 'cyber-lead', 'feature': 'sandboxed_ctf_scenarios', 'role': 'Environnements d’entraînement CTF isolés et challenges d’audit', 'can_create_subagents': False},
    'cyber-pyops-keeper': {'module': 'cyber', 'role_type': 'subagent', 'parent_master': 'cyber-lead', 'feature': 'deps_sbom_secrets_scanner', 'role': 'Scan de dépendances (pip/npm/cargo), SBOM et détection de secrets', 'can_create_subagents': False},
    'cyber-network-recon': {'module': 'cyber', 'role_type': 'subagent', 'parent_master': 'cyber-lead', 'feature': 'local_surface_headers_tls', 'role': 'Inspection des ports locaux autorisés, en-têtes HTTP/CSP/CORS et TLS', 'can_create_subagents': False},
    # 11. Cowork & Extension Chrome (Maître + 4 sous-agents)
    'cowork-lead': {'module': 'cowork', 'role_type': 'master', 'role': 'Agent Maître du module Cowork, Automatisation & Extension Chrome', 'can_create_subagents': True},
    'cowork-orchestrator-tuner': {'module': 'cowork', 'role_type': 'subagent', 'parent_master': 'cowork-lead', 'feature': 'multi_step_workflow_planner', 'role': 'Planification multi-étapes Cowork, handoffs inter-modules et exécution de tâches', 'can_create_subagents': False},
    'cowork-connector-keeper': {'module': 'cowork', 'role_type': 'subagent', 'parent_master': 'cowork-lead', 'feature': 'chrome_extension_machine_connectors', 'role': 'Connecteurs machine, bridge WebSocket de l’extension Chrome MV3 et actions DOM', 'can_create_subagents': False},
    'cowork-safety-auditor': {'module': 'cowork', 'role_type': 'subagent', 'parent_master': 'cowork-lead', 'feature': 'action_permission_gate', 'role': 'Contrôle des permissions (SAFE/STANDARD/AUTONOMOUS) et validation humaine', 'can_create_subagents': False},
    'cowork-doc-extractor': {'module': 'cowork', 'role_type': 'subagent', 'parent_master': 'cowork-lead', 'feature': 'pdf_docx_xlsx_ingestion', 'role': 'Ingestion structurée de PDF, DOCX, XLSX, CSV et synthèse documentaire', 'can_create_subagents': False},
    # 12. Business Intelligence (Maître + 9 sous-agents)
    'business-ceo': {'module': 'business', 'role_type': 'master', 'role': 'Agent Maître (Sovereign CEO) du module Business Intelligence (Lab & Live)', 'can_create_subagents': True},
    'business-market-research': {'module': 'business', 'role_type': 'subagent', 'parent_master': 'business-ceo', 'feature': 'tam_sam_som_competition', 'role': 'Market Research Agent : étude de marché, demande, prix et concurrence', 'can_create_subagents': True},
    'business-financial-analyst': {'module': 'business', 'role_type': 'subagent', 'parent_master': 'business-ceo', 'feature': 'unit_economics_cashflow_dso', 'role': 'Financial Analyst Agent : coûts, marges, trésorerie, BFR (DSO) et valorisation', 'can_create_subagents': True},
    'business-risk-manager': {'module': 'business', 'role_type': 'subagent', 'parent_master': 'business-ceo', 'feature': 'ruin_probability_kelly_veto', 'role': 'Risk Manager Agent : préservation du capital, probabilité de ruine et droit de veto', 'can_create_subagents': True},
    'business-entrepreneur': {'module': 'business', 'role_type': 'subagent', 'parent_master': 'business-ceo', 'feature': 'venture_gtm_design', 'role': 'Entrepreneur Agent : création d’entreprises virtuelles/réelles (SaaS, B2B, e-commerce)', 'can_create_subagents': True},
    'business-quant': {'module': 'business', 'role_type': 'subagent', 'parent_master': 'business-ceo', 'feature': 'montecarlo_walkforward_sim', 'role': 'Quantitative Agent : backtest Walk-Forward, Monte-Carlo et optimisation bayésienne', 'can_create_subagents': True},
    'business-economist': {'module': 'business', 'role_type': 'subagent', 'parent_master': 'business-ceo', 'feature': 'macro_inflation_rates_tax', 'role': 'Economist Agent : inflation, taux directeurs, fiscalité et cycles macroéconomiques', 'can_create_subagents': True},
    'business-technical': {'module': 'business', 'role_type': 'subagent', 'parent_master': 'business-ceo', 'feature': 'infra_cloud_ai_costs', 'role': 'Technical Agent : faisabilité technique, coûts cloud/IA et automatisation', 'can_create_subagents': True},
    'business-critic': {'module': 'business', 'role_type': 'subagent', 'parent_master': 'business-ceo', 'feature': 'redteam_premortem_bias', 'role': 'Critic Agent : contradicteur systématique (Red-Team), biais de survie et coûts cachés', 'can_create_subagents': True},
    'business-auditor': {'module': 'business', 'role_type': 'subagent', 'parent_master': 'business-ceo', 'feature': 'provenance_killswitch_gate', 'role': 'Auditor Agent : audit de provenance des données, calibration et porte de promotion', 'can_create_subagents': True},
    # 13. Entraînement Auto-RL, Kaggle 2×T4 & Modèles Renforcés (Maître + 6 sous-agents)
    'training-lead': {'module': 'training', 'role_type': 'master', 'role': 'Agent Maître de l’Entraînement Auto-RL, Cloud Kaggle 2×T4 & Modèles Renforcés', 'can_create_subagents': True},
    'training-control-supervisor': {'module': 'training', 'role_type': 'subagent', 'parent_master': 'training-lead', 'feature': 'daemon_lifecycle_stop_after_cycle', 'role': 'Contrôle du démon Auto-RL, arrêt propre en fin de cycle (stop_after_cycle) et verrous', 'can_create_subagents': False},
    'training-kaggle-cloud': {'module': 'training', 'role_type': 'subagent', 'parent_master': 'training-lead', 'feature': 'kaggle_2xt4_offload', 'role': 'Orchestration des kernels GPU Kaggle 2×T4 / Colab, push et rapatriement des poids', 'can_create_subagents': False},
    'training-3d-flow-dpo': {'module': 'training', 'role_type': 'subagent', 'parent_master': 'training-lead', 'feature': 'hunyuan3d_flow_dpo_lora', 'role': 'Algorithme 3D-Flow-DPO + Géométrie Différentiable (Chamfer + Manifold + BVH)', 'can_create_subagents': False},
    'training-multi-module-dpo': {'module': 'training', 'role_type': 'subagent', 'parent_master': 'training-lead', 'feature': 'code_chat_image_voice_rl', 'role': 'Collecte des paires DPO/GRPO et fine-tuning LoRA pour Code, Chat, Image, Vidéo, Voix, Academy, Cyber', 'can_create_subagents': False},
    'training-audit-evaluator': {'module': 'training', 'role_type': 'subagent', 'parent_master': 'training-lead', 'feature': 'holdout_benchmark_promotion_gate', 'role': 'Évaluation sur holdout fixe, calcul des vrais gains mesurés et porte de promotion (>= +1.5 pt)', 'can_create_subagents': False},
    'training-reinforced-registry': {'module': 'training', 'role_type': 'subagent', 'parent_master': 'training-lead', 'feature': 'base_vs_reinforced_selector_gains_md', 'role': 'Sélecteur Base (intouchable) vs Renforcé (modele/renforce/<module>/) et génération des GAINS.md', 'can_create_subagents': False},
    # 14. Missions Autonomes & Moteur Épistémique (Maître + 4 sous-agents)
    'missions-lead': {'module': 'missions', 'role_type': 'master', 'role': 'Agent Maître des Missions Autonomes, File d’Attente & Moteur Épistémique', 'can_create_subagents': True},
    'missions-store-lease': {'module': 'missions', 'role_type': 'subagent', 'parent_master': 'missions-lead', 'feature': 'mission_queue_heartbeat_ui_sync', 'role': 'Synchronisation temps réel des missions entre UI (AuroraMissionStore) et SQLite/JSON', 'can_create_subagents': False},
    'missions-protocol-verifier': {'module': 'missions', 'role_type': 'subagent', 'parent_master': 'missions-lead', 'feature': 'deliverable_contract_verification', 'role': 'Vérification des critères d’acceptation, hash SHA-256 des livrables et statut VERIFIED', 'can_create_subagents': False},
    'missions-epistemic-recovery': {'module': 'missions', 'role_type': 'subagent', 'parent_master': 'missions-lead', 'feature': 'black_swan_root_cause_healing', 'role': 'Diagnostic de cause racine sur échec, synthèse d’outil de secours et reprise', 'can_create_subagents': False},
    'missions-delegation-spawner': {'module': 'missions', 'role_type': 'subagent', 'parent_master': 'missions-lead', 'feature': 'subagent_task_decomposition', 'role': 'Décomposition d’une mission complexe en sous-missions attribuées aux sous-agents fonctionnels', 'can_create_subagents': False},
    # 15. Bridge HTTP, Gouverneur GPU & Tauri Natif (Maître + 4 sous-agents)
    'bridge-doctor': {'module': 'bridge', 'role_type': 'master', 'role': 'Agent Maître du Bridge HTTP (:3001), Gouverneur GPU & Runtime Natif Tauri', 'can_create_subagents': True},
    'bridge-routes-contract': {'module': 'bridge', 'role_type': 'subagent', 'parent_master': 'bridge-doctor', 'feature': 'flask_api_blueprint_contracts', 'role': 'Cohérence des schémas JSON HTTP, CORS, SSE et contrat de santé /api/health', 'can_create_subagents': False},
    'bridge-gpu-governor': {'module': 'bridge', 'role_type': 'subagent', 'parent_master': 'bridge-doctor', 'feature': 'vram_arbitration_ollama_comfy', 'role': 'Arbitrage exclusif de la VRAM RTX 5080 (16 Go) entre Ollama, ComfyUI et Hunyuan3D', 'can_create_subagents': False},
    'bridge-tauri-native': {'module': 'bridge', 'role_type': 'subagent', 'parent_master': 'bridge-doctor', 'feature': 'rust_tauri_window_ipc', 'role': 'Shell natif Rust/Tauri, WebKitGTK, lancement automatique des services et IPC', 'can_create_subagents': False},
    'bridge-systemd-watchdog': {'module': 'bridge', 'role_type': 'subagent', 'parent_master': 'bridge-doctor', 'feature': 'daemon_health_restart', 'role': 'Surveillance des ports (:3001, :3034, :8188, :11434), rotation des logs et auto-restart', 'can_create_subagents': False},
    # 16. CLI Distant, Tunnel Cloudflare & Web 3034 (Maître + 4 sous-agents)
    'cli-tunnel-lead': {'module': 'cli-tunnel', 'role_type': 'master', 'role': 'Agent Maître du CLI Distant (JOBIA), Tunnel Cloudflare & Parité Web (:3034)', 'can_create_subagents': True},
    'cli-jobia-tui': {'module': 'cli-tunnel', 'role_type': 'subagent', 'parent_master': 'cli-tunnel-lead', 'feature': 'terminal_commands_repl_doctor', 'role': 'Interface CLI/TUI, commandes chat/mission/doctor/agents et ergonomie terminal', 'can_create_subagents': False},
    'tunnel-validator': {'module': 'cli-tunnel', 'role_type': 'subagent', 'parent_master': 'cli-tunnel-lead', 'feature': 'cloudflared_routing_auth', 'role': 'Configuration cloudflared, routage auroracli.uk / app.auroracli.uk et tokens', 'can_create_subagents': False},
    'cli-artifact-delivery': {'module': 'cli-tunnel', 'role_type': 'subagent', 'parent_master': 'cli-tunnel-lead', 'feature': 'chunked_download_sha256_pull', 'role': 'Téléchargement résilient des artefacts (GLB, PNG, ZIP) vers le poste client avec SHA-256', 'can_create_subagents': False},
    'web-action-runner': {'module': 'cli-tunnel', 'role_type': 'subagent', 'parent_master': 'cli-tunnel-lead', 'feature': 'persistent_workspace_3034', 'role': 'Serveur aurora_workspace_server (:3034), sessions persistantes et exécution asynchrone', 'can_create_subagents': False},
    # 17. Mémoire Vectorielle & Épisodique (Maître + 2 sous-agents)
    'memory-lead': {'module': 'memory', 'role_type': 'master', 'role': 'Agent Maître de la Mémoire Vectorielle, Épisodique & Profil Utilisateur', 'can_create_subagents': True},
    'memory-vector-indexer': {'module': 'memory', 'role_type': 'subagent', 'parent_master': 'memory-lead', 'feature': 'sqlite_embeddings_rag', 'role': 'Indexation SQLite/FAISS, embeddings locaux et recherche sémantique rapide', 'can_create_subagents': False},
    'memory-episodic-consolidator': {'module': 'memory', 'role_type': 'subagent', 'parent_master': 'memory-lead', 'feature': 'preference_summary_pruning', 'role': 'Consolidation des préférences utilisateur, résumé d’épisodes et déduplication', 'can_create_subagents': False},
    # 18. Protocole MCP (Maître + 2 sous-agents)
    'mcp-lead': {'module': 'mcp', 'role_type': 'master', 'role': 'Agent Maître des Serveurs & Outils MCP (Model Context Protocol)', 'can_create_subagents': True},
    'mcp-stdio-prober': {'module': 'mcp', 'role_type': 'subagent', 'parent_master': 'mcp-lead', 'feature': 'server_handshake_discovery', 'role': 'Découverte, handshake JSON-RPC stdio/SSE, timeouts et diagnostic de santé MCP', 'can_create_subagents': False},
    'mcp-tool-executor': {'module': 'mcp', 'role_type': 'subagent', 'parent_master': 'mcp-lead', 'feature': 'schema_validation_sandbox_call', 'role': 'Validation des arguments JSON Schema, appel sécurisé des outils MCP et formatage', 'can_create_subagents': False},
    # 19. Compétences Réutilisables / Skills (Maître + 2 sous-agents)
    'skills-lead': {'module': 'skills', 'role_type': 'master', 'role': 'Agent Maître de la Bibliothèque de Compétences Réutilisables (Skills)', 'can_create_subagents': True},
    'skills-curator-creator': {'module': 'skills', 'role_type': 'subagent', 'parent_master': 'skills-lead', 'feature': 'skill_authoring_versioning', 'role': 'Création, validation de frontmatter et versionnement des fichiers SKILL.md', 'can_create_subagents': False},
    'skills-context-injector': {'module': 'skills', 'role_type': 'subagent', 'parent_master': 'skills-lead', 'feature': 'dynamic_skill_matching', 'role': 'Sélection dynamique des compétences pertinentes selon la demande sans polluer le contexte', 'can_create_subagents': False},
}



class LocalMission:
    READ_ONLY_TOOLS = {
        'inspect_runtime', 'list_files', 'find_files', 'search_code',
        'read_file', 'verify_file', 'git_status', 'list_skills', 'read_skill',
        'list_mcp_tools', 'list_agents', 'search_web', 'fetch_url', 'test_hypothesis',
    }
    EFFECT_TOOLS = {
        'write_file', 'edit_file', 'run_python', 'run_command',
        'call_mcp_tool', 'create_agent', 'create_skill',
    }
    ALL_TOOLS = READ_ONLY_TOOLS | EFFECT_TOOLS

    def __init__(self, request, workspace, permissions='SAFE', *, resume_id=''):
        if permissions not in {'SAFE', 'STANDARD', 'AUTONOMOUS', 'FULL'}:
            raise ValueError('Niveau de permission inconnu')
        self.root = Path(workspace).expanduser().resolve(strict=True)
        if not self.root.is_dir() or is_forbidden(self.root):
            raise ValueError('Workspace invalide ou système')
        directory = locations.data_dir() / 'local-missions'
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        if resume_id and not re.fullmatch(r'loc_[0-9a-f]{32}', resume_id):
            raise ValueError('Identifiant de mission locale invalide')
        self.id = resume_id or 'loc_' + uuid.uuid4().hex
        self.path = directory / (self.id + '.json')
        # Exclusive lock released by the OS on a crash, on all supported OSes.
        self.lock = open(directory / (self.id + '.lock'), 'a+b')
        try:
            if os.name == 'nt':
                import msvcrt
                self.lock.write(b'0'); self.lock.flush(); self.lock.seek(0)
                msvcrt.locking(self.lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if resume_id:
                self.state = json.loads(self.path.read_text(encoding='utf-8'))
                if self.state['workspace'] != str(self.root):
                    raise ValueError('La reprise exige le workspace original')
                if request and request != self.state['request']:
                    raise ValueError('La reprise conserve la demande originale')
                # A resume may reduce permissions, never silently increase them.
                levels = ['SAFE', 'STANDARD', 'AUTONOMOUS', 'FULL']
                self.state['permissions'] = levels[min(levels.index(permissions), levels.index(self.state['permissions']))]
            else:
                self.state = dict(id=self.id, request=request, workspace=str(self.root), permissions=permissions,
                                  status='planning', plan=[], criteria=[], evidence=[], verified={}, files={},
                                  pending_action=None, result='')
                self.save()
        except BaseException:
            self.lock.close()
            raise

    def close(self):
        self.lock.close()

    def save(self):
        _atomic(self.path, (json.dumps(self.state, ensure_ascii=False, indent=2)+'\n').encode('utf-8'))

    def target(self, raw):
        if not isinstance(raw, str) or not raw or '\x00' in raw:
            raise ValueError('Chemin requis')
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = self.root / path
        resolved = path.resolve()
        if not resolved.is_relative_to(self.root) or is_forbidden(resolved):
            raise ValueError('Chemin hors du workspace')
        if any(part in {'.git', '.secrets', '.ssh'} for part in resolved.relative_to(self.root).parts):
            raise ValueError('Répertoire privé/protégé')
        if resolved.name == '.env' or resolved.name.startswith('.env.'):
            raise ValueError('Fichier de secrets protégé')
        return resolved

    def _discover_local_skills(self) -> list[dict]:
        bases = [
            ('project', self.root / '.aurora' / 'skills'),
            ('project', self.root.parent / '.aurora' / 'skills'),
            ('user', Path.home() / '.aurora' / 'skills'),
        ]
        found, seen = [], set()
        for level, base in bases:
            if not base.is_dir():
                continue
            for entry in sorted(base.iterdir())[:100]:
                skill_file = entry / 'SKILL.md'
                if entry.name in seen or not skill_file.is_file():
                    continue
                try:
                    if skill_file.stat().st_size > 65536:
                        continue
                    content = skill_file.read_text(encoding='utf-8')
                except (OSError, UnicodeError):
                    continue
                seen.add(entry.name)
                desc = ''
                if content.startswith('---'):
                    for line in content.split('---', 2)[1].splitlines():
                        if line.startswith('description:'):
                            desc = line.split(':', 1)[1].strip().strip('\'"')
                found.append({
                    'name': entry.name,
                    'description': desc,
                    'level': level,
                    'file': str(skill_file),
                    'content': content,
                })
        return found

    def _discover_local_mcp_servers(self) -> dict[str, dict]:
        configs = [
            self.root / '.mcp.json',
            self.root / '.aurora' / 'mcp_config.json',
            Path.home() / '.aurora' / 'mcp_config.json',
        ]
        servers: dict[str, dict] = {}
        for cfg_path in configs:
            if not cfg_path.is_file():
                continue
            try:
                raw = json.loads(cfg_path.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                continue
            if not isinstance(raw, dict):
                continue
            entries = raw.get('mcpServers') if isinstance(raw.get('mcpServers'), dict) else raw
            for srv_name, srv_cfg in entries.items():
                if srv_name in servers or not isinstance(srv_cfg, dict):
                    continue
                cmd = srv_cfg.get('command')
                if isinstance(cmd, str) and cmd.strip():
                    servers[srv_name] = {
                        'command': cmd.strip(),
                        'args': [str(a) for a in srv_cfg.get('args', [])] if isinstance(srv_cfg.get('args'), list) else [],
                        'env': {str(k): str(v) for k, v in srv_cfg.get('env', {}).items()} if isinstance(srv_cfg.get('env'), dict) else {},
                        'source': str(cfg_path),
                    }
        return servers

    def _query_mcp_server(self, server_name: str, cfg: dict, method: str, params: dict | None = None) -> dict:
        argv = [cfg['command'], *cfg.get('args', [])]
        env = dict(os.environ)
        env.update(cfg.get('env') or {})
        messages = [
            {
                'jsonrpc': '2.0',
                'id': 1,
                'method': 'initialize',
                'params': {
                    'protocolVersion': '2024-11-05',
                    'capabilities': {},
                    'clientInfo': {'name': 'aurora-local-cli', 'version': '1.0.0'},
                },
            },
            {'jsonrpc': '2.0', 'method': 'notifications/initialized'},
            {'jsonrpc': '2.0', 'id': 2, 'method': method, 'params': params or {}},
        ]
        payload = '\n'.join(json.dumps(m, ensure_ascii=False) for m in messages) + '\n'
        proc = subprocess.run(
            argv,
            input=payload,
            cwd=self.root,
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
        )
        parsed = []
        for line in proc.stdout.splitlines():
            line = line.strip()
            if not line.startswith('{'):
                continue
            try:
                obj = json.loads(line)
                if isinstance(obj, dict):
                    parsed.append(obj)
            except ValueError:
                continue
        target_msg = next((m for m in parsed if m.get('id') == 2), parsed[-1] if parsed else None)
        if not target_msg:
            err = proc.stderr[:1000].strip() or f'Aucune réponse JSON-RPC du serveur MCP {server_name}'
            raise ValueError(err)
        if 'error' in target_msg and target_msg['error']:
            raise ValueError(f"Erreur MCP ({server_name}) : {target_msg['error']}")
        res = target_msg.get('result')
        return res if isinstance(res, dict) else {'value': res}

    def _refresh_tracked_files(self):
        for raw in list(self.state['files']):
            path = self.target(raw)
            self.state['files'][raw] = _digest(path.read_bytes()) if path.is_file() else None

    def _observe(self, tool, args, result):
        evidence = dict(id='ev_'+uuid.uuid4().hex[:12], tool=tool, args=args, **result)
        self.state['evidence'].append(evidence)
        # A criterion can only be verified by a current successful observation.
        criterion = args.get('criterion')
        if result.get('ok') and tool in {'verify_file', 'run_python', 'run_command', 'call_mcp_tool', 'test_hypothesis', 'fetch_url'} and criterion:
            if criterion not in self.state['criteria']:
                raise ValueError('Critère absent du plan accepté')
            self.state['verified'][criterion] = evidence['id']
        self.state['pending_action'] = None
        self.save()
        return evidence

    def tool(self, name, args):
        if not isinstance(args, dict):
            raise ValueError('Arguments objet requis')
        if args.get('criterion') and args['criterion'] not in self.state['criteria']:
            raise ValueError('Critère absent du plan accepté')
        if name == 'set_plan':
            plan, criteria = args.get('steps'), args.get('criteria')
            if not all(isinstance(x, list) and 0 < len(x) <= 32 and
                       all(isinstance(v, str) and v.strip() and len(v) < 1000 for v in x)
                       for x in [plan, criteria]):
                raise ValueError('Plan et critères non vides requis')
            if self.state['plan']:
                if self.state['plan'] != plan or self.state['criteria'] != criteria:
                    raise ValueError('Plan accepté conservé ; une observation ne le remplace pas')
                return {'ok': True, 'reused': True, 'tool':'set_plan'}
            self.state.update(plan=plan, criteria=criteria)
            return self._observe(name, args, {'ok': True})
        if name == 'revise_plan':
            if not self.state['plan']:
                raise ValueError('Accepter un plan initial avant de réviser ses étapes')
            steps = args.get('steps')
            reason = args.get('reason', '')
            evidence_ids = args.get('evidence_ids', [])
            if not (isinstance(steps, list) and 0 < len(steps) <= 32 and
                    all(isinstance(v, str) and v.strip() and len(v) < 1000 for v in steps)):
                raise ValueError('Étapes révisées non vides requises')
            if not isinstance(reason, str) or not reason.strip():
                raise ValueError('Motif de révision requis')
            known_ids = {e['id'] for e in self.state['evidence'] if e.get('tool') not in {'set_plan', 'revise_plan'}}
            if not isinstance(evidence_ids, list) or not evidence_ids or any(eid not in known_ids for eid in evidence_ids):
                raise ValueError('Citer au moins un identifiant de preuve réel observé')
            self.state['plan'] = list(steps)
            return self._observe(name, args, {
                'ok': True, 'steps': self.state['plan'],
                'criteria': self.state['criteria'], 'verified': self.state['verified'],
            })
        if name not in self.ALL_TOOLS:
            raise ValueError('Outil non disponible : '+str(name))
        effects = name in self.EFFECT_TOOLS
        if effects:
            if self.state['permissions'] == 'SAFE':
                raise PermissionError('SAFE interdit écritures et exécution')
            if not self.state['plan']:
                raise ValueError('Accepter un plan et ses critères avant toute action')
            if self.state['pending_action']:
                raise RuntimeError('Action interrompue de résultat inconnu ; observation nécessaire, aucun rejeu')
            self.state['pending_action'] = {'tool': name, 'args': args}
            self.save()
        result = {'ok': True}
        try:
            if name == 'inspect_runtime':
                from .epistemic import classify_problem_regime
                skills = self._discover_local_skills()
                mcp_servers = self._discover_local_mcp_servers()
                epistemic_routing = classify_problem_regime(self.state.get('request', ''), skills=skills)
                result.update(python=sys.executable, platform=sys.platform, workspace=str(self.root),
                              tools=sorted(self.ALL_TOOLS),
                              skills=[{'name': s['name'], 'description': s['description'], 'level': s['level']} for s in skills],
                              mcp_servers=sorted(mcp_servers.keys()),
                              epistemic_routing=epistemic_routing,
                              scope='Python hôte, sans sandbox OS ; moteurs média non exécutés par cette boucle')
            elif name == 'list_files':
                path = self.target(args.get('path', '.'))
                result['files'] = sorted(p.name+('/' if p.is_dir() else '') for p in path.iterdir())[:256]
            elif name == 'find_files':
                import fnmatch
                base = self.target(args.get('path', '.'))
                pattern = args.get('pattern', '*')
                if not isinstance(pattern, str) or not pattern.strip():
                    pattern = '*'
                skip_dirs = {'.git', '.secrets', '.ssh', 'node_modules', '__pycache__', '.venv', 'venv', 'dist', 'build'}
                found = []
                for dirpath, dirnames, filenames in os.walk(base, followlinks=False):
                    dirnames[:] = sorted(d for d in dirnames if d not in skip_dirs and not d.startswith('.'))
                    for fname in sorted(filenames):
                        if fname == '.env' or fname.startswith('.env.'):
                            continue
                        if fnmatch.fnmatch(fname, pattern):
                            full = (Path(dirpath) / fname).resolve()
                            if full.is_relative_to(self.root):
                                found.append(full.relative_to(self.root).as_posix())
                                if len(found) >= 256:
                                    break
                    if len(found) >= 256:
                        break
                result.update(files=found, total=len(found), truncated=len(found) >= 256)
            elif name == 'search_code':
                import fnmatch
                query = args.get('query', '')
                if not isinstance(query, str) or not query.strip():
                    raise ValueError('Requête de recherche non vide requise')
                base = self.target(args.get('path', '.'))
                use_regex = bool(args.get('regex', False))
                case_sensitive = bool(args.get('case_sensitive', False))
                file_pattern = args.get('file_pattern', '')
                flags = 0 if case_sensitive else re.IGNORECASE
                compiled = re.compile(query if use_regex else re.escape(query), flags)
                skip_dirs = {'.git', '.secrets', '.ssh', 'node_modules', '__pycache__', '.venv', 'venv', 'dist', 'build'}
                candidates = [base] if base.is_file() else []
                if base.is_dir():
                    for dirpath, dirnames, filenames in os.walk(base, followlinks=False):
                        dirnames[:] = sorted(d for d in dirnames if d not in skip_dirs and not d.startswith('.'))
                        for fname in sorted(filenames):
                            if fname == '.env' or fname.startswith('.env.'):
                                continue
                            if file_pattern and not fnmatch.fnmatch(fname, file_pattern):
                                continue
                            candidates.append(Path(dirpath) / fname)
                            if len(candidates) >= 1000:
                                break
                        if len(candidates) >= 1000:
                            break
                matches = []
                total_matches = 0
                for cand in candidates:
                    try:
                        resolved = cand.resolve()
                        if not resolved.is_relative_to(self.root) or resolved.stat().st_size > 1_000_000:
                            continue
                        raw = resolved.read_bytes()
                        if b'\x00' in raw[:4096]:
                            continue
                        text = raw.decode('utf-8', errors='replace')
                    except OSError:
                        continue
                    rel = resolved.relative_to(self.root).as_posix()
                    for lineno, line in enumerate(text.splitlines(), 1):
                        if compiled.search(line):
                            total_matches += 1
                            if len(matches) < 50:
                                matches.append({'path': rel, 'line': lineno, 'content': line.strip()[:240]})
                result.update(query=query, matches=matches, total_matches=total_matches, truncated=total_matches > len(matches))
            elif name == 'git_status':
                proc_status = subprocess.run(
                    ['git', 'status', '--short', '--branch'],
                    cwd=self.root, capture_output=True, text=True, timeout=10,
                )
                proc_diff = subprocess.run(
                    ['git', 'diff', '--stat'],
                    cwd=self.root, capture_output=True, text=True, timeout=10,
                )
                result.update(
                    ok=proc_status.returncode == 0,
                    status=proc_status.stdout[:8000].strip(),
                    diff_stat=proc_diff.stdout[:8000].strip(),
                    error=proc_status.stderr[:1000].strip() if proc_status.returncode != 0 else '',
                )
            elif name == 'list_skills':
                skills = self._discover_local_skills()
                result['skills'] = [{'name': s['name'], 'description': s['description'], 'level': s['level']} for s in skills]
            elif name == 'read_skill':
                skill_name = args.get('name', '')
                skills = self._discover_local_skills()
                matched = next((s for s in skills if s['name'] == skill_name), None)
                if not matched:
                    raise ValueError(f'Skill introuvable : {skill_name}')
                result.update(name=matched['name'], level=matched['level'], content=matched['content'][:16000])
            elif name == 'create_skill':
                skill_name = str(args.get('name', '') or '').strip()
                description = str(args.get('description', '') or '').strip()
                instructions = str(args.get('instructions', '') or '').strip()
                if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', skill_name):
                    raise ValueError('Nom de skill invalide (1-80 caractères alphanumériques, _ ou -)')
                if not description or not instructions:
                    raise ValueError('description et instructions non vides requises pour create_skill')
                skill_file = self.root / '.aurora' / 'skills' / skill_name / 'SKILL.md'
                body = f"---\nname: {skill_name}\ndescription: {description}\n---\n{instructions}\n"
                raw_bytes = body.encode('utf-8')
                _atomic(skill_file, raw_bytes)
                digest = _digest(skill_file.read_bytes())
                self.state['files'][str(skill_file)] = digest
                self.state['verified'] = {}
                result.update(name=skill_name, path=str(skill_file), sha256=digest, bytes=len(raw_bytes))
            elif name == 'search_web':
                from urllib.parse import quote_plus, parse_qs, urljoin, urlsplit
                from urllib.request import Request, urlopen
                from html.parser import HTMLParser
                query = str(args.get('query', '') or '').strip()
                if not query:
                    raise ValueError('Requête search_web non vide requise')
                req = Request(
                    f'https://html.duckduckgo.com/html/?q={quote_plus(query)}',
                    headers={'User-Agent': 'Aurora-CLI/1.0'},
                )
                with urlopen(req, timeout=10) as resp:
                    html_text = resp.read(256_000).decode('utf-8', errors='replace')
                class _LinkParser(HTMLParser):
                    def __init__(self):
                        super().__init__()
                        self.links, self.cur = [], None
                    def handle_starttag(self, tag, attrs):
                        d = dict(attrs)
                        if tag == 'a' and 'result__a' in d.get('class', '').split():
                            self.cur = {'url': d.get('href', ''), 'title': ''}
                    def handle_data(self, data):
                        if self.cur is not None:
                            self.cur['title'] += data
                    def handle_endtag(self, tag):
                        if tag == 'a' and self.cur is not None:
                            item, self.cur = self.cur, None
                            raw_u = urljoin('https://html.duckduckgo.com', item['url'])
                            item['url'] = parse_qs(urlsplit(raw_u).query).get('uddg', [raw_u])[0]
                            if urlsplit(item['url']).scheme in {'http', 'https'}:
                                self.links.append(item)
                parser = _LinkParser()
                parser.feed(html_text)
                result.update(query=query, results=parser.links[:10], total=len(parser.links))
            elif name == 'fetch_url':
                import ipaddress
                import socket
                from urllib.parse import urlsplit
                from urllib.request import Request, urlopen
                from html.parser import HTMLParser
                url = str(args.get('url', '') or '').strip()
                parsed_u = urlsplit(url)
                if parsed_u.scheme not in {'http', 'https'} or not parsed_u.hostname:
                    raise ValueError('URL http(s) valide requise pour fetch_url')
                host = parsed_u.hostname.lower()
                if host in {'localhost', '127.0.0.1', '::1', '0.0.0.0'} or host.endswith('.local') or host.endswith('.internal'):
                    raise PermissionError('Accès SSRF local/interne interdit dans fetch_url')
                for info in socket.getaddrinfo(host, parsed_u.port or (443 if parsed_u.scheme == 'https' else 80)):
                    ip_obj = ipaddress.ip_address(info[4][0])
                    if ip_obj.is_private or ip_obj.is_loopback or ip_obj.is_link_local or ip_obj.is_reserved:
                        raise PermissionError('Adresse IP privée ou réservée interdite dans fetch_url')
                req = Request(url, headers={'User-Agent': 'Aurora-CLI/1.0'})
                with urlopen(req, timeout=10) as resp:
                    raw_body = resp.read(256_000)
                text_body = raw_body.decode('utf-8', errors='replace')
                class _TextExtractor(HTMLParser):
                    def __init__(self):
                        super().__init__()
                        self.parts, self.hide = [], 0
                    def handle_starttag(self, tag, attrs):
                        if tag in {'script', 'style', 'noscript'}:
                            self.hide += 1
                    def handle_endtag(self, tag):
                        if tag in {'script', 'style', 'noscript'} and self.hide:
                            self.hide -= 1
                        if tag in {'p', 'div', 'h1', 'h2', 'li', 'br'}:
                            self.parts.append('\n')
                    def handle_data(self, data):
                        if not self.hide:
                            self.parts.append(data)
                if '<html' in text_body.lower() or '<body' in text_body.lower():
                    ext = _TextExtractor()
                    ext.feed(text_body)
                    text_body = re.sub(r'\n{3,}', '\n\n', ''.join(ext.parts)).strip()
                result.update(url=url, sha256=_digest(raw_body), content=text_body[:16000], bytes=len(raw_body))
            elif name == 'test_hypothesis':
                from .epistemic import (
                    classify_problem_regime,
                    evaluate_competing_hypotheses,
                    verify_cyber_hypothesis,
                    verify_math_hypothesis,
                    verify_optimization_hypothesis,
                )
                mode = str(args.get('mode', 'math') or 'math').strip().lower()
                hypothesis = str(args.get('hypothesis', '') or '').strip()
                conjecture = str(args.get('conjecture', '') or '').strip()
                code_str = str(args.get('code', '') or '').strip()
                domain = str(args.get('domain', 'int') or 'int').strip()
                if mode == 'math':
                    result.update(verify_math_hypothesis(conjecture, domain=domain, hypothesis=hypothesis))
                elif mode == 'cyber':
                    result.update(verify_cyber_hypothesis(code_str, hypothesis=hypothesis))
                elif mode == 'optimize':
                    baseline_code = str(args.get('baseline_code', '') or '').strip()
                    candidate_code = str(args.get('candidate_code', '') or code_str).strip()
                    entry_fn = str(args.get('entry_function', '') or '').strip()
                    test_inputs = args.get('test_inputs') if isinstance(args.get('test_inputs'), list) else None
                    result.update(verify_optimization_hypothesis(
                        baseline_code, candidate_code,
                        entry_function=entry_fn, test_inputs=test_inputs, hypothesis=hypothesis,
                    ))
                elif mode == 'route':
                    skills = self._discover_local_skills()
                    result.update(classify_problem_regime(
                        hypothesis or conjecture or self.state.get('request', ''),
                        code=code_str,
                        skills=skills,
                    ))
                elif mode == 'explore':
                    hypotheses_list = args.get('hypotheses')
                    result.update(evaluate_competing_hypotheses(
                        hypothesis or self.state.get('request', ''),
                        hypotheses_list if isinstance(hypotheses_list, list) else [],
                    ))
                else:
                    raise ValueError('mode test_hypothesis invalide (attendu: math, cyber, optimize, route ou explore)')
            elif name == 'list_agents':
                module_filter = str(args.get('module', '') or '').strip().lower()
                agents_file = self.root / '.aurora' / 'agents.json'
                custom_agents = {}
                if agents_file.is_file():
                    try:
                        raw_agents = json.loads(agents_file.read_text(encoding='utf-8'))
                        if isinstance(raw_agents, dict) and isinstance(raw_agents.get('agents'), dict):
                            custom_agents = raw_agents['agents']
                    except (OSError, ValueError):
                        pass
                combined = []
                for aname, info in sorted(BUILTIN_MODULE_AGENTS.items()):
                    if not module_filter or info.get('module') == module_filter:
                        combined.append({'name': aname, **info, 'origin': 'builtin'})
                for aname, info in sorted(custom_agents.items()):
                    if isinstance(info, dict) and (not module_filter or str(info.get('module', '')).lower() == module_filter):
                        combined.append({'name': aname, **info, 'origin': 'custom'})
                result.update(agents=combined, total=len(combined))
            elif name == 'create_agent':
                agent_name = str(args.get('name', '') or '').strip()
                agent_role = str(args.get('role', '') or '').strip()
                agent_module = str(args.get('module', '') or 'general').strip().lower()
                parent_agent = str(args.get('parent_agent', '') or '').strip()
                if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', agent_name):
                    raise ValueError('Nom d’agent invalide (1-80 caractères alphanumériques, _ ou -)')
                if not agent_role or len(agent_role) > 4000:
                    raise ValueError('Rôle d’agent non vide requis (max 4000 caractères)')
                agents_file = self.root / '.aurora' / 'agents.json'
                store_data = {'agents': {}}
                if agents_file.is_file():
                    try:
                        parsed = json.loads(agents_file.read_text(encoding='utf-8'))
                        if isinstance(parsed, dict) and isinstance(parsed.get('agents'), dict):
                            store_data = parsed
                    except (OSError, ValueError):
                        pass
                entry = {
                    'name': agent_name,
                    'role': agent_role,
                    'module': agent_module,
                    'parent_agent': parent_agent or BUILTIN_MODULE_AGENTS.get(f'{agent_module}-lead', {}).get('name', ''),
                    'can_create_subagents': True,
                    'created_by': self.id,
                }
                store_data['agents'][agent_name] = entry
                _atomic(agents_file, (json.dumps(store_data, ensure_ascii=False, indent=2) + '\n').encode('utf-8'))
                result.update(agent=entry, path=str(agents_file))
            elif name == 'list_mcp_tools':
                servers = self._discover_local_mcp_servers()
                requested_server = args.get('server', '')
                if requested_server:
                    if requested_server not in servers:
                        raise ValueError(f'Serveur MCP introuvable : {requested_server}')
                    servers = {requested_server: servers[requested_server]}
                tools_list = []
                errors = {}
                for srv_name, srv_cfg in servers.items():
                    try:
                        resp = self._query_mcp_server(srv_name, srv_cfg, 'tools/list')
                        for item in resp.get('tools', []):
                            if isinstance(item, dict) and item.get('name'):
                                tools_list.append({
                                    'server': srv_name,
                                    'name': item['name'],
                                    'description': item.get('description', ''),
                                    'inputSchema': item.get('inputSchema', {}),
                                })
                    except Exception as exc:
                        errors[srv_name] = str(exc)
                result.update(servers=sorted(servers.keys()), tools=tools_list, errors=errors)
            elif name == 'call_mcp_tool':
                servers = self._discover_local_mcp_servers()
                if not servers:
                    raise ValueError('Aucun serveur MCP configuré dans .mcp.json ou .aurora/mcp_config.json')
                tool_name = args.get('name', '')
                if not isinstance(tool_name, str) or not tool_name.strip():
                    raise ValueError('Nom de l’outil MCP requis')
                server_name = args.get('server', '')
                if server_name:
                    if server_name not in servers:
                        raise ValueError(f'Serveur MCP inconnu : {server_name}')
                    target_servers = [(server_name, servers[server_name])]
                else:
                    target_servers = list(servers.items())
                tool_args = args.get('arguments') or {}
                if not isinstance(tool_args, dict):
                    raise ValueError('arguments MCP doit être un objet JSON')
                self.state['verified'] = {}
                last_err = None
                mcp_res = None
                chosen_server = ''
                for srv_name, srv_cfg in target_servers:
                    try:
                        mcp_res = self._query_mcp_server(srv_name, srv_cfg, 'tools/call', {
                            'name': tool_name,
                            'arguments': tool_args,
                        })
                        chosen_server = srv_name
                        break
                    except Exception as exc:
                        last_err = exc
                if mcp_res is None:
                    raise ValueError(str(last_err or f'Échec d’appel MCP pour {tool_name}'))
                is_err = bool(mcp_res.get('isError', False)) if isinstance(mcp_res, dict) else False
                self._refresh_tracked_files()
                result.update(ok=not is_err, server=chosen_server, name=tool_name, output=mcp_res)
            elif name == 'edit_file':
                path = self.target(args.get('path'))
                if not path.is_file():
                    raise ValueError('Fichier existant requis pour edit_file')
                old_string = args.get('old_string')
                new_string = args.get('new_string')
                if not isinstance(old_string, str) or not old_string:
                    raise ValueError('old_string non vide requis')
                if not isinstance(new_string, str):
                    raise ValueError('new_string chaîne requise')
                previous = path.read_bytes()
                expected = args.get('expected_sha256')
                if expected and expected != _digest(previous):
                    raise ValueError('expected_sha256 ne correspond pas au fichier observé')
                text = previous.decode('utf-8')
                count = text.count(old_string)
                if count == 0:
                    raise ValueError('old_string introuvable dans le fichier')
                replace_all = bool(args.get('replace_all', False))
                if count > 1 and not replace_all:
                    raise ValueError('old_string apparaît plusieurs fois ; préciser davantage de contexte ou replace_all=true')
                updated_text = text.replace(old_string, new_string) if replace_all else text.replace(old_string, new_string, 1)
                updated_bytes = updated_text.encode('utf-8')
                if len(updated_bytes) > 1_000_000:
                    raise ValueError('Contenu UTF-8 maximum 1 Mo dépassé')
                if updated_bytes == previous:
                    return self._observe(name, args, {
                        'ok': True, 'reused': True, 'path': str(path),
                        'bytes': len(previous), 'sha256': _digest(previous), 'replacements': 0,
                    })
                backup = self.path.parent / self.id / ('before-' + _digest(previous))
                if not backup.exists():
                    _atomic(backup, previous)
                _atomic(path, updated_bytes)
                digest = _digest(path.read_bytes())
                self.state['files'][str(path)] = digest
                self.state['verified'] = {}
                result.update(
                    path=str(path),
                    bytes=len(updated_bytes),
                    sha256=digest,
                    replacements=count if replace_all else 1,
                )
            elif name in {'read_file', 'verify_file', 'write_file'}:
                path = self.target(args.get('path'))
                if name == 'write_file':
                    content = args.get('content')
                    if not isinstance(content, str) or len(content.encode('utf-8')) > 1_000_000:
                        raise ValueError('Contenu UTF-8 requis, maximum 1 Mo')
                    previous = path.read_bytes() if path.exists() else None
                    expected = args.get('expected_sha256')
                    if previous == content.encode('utf-8'):
                        return self._observe(name, args, {'ok':True, 'reused':True, 'path':str(path),
                            'bytes':len(previous), 'sha256':_digest(previous)})
                    if previous is not None and expected != _digest(previous):
                        raise ValueError('Observer puis fournir expected_sha256 avant de remplacer un fichier existant')
                    if previous is not None:
                        backup = self.path.parent / self.id / ('before-'+_digest(previous))
                        if not backup.exists():
                            _atomic(backup, previous)
                    _atomic(path, content.encode('utf-8'))
                    self.state['files'][str(path)] = _digest(path.read_bytes())
                    # Changed content invalidates all previous completion proofs.
                    self.state['verified'] = {}
                data = path.read_bytes()
                result.update(path=str(path), bytes=len(data), sha256=_digest(data))
                if name == 'read_file':
                    offset = max(0, int(args.get('offset', 0) or 0))
                    limit = min(max(1, int(args.get('limit', 16000) or 16000)), 16000)
                    slice_bytes = data[offset:offset + limit]
                    result['content'] = slice_bytes.decode('utf-8', errors='replace') if offset or limit < 16000 else data[:16000].decode('utf-8')
                    result['truncated'] = len(data) > offset + limit
                    if offset:
                        result['offset'] = offset
                elif name == 'verify_file':
                    if not args.get('criterion') or not ('content' in args or 'sha256' in args):
                        raise ValueError('Critère et contenu exact ou empreinte attendue requis')
                    result['ok'] = (('content' not in args or data == args['content'].encode('utf-8')) and
                                    ('sha256' not in args or result['sha256'] == args['sha256']))
                    if result['ok']:
                        self.state['files'][str(path)] = result['sha256']
            elif name == 'run_python':
                script = self.target(args.get('path'))
                argv = args.get('args', [])
                if not isinstance(argv, list) or not all(isinstance(v, str) and '\x00' not in v for v in argv):
                    raise ValueError('Arguments Python : liste de chaînes requise')
                fingerprint = _digest(script.read_bytes())
                # Execution can affect the project. Require fresh verification afterwards.
                self.state['verified'] = {}
                proc = subprocess.run([sys.executable, str(script), *argv], cwd=self.root,
                                      capture_output=True, timeout=60)
                result.update(ok=proc.returncode == 0, returncode=proc.returncode, path=str(script),
                              script_sha256=fingerprint, stdout=proc.stdout[-16000:].decode('utf-8',errors='replace'),
                              stderr=proc.stderr[-16000:].decode('utf-8',errors='replace'))
                self._refresh_tracked_files()
            elif name == 'run_command':
                raw_argv = args.get('argv')
                if raw_argv is None and isinstance(args.get('command'), str) and args['command'].strip():
                    raw_argv = shlex.split(args['command'])
                if not isinstance(raw_argv, list) or not raw_argv or not all(isinstance(v, str) and v and '\x00' not in v for v in raw_argv):
                    raise ValueError('argv non vide (liste de chaînes sans NUL) requis pour run_command')
                exe_name = Path(raw_argv[0]).name.lower()
                if exe_name in {'sudo', 'su', 'shutdown', 'reboot', 'poweroff', 'halt', 'mkfs', 'dd'}:
                    raise PermissionError(f'Exécutable système interdit dans run_command : {exe_name}')
                self.state['verified'] = {}
                proc = subprocess.run(raw_argv, cwd=self.root, capture_output=True, timeout=60)
                result.update(
                    ok=proc.returncode == 0,
                    returncode=proc.returncode,
                    argv=raw_argv,
                    stdout=proc.stdout[-16000:].decode('utf-8', errors='replace'),
                    stderr=proc.stderr[-16000:].decode('utf-8', errors='replace'),
                )
                self._refresh_tracked_files()
        except subprocess.TimeoutExpired:
            # Timeout kills the direct subprocess, but its effects are unknown.
            self.state['status'] = 'interrupted'
            self.save()
            raise RuntimeError('Exécution a dépassé 60 s ; effets inconnus, état conservé sans rejeu')
        except Exception as exc:
            result = {'ok': False, 'error': str(exc)}
        return self._observe(name, args, result)

    def finish(self, text):
        if not isinstance(text, str) or not text.strip():
            raise ValueError('Conclusion requise')
        if not self.state['criteria'] or set(self.state['verified']) != set(self.state['criteria']):
            raise ValueError('Tous les critères doivent avoir une preuve réelle')
        for raw, digest in self.state['files'].items():
            path = self.target(raw)
            if not path.is_file() or _digest(path.read_bytes()) != digest:
                raise ValueError('Sortie modifiée après observation : '+raw)
        if self.state['pending_action']:
            raise ValueError('Action en attente de résultat ; aucun succès annoncé')
        self.state.update(status='completed', result=text)
        self.save()
        return True

    def run(self, runtime, model, *, context_tokens=8192, progress=lambda event: None, max_steps=32):
        if self.state['status'] == 'completed':
            return self.finish(self.state['result'])
        if self.state['pending_action']:
            raise RuntimeError('Action précédente de résultat inconnu ; checkpoint conservé sans rejeu : '+str(self.path))
        self.state.update(status='running', model=model)
        self.save()
        protocol = '''Execute the original request using only actual tools. Return ONE tool/args JSON object per turn, matching the schema.
Apply 3-Regime Adaptive Epistemic Intelligence:
1. KNOWN_CANONICAL (Reuse without reinventing): When a verified skill (list_skills/read_skill) or canonical stdlib algorithm already solves the task, reuse it directly without redundant exploration.
2. KNOWN_OPTIMIZABLE (Pareto Optimization): When optimizing an existing algorithm or baseline, verify strict output equivalence + measurable speedup via test_hypothesis(mode="optimize", baseline_code=..., candidate_code=...).
3. UNKNOWN_EXPLORATORY (Abductive Discovery): For unfamiliar problems, fuzzy requirements, 0-days, or mathematical conjectures, initiate competing falsifiable hypotheses and test them with test_hypothesis(mode="explore"|"math"|"cyber") / run_python / search_web / fetch_url, then capitalize proven discoveries with create_skill.
set_plan: choose real steps and criteria derived from the original request.
revise_plan: adapt remaining steps citing fresh evidence_ids and reason; criteria and verified proofs are preserved.
inspect_runtime: discover the actual interpreter, workspace, available skills, configured MCP servers, and epistemic_routing.
list_files: list a workspace directory.
find_files: recursively find files matching a glob pattern inside the workspace.
search_code: search UTF-8 files in the workspace for a literal or regex pattern with line numbers.
git_status: inspect git branch, status, and diff summary in the workspace.
list_skills / read_skill / create_skill: inspect or persist reusable project skills (.aurora/skills/<name>/SKILL.md).
search_web / fetch_url: research unknown facts, CVEs, or documentation over HTTP(S).
test_hypothesis: verify math conjectures (mode="math"), 0-day code constraints (mode="cyber"), Pareto optimizations (mode="optimize"), epistemic regime routing (mode="route"), or competing hypotheses (mode="explore").
list_mcp_tools / call_mcp_tool: discover and invoke local MCP servers (.mcp.json or .aurora/mcp_config.json).
read_file: inspect a real workspace file; returns its content and SHA-256.
write_file: exact UTF-8 content; existing files require the SHA-256 actually observed in read_file.
edit_file: surgical search-and-replace on an existing workspace file with automatic backup.
verify_file: compare real bytes with expected content or SHA-256; use an EXACT accepted criterion label.
run_python: run an existing inspected Python script; attach an EXACT accepted criterion label when checking it.
run_command: run a bounded non-shell command (argv list) in the workspace; attach an EXACT accepted criterion label when verifying.
Accept ONE plan with criteria before writing or running. Never replace an accepted plan.
Each criterion needs successful verify_file, run_python, run_command, call_mcp_tool, test_hypothesis, or fetch_url evidence.
Writes/runs invalidate old verification; verify again after the last change.
Paths are on THIS client, confined to the actual workspace. Discover files before guessing names.
SAFE permits reads only. Do not announce unexecuted work as done. Repeated identical observations do not advance the task.'''
        last_call = None
        repeats = 0
        parse_errors = 0
        try:
            for _ in range(max_steps):
                snapshot = {k:v for k,v in self.state.items() if k != 'evidence'}
                snapshot['evidence'] = self.state['evidence'][-6:]
                tools = [
                    'inspect_runtime', 'list_files', 'find_files', 'search_code',
                    'git_status', 'list_skills', 'read_skill', 'create_skill',
                    'search_web', 'fetch_url', 'test_hypothesis',
                    'list_agents', 'create_agent',
                    'list_mcp_tools', 'call_mcp_tool',
                    'read_file', 'write_file', 'edit_file', 'verify_file',
                    'run_python', 'run_command',
                ]
                instructions = protocol
                missing = [c for c in self.state['criteria'] if c not in self.state['verified']]
                completed = bool(self.state['criteria']) and not missing
                if not self.state['plan']:
                    tools.insert(0, 'set_plan')
                else:
                    tools.insert(0, 'revise_plan')
                    instructions = '\n'.join(line for line in protocol.splitlines() if '"tool":"set_plan"' not in line)
                    instructions += '\nThe plan is ALREADY ACCEPTED. Continue from the observed files. '
                    instructions += 'Missing criteria: '+json.dumps(missing)
                definitions = {
                    'set_plan':({'steps':{'type':'array','minItems':1,'items':{'type':'string'}},
                                 'criteria':{'type':'array','minItems':1,'items':{'type':'string'}}}, ['steps','criteria']),
                    'revise_plan':({'steps':{'type':'array','minItems':1,'items':{'type':'string'}},
                                    'reason':{'type':'string'},
                                    'evidence_ids':{'type':'array','minItems':1,'items':{'type':'string'}}}, ['steps','reason','evidence_ids']),
                    'inspect_runtime':({}, []),
                    'list_files':({'path':{'type':'string'}}, ['path']),
                    'find_files':({'path':{'type':'string'},'pattern':{'type':'string'}}, []),
                    'search_code':({'query':{'type':'string'},'path':{'type':'string'},
                                    'regex':{'type':'boolean'},'case_sensitive':{'type':'boolean'},
                                    'file_pattern':{'type':'string'}}, ['query']),
                    'git_status':({}, []),
                    'list_skills':({}, []),
                    'read_skill':({'name':{'type':'string'}}, ['name']),
                    'create_skill':({'name':{'type':'string'},'description':{'type':'string'},
                                     'instructions':{'type':'string'}}, ['name','description','instructions']),
                    'search_web':({'query':{'type':'string'}}, ['query']),
                    'fetch_url':({'url':{'type':'string'},
                                  'criterion':{'type':'string','enum':self.state['criteria']}}, ['url']),
                    'test_hypothesis':({'mode':{'type':'string','enum':['math','cyber','optimize','route','explore']},
                                        'hypothesis':{'type':'string'},
                                        'conjecture':{'type':'string'},
                                        'code':{'type':'string'},
                                        'baseline_code':{'type':'string'},
                                        'candidate_code':{'type':'string'},
                                        'entry_function':{'type':'string'},
                                        'test_inputs':{'type':'array'},
                                        'hypotheses':{'type':'array','items':{'type':'object'}},
                                        'domain':{'type':'string','enum':['int','pos_int','real']},
                                        'criterion':{'type':'string','enum':self.state['criteria']}}, ['mode']),
                    'list_agents':({'module':{'type':'string'}}, []),
                    'create_agent':({'name':{'type':'string'},'role':{'type':'string'},
                                     'module':{'type':'string'},'parent_agent':{'type':'string'}}, ['name','role']),
                    'list_mcp_tools':({'server':{'type':'string'}}, []),
                    'call_mcp_tool':({'name':{'type':'string'},'server':{'type':'string'},
                                      'arguments':{'type':'object'},
                                      'criterion':{'type':'string','enum':self.state['criteria']}}, ['name']),
                    'read_file':({'path':{'type':'string'},'offset':{'type':'integer'},'limit':{'type':'integer'}}, ['path']),
                    'write_file':({'path':{'type':'string'},'content':{'type':'string'},
                                   'expected_sha256':{'type':'string'}}, ['path','content']),
                    'edit_file':({'path':{'type':'string'},'old_string':{'type':'string'},
                                  'new_string':{'type':'string'},'expected_sha256':{'type':'string'},
                                  'replace_all':{'type':'boolean'}}, ['path','old_string','new_string']),
                    'verify_file':({'path':{'type':'string'},'content':{'type':'string'},'sha256':{'type':'string'},
                                    'criterion':{'type':'string','enum':self.state['criteria']}}, ['path','criterion']),
                    'run_python':({'path':{'type':'string'},'args':{'type':'array','items':{'type':'string'}},
                                   'criterion':{'type':'string','enum':self.state['criteria']}}, ['path']),
                    'run_command':({'argv':{'type':'array','minItems':1,'items':{'type':'string'}},
                                    'criterion':{'type':'string','enum':self.state['criteria']}}, ['argv']),
                }
                choices = []
                for tool in tools:
                    properties, required = definitions[tool]
                    args_schema = {'type':'object','properties':properties, 'required':required, 'additionalProperties':False}
                    if tool == 'verify_file':
                        args_schema['anyOf'] = [{'required':['content']},{'required':['sha256']}]
                    choices.append({'type':'object', 'properties':{'tool':{'const':tool},'args':args_schema},
                                    'required':['tool','args'], 'additionalProperties':False})
                schema = {'oneOf': choices}
                if completed:
                    instructions = 'Conclude the original request from the verified facts. Return only {"result":"concise final answer"}. No more actions.'
                    snapshot = {k:self.state[k] for k in ['request','workspace','criteria','verified','files']}
                    snapshot['evidence'] = self.state['evidence'][-3:]
                    schema = {'type':'object','properties':{'result':{'type':'string'}},
                              'required':['result'],'additionalProperties':False}
                answer = runtime.complete(model, [dict(role='system', content=instructions),
                    dict(role='user', content=json.dumps(snapshot, ensure_ascii=False))],
                    temperature=0, max_tokens=2048, context_length=context_tokens,
                    response_format=schema)
                raw = (answer or '').strip()
                candidates = re.findall(r'```(?:json)?\s*(.*?)```', raw, re.DOTALL) + [raw]
                call = None
                for cand in candidates:
                    cand = cand.strip()
                    if not cand:
                        continue
                    try:
                        parsed = json.loads(cand)
                        if isinstance(parsed, dict):
                            call = parsed
                            break
                    except (TypeError, ValueError):
                        continue
                if not isinstance(call, dict):
                    parse_errors += 1
                    self._observe('self_correction', {}, {
                        'ok': False,
                        'error': 'Réponse outil JSON objet requise ({tool, args} ou {result}) ; correction automatique engagée',
                    })
                    if parse_errors >= 3:
                        raise ValueError('Réponse outil JSON objet requise')
                    continue
                parse_errors = 0
                signature = json.dumps(call, sort_keys=True, ensure_ascii=False)
                repeats = repeats+1 if signature == last_call else 0
                last_call = signature
                if repeats >= 2:
                    if repeats < 4:
                        self._observe('self_correction', {'stalled_call': call}, {
                            'ok': False,
                            'error': 'Action répétée sans observation nouvelle ; changer d’outil ou vérifier les critères manquants : ' + json.dumps(missing, ensure_ascii=False),
                        })
                        continue
                    raise RuntimeError('Action répétée sans observation nouvelle ; travail conservé')
                try:
                    if set(call) == {'result'}:
                        return self.finish(call['result'])
                    if set(call) != {'tool', 'args'}:
                        raise ValueError('Réponse attendue : tool/args ou result')
                    event = self.tool(call['tool'], call['args'])
                    progress(event)
                except (ValueError, PermissionError) as exc:
                    self._observe('protocol_error', {}, {'ok':False, 'error':str(exc)})
            raise RuntimeError('Budget de mission atteint ; checkpoint conservé')
        except BaseException as exc:
            self.state.update(status='interrupted' if self.state['pending_action'] else 'blocked', error=str(exc))
            self.save()
            raise


def execute_local(request, *, workspace=None, model='', permissions='SAFE', resume_id='',
                  context_tokens=8192, runtime=None, progress=None, observer=None):
    from . import display
    if runtime is None:
        from .core.router import Router
        router = Router(mode='local')
        route = router.route()
        if not route.ok or not route.models:
            raise RuntimeError('Aucun moteur LLM local réellement servi ; démarre un runtime local')
        if model and model not in {m.name for m in route.models}:
            raise RuntimeError('Modèle non servi localement : '+model)
        model = model or router.pick_model(route=route, role='code')
        runtime = route.runtime
    if not model:
        raise RuntimeError('Aucun modèle local disponible dans le budget mémoire')
    mission = LocalMission(request, workspace or Path.cwd(), permissions, resume_id=resume_id)
    display.info('Mission locale '+mission.id+' · '+model)
    display.hint('Checkpoint : '+str(mission.path))
    try:
        mission.run(runtime, model, context_tokens=context_tokens,
                    progress=progress or (lambda e: display.hint(e['tool']+' · '+('OK' if e['ok'] else str(e.get('error','échec'))))))
        return dict(mission.state)
    finally:
        try:
            if observer:
                observer(mission.state)
        finally:
            mission.close()
