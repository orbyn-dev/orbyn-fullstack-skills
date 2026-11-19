"""Inspect exact rebuildable output units; the caller owns policy, provider and Trash effects."""
import datetime
import hashlib
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import stat
import time

PLAN_FOLDER = 'cleanup-plans'
PROFILES = {'safe': 14, 'moderate': 7, 'aggressive': 3}
KINDS = {'cargo-deps', 'cargo-build', 'cargo-incremental', 'node-modules'}
MAX_BYTES = 65536
SOURCE_MAX_BYTES = 8 * 1024 * 1024


class Probes:
    def __init__(self, workspace, helpers, artifact, artifact_probes, policy, completion):
        self.workspace, self.helpers = str(workspace), helpers
        self.artifact, self.artifact_probes = artifact, artifact_probes
        self.policy, self.completion = policy, completion


def plan_path(task, workspace):
    canonical = os.path.normcase(os.path.realpath(task))
    name = hashlib.sha256(os.fsencode(canonical)).hexdigest() + '.json'
    return Path(workspace)/'output'/PLAN_FOLDER/name


def _under(path, root):
    return path == root or root in path.parents


def _relative(value, allow_root=False):
    if allow_root and value == '.':
        return Path('.')
    if not isinstance(value, str) or not value or any(c in value for c in '\\:*?[]\0'):
        raise ValueError('plan paths must be exact relative paths')
    parts = value.split('/')
    if PurePosixPath(value).is_absolute() or PureWindowsPath(value).drive or any(p in ('', '.', '..') for p in parts):
        raise ValueError('plan path traverses or leaves its root')
    return Path(*parts)


def _plain(path, boundary, probes, allow_missing=False):
    if not _under(path, boundary) or str(path) != probes.helpers.canon(path):
        raise ValueError('path is not canonical inside its boundary')
    here = path
    while True:
        try:
            os.lstat(here)
            if probes.helpers.linked(here):
                raise ValueError('link or reparse point on protected path')
        except FileNotFoundError:
            if not allow_missing:
                raise
        if here == boundary:
            return
        if here == here.parent:
            raise ValueError('cannot establish path ancestry')
        here = here.parent


def _snapshot(path, probes):
    info = os.lstat(path)
    return {**probes.artifact.identity(path), 'mtime_ns': info.st_mtime_ns, 'size': info.st_size}


def _strict_record(task, probes):
    task, workspace = Path(task), Path(probes.workspace)
    if not task.is_absolute() or task.parent != workspace/'output' or task.name.casefold() in (PLAN_FOLDER, 'cleanup-receipts', 'worktree-archive'):
        raise ValueError('not an exact authorized output task')
    _plain(task, workspace, probes)
    if not task.is_dir():
        raise ValueError('output task is not a directory')
    record = plan_path(task, workspace)
    if not os.path.lexists(record):
        return None
    _plain(record, workspace, probes)
    before = _snapshot(record, probes)
    state, data = probes.helpers.read_json(record)
    if state != 'ok':
        raise ValueError('output plan is malformed, oversized or unreadable')
    with open(record, 'rb') as handle:
        raw = handle.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES or _snapshot(record, probes) != before:
        raise ValueError('output plan changed during inspection')
    tracked = probes.artifact_probes.git(workspace, 'ls-files', '-z')
    record_name = probes.artifact._fold(record.relative_to(workspace).as_posix())
    if tracked.returncode or any(probes.artifact._fold(name) == record_name for name in _text(tracked).split('\0') if name):
        raise ValueError('output plan must be untracked owner-local metadata')
    if set(data) != {'schema_version', 'task', 'repository', 'completion', 'keep', 'units'} or type(data['schema_version']) is not int or data['schema_version'] != 1:
        raise ValueError('unsupported output plan schema')
    if data['task'] != str(task):
        raise ValueError('output plan task does not match its registry key')
    repository = data['repository']
    if not isinstance(repository, str) or not Path(repository).is_absolute():
        raise ValueError('repository must be an existing canonical checkout')
    repo = Path(repository)
    _plain(repo, Path(repo.anchor), probes)
    if not repo.is_dir():
        raise ValueError('repository is not an existing directory')
    completion = data['completion']
    if not isinstance(completion, dict) or set(completion) != {'provider', 'number', 'head', 'target'}:
        raise ValueError('invalid completion record')
    if completion['provider'] not in ('github', 'gitlab') or type(completion['number']) is not int or completion['number'] <= 0:
        raise ValueError('unsupported completion provider or number')
    if not isinstance(completion['head'], str) or not re.fullmatch(r'(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})', completion['head']):
        raise ValueError('completion needs an exact produced commit')
    target = completion['target']
    if not isinstance(target, str) or not target or target.startswith('-') or '@{' in target or any(ord(c) < 32 for c in target):
        raise ValueError('invalid completion target branch')
    branch = probes.artifact_probes.git(repo, 'check-ref-format', '--branch', target)
    if branch.returncode:
        raise ValueError('invalid completion target branch')
    if not isinstance(data['keep'], list) or not data['keep'] or not isinstance(data['units'], list) or not data['units']:
        raise ValueError('output plan needs non-empty keep and unit lists')
    keep = []
    for name in data['keep']:
        path = task/_relative(name)
        _plain(path, task, probes)
        keep.append(path)
    units = []
    for entry in data['units']:
        if not isinstance(entry, dict) or set(entry) != {'path', 'target', 'kind', 'source'} or entry['kind'] not in KINDS:
            raise ValueError('unknown output unit schema or kind')
        path, target = (task/_relative(entry[k]) for k in ('path', 'target'))
        source = repo/_relative(entry['source'], allow_root=True)
        for candidate, boundary in ((path, task), (target, task), (source, repo)):
            _plain(candidate, boundary, probes, allow_missing=candidate != source)
            if os.path.lexists(candidate) and not candidate.is_dir():
                raise ValueError('unit, cache root and source must be directories')
        if path == task or target == task or not _under(path, target):
            raise ValueError('unit must be inside its exact child cache root')
        if entry['kind'] == 'node-modules' and (path != target or path.name != 'node_modules'):
            raise ValueError('dependency unit must be exactly node_modules')
        if any(_under(path, kept) or _under(kept, path) for kept in keep):
            raise ValueError('output unit overlaps preserved content')
        if any(_under(path, prior['path']) or _under(prior['path'], path) for prior in units):
            raise ValueError('output units overlap')
        units.append({'path': path, 'unit_root': target, 'owner': source, 'kind': entry['kind']})
    return {'data': data, 'path': str(record), 'identity': before, 'digest': hashlib.sha256(raw).hexdigest(),
            'keep': {str(path): _snapshot(path, probes) for path in keep}, 'units': units}


def discover(task, probes):
    try:
        plan = _strict_record(task, probes)
        if plan is None:
            return None
        return [{'class': 'output', 'output_unit': True, 'path': str(unit['path']), 'task_root': str(task),
                 'repo': plan['data']['repository'], 'source': str(unit['owner']), 'unit_root': str(unit['unit_root']),
                 'kind': unit['kind'], 'record_path': plan['path'], 'record_identity': plan['identity'],
                 'record_digest': plan['digest'], 'keep_identities': plan['keep']} for unit in plan['units']]
    except ValueError as error:
        return [{'class': 'output', 'output_unit': True, 'path': str(task), 'task_root': str(task),
                 'discovery_error': probes.helpers.clean('invalid output plan: ' + str(error))[:300]}]
    except (OSError, TypeError, RecursionError):
        return [{'class': 'output', 'output_unit': True, 'path': str(task), 'task_root': str(task),
                 'discovery_error': 'output plan is invalid or cannot be inspected'}]


def _text(result):
    return result.stdout.decode('utf-8', 'surrogateescape') if isinstance(result.stdout, bytes) else result.stdout


def _source_proof(spec, completion, probes):
    repo, source = Path(spec['repo']), Path(spec['source'])
    ap = probes.artifact_probes
    started = time.monotonic()
    def within_budget():
        if time.monotonic() - started >= ap.limits['seconds_limit']:
            raise ValueError('produced source proof inspection budget exceeded')
    def git(*args):
        within_budget()
        result = ap.git(repo, *args)
        within_budget()
        return result
    # Older Git versions may ignore GIT_NO_LAZY_FETCH. Reject partial clones
    # before object access rather than risking an implicit network fetch.
    promisor = git('config', '--get-regexp', r'^remote\..*\.promisor$')
    if promisor.returncode not in (0, 1):
        raise ValueError('cannot establish local complete source history')
    if promisor.returncode == 0:
        for line in _text(promisor).splitlines():
            value = line.split(maxsplit=1)
            if len(value) != 2 or value[1].casefold() not in ('false', 'no', 'off', '0'):
                raise ValueError('partial-clone source history is unsupported')
    partial = git('config', '--get', 'extensions.partialClone')
    if partial.returncode != 1:
        raise ValueError('partial-clone source history is unsupported or unknown')
    result = git('ls-files', '-z')
    if result.returncode:
        raise ValueError('cannot establish tracked source ownership')
    names = tuple(name for name in _text(result).split('\0') if name)
    if len(names) > ap.limits['entry_limit']:
        raise ValueError('produced source proof entry budget exceeded')
    head = completion['head']
    result = git('cat-file', '-t', head)
    if result.returncode or _text(result).strip() != 'commit':
        raise ValueError('produced source commit is not available locally')
    filenames = ('Cargo.toml', 'Cargo.lock') if spec['kind'].startswith('cargo-') else ('package.json', 'package-lock.json')
    hashes, identities = {}, {}
    for name in filenames:
        path = source/name
        _plain(path, repo, probes)
        info = os.lstat(path)
        before = _snapshot(path, probes)
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= SOURCE_MAX_BYTES:
            raise ValueError('source manifest or lock is not a bounded non-empty regular file')
        relative = path.relative_to(repo).as_posix()
        if relative not in names:
            raise ValueError('source manifest and lock must be tracked')
        entry = git('ls-tree', '-z', head, '--', ':(literal)' + relative)
        records = [record for record in _text(entry).split('\0') if record]
        if entry.returncode or len(records) != 1 or '\t' not in records[0]:
            raise ValueError('produced source path is missing or ambiguous in Git history')
        metadata, recorded_path = records[0].split('\t', 1)
        fields = metadata.split()
        if (len(fields) != 3 or fields[0] not in ('100644', '100755') or fields[1] != 'blob'
                or not re.fullmatch(r'(?:[0-9a-f]{40}|[0-9a-f]{64})', fields[2]) or recorded_path != relative):
            raise ValueError('produced source manifest or lock is not a regular Git blob')
        object_name = head + ':' + relative
        size = git('cat-file', '-s', object_name)
        if size.returncode or not _text(size).strip().isdigit() or not 0 < int(_text(size).strip()) <= SOURCE_MAX_BYTES:
            raise ValueError('produced source blob is missing or outside the size bound')
        historical = git('show', object_name)
        if historical.returncode:
            raise ValueError('cannot read produced source blob locally')
        # Existing Git helper decodes output with surrogateescape. Round-tripping
        # preserves original bytes; only CRLF is normalized for checkout parity.
        blob = historical.stdout if isinstance(historical.stdout, bytes) else historical.stdout.encode('utf-8', 'surrogateescape')
        with open(path, 'rb') as handle:
            current = handle.read(SOURCE_MAX_BYTES + 1)
        if len(current) > SOURCE_MAX_BYTES or len(blob) > SOURCE_MAX_BYTES or current.replace(b'\r\n', b'\n') != blob.replace(b'\r\n', b'\n'):
            raise ValueError('produced source manifest or lock differs from tracked checkout')
        if _snapshot(path, probes) != before:
            raise ValueError('source manifest or lock changed during inspection')
        hashes[name] = hashlib.sha256(current.replace(b'\r\n', b'\n')).hexdigest()
        identities[str(path)] = before
    within_budget()
    return names, hashes, identities


def _protections(spec, probes):
    path, task, workspace = Path(spec['path']), Path(spec['task_root']), Path(probes.workspace)
    _plain(path, workspace, probes)
    here = path
    today = datetime.datetime.fromtimestamp(probes.artifact_probes.now, datetime.timezone.utc).date()
    while True:
        names = os.listdir(here)
        if any(name.casefold() == 'hold.json' and name != 'HOLD.json' for name in names):
            return 'held', 'ambiguous HOLD.json protection'
        hold = probes.helpers.read_hold(here, today)
        if hold:
            return ('report-only', 'expired HOLD.json requires review') if hold['state'] == 'expired' else ('held', 'HOLD.json protects output unit or ancestor')
        keep = probes.helpers.read_keep(here)
        if keep:
            if keep.get('fault'):
                return 'held', 'KEEP.txt protection is malformed'
            if not any(_under(path, Path(candidate)) for candidate in probes.helpers.outside_keep(here, keep)):
                return 'held', 'KEEP.txt protects output unit or ancestor'
        if here == workspace:
            break
        if here == here.parent:
            return 'held', 'cannot establish output protection ancestry'
        here = here.parent
    # Output may be nested in a registered library checkout. Reject tracked
    # content with one caller-owned, read-only Git query; failure is unknown.
    result = probes.artifact_probes.git(workspace, 'ls-files', '-z')
    relative = probes.artifact._fold(path.relative_to(workspace).as_posix())
    if result.returncode or any(probes.artifact._fold(name) == relative or probes.artifact._fold(name).startswith(relative + '/')
                               for name in _text(result).split('\0') if name):
        return 'held', 'output unit contains tracked files or Git cannot verify it'
    return None, None


def evaluate(spec, probes, budget=None):
    item = {**spec, 'status': 'held', 'reason': '', 'root': spec['task_root'], 'kb': 0, 'kb_unique': 0,
            'complete': True, 'profile': 'legacy', 'profile_source': '', 'age_days': 0, 'threshold_days': None,
            'tool_proof': {}}
    def done(status, reason):
        item.update(status=status, reason=probes.helpers.clean(reason))
        return item
    if spec.get('discovery_error'):
        item['complete'] = False
        return done('held', spec['discovery_error'])
    try:
        fresh = discover(spec['task_root'], probes)
        current = next((s for s in fresh or [] if s['path'] == spec['path'] and not s.get('discovery_error')), None)
        snapshots = ('record_identity', 'record_digest', 'keep_identities', 'kind', 'source', 'unit_root', 'repo')
        if current is None or any(current.get(key) != spec.get(key) for key in snapshots):
            if current is None:
                item['complete'] = False
            return done('held', 'output plan or preserved paths changed since discovery')
        if not os.path.lexists(spec['path']):
            return done('held', 'exact output unit is already absent; no substitute path selected')
        state, plan = probes.helpers.read_json(spec['record_path'])
        if state != 'ok':
            return done('held', 'cannot re-read output plan')
        profile, source, reason = probes.policy(spec['repo'])
        item.update(profile=profile, profile_source=source, threshold_days=PROFILES.get(profile))
        if reason or profile not in PROFILES:
            return done('held', reason or 'output cleanup profile is disabled')
        proof, why = probes.completion(spec['repo'], plan['completion'])
        if not isinstance(proof, dict) or not proof:
            return done('held', why or 'cannot establish exact provider completion')
        item['completion_proof'] = proof
        names, hashes, source_identities = _source_proof(spec, plan['completion'], probes)
        item['source_proof'] = hashes
        item['source_identities'] = source_identities
        status, reason = _protections(spec, probes)
        if status:
            return done(status, reason)
        item['identity'] = probes.artifact.identity(spec['path'])
        typed = {'path': spec['path'], 'owner': spec['source'], 'unit_root': spec['unit_root'],
                 'tree_path': spec['repo'], 'tracked_names': names, 'kind': spec['kind']}
        status, reason, tool = probes.artifact._proof(typed, probes.artifact_probes)
        item['tool_proof'] = tool
        seen = probes.artifact._scan(spec['path'], probes.artifact_probes, budget or probes.artifact_probes.new_budget())
        item.update({key: seen[key] for key in ('kb', 'kb_unique', 'complete', 'drive')})
        item['age_days'] = (probes.artifact_probes.now - seen['newest']) / probes.artifact.DAY
        if seen['reasons']:
            return done('held', '; '.join(seen['reasons']))
        if status:
            return done(status, reason)
        if item['age_days'] <= PROFILES[profile]:
            return done('waiting', 'output unit is not older than the selected threshold')
        quiet = {spec['task_root'], spec['unit_root'], str(Path(spec['path']).parent)}
        if tool.get('tool_lock') and os.path.lexists(tool['tool_lock']):
            quiet.add(tool['tool_lock'])
        for path in quiet:
            _plain(Path(path), Path(probes.workspace), probes)
            info = os.lstat(path)
            if probes.artifact_probes.now - max(info.st_mtime, info.st_ctime) <= probes.artifact.QUIET_SECONDS:
                return done('held', 'output producer activity is within the quiet window or ahead of the clock')
        usage = probes.artifact_probes.usage(spec['path'])
        if usage:
            return done('held', usage)
        busy = probes.artifact_probes.dirs_busy(sorted(quiet | {spec['path']}))
        if busy is None or busy:
            return done('held', 'output producer or tool lock is busy or unknown')
        source_changed = any(_snapshot(path, probes) != identity for path, identity in source_identities.items())
        if source_changed or probes.artifact.identity(spec['path']) != item['identity'] or discover(spec['task_root'], probes) != fresh:
            return done('held', 'output unit or ownership plan changed during inspection')
        item.update(trash=[spec['path']], trash_kb=item['kb'])
        return done('eligible', 'finished provider-bound task; quiet tool-proven rebuildable output unit')
    except ValueError as error:
        item['complete'] = False
        return done('held', str(error)[:300])
    except (OSError, TypeError, KeyError, RecursionError):
        item['complete'] = False
        return done('held', 'cannot establish output ownership, source proof or safety')


def recheck(spec, planned, probes):
    fresh = evaluate(spec, probes)
    snapshots = ('record_identity', 'record_digest', 'keep_identities', 'identity', 'profile', 'profile_source',
                 'completion_proof', 'source_proof', 'source_identities', 'tool_proof')
    if fresh['status'] != 'eligible':
        fresh['status'] = 'skipped'
    elif any(fresh.get(key) != planned.get(key) for key in snapshots):
        fresh.update(status='skipped', reason='output identity, plan, policy, completion or source proof changed')
    return fresh
