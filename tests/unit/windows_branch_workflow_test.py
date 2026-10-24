"""Contract checks for the isolated nonpublishing development build route."""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[2]


class BranchWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.source = (ROOT / '.github/workflows/windows-branch-validation.yml').read_text()

    def test_exact_branch_and_repository_only_no_privileged_triggers(self):
        self.assertIn('branches: [codex/esr-153-5-1-security]', self.source)
        for job in ('quality', 'installer-safety', 'build'):
            body = self.source.split('  ' + job + ':\n', 1)[1].split('    runs-on:', 1)[0]
            self.assertIn("github.repository == 'crevice0308/PrivacyBrowser'", body)
            self.assertIn("github.event_name == 'push'", body)
            self.assertIn("github.ref == 'refs/heads/codex/esr-153-5-1-security'", body)
        for forbidden in ('pull_request_target:', 'workflow_dispatch:', 'workflow_run:', 'contents: write',
                          'secrets.', 'gh release', 'git push', 'enable-auto-merge'):
            self.assertNotIn(forbidden, self.source)
        self.assertIn('permissions:\n  contents: read', self.source)
        self.assertIn('group: privacybrowser-native-windows\n  cancel-in-progress: false', self.source)

    def test_quality_and_safety_precede_full_native_checks(self):
        self.assertIn('os: [ubuntu-24.04, windows-2025]', self.source)
        self.assertIn("python -m unittest discover -s tests/unit -p '*_test.py' -v", self.source)
        self.assertIn('node --test tests/unit/*.test.mjs', self.source)
        self.assertIn('python tools/check_source.py', self.source)
        self.assertIn('ParseFile', self.source)
        self.assertIn('  installer-safety:\n    needs: quality', self.source)
        self.assertIn('  build:\n    needs: [quality, installer-safety]', self.source)
        self.assertIn('run: python tools/run_windows_installer_safety.py', self.source)
        self.assertIn('run: ./tools/build_windows_native.ps1', self.source)
        self.assertIn('PB_QUALITY_RUN_ID: ${{ github.run_id }}', self.source)
        self.assertEqual(self.source.count('PB_EXPECTED_COMMIT: ${{ github.sha }}'), 2)
        self.assertIn('timeout-minutes: 360', self.source)
        self.assertIn('timeout-minutes: 15', self.source)

    def test_exact_checkouts_pinned_actions_and_success_only_candidate(self):
        self.assertEqual(self.source.count('ref: ${{ github.sha }}'), 3)
        self.assertEqual(self.source.count('persist-credentials: false'), 3)
        actions = re.findall(r'uses: (\S+)', self.source)
        self.assertTrue(actions)
        self.assertTrue(all(re.fullmatch(r'actions/[a-z-]+@[a-f0-9]{40}', action) for action in actions))
        candidate = self.source.split('name: Collect only the successfully verified unpublished candidate', 1)[1]
        self.assertIn('if: success()', candidate)
        self.assertIn('path: D:/pb/artifact/', candidate)
        self.assertIn('if-no-files-found: error', candidate)
        self.assertIn('UNSIGNED-UNPUBLISHED-', candidate)
        self.assertIn('path: D:/pb/evidence/', self.source)


if __name__ == '__main__':
    unittest.main()
