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


def needs_actions(request):
    return bool(re.search(
        r'\b(?:fichiers?|files?|dossiers?|répertoires?|folders?|directories|scripts?'
        r'|corrige\w*|fix\w*|bugs?|impl[ée]ment\w*|test(?:e|er|s)?|pytest|v[ée]rifi\w*'
        r'|analys\w*|refactor\w*|modifi\w*|cr[ée](?:e|er)|ajout\w*|fonctions?|classes?'
        r'|modules?|d[ée]p[ôo]ts?|repos?|workspaces?|git)\b'
        r'|\.(?:py|json|csv|txt|js|ts|tsx|jsx|html|css|md|toml|ya?ml|sh|sql)\b',
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


class LocalMission:
    READ_ONLY_TOOLS = {
        'inspect_runtime', 'list_files', 'find_files', 'search_code',
        'read_file', 'verify_file', 'git_status', 'list_skills', 'read_skill',
    }
    EFFECT_TOOLS = {'write_file', 'run_python'}
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

    def _observe(self, tool, args, result):
        evidence = dict(id='ev_'+uuid.uuid4().hex[:12], tool=tool, args=args, **result)
        self.state['evidence'].append(evidence)
        # A criterion can only be verified by a current successful observation.
        criterion = args.get('criterion')
        if result.get('ok') and tool in {'verify_file', 'run_python'} and criterion:
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
                skills = self._discover_local_skills()
                result.update(python=sys.executable, platform=sys.platform, workspace=str(self.root),
                              tools=sorted(self.ALL_TOOLS),
                              skills=[{'name': s['name'], 'description': s['description'], 'level': s['level']} for s in skills],
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
                # Track changes to every file written by this mission.
                for raw in self.state['files']:
                    path = self.target(raw)
                    self.state['files'][raw] = _digest(path.read_bytes()) if path.is_file() else None
        except subprocess.TimeoutExpired:
            # Timeout kills the direct subprocess, but its effects are unknown.
            self.state['status'] = 'interrupted'
            self.save()
            raise RuntimeError('Python a dépassé 60 s ; effets inconnus, état conservé sans rejeu')
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
set_plan: choose real steps and criteria derived from the original request.
revise_plan: adapt remaining steps citing fresh evidence_ids and reason; criteria and verified proofs are preserved.
inspect_runtime: discover the actual interpreter, workspace, and available skills.
list_files: list a workspace directory.
find_files: recursively find files matching a glob pattern inside the workspace.
search_code: search UTF-8 files in the workspace for a literal or regex pattern with line numbers.
git_status: inspect git branch, status, and diff summary in the workspace.
list_skills / read_skill: inspect project or user skills (.aurora/skills).
read_file: inspect a real workspace file; returns its content and SHA-256.
write_file: exact UTF-8 content; existing files require the SHA-256 actually observed in read_file.
verify_file: compare real bytes with expected content or SHA-256; use an EXACT accepted criterion label.
run_python: run an existing inspected Python script; attach an EXACT accepted criterion label when checking it.
Accept ONE plan with criteria before writing or running. Never replace an accepted plan.
Each criterion needs successful verify_file or run_python evidence. Run Python checks with real assertions.
Writes/runs invalidate old verification; verify again after the last change.
Paths are on THIS client, confined to the actual workspace. Discover files before guessing names.
SAFE permits reads only. No shell, deletion, installation, media engines, or web tools in this loop.
Do not announce unexecuted work as done. Repeated identical observations do not advance the task.'''
        last_call = None
        repeats = 0
        try:
            for _ in range(max_steps):
                snapshot = {k:v for k,v in self.state.items() if k != 'evidence'}
                snapshot['evidence'] = self.state['evidence'][-6:]
                tools = [
                    'inspect_runtime', 'list_files', 'find_files', 'search_code',
                    'git_status', 'list_skills', 'read_skill',
                    'read_file', 'write_file', 'verify_file', 'run_python',
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
                    'read_file':({'path':{'type':'string'},'offset':{'type':'integer'},'limit':{'type':'integer'}}, ['path']),
                    'write_file':({'path':{'type':'string'},'content':{'type':'string'},
                                   'expected_sha256':{'type':'string'}}, ['path','content']),
                    'verify_file':({'path':{'type':'string'},'content':{'type':'string'},'sha256':{'type':'string'},
                                    'criterion':{'type':'string','enum':self.state['criteria']}}, ['path','criterion']),
                    'run_python':({'path':{'type':'string'},'args':{'type':'array','items':{'type':'string'}},
                                   'criterion':{'type':'string','enum':self.state['criteria']}}, ['path']),
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
                # Reject prose, multiple objects and truncated answers before any effect.
                raw = answer.strip()
                if raw.startswith('```json') and raw.endswith('```'):
                    raw = raw[7:-3].strip()
                call = json.loads(raw)
                if not isinstance(call, dict):
                    raise ValueError('Réponse outil JSON objet requise')
                signature = json.dumps(call, sort_keys=True, ensure_ascii=False)
                repeats = repeats+1 if signature == last_call else 0
                last_call = signature
                if repeats >= 2:
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
