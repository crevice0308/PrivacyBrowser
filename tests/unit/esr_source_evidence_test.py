"""Keep adopted ESR and reviewed source compatibility evidence synchronized.

This contract does not replace signature verification, source integration, or
native execution. It prevents a version-label-only maintenance update.
"""
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
import patch_vsdownload


class AdoptedEsrEvidenceTests(unittest.TestCase):
    def test_adopted_source_proof_matches_version_and_trust_policy(self):
        config = json.loads((ROOT / 'config/esr.json').read_text())
        trust = json.loads((ROOT / 'config/update-trust.json').read_text())
        version = config['upstream_version']
        evidence = json.loads((ROOT / f'tests/results/esr-{version.removesuffix("esr")}-source-verification.json').read_text())
        self.assertFalse(config['release_allowed'])
        self.assertEqual(evidence['upstream_version'], version)
        self.assertEqual(evidence['kind'], 'source')
        self.assertTrue(evidence['signature_verified'])
        self.assertIn(evidence['checksum_signer'], trust['mozilla_release_key_fingerprints'])
        self.assertIn(evidence['checksum_signing_subkey'], trust['mozilla_release_signing_subkey_fingerprints'])
        self.assertRegex(evidence['sha512'], r'^[a-f0-9]{128}$')
        self.assertEqual(evidence['source_url'],
                         f'https://archive.mozilla.org/pub/firefox/releases/{version}/source/firefox-{version}.source.tar.xz')
        self.assertFalse(evidence['source_compiled'])
        self.assertFalse(evidence['runtime_tested'])
        pin = json.loads((ROOT / 'config/windows-native-toolchain.json').read_text())
        self.assertEqual(pin['reviewed_esr'], version)
        for path, key in (
            ('python/mozboot/mozboot/bootstrap.py', 'reviewed_bootstrap_sha256'),
            ('python/mozboot/mozboot/mach_commands.py', 'reviewed_mach_bootstrap_sha256'),
            ('taskcluster/kinds/toolchain/rust.yml', 'reviewed_rust_definition_sha256'),
        ):
            self.assertEqual(evidence['unchanged_compatibility_sha256'][path], pin[key])
        self.assertEqual(evidence['unchanged_compatibility_sha256'][patch_vsdownload.RELATIVE_SOURCE],
                         patch_vsdownload.BEFORE_SHA256)
        self.assertEqual(evidence['pr1_downloader_patch_after_sha256'], patch_vsdownload.AFTER_SHA256)
        for target in ('linux', 'windows'):
            preparation = evidence['source_preparation'][target]
            self.assertTrue(preparation['prepared'])
            self.assertFalse(preparation['compiled'])
            self.assertEqual(preparation['target'], target)
            self.assertEqual(preparation['upstream_version'], version)
        workflow = (ROOT / '.github/workflows/source-build.yml').read_text()
        self.assertIn(f"default: '{version}'", workflow)


if __name__ == '__main__':
    unittest.main()
