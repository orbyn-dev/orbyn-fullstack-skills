"""Read-only disk observations and conservative logical cleanup estimates."""
import datetime
import math
import ntpath
import os
from pathlib import Path
import shutil

GIB = 1024 ** 3
MAX_GIB = 1024 ** 2
BIN_REASON_CODES = frozenset(('ok', 'volume_unknown', 'unsupported_volume', 'recycle_bin_unavailable',
                              'invalid_bin_response', 'api_unavailable', 'invalid_path', 'access_denied',
                              'missing', 'os_error'))


def normalized_drive(value):
    """Canonical drive identity; unknown is None, not a guessed volume."""
    if not isinstance(value, str) or not value:
        return None
    drive, _ = ntpath.splitdrive(value)
    if len(drive) == 2 and drive[0].isalpha() and drive[1] == ':':
        return drive.lower()
    if value == '/' or (value.startswith('/') and not value.startswith('//')):
        return '/'
    return None


def validate_goal(value):
    """Return a bounded diagnostic goal in GiB; it never grants action authority."""
    if isinstance(value, bool):
        raise ValueError('reclaim goal must be a positive finite number of GiB, at most 1048576')
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError('reclaim goal must be a positive finite number of GiB, at most 1048576') from error
    if not math.isfinite(result) or not 0 < result <= MAX_GIB:
        raise ValueError('reclaim goal must be a positive finite number of GiB, at most 1048576')
    return result


def _goal(value):
    return None if value is None else {'requested_gib': validate_goal(value),
                                       'target_bytes': int(validate_goal(value) * GIB),
                                       'status': 'not_guaranteed'}


def _amount(value):
    return value if type(value) is int and value >= 0 else None


def snapshot(path, bin_info, *, disk_usage=None, clock=None):
    """Measure a volume and its aggregate bin without listing or changing contents."""
    original = os.fspath(path)
    path = os.path.abspath(original)
    drive = normalized_drive(original) or normalized_drive(path)
    now = (clock or (lambda: datetime.datetime.now(datetime.timezone.utc)))()
    if now.tzinfo is None:
        now = now.replace(tzinfo=datetime.timezone.utc)
    result = {'state': 'unknown', 'path': path, 'drive': drive,
              'total_bytes': None, 'used_bytes': None, 'free_bytes': None,
              'measured_at_utc': now.astimezone(datetime.timezone.utc).isoformat(),
              'recycle_bin': {'state': 'unknown', 'volume': None, 'reported_bytes': None, 'items': None, 'reason_code': None}}
    try:
        values = (disk_usage or shutil.disk_usage)(path)
        total, used, free = (_amount(v) for v in values)
        if None not in (total, used, free) and used + free <= total:
            result.update(state='ok', total_bytes=total, used_bytes=used, free_bytes=free)
    except (OSError, ValueError, TypeError):
        pass  # Unknown capacity never becomes zero or authorizes an action.
    try:
        data = bin_info(path)
        state = data.get('state') if isinstance(data, dict) else None
        reason = data.get('reason_code') if isinstance(data, dict) else None
        reason = reason if isinstance(reason, str) and reason in BIN_REASON_CODES else None
        if (state == 'ok' and isinstance(data.get('volume'), str) and data['volume']
                and _amount(data.get('reported_bytes')) is not None and _amount(data.get('items')) is not None):
            result['recycle_bin'] = {'state': 'ok', 'volume': data['volume'],
                                     'reported_bytes': data['reported_bytes'], 'items': data['items'],
                                     'reason_code': reason}
        elif state in ('unsupported', 'unknown'):
            result['recycle_bin'].update(state=state, reason_code=reason)
    except (OSError, ValueError, TypeError):
        pass  # The caller converts platform-specific exceptions to unknown.
    return result


def _canonical(path):
    if not isinstance(path, str) or not path:
        return None
    if normalized_drive(path) not in (None, '/'):
        return ntpath.normcase(ntpath.normpath(path))
    return os.path.normcase(os.path.abspath(path))


def _under(path, root):
    try:
        module = ntpath if normalized_drive(root) not in (None, '/') else os.path
        return module.commonpath((path, root)) == root
    except ValueError:
        return False


def _selected(items, *, results=False):
    candidates, excluded = [], []
    coverage = {'measured': 0, 'unmeasured': 0, 'incomplete': 0}
    for item in items:
        if (item.get('outcome') != 'trashed') if results else (item.get('status') != 'eligible'):
            continue
        path = _canonical(item.get('path'))
        amount = _amount(item.get('kb') if results else item.get('trash_kb', item.get('kb')))
        if item.get('complete') is False:
            coverage['incomplete'] += 1
            excluded.append({'path': item.get('path'), 'reason': 'incomplete size'})
        elif path is None or amount is None:
            coverage['unmeasured'] += 1
            excluded.append({'path': item.get('path'), 'reason': 'missing path or size'})
        else:
            candidates.append((path, item, amount * 1024))
    candidates.sort(key=lambda entry: (entry[0].replace('\\', '/').count('/'), entry[0]))
    selected = []
    for path, item, size in candidates:
        if any(_under(path, prior[0]) for prior in selected):
            excluded.append({'path': item['path'], 'reason': 'overlap'})
        else:
            selected.append((path, item, size))
            coverage['measured'] += 1
    return selected, excluded, coverage


def plan(items, snapshot, goal_gib=None):
    """Describe eligible logical payload, explicitly excluding overlapping paths."""
    selected, excluded, coverage = _selected(items)
    drives, held = {}, {}
    for _, item, size in selected:
        drive = normalized_drive(item.get('drive')) or 'unknown'
        entry = drives.setdefault(drive, {'eligible_logical_estimate_bytes': 0, 'count': 0})
        entry['eligible_logical_estimate_bytes'] += size
        entry['count'] += 1
    for item in items:
        if item.get('status') not in ('eligible', None):
            reason = item.get('reason') if isinstance(item.get('reason'), str) and item['reason'] else 'unspecified'
            held[reason] = held.get(reason, 0) + 1
    snapshot_drive = normalized_drive(snapshot.get('drive'))
    own_bytes = drives.get(snapshot_drive, {}).get('eligible_logical_estimate_bytes', 0) if snapshot_drive else None
    other_bytes = sum(entry['eligible_logical_estimate_bytes'] for drive, entry in drives.items()
                      if snapshot_drive and drive not in (snapshot_drive, 'unknown'))
    return {'eligible_logical_estimate_bytes': sum(size for _, _, size in selected),
            'snapshot_drive_eligible_logical_estimate_bytes': own_bytes,
            'other_volume_eligible_logical_estimate_bytes': other_bytes if snapshot_drive else None,
            'held_by_reason': held, 'size_coverage': coverage,
            'selected_estimate_paths': [item['path'] for _, item, _ in selected],
            'excluded_paths': excluded, 'overlap_count': sum(e['reason'] == 'overlap' for e in excluded),
            'drive_breakdown': drives, 'snapshot_drive': snapshot_drive,
            'guaranteed_immediate_reclaim_bytes': 0, 'disposal': 'system-trash',
            'logical_is_physical_reclaim': False, 'goal': _goal(goal_gib)}


def change(before, after, results, goal_gib=None):
    """Report observed free-space movement separately from Trash payload."""
    selected, excluded, coverage = _selected(results, results=True)
    first, last = _amount(before.get('free_bytes')), _amount(after.get('free_bytes'))
    first_drive, last_drive = normalized_drive(before.get('drive')), normalized_drive(after.get('drive'))
    same_drive = first_drive is not None and first_drive == last_drive
    delta = last - first if first is not None and last is not None and same_drive else None
    return {'before': before, 'after': after, 'moved_logical_estimate_bytes': sum(size for _, _, size in selected),
            'selected_estimate_paths': [item['path'] for _, item, _ in selected], 'excluded_paths': excluded,
            'size_coverage': coverage, 'observed_free_space_change_bytes': delta,
            'measurement_not_attribution': True, 'guaranteed_immediate_reclaim_bytes': 0,
            'disposal': 'system-trash', 'goal': _goal(goal_gib)}
