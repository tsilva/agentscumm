#!/usr/bin/env python3
"""Version changed AgentSCUMM content, then enforce the clean-commit play gate."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent.parent
STATE = '.agentscumm-version.json'
SEMVER = re.compile(r'(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)')


def git(repo, *args):
    return subprocess.check_output(['git', '-C', str(repo), *args], stderr=subprocess.DEVNULL).decode().strip()


def fingerprint(repo, ref=None):
    names = subprocess.check_output(['git', '-C', str(repo), *(['ls-tree', '-r', '--name-only', '-z', ref] if ref else
        ['ls-files', '-z', '--cached', '--others', '--exclude-standard'])]).decode().split('\0')
    digest = hashlib.sha256()
    modes = {}
    if ref:
        tree = subprocess.check_output(['git', '-C', str(repo), 'ls-tree', '-r', '-z', ref]).decode()
        modes = {entry.split('\t', 1)[1]: entry.split(' ', 1)[0] == '100755'
                 for entry in tree.split('\0') if entry}
    for name in sorted(set(names) - {'', STATE}):
        path = repo / name
        if ref:
            data = subprocess.check_output(['git', '-C', str(repo), 'show', f'{ref}:{name}'])
        elif not path.exists() and not path.is_symlink():
            continue
        elif path.is_symlink():
            raise ValueError('Versioned inputs must not be symlinks')
        else:
            data = path.read_bytes()
        if name == 'package.json':
            package = json.loads(data)
            package.pop('version', None)
            data = json.dumps(package, sort_keys=True, separators=(',', ':')).encode()
        executable = modes.get(name, False) if ref else bool(path.stat().st_mode & 0o111)
        digest.update(name.encode() + bytes([executable]) + b'\0' + hashlib.sha256(data).digest())
    return digest.hexdigest()


def prepare(repo=ROOT):
    repo = Path(repo).resolve()
    if Path(git(repo, 'rev-parse', '--show-toplevel')).resolve() != repo:
        raise ValueError('Select the owning AgentSCUMM Git root')
    package_path, state_path = repo / 'package.json', repo / STATE
    if package_path.is_symlink() or state_path.is_symlink():
        raise ValueError('Linked version files are not allowed')
    package = json.loads(package_path.read_text())
    version = package['version']
    if package.get('name') != 'agentscumm' or not SEMVER.fullmatch(version):
        raise ValueError('AgentSCUMM needs a stable major.minor.patch package version')
    head, content = git(repo, 'rev-parse', '--verify', 'HEAD'), fingerprint(repo)
    state = json.loads(state_path.read_text()) if state_path.exists() else None
    if state and (state.get('schema_version') != 1 or not SEMVER.fullmatch(state.get('version', ''))
                  or not re.fullmatch('[a-f0-9]{64}', state.get('fingerprint', ''))
                  or not re.fullmatch('[a-f0-9]{40}', state.get('base_commit', ''))
                  or not SEMVER.fullmatch(state.get('base_version', ''))
                  or type(state.get('level')) is not int or state['level'] not in (0, 1, 2)):
        raise ValueError('Invalid version baseline')
    if state and state['version'] != version:
        raise ValueError('Package version differs from its baseline; use the automatic version helper')
    changed = state is None or content != state['fingerprint']
    if changed:
        # A pending bump belongs to this HEAD until committed. Further edits
        # refresh its fingerprint without creating an endless bump/commit loop.
        pending = bool(state and state['base_commit'] == head)
        baseline = state['base_commit'] if state else git(repo, 'log', '-1', '--format=%H', '-G', '^  "version":', '--', 'package.json')
        baseline = baseline or head
        messages = git(repo, 'log', '--format=%B', baseline + '..HEAD')
        level = 2 if re.search(r'(?m)^\w+(?:\([^\n]*\))?!:|^BREAKING[ -]CHANGE:', messages) else (
            1 if re.search(r'(?m)^feat(?:\([^\n]*\))?:', messages) else 0)
        parts = list(map(int, (state['base_version'] if pending else version).split('.')))
        if pending:
            level = max(level, state['level'])
        # First use compares against the last package-version change, including
        # already committed but unversioned changes. No changes need no bump.
        bump = pending or state is not None or content != fingerprint(repo, baseline)
        if bump:
            index = 2 - level
            parts[index] += 1
            for position in range(index + 1, 3):
                parts[position] = 0
            package['version'] = '.'.join(map(str, parts))
        baseline_version = state['base_version'] if pending else version
        state = {'schema_version': 1, 'version': package['version'], 'fingerprint': content,
                 'base_commit': head, 'base_version': baseline_version, 'level': level}
        package_path.write_text(json.dumps(package, indent=2) + '\n')
        state_path.write_text(json.dumps(state, indent=2) + '\n')
    dirty = git(repo, 'status', '--porcelain=v1', '--untracked-files=all')
    result = {'name': 'agentscumm', 'version': package['version'], 'commit_id': head,
              'branch': git(repo, 'branch', '--show-current'), 'dirty': bool(dirty),
              'source': 'clean_play_preflight', 'ready': not dirty}
    if dirty:
        result['blocked_paths'] = dirty.splitlines()
    return result


def initialize(repo, result, work, game, run_id, resume=False):
    repo, work = Path(repo).resolve(), Path(work).absolute()
    if not result['ready'] or work != repo / '.internal' / game / run_id:
        raise ValueError('A clean checkout and exact .internal/game/run directory are required')
    if not re.fullmatch('[a-z0-9]+(?:-[a-z0-9]+)*', game) or not re.fullmatch('[A-Za-z0-9][A-Za-z0-9._-]{0,99}', run_id):
        raise ValueError('Invalid game or run ID')
    if git(repo, 'ls-files', '--', '.internal') or not git(repo, 'check-ignore', str(work / 'run.json')):
        raise ValueError('Working runs must be ignored and untracked')
    for parent in [work, *work.parents]:
        if parent.is_symlink():
            raise ValueError('Linked working directory')
    path = work / 'run.json'
    if path.is_symlink():
        raise ValueError('Linked working manifest')
    identity = {key: result[key] for key in ('name', 'version', 'commit_id', 'branch', 'dirty', 'source')}
    baseline = {key: identity[key] for key in ('commit_id', 'branch', 'dirty', 'source')}
    when = datetime.now(timezone.utc).isoformat()
    if resume:
        data = json.loads(path.read_text())
        if data.get('kind') != 'working_run' or data.get('game_id') != game or data.get('run_id') != run_id:
            raise ValueError('Working run identity mismatch')
        data.setdefault('git', {}).setdefault('play_start', None)
        data['git'].setdefault('resume_checkpoints', []).append({'recorded_at': when, **baseline})
        if data.get('agent'):
            data['agent'].setdefault('resume_checkpoints', []).append({'recorded_at': when, **identity})
        else:
            data.setdefault('agent_resume_checkpoints', []).append({'recorded_at': when, **identity})
    else:
        if path.exists():
            raise ValueError('Run already exists; use --resume')
        data = {'schema_version': 1, 'kind': 'working_run', 'status': 'in_progress', 'game_id': game,
                'run_id': run_id, 'started_at': when, 'git': {'play_start': baseline}, 'agent': identity}
    if git(repo, 'rev-parse', 'HEAD') != result['commit_id'] or git(repo, 'status', '--porcelain=v1', '--untracked-files=all'):
        raise ValueError('Checkout changed before initializing run')
    work.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary = tempfile.mkstemp(prefix='.prepare-play-', dir=work)
    scratch = Path(temporary)
    try:
        with os.fdopen(descriptor, 'w') as stream:
            stream.write(json.dumps(data, indent=2) + '\n')
        scratch.replace(path)
    finally:
        if scratch.exists():
            scratch.unlink()
    return data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--work-dir', type=Path)
    parser.add_argument('--game')
    parser.add_argument('--run-id')
    parser.add_argument('--resume', action='store_true')
    args = parser.parse_args()
    if any((args.work_dir, args.game, args.run_id, args.resume)) and not all((args.work_dir, args.game, args.run_id)):
        parser.error('--work-dir, --game and --run-id are required together')
    try:
        result = prepare()
        if result['ready'] and args.work_dir:
            initialize(ROOT, result, args.work_dir, args.game, args.run_id, args.resume)
        print(json.dumps(result, indent=2))
        raise SystemExit(0 if result['ready'] else 2)
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        parser.exit(1, f'Play preparation failed: {error}\n')


if __name__ == '__main__':
    main()
