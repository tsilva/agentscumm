"""Synthetic clean-commit versioning and portable identity checks."""
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from test_export_run import EXPORT, fixture, rates_fixture

ROOT = Path(__file__).resolve().parents[1]

def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

PREPARE = load('prepare_play', ROOT / 'scripts/prepare-play.py')
AGENT = load('run_agent', ROOT / 'scripts/run_agent.py')

class PlayVersionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='agentscumm-version-')
        self.addCleanup(self.temporary.cleanup)
        self.repo = Path(self.temporary.name).resolve()
        self.git('init', '-q')
        (self.repo / 'package.json').write_text(json.dumps({'name': 'agentscumm', 'version': '1.2.3'}, indent=2)+'\n')
        (self.repo / '.gitignore').write_text('/.internal/\n')
        (self.repo / 'strategy.md').write_text('Synthetic strategy')
        self.commit('Initial version')
        PREPARE.prepare(self.repo)
        self.commit('Record version baseline')

    def git(self, *args):
        return subprocess.check_output(['git', '-C', str(self.repo), *args], stderr=subprocess.DEVNULL).decode().strip()

    def commit(self, message):
        self.git('add', '-A')
        self.git('-c', 'user.name=Synthetic', '-c', 'user.email=synthetic@example.invalid',
                 '-c', 'commit.gpgsign=false', 'commit', '-qm', message)

    def test_changes_bump_once_and_play_requires_the_bump_to_be_committed(self):
        path = self.repo / 'strategy.md'
        path.write_text('New strategy')
        first = PREPARE.prepare(self.repo)
        self.assertEqual(first['version'], '1.2.4')
        self.assertFalse(first['ready'])
        path.write_text('Revised new strategy')
        self.assertEqual(PREPARE.prepare(self.repo)['version'], '1.2.4')
        self.git('add', '-A')
        self.assertFalse(PREPARE.prepare(self.repo)['ready'])
        self.commit('fix: improve strategy')
        clean = PREPARE.prepare(self.repo)
        self.assertTrue(clean['ready'])
        self.assertEqual(clean['version'], '1.2.4')
        self.assertFalse(self.git('status', '--porcelain'))

    def test_committed_features_and_breaking_changes_bump_minor_and_major(self):
        (self.repo / 'strategy.md').write_text('Feature')
        self.commit('feat: new strategy')
        self.assertEqual(PREPARE.prepare(self.repo)['version'], '1.3.0')
        self.commit('Version feature')
        (self.repo / 'strategy.md').unlink()
        self.commit('feat!: replace strategy contract')
        self.assertEqual(PREPARE.prepare(self.repo)['version'], '2.0.0')

    def test_executable_mode_changes_are_versioned(self):
        (self.repo / 'strategy.md').chmod(0o755)
        self.assertEqual(PREPARE.prepare(self.repo)['version'], '1.2.4')

    def test_gate_writes_exact_agent_version_and_resume_preserves_start(self):
        ready = PREPARE.prepare(self.repo)
        work = self.repo / '.internal/synthetic-game/run-one'
        start = PREPARE.initialize(self.repo, ready, work, 'synthetic-game', 'run-one')
        (self.repo / 'strategy.md').write_text('Changed')
        PREPARE.prepare(self.repo)
        self.commit('fix: updated agent')
        resumed = PREPARE.initialize(self.repo, PREPARE.prepare(self.repo), work, 'synthetic-game', 'run-one', True)
        self.assertEqual(resumed['agent']['version'], '1.2.3')
        self.assertEqual(resumed['agent']['resume_checkpoints'][0]['version'], '1.2.4')
        self.assertEqual(start['git']['play_start'], resumed['git']['play_start'])
        destination = self.repo / '.internal/archive'
        destination.mkdir()
        identity = AGENT.snapshot(self.repo, work, destination, 'synthetic-game', 'run-one')
        AGENT.verify(destination, identity)
        identity['version'] = '9.9.9'
        with self.assertRaisesRegex(ValueError, 'checksum or identity'):
            AGENT.verify(destination, identity)

    def test_dirty_checkout_cannot_create_working_run_and_unknown_stays_unknown(self):
        (self.repo / 'untracked.txt').write_text('Change')
        work = self.repo / '.internal/synthetic-game/run-one'
        with self.assertRaises(ValueError):
            PREPARE.initialize(self.repo, PREPARE.prepare(self.repo), work, 'synthetic-game', 'run-one')
        self.assertFalse(work.exists())
        self.assertIsNone(AGENT.snapshot(self.repo, work, self.repo))

    def test_export_carries_starting_identity_when_archive_and_work_ids_differ(self):
        work = self.repo / '.internal/synthetic-game/work-one'
        PREPARE.initialize(self.repo, PREPARE.prepare(self.repo), work, 'synthetic-game', 'work-one')
        (self.repo / '.gitignore').write_text('/.internal/\n/runs/\n')
        # The starting identity stays tied to its original commit after export edits.
        source = work / 'rollout.jsonl'
        source.write_text(''.join(json.dumps(row) + '\n' for row in fixture()))
        with patch.object(EXPORT.PRICING, 'fetch_rates', return_value=rates_fixture()):
            exported, _ = EXPORT.export_run(source, 'synthetic-game', repo=self.repo,
                                         run_id='different-archive-id', work_dir=work)
        stats = EXPORT.verify_run(exported)
        self.assertEqual(stats['agent']['version'], '1.2.3')
        self.assertEqual(stats['agent']['commit_id'], json.loads((work / 'run.json').read_text())['agent']['commit_id'])

if __name__ == '__main__':
    unittest.main()
