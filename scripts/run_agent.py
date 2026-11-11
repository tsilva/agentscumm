"""Portable agent identity captured by the clean pre-play gate."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

SEMVER = re.compile(r'(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)')


def validate(identity):
    if not isinstance(identity, dict) or identity.get('name') != 'agentscumm':
        raise ValueError('Invalid AgentSCUMM identity')
    unknown = (identity.get('source') == 'historical_start_unknown' and identity.get('version') is None
               and identity.get('commit_id') is None and identity.get('dirty') is None
               and bool(identity.get('resume_checkpoints')))
    if not unknown and (not isinstance(identity.get('version'), str) or not SEMVER.fullmatch(identity['version'])
            or not isinstance(identity.get('commit_id'), str) or not re.fullmatch('[a-f0-9]{40}', identity['commit_id'])
            or identity.get('dirty') is not False or identity.get('source') != 'clean_play_preflight'):
        raise ValueError('AgentSCUMM identity requires a clean versioned starting commit')
    checkpoints = identity.get('resume_checkpoints', [])
    if not isinstance(checkpoints, list):
        raise ValueError('Invalid agent resume checkpoints')
    for checkpoint in checkpoints:
        validate(checkpoint)
    return identity


def snapshot(repo, work, destination, game=None, run_id=None):
    work = Path(os.path.abspath(work)) if work else None
    manifest = work / 'run.json' if work else None
    if manifest is None or not manifest.exists():
        return None  # Historical exports cannot borrow today's package version.
    if any(path.is_symlink() for path in [manifest, *manifest.parents]) or not manifest.is_relative_to(Path(repo).resolve() / '.internal'):
        raise ValueError('Agent provenance must come from the owning working run')
    working = json.loads(manifest.read_text())
    identity = working.get('agent')
    if identity is None and working.get('agent_resume_checkpoints'):
        identity = {'name': 'agentscumm', 'version': None, 'commit_id': None, 'dirty': None,
                    'source': 'historical_start_unknown', 'resume_checkpoints': working['agent_resume_checkpoints']}
    if identity is None:
        return None
    if game and (working.get('game_id') != game or working.get('run_id') != Path(work).name):
        raise ValueError('Agent provenance belongs to a different working run')
    validate(identity)
    if (working.get('kind') != 'working_run' or
            (working.get('git', {}).get('play_start') or {}).get('commit_id') != identity['commit_id']):
        raise ValueError('Agent identity differs from the working run baseline')
    for revision in [identity, *identity.get('resume_checkpoints', [])]:
        if revision.get('commit_id') is None:
            continue
        package = json.loads(subprocess.check_output(['git', '-C', str(repo), 'show',
            revision['commit_id'] + ':package.json'], stderr=subprocess.DEVNULL))
        if package.get('name') != 'agentscumm' or package.get('version') != revision['version']:
            raise ValueError('Agent version differs from its committed package')
    data = (json.dumps(identity, sort_keys=True, indent=2) + '\n').encode()
    with (Path(destination) / 'agent-provenance.json').open('xb') as stream:
        stream.write(data)
    return {**identity, 'evidence': {'path': 'agent-provenance.json', 'sha256': hashlib.sha256(data).hexdigest()}}


def verify(directory, identity):
    if identity is None:
        return
    validate(identity)
    evidence = identity.get('evidence') or {}
    path = Path(directory) / 'agent-provenance.json'
    if evidence.get('path') != path.name or path.is_symlink():
        raise ValueError('Invalid agent provenance path')
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != evidence.get('sha256') or json.loads(data) != {
            key: value for key, value in identity.items() if key != 'evidence'}:
        raise ValueError('Agent provenance checksum or identity mismatch')
