import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


@unittest.skipUnless(shutil.which('bash'),
     'NAS deployment script requires Bash')
class SynologyDeployment(unittest.TestCase):
    """Exercise deployment failure boundaries without modifying real containers."""

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        (self.root / 'scripts').mkdir()
        source = Path(__file__).resolve().parents[3]
        shutil.copyfile(source / 'scripts/synology-update.sh',
                        self.root / 'scripts/synology-update.sh')
        (self.root / '.env.synology').write_text('PUID=1026\nPGID=100\n')
        self.calls = self.root / 'calls'
        docker = self.root / 'docker'
        docker.write_text('''#!/usr/bin/env bash
printf '%s\n' "$*" >> "$CALLS"
case "$*" in
  'compose version') exit 0 ;;
  'info') exit 0 ;;
  *' build kapowarr') exit "${BUILD_EXIT:-0}" ;;
  *' run --rm '*) exit "${BACKUP_EXIT:-0}" ;;
  *' ps -q kapowarr') echo fixture-container ;;
  inspect*) echo "${HEALTH:-healthy}" ;;
esac
''')
        docker.chmod(0o755)

    def run_script(self, mode, **settings):
        result = subprocess.run(
            ['bash', str(self.root / 'scripts/synology-update.sh'), mode],
            env={**os.environ, 'PATH': f'{self.root}:{os.environ["PATH"]}',
                 'CALLS': str(self.calls), **settings},
            text=True, capture_output=True, timeout=10
        )
        calls = self.calls.read_text() if self.calls.exists() else ''
        return result, calls

    def test_check_does_not_build_or_stop(self):
        result, calls = self.run_script('--check')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn(' build ', calls)
        self.assertNotIn(' stop ', calls)
        self.assertNotIn(' up -d ', calls)

    def test_build_failure_keeps_existing_container(self):
        result, calls = self.run_script('--start', BUILD_EXIT='1')
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn(' stop ', calls)
        self.assertNotIn(' up -d ', calls)

    def test_backup_failure_restarts_existing_container(self):
        result, calls = self.run_script('--start', BACKUP_EXIT='1')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(' start kapowarr', calls)
        self.assertNotIn(' up -d ', calls)

    def test_success_builds_then_stops_backs_up_and_starts(self):
        result, calls = self.run_script('--start')
        self.assertEqual(result.returncode, 0, result.stderr)
        positions = [calls.index(step) for step in (
            ' build kapowarr', ' stop kapowarr', ' run --rm ',
            ' up -d --no-build kapowarr', 'inspect --format'
        )]
        self.assertEqual(positions, sorted(positions))
        self.assertNotIn(' push ', calls)

    def test_health_failure_is_reported(self):
        result, calls = self.run_script('--start', HEALTH='unhealthy')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(' logs --tail 80 kapowarr', calls)

    def test_missing_configuration_exits_before_docker(self):
        (self.root / '.env.synology').unlink()
        result, calls = self.run_script('--start')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(calls, '')
