"""Read-only, bounded inspection of rebuildable cache units.

The caller owns repository authorization and Trash effects. No project code or
package manager is executed here. Native handle checks and existing protection
parsers are injected so report and pre-move checks share the same rules.
"""
import datetime
from itertools import chain
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import time
import unicodedata

try:
    import tomllib
except ImportError:
    tomllib = None

DAY = 86400
QUIET_SECONDS = 2 * 3600
SCAN_ENTRY_BUDGET = 400_000
SCAN_SECONDS_BUDGET = 60
SCAN_DEPTHS = {
    'standard': {'entry_limit': 400_000, 'seconds_limit': 60, 'usage_seconds': 30},
    'deep': {'entry_limit': 2_000_000, 'seconds_limit': 300, 'usage_seconds': 300},
}
PROFILES = {
    'safe': {'intermediate_days': 7, 'dependency_days': None},
    'moderate': {'intermediate_days': 3, 'dependency_days': 7},
    'aggressive': {'intermediate_days': 1, 'dependency_days': 1},
}
GENERIC = {'.venv', 'venv', 'dist', 'build', 'out', '.next', '.turbo', '.gradle', '__pycache__'}
DOCKER_COMMANDS = (
    ('docker', 'version', '--format', '{{json .}}'),
    ('docker', 'system', 'df', '--format', '{{json .}}'),
    ('docker', 'buildx', 'du'),
)
CACHE_SIGNATURE = 'Signature: 8a477f597d28d172789f06886806bc55'
INSTALL_JSON_MAX_BYTES = 8 * 1024 * 1024


def scan_limits(name):
    if not isinstance(name, str) or name not in SCAN_DEPTHS:
        raise ValueError('scan depth must be standard or deep')
    return dict(SCAN_DEPTHS[name])


class Probes:
    def __init__(self, git, usage, dirs_busy, now, run_readonly, helpers, workspace=None, scan_depth='standard'):
        self.git, self.usage, self.dirs_busy = git, usage, dirs_busy
        self.now, self.run_readonly, self.helpers = now, run_readonly, helpers
        self.workspace = workspace
        self.scan_depth, self.limits = scan_depth, scan_limits(scan_depth)
        self.discovery = {}
        self.owner_activity, self.source_names = {}, {}

    def new_budget(self):
        return Budget(entry_limit=self.limits['entry_limit'], seconds_limit=self.limits['seconds_limit'])


class Budget:
    """A run's memoized scans and unique-file accounting; limits apply per unit."""
    def __init__(self, entry_limit=SCAN_ENTRY_BUDGET, seconds_limit=SCAN_SECONDS_BUDGET):
        self.entry_limit, self.seconds_limit = entry_limit, seconds_limit
        self.scans, self.inodes = {}, set()


def profile_rules(name):
    rules = PROFILES.get(name)
    return dict(rules) if rules else None


def identity(path):
    info = os.lstat(path)
    return {'dev': info.st_dev, 'ino': info.st_ino, 'ctime_ns': info.st_ctime_ns}


def _fold(value):
    return unicodedata.normalize('NFC', str(value).replace('\\', '/')).casefold().rstrip('/')


def _under(path, root):
    try:
        Path(path).relative_to(root)
        return True
    except ValueError:
        return False


def _text(result):
    return result.stdout.decode('utf-8', 'surrogateescape') if isinstance(result.stdout, bytes) else result.stdout


def _git_names(root, probes, ignored=False):
    args = ('ls-files', '-z', '--others', '--ignored', '--exclude-standard', '--directory') if ignored else ('ls-files', '-z')
    result = probes.git(root, *args)
    if result.returncode:
        raise ValueError('cannot ask Git whether cache paths are ignored or tracked')
    return tuple(name for name in _text(result).split('\0') if name)


def _spec(root, gitdir, path, kind, owner, unit_root, names, **extra):
    return {'class': 'artifact', 'path': str(path), 'repo': str(root), 'tree_path': str(root),
            'gitdir': str(gitdir), 'kind': kind, 'owner': str(owner), 'unit_root': str(unit_root),
            'tracked_names': names, **extra}


def _cargo_specs(root, gitdir, target, owner, names, probes):
    specs = []
    started, entries = time.monotonic(), 0
    def children(folder):
        nonlocal entries
        found = []
        with os.scandir(folder) as iterator:
            for entry in iterator:
                entries += 1
                if entries > probes.limits['entry_limit'] or time.monotonic() - started > probes.limits['seconds_limit']:
                    raise ValueError('partial discovery; Cargo layout inspection budget exceeded')
                found.append(Path(entry.path))
        return sorted(found)
    # Only the conventional debug layout is recognized. Release and custom
    # profiles remain separate report-only units, never a whole-target move.
    try:
        if not probes.helpers.confined(target, root):
            return [_spec(root, gitdir, target, 'cargo-target-other', owner, target, names,
                          discovery_error='Cargo cache root is not confined without links')]
        target_children = children(target)
        debug_roots = [target/'debug']
        for child in target_children:
            if not probes.helpers.linked(child) and child.is_dir() and child.name != 'debug' and (child/'debug').is_dir():
                debug_roots.append(child/'debug')
        for debug in debug_roots:
            if probes.helpers.linked(debug):
                raise ValueError('link or reparse point in Cargo layout')
            if not debug.is_dir():
                continue
            for child in children(debug):
                if child.name == 'incremental' and child.is_dir() and not probes.helpers.linked(child):
                    for unit in children(child):
                        specs.append(_spec(root, gitdir, unit, 'cargo-incremental', owner, target, names))
                else:
                    kind = {'deps': 'cargo-deps', 'build': 'cargo-build'}.get(child.name, 'cargo-target-other')
                    specs.append(_spec(root, gitdir, child, kind, owner, target, names))
        for child in target_children:
            if child.name != 'debug' and not any(_under(debug, child) for debug in debug_roots):
                specs.append(_spec(root, gitdir, child, 'cargo-target-other', owner, target, names))
        if time.monotonic() - started >= probes.limits['seconds_limit']:
            raise ValueError('partial discovery; Cargo layout inspection budget exceeded')
    except (OSError, ValueError) as error:
        # Discard any partial units so incomplete discovery cannot lead to moves.
        specs = [_spec(root, gitdir, target, 'cargo-target-other', owner, target, names,
                       discovery_error=str(error) if isinstance(error, ValueError) else 'cannot inspect Cargo cache layout')]
    return specs


def discover(worktree, gitdir, probes, within=None):
    root = Path(worktree)
    key = (str(root), str(gitdir), str(within))
    if key in probes.discovery:
        return probes.discovery[key]
    specs = []
    try:
        names, ignored = _git_names(root, probes), _git_names(root, probes, True)
        configured = {}
        config_error = None
        for config in (root/'.cargo/config.toml', root/'.cargo/config'):
            if not os.path.lexists(config):
                continue
            try:
                if probes.helpers.linked(config) or not config.is_file() or config.stat().st_size > 65536 or tomllib is None:
                    raise ValueError('unsupported Cargo config')
                data = tomllib.loads(config.read_text(encoding='utf-8'))
                build = data.get('build', {})
                if not isinstance(build, dict):
                    raise ValueError('invalid Cargo build config')
                value = build.get('target-dir')
                if value is not None:
                    if not isinstance(value, str) or not value or '\0' in value:
                        raise ValueError('invalid Cargo target-dir')
                    target = Path(value) if Path(value).is_absolute() else root/value
                    configured[os.path.abspath(target)] = root
            except (OSError, ValueError):
                config_error = 'malformed or unreadable Cargo configuration'
        roots = []
        for name in ignored:
            path = root/name.rstrip('/')
            if not _under(path, root) or '..' in Path(name).parts:
                raise ValueError('Git returned a path outside the worktree')
            if path.name in ('target', 'node_modules') or path.name in GENERIC or str(path) in configured:
                roots.append(path)
        for name, owner in configured.items():
            path = Path(name)
            if not _under(path, root):
                specs.append(_spec(root, gitdir, path, 'configured-store', owner, path, names,
                                   report_reason='configured Cargo store is outside the worktree; not inspected'))
            elif path not in roots and os.path.lexists(path):
                roots.append(path)
        claimed = []
        for path in sorted(set(roots), key=lambda p: (len(p.parts), str(p))):
            if within and not (_under(path, Path(within)) or _under(Path(within), path)):
                continue
            outer = next((s for s in claimed if _under(path, Path(s['path']))), None)
            if outer:
                if outer['items']:
                    outer['items'][0].setdefault('nested_in', []).append(str(path))
                continue
            owner = configured.get(str(path), path.parent)
            if path.name == 'target' or str(path) in configured:
                found = _cargo_specs(root, gitdir, path, owner, names, probes)
            else:
                found = [_spec(root, gitdir, path, 'node-modules' if path.name == 'node_modules' else 'build-other', owner, path, names)]
            if config_error:
                for spec in found:
                    spec['discovery_error'] = config_error
            specs.extend(found)
            claimed.append({'path': str(path), 'items': found})
    except (OSError, ValueError) as error:
        specs = [_spec(root, gitdir, root, 'build-other', root, root, (), discovery_error=str(error))]
    if within:
        specs = [s for s in specs if s.get('discovery_error') or _under(Path(s['path']), Path(within))]
    probes.discovery[key] = specs
    return specs


def _regular(path, probes):
    info = os.lstat(path)
    return stat.S_ISREG(info.st_mode) and not probes.helpers.linked(path) and info.st_size > 0


def _tracked_file(path, root, names):
    return _fold(Path(path).relative_to(root).as_posix()) in {_fold(n) for n in names}


def _install_json(path, probes):
    """Parse generated dependency metadata without weakening protection-file caps."""
    info = os.lstat(path)
    if not stat.S_ISREG(info.st_mode) or probes.helpers.linked(path) or info.st_size > INSTALL_JSON_MAX_BYTES:
        return None
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('duplicate installed-state key')
            result[key] = value
        return result
    def reject_constant(value):
        raise ValueError(f'nonfinite JSON number: {value}')
    try:
        with open(path, 'rb') as handle:
            raw = handle.read(INSTALL_JSON_MAX_BYTES + 1)
        if len(raw) > INSTALL_JSON_MAX_BYTES:
            return None
        data = json.loads(raw.decode('utf-8'), object_pairs_hook=unique,
                          parse_constant=reject_constant)
        if not isinstance(data, dict):
            return None
        return data
    except (OSError, ValueError, RecursionError):
        return None


def _npm_install_proof(source, installed):
    """Recognize npm v2/v3 locks with v3 hidden installed-state records."""
    if source is None or installed is None:
        return 'malformed or oversized npm lockfile or install marker'
    for data, versions in ((source, (2, 3)), (installed, (3,))):
        if type(data.get('lockfileVersion')) is not int or data['lockfileVersion'] not in versions:
            return 'unsupported or missing npm lockfile/install-marker version'
        if not isinstance(data.get('packages'), dict) or not data['packages']:
            return 'npm proof requires non-empty package records'
        if 'dependencies' in data and not isinstance(data['dependencies'], dict):
            return 'npm dependency records have invalid types'
        if any(not isinstance(record, dict) for record in data['packages'].values()):
            return 'npm package records have invalid types'
    package = r'(?:@[A-Za-z0-9_.~-]+/)?[A-Za-z0-9_.~-]+'
    locator = re.compile(r'node_modules/' + package + r'(?:/node_modules/' + package + r')*')
    for name, record in installed['packages'].items():
        if not locator.fullmatch(name) or any(part in ('.', '..') for part in name.split('/')):
            return 'unrecognized npm installed package locator'
        locked = source['packages'].get(name)
        if not isinstance(locked, dict):
            return 'installed npm package is absent from the tracked lockfile'
        if record.get('link') or locked.get('link'):
            return 'linked npm package records are unsupported'
        if not isinstance(record.get('version'), str) or not record['version'].strip():
            return 'npm installed package record has no version'
        if record['version'] != locked.get('version'):
            return 'npm installed package version differs from the tracked lockfile'
        for field in ('integrity', 'resolved'):
            if field in record or field in locked:
                if not isinstance(record.get(field), str) or not record[field] or record[field] != locked.get(field):
                    return f'npm installed package {field} differs from the tracked lockfile'
    return None


def _proof(spec, probes):
    owner, target, path = (Path(spec[k]) for k in ('owner', 'unit_root', 'path'))
    names, root = spec['tracked_names'], Path(spec['tree_path'])
    kind = spec['kind']
    if kind.startswith('cargo-'):
        for name in ('Cargo.toml', 'Cargo.lock'):
            if not _regular(owner/name, probes) or not _tracked_file(owner/name, root, names):
                return 'held', 'Cargo source manifest and non-empty lockfile must be tracked', {}
        if not _regular(target/'CACHEDIR.TAG', probes) or (target/'CACHEDIR.TAG').stat().st_size > 65536:
            return 'held', 'missing Cargo cache signature', {}
        if CACHE_SIGNATURE not in (target/'CACHEDIR.TAG').read_text(encoding='utf-8').splitlines():
            return 'held', 'invalid Cargo cache signature', {}
        state, data = probes.helpers.read_json(target/'.rustc_info.json')
        if state != 'ok':
            return 'held', 'missing or malformed compiler metadata', {}
        # Cargo has changed its internal layout. This adapter recognizes stable
        # rustc 1.60 through 1.99 and never guesses support for newer layouts.
        strings = []
        def collect(value):
            if isinstance(value, str):
                strings.append(value)
            elif isinstance(value, dict):
                for entry in value.values():
                    collect(entry)
            elif isinstance(value, list):
                for entry in value:
                    collect(entry)
        collect(data)
        versions = {m.group(0) for text in strings for m in re.finditer(r'(?m)^rustc 1\.\d+\.\d+(?:[^\n]*)', text)}
        proof = {'rustc': sorted(versions), 'adapter': 'cargo-debug-v1', 'layout': 'debug'}
        if len(versions) != 1:
            return 'held', 'compiler metadata does not identify one rustc version', proof
        version = next(iter(versions))
        stable = re.fullmatch(r'rustc 1\.(\d+)\.\d+ \([^()\r\n]+\)', version)
        if stable is None or not 60 <= int(stable.group(1)) < 100:
            return 'report-only', 'compiler version is outside the recognized stable layout', proof
        if kind == 'cargo-incremental':
            valid = path.parent.name == 'incremental' and path.parent.parent.name == 'debug' and bool(re.fullmatch(r'[A-Za-z0-9_-]+-[0-9a-z]+', path.name))
            debug = path.parent.parent
        else:
            valid = kind in ('cargo-deps', 'cargo-build') and path.parent.name == 'debug' and path.name in ('deps', 'build')
            debug = path.parent
        relative = debug.relative_to(target).parts
        valid = valid and (relative == ('debug',) or len(relative) == 2 and relative[-1] == 'debug' and bool(re.fullmatch(r'[a-zA-Z0-9_]+(?:-[a-zA-Z0-9_]+){2,}', relative[0])))
        if not valid:
            return 'report-only', 'unrecognized or release Cargo layout', proof
        proof['tool_lock'] = str(debug/'.cargo-lock')
        return None, None, proof
    if kind == 'node-modules':
        unsupported_marker = None
        for lock, markers in (('package-lock.json', ('.package-lock.json',)), ('pnpm-lock.yaml', ('.modules.yaml',)), ('yarn.lock', ('.yarn-state.yml', '.yarn-integrity'))):
            if (owner/lock).exists() and _regular(owner/lock, probes) and _tracked_file(owner/lock, root, names):
                for marker in markers:
                    if (path/marker).exists() and _regular(path/marker, probes):
                        if marker == '.package-lock.json':
                            reason = _npm_install_proof(_install_json(owner/lock, probes), _install_json(path/marker, probes))
                            if reason:
                                return 'held', reason, {}
                        else:
                            # Neither YAML nor Yarn Classic's JSON integrity file
                            # has a validated lock-to-install adapter here.
                            unsupported_marker = {'lockfile': str(owner/lock), 'install_marker': marker}
                            continue
                        return None, None, {'lockfile': str(owner/lock), 'install_marker': marker,
                                            'adapter': 'npm-lock-v2-v3-installed-v3'}
        if unsupported_marker:
            return 'report-only', 'install marker has no validated lock/install parser adapter', unsupported_marker
        return 'held', 'no tracked lockfile and matching install marker at this package root', {}
    return 'report-only', 'unrecognized build storage; report only', {}


def _scan(path, probes, budget):
    key = str(path)
    if key in budget.scans:
        return budget.scans[key]
    result = {'kb': 0, 'kb_unique': 0, 'newest': 0, 'complete': True, 'reasons': [], 'drive': str(Path(path).anchor)}
    started, entries = time.monotonic(), 0
    stack = [Path(path)]
    while stack:
        if entries >= budget.entry_limit or time.monotonic() - started >= budget.seconds_limit:
            result['complete'] = False
            result['reasons'].append('partial scan; size is a lower bound')
            break
        current = stack.pop()
        entries += 1
        try:
            info = os.lstat(current)
            result['drive'] = str(Path(path).anchor or info.st_dev)
            result['newest'] = max(result['newest'], info.st_mtime, info.st_ctime)
            if max(info.st_mtime, info.st_ctime) > probes.now:
                result['reasons'].append('file times are ahead of the clock')
            if probes.helpers.linked(current):
                result['reasons'].append('link or reparse point in cache')
                continue
            relative = current.relative_to(path)
            parts = (Path(path).name, *relative.parts[:-1])
            if current.name.casefold() == '.git':
                result['reasons'].append('contains a Git repository or worktree')
                continue
            if probes.helpers.secret_name(current.name) and not probes.helpers.package_tree(parts):
                result['reasons'].append('holds secret-looking files')
            if stat.S_ISDIR(info.st_mode):
                children = []
                with os.scandir(current) as iterator:
                    for child in iterator:
                        children.append(child)
                        if len(children) + entries > budget.entry_limit or time.monotonic() - started >= budget.seconds_limit:
                            break
                if len(children) + entries > budget.entry_limit or time.monotonic() - started >= budget.seconds_limit:
                    result['complete'] = False
                    result['reasons'].append('partial scan; size is a lower bound')
                    break
                folded = {child.name.casefold() for child in children}
                if {'head', 'objects', 'refs'} <= folded:
                    result['reasons'].append('contains a bare Git repository')
                if 'hold.json' in folded or 'keep.txt' in folded:
                    result['reasons'].append('contains HOLD.json or KEEP.txt protection')
                stack.extend(Path(child.path) for child in children)
            elif stat.S_ISREG(info.st_mode):
                size = (info.st_size + 1023)//1024
                result['kb'] += size
                inode = (info.st_dev, info.st_ino)
                if inode not in budget.inodes:
                    result['kb_unique'] += size
                    budget.inodes.add(inode)
            else:
                result['reasons'].append('unsupported special file in cache')
        except OSError:
            result['complete'] = False
            result['reasons'].append('part of the cache cannot be read')
    if result['complete'] and time.monotonic() - started >= budget.seconds_limit:
        result['complete'] = False
        result['reasons'].append('partial scan; size is a lower bound')
    result['reasons'] = list(dict.fromkeys(result['reasons']))
    budget.scans[key] = result
    return result


def _static_guards(spec, probes):
    path, root = Path(spec['path']), Path(spec['tree_path'])
    boundary = root
    if probes.workspace:
        # Caller authorization also covers the library's sibling -wt root.
        # Check protections through its common ancestry without treating that
        # ancestor as an allowed move root or crossing into another drive.
        try:
            boundary = Path(os.path.commonpath((str(root), str(probes.workspace))))
        except ValueError:
            boundary = Path(root.anchor)
    if not _under(path, root) or path == root or not probes.helpers.confined(path, root):
        return 'held', 'not a plain confined cache unit'
    report_file = spec['kind'] in ('cargo-target-other', 'build-other') and path.is_file()
    if not path.is_dir() and not report_file:
        return 'held', 'cache unit is not a directory'
    here = path.parent if report_file else path
    expired = False
    while True:
        if probes.helpers.linked(here):
            return 'held', 'link or reparse point on cache path'
        names = os.listdir(here)
        if any(n.casefold() == 'keep.txt' for n in names):
            # Parse as well, preserving the existing parser's fail-closed rules.
            probes.helpers.read_keep(here)
            return 'held', 'KEEP.txt protects cache or ancestor'
        if any(n.casefold() == 'hold.json' and n != 'HOLD.json' for n in names):
            return 'held', 'HOLD.json has ambiguous spelling'
        hold = probes.helpers.read_hold(here, datetime.datetime.fromtimestamp(probes.now, datetime.timezone.utc).date())
        if hold:
            if hold['state'] != 'expired':
                return 'held', 'HOLD.json protects cache or ancestor (held or malformed)'
            expired = True
        if here == boundary:
            break
        if here == here.parent:
            return 'held', 'cannot establish workspace ancestry'
        here = here.parent
    if expired:
        return 'report-only', 'expired HOLD.json requires review'
    relative = _fold(path.relative_to(root).as_posix())
    if any(_fold(name) == relative or _fold(name).startswith(relative + '/') for name in spec['tracked_names']):
        return 'held', 'holds tracked files'
    result = probes.git(root, 'check-ignore', '-q', '--', path.relative_to(root).as_posix())
    if result.returncode != 0:
        return 'held', 'cache unit is not proven ignored by Git'
    gitdir = Path(spec['gitdir'])
    for name in (*probes.helpers.GIT_BUSY, 'index.lock', 'HEAD.lock'):
        if os.path.lexists(gitdir/name):
            return 'held', 'Git operation in progress'
    return None, None


def _source_activity(spec, probes):
    """Bounded timestamp proof; ignored build trees and source contents stay unread."""
    root, owner = Path(spec['tree_path']), Path(spec['owner'])
    key = (str(root), str(owner))
    if key in probes.owner_activity:
        return probes.owner_activity[key]
    def finish(reason):
        probes.owner_activity[key] = reason
        return reason
    started = time.monotonic()
    if str(root) not in probes.source_names:
        result = probes.git(root, 'ls-files', '-z', '--others', '--exclude-standard')
        if result.returncode:
            return finish('cannot establish owner source activity; Git cannot list non-ignored source')
        probes.source_names[str(root)] = tuple(name for name in _text(result).split('\0') if name)
    checked, count = set(), 0
    for name in chain(spec['tracked_names'], probes.source_names[str(root)]):
        count += 1
        if count > probes.limits['entry_limit'] or time.monotonic() - started >= probes.limits['seconds_limit']:
            return finish('cannot establish owner source activity; inspection budget exceeded')
        path = root/name
        if Path(name).is_absolute() or '..' in Path(name).parts or not _under(path, root):
            return finish('cannot establish owner source activity; invalid Git source path')
        if not _under(path, owner):
            continue
        here = path
        while True:
            if str(here) not in checked:
                count += 1
                if count > probes.limits['entry_limit'] or time.monotonic() - started >= probes.limits['seconds_limit']:
                    return finish('cannot establish owner source activity; inspection budget exceeded')
                checked.add(str(here))
                try:
                    if probes.helpers.linked(here):
                        return finish('cannot establish owner source activity; link or reparse point')
                    info = os.lstat(here)
                    if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
                        return finish('cannot establish owner source activity; unsupported source file')
                    if probes.now - max(info.st_mtime, info.st_ctime) <= QUIET_SECONDS:
                        return finish('owner source activity is within the quiet window or ahead of the clock')
                except OSError:
                    return finish('cannot establish owner source activity; source path cannot be read')
            if here == owner:
                break
            if here == here.parent:
                return finish('cannot establish owner source ancestry')
            here = here.parent
    if time.monotonic() - started >= probes.limits['seconds_limit']:
        return finish('cannot establish owner source activity; inspection budget exceeded')
    return finish(None)


def evaluate(spec, profile, probes, budget=None):
    item = {**spec, 'status': 'held', 'reason': '', 'kb': 0, 'kb_unique': 0, 'complete': True,
            'drive': str(Path(spec['path']).anchor), 'age_days': 0, 'threshold_days': None,
            'profile': profile, 'profile_source': spec.get('profile_source', ''), 'tool_proof': {},
            'root': spec['owner']}
    # Git name snapshots are internal inspection state, not report payload.
    item.pop('tracked_names', None)
    def done(status, reason):
        item.update(status=status, reason=probes.helpers.clean(reason))
        return item
    rules = profile_rules(profile)
    if not rules:
        return done('held', 'artifact cache profile is disabled')
    if spec.get('discovery_error'):
        item['complete'] = False
        return done('held', spec['discovery_error'])
    if spec.get('report_reason'):
        return done('report-only', spec['report_reason'])
    try:
        item['identity'] = identity(spec['path'])
        status, reason = _static_guards(spec, probes)
        if status:
            return done(status, reason)
        status, reason, proof = _proof(spec, probes)
        item['tool_proof'] = proof
        seen = _scan(spec['path'], probes, budget or probes.new_budget())
        item.update({k: seen[k] for k in ('kb', 'kb_unique', 'complete', 'drive')})
        item['age_days'] = (probes.now - seen['newest']) / DAY
        if seen['reasons']:
            return done('held', '; '.join(seen['reasons']))
        if status:
            return done(status, reason)
        threshold = rules['dependency_days' if spec['kind'] == 'node-modules' else 'intermediate_days']
        item['threshold_days'] = threshold
        if threshold is None:
            return done('report-only', 'dependency caches are report-only in this profile')
        owner, gitdir = Path(spec['owner']), Path(spec['gitdir'])
        quiet_paths = [gitdir/'index', gitdir/'logs/HEAD', Path(spec['path']).parent]
        quiet_paths += [owner/name for name in ('Cargo.toml', 'Cargo.lock', 'package.json', 'package-lock.json', 'pnpm-lock.yaml', 'yarn.lock') if os.path.lexists(owner/name)]
        for path in quiet_paths:
            if probes.helpers.linked(path):
                return done('held', 'link or reparse point in owner activity proof')
            age = probes.now - path.stat().st_mtime
            if age <= QUIET_SECONDS:
                return done('held', 'owner activity is within the quiet window or ahead of the clock')
        if item['age_days'] <= threshold:
            return done('waiting', f'cache idle {item["age_days"]:.1f} of {threshold} days')
        source_activity = _source_activity(spec, probes)
        if source_activity:
            if source_activity.startswith('cannot establish'):
                item['complete'] = False
            return done('held', source_activity)
        # Cheap age/activity checks finish before recursive native usage probes.
        usage = probes.usage(str(spec['path']))
        if usage:
            return done('held', usage)
        paths = list(dict.fromkeys([spec['tree_path'], spec['owner']]))
        if proof.get('tool_lock') and os.path.lexists(proof['tool_lock']):
            paths.append(proof['tool_lock'])
        busy = probes.dirs_busy(paths)
        if busy is None:
            return done('held', 'cannot establish quiescence')
        if busy:
            return done('held', 'owner directory or tool lock is busy')
        if identity(spec['path']) != item['identity']:
            return done('held', 'cache identity changed during inspection')
        item.update(trash=[spec['path']], trash_kb=item['kb'])
        return done('eligible', 'quiet tool-proven rebuildable cache')
    except (OSError, ValueError, TypeError, RecursionError) as error:
        return done('held', f'cannot establish cache safety ({type(error).__name__})')


def recheck(spec, item, profile, probes):
    # Deliberately bypass report memoization: a fresh Git snapshot and full scan
    # are needed immediately before the caller's recoverable move.
    fresh_spec = dict(spec)
    probes.owner_activity.pop((str(spec['tree_path']), str(spec['owner'])), None)
    probes.source_names.pop(str(spec['tree_path']), None)
    try:
        fresh_spec['tracked_names'] = _git_names(spec['tree_path'], probes)
        if probes.helpers.canon(spec['path']) != spec['path'] or identity(spec['path']) != item.get('identity'):
            return {**item, 'status': 'skipped', 'reason': 'cache path or identity changed since report'}
    except (OSError, ValueError):
        return {**item, 'status': 'skipped', 'reason': 'cannot recheck cache identity or tracked files'}
    fresh = evaluate(fresh_spec, profile, probes, probes.new_budget())
    if fresh['status'] != 'eligible':
        fresh['status'] = 'skipped'
    return fresh


def _size_kb(text):
    match = re.fullmatch(r'\s*([0-9]+(?:\.[0-9]+)?)\s*(B|kB|KB|KiB|MB|MiB|GB|GiB|TB|TiB)\s*', str(text))
    if not match:
        return 0
    units = {'B': 1, 'kB': 1000, 'KB': 1000, 'KiB': 1024, 'MB': 1000**2, 'MiB': 1024**2,
             'GB': 1000**3, 'GiB': 1024**3, 'TB': 1000**4, 'TiB': 1024**4}
    return int(float(match[1]) * units[match[2]] / 1024)


def build_storage(probes, home, env):
    docker = {'state': 'unknown', 'reason': '', 'build_cache': {'status': 'report-only', 'kb': 0},
              'images': {'status': 'report-only', 'kb': 0},
              'volumes': {'status': 'held', 'reason': 'data volumes are never cleaned', 'kb': 0}}
    outputs = []
    for command in DOCKER_COMMANDS:
        try:
            result = probes.run_readonly(list(command), 15)
        except FileNotFoundError:
            docker.update(state='absent', reason='Docker executable unavailable')
            break
        except (OSError, subprocess.SubprocessError):
            docker.update(state='unknown', reason='Docker read-only probe unavailable or timed out')
            break
        if result.returncode:
            # Do not echo daemon errors that may contain private paths/URLs.
            docker.update(state='unknown', reason='Docker engine or build-cache probe unavailable')
            break
        outputs.append(_text(result))
    else:
        docker.update(state='ok', reason='read-only Docker storage probes completed')
        try:
            json.loads(outputs[0])
            for line in outputs[1].splitlines():
                row = json.loads(line)
                kind = {'Images': 'images', 'Build Cache': 'build_cache', 'Local Volumes': 'volumes'}.get(row.get('Type'))
                if kind:
                    docker[kind]['kb'] += _size_kb(row.get('Size', ''))
        except (ValueError, TypeError, AttributeError, RecursionError):
            docker.update(state='unknown', reason='unrecognized Docker storage response')
    home = Path(home)
    paths = [home/'.cargo/registry', home/'.cargo/git', home/'.npm', home/'.cache/pnpm', home/'.local/share/pnpm/store',
             home/'.cache/yarn', home/'.cache/pip']
    if env.get('LOCALAPPDATA'):
        local = Path(env['LOCALAPPDATA'])
        paths += [local/'npm-cache', local/'pnpm/store', local/'Yarn/Cache', local/'pip/Cache']
    shared, drives, budget = [], {}, probes.new_budget()
    for path in dict.fromkeys(paths):
        if not os.path.lexists(path):
            continue
        seen = _scan(path, probes, budget)
        entry = {'path': str(path), 'drive': seen['drive'], 'kb': seen['kb'], 'kb_unique': seen['kb_unique'],
                 'complete': seen['complete'], 'status': 'report-only', 'reason': '; '.join(seen['reasons']) or 'shared cache; report only'}
        shared.append(entry)
        totals = drives.setdefault(entry['drive'], {'eligible_kb': 0, 'held_kb': 0, 'report_only_kb': 0})
        totals['report_only_kb'] += entry['kb_unique']
    return {'docker': docker, 'shared_caches': shared, 'drives': drives}
