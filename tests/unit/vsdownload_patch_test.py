"""Inert failure transport tests; no network, SDK, compiler or installer runs."""
import hashlib
import json
from pathlib import Path
import pickle
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import urllib.error

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
import patch_vsdownload as patch

# Self-authored worker fixture, not a vendored upstream downloader. The small
# exception-handler anchor is the only shared control flow needed for the fix.
FIXTURE = b'''import urllib.error

def worker(kind):
    attempts = 5
    for attempt in range(attempts):
        try:
            if kind == "short":
                raise urllib.error.ContentTooShortError("inert truncated payload", ("fixture", {}))
            if kind == "other":
                raise ValueError("inert ordinary failure")
            return attempt
''' + patch.BEFORE


class VsDownloadPatchTests(unittest.TestCase):
    def fixture_pins(self, data=FIXTURE):
        return mock.patch.multiple(
            patch, BEFORE_SHA256=hashlib.sha256(data).hexdigest(),
            AFTER_SHA256=hashlib.sha256(data.replace(patch.BEFORE, patch.AFTER, 1)).hexdigest())

    def test_real_reviewed_pins_are_explicit_and_distinct(self):
        self.assertEqual(patch.BEFORE_SHA256,
                         '03b990385482f1dcffd6625028b1357a8f420ad19664ded0b038a5b0041f09e9')
        self.assertEqual(patch.AFTER_SHA256,
                         'af337035cc729acdce158fba7845584413dda1033ba89a3c66a91c7e1c5f1741')

    def test_unreviewed_bytes_and_wrong_result_are_rejected(self):
        with self.assertRaisesRegex(ValueError, 'reviewed ESR'):
            patch.patched_source(FIXTURE)
        with self.fixture_pins(), mock.patch.object(patch, 'AFTER_SHA256', '0' * 64):
            with self.assertRaisesRegex(ValueError, 'reviewed result'):
                patch.patched_source(FIXTURE)

    def test_ambiguous_or_missing_anchor_is_rejected(self):
        for data in (b'inert', FIXTURE + patch.BEFORE):
            with self.subTest(data=data), self.fixture_pins(data):
                with self.assertRaisesRegex(ValueError, 'exactly one'):
                    patch.patched_source(data)

    def test_only_exhausted_short_error_changes_and_cause_is_retained(self):
        with self.fixture_pins():
            changed = patch.patched_source(FIXTURE)
        self.assertEqual(changed.replace(patch.AFTER, patch.BEFORE, 1), FIXTURE)
        scope = {}
        exec(changed, scope)
        with self.assertRaises(RuntimeError) as raised:
            scope['worker']('short')
        self.assertIsInstance(raised.exception.__cause__, urllib.error.ContentTooShortError)
        transported = pickle.loads(pickle.dumps(raised.exception))
        self.assertIn('ContentTooShortError', str(transported))
        with self.assertRaisesRegex(ValueError, 'ordinary failure'):
            scope['worker']('other')
        self.assertEqual(scope['worker']('ok'), 0)

    def test_five_attempts_remain_and_only_final_error_is_normalized(self):
        with self.fixture_pins():
            changed = patch.patched_source(FIXTURE)
        scope = {}
        exec(changed, scope)
        original = urllib.error.ContentTooShortError
        count = []
        class CountedShortError(original):
            def __init__(self, *args):
                count.append(1)
                super().__init__(*args)
        with mock.patch.object(urllib.error, 'ContentTooShortError', CountedShortError):
            with self.assertRaises(RuntimeError):
                scope['worker']('short')
        self.assertEqual(len(count), 5)

    def test_real_spawn_pool_returns_failure_instead_of_hanging(self):
        with self.fixture_pins():
            changed = patch.patched_source(FIXTURE)
        # Importable top-level worker: exercises Windows-style spawn even on
        # Linux. All child processes are scoped to this inert fixture pool.
        suffix = b'''
if __name__ == "__main__":
    import multiprocessing
    with multiprocessing.get_context("spawn").Pool(1) as pool:
        result = pool.apply_async(worker, ("short",))
        try:
            result.get(timeout=8)
        except RuntimeError as error:
            assert "ContentTooShortError" in str(error)
            print("failure transported")
        else:
            raise AssertionError("short download unexpectedly succeeded")
'''
        with tempfile.TemporaryDirectory() as temporary:
            script = Path(temporary) / 'inert_spawn.py'
            script.write_bytes(changed + suffix)
            result = subprocess.run([sys.executable, str(script)], capture_output=True,
                                    text=True, timeout=15, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('failure transported', result.stdout)

    def test_evidence_is_fresh_and_source_is_not_silently_repatched(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'fixture.py'
            source.write_bytes(FIXTURE)
            evidence = root / 'evidence'
            evidence.mkdir()
            with self.fixture_pins():
                patch.apply_patch(source, evidence)
                report = json.loads((evidence / 'vsdownload-exception-patch.json').read_text())
                self.assertEqual(report['before_sha256'], hashlib.sha256(FIXTURE).hexdigest())
                self.assertEqual(report['after_sha256'], hashlib.sha256(source.read_bytes()).hexdigest())
                self.assertFalse(report['retry_count_changed'])
                self.assertFalse(report['payload_hash_checks_changed'])
                self.assertTrue(report['download_failure_remains_fatal'])
                with self.assertRaises(ValueError):
                    patch.apply_patch(source, evidence)
                source.write_bytes(FIXTURE)
                with self.assertRaises(FileExistsError):
                    patch.apply_patch(source, evidence)
                self.assertEqual(source.read_bytes(), FIXTURE)

    def test_nonregular_source_is_rejected_before_evidence_creation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaises(ValueError):
                patch.apply_patch(root, root)
            self.assertFalse((root / 'vsdownload-exception-patch.json').exists())

    def test_symlink_source_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'fixture.py'
            source.write_bytes(FIXTURE)
            linked = root / 'linked.py'
            try:
                linked.symlink_to(source)
            except OSError:
                self.skipTest('Host does not permit symlink creation')
            with self.fixture_pins(), self.assertRaises(ValueError):
                patch.apply_patch(linked, root)
            self.assertEqual(source.read_bytes(), FIXTURE)

    def test_production_rejects_overrides_before_source_access(self):
        with mock.patch.object(sys, 'argv', ['patch_vsdownload.py', '--path', 'other']), \
                mock.patch.object(patch.run_mach_bootstrap, 'require_source') as guard, \
                mock.patch.object(patch, 'apply_patch') as apply:
            with self.assertRaisesRegex(ValueError, 'overrides'):
                patch.main()
            guard.assert_not_called()
            apply.assert_not_called()

    def test_production_requires_exact_ephemeral_source_context(self):
        with mock.patch.object(sys, 'argv', ['patch_vsdownload.py']), \
                mock.patch.dict(patch.run_mach_bootstrap.os.environ, {'GITHUB_ACTIONS': 'false'}), \
                mock.patch.object(patch, 'apply_patch') as apply:
            with self.assertRaisesRegex(ValueError, 'ephemeral hosted Windows'):
                patch.main()
            apply.assert_not_called()


if __name__ == '__main__':
    unittest.main()
