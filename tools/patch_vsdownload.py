#!/usr/bin/env python3
"""Repair only exhausted short-download exception transport in pinned ESR source.

The verified downloader's retry count, TLS/download implementation, payload
selection and SHA-256 checks stay unchanged. A short download still fails;
its error can now cross a multiprocessing queue instead of killing the result
handler and leaving bootstrap blocked forever.
"""
import hashlib
import json
from pathlib import Path
import stat
import sys

import run_mach_bootstrap

RELATIVE_SOURCE = 'third_party/python/vsdownload/vsdownload.py'
# Reviewed source: https://hg.mozilla.org/releases/mozilla-esr153/raw-file/
# FIREFOX_153_5_0esr_RELEASE/third_party/python/vsdownload/vsdownload.py
BEFORE_SHA256 = '03b990385482f1dcffd6625028b1357a8f420ad19664ded0b038a5b0041f09e9'
BEFORE = b'''        except Exception as e:
            if attempt == attempts - 1:
                raise
'''
AFTER = b'''        except Exception as e:
            if attempt == attempts - 1:
                # ContentTooShortError cannot round-trip through multiprocessing.
                # Preserve failure after the unchanged retry/hash checks.
                if isinstance(e, urllib.error.ContentTooShortError):
                    raise RuntimeError("ContentTooShortError: " + str(e)) from e
                raise
'''
AFTER_SHA256 = 'af337035cc729acdce158fba7845584413dda1033ba89a3c66a91c7e1c5f1741'


def patched_source(data):
    if hashlib.sha256(data).hexdigest() != BEFORE_SHA256:
        raise ValueError('VS downloader differs from reviewed ESR source')
    if data.count(BEFORE) != 1:
        raise ValueError('Expected exactly one exhausted-download exception handler')
    result = data.replace(BEFORE, AFTER, 1)
    if hashlib.sha256(result).hexdigest() != AFTER_SHA256:
        raise ValueError('VS downloader patch differs from reviewed result')
    return result


def safe_path(path, directory=False):
    for parent in reversed(path.parents):
        info = parent.lstat()
        if (not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)
                or getattr(info, 'st_file_attributes', 0) & 0x400):
            raise ValueError('VS patch path has an unsafe parent')
    info = path.lstat()
    expected_type = stat.S_ISDIR if directory else stat.S_ISREG
    if (not expected_type(info.st_mode) or stat.S_ISLNK(info.st_mode)
            or getattr(info, 'st_file_attributes', 0) & 0x400):
        raise ValueError('VS patch path is not an ordinary file/directory')


def apply_patch(source, evidence):
    safe_path(source)
    safe_path(evidence, directory=True)
    original = source.read_bytes()
    replacement = patched_source(original)
    # Reserve evidence before mutation. Never overwrite a previous attempt.
    with (evidence / 'vsdownload-exception-patch.json').open('x', encoding='utf-8') as report:
        # Recheck immediately before writing the fixed, verified bytes.
        safe_path(source)
        if source.read_bytes() != original:
            raise ValueError('VS downloader changed before patching')
        source.write_bytes(replacement)
        if source.read_bytes() != replacement:
            raise ValueError('VS downloader patch readback failed')
        json.dump({
            'schema_version': 1,
            'source': RELATIVE_SOURCE,
            'before_sha256': BEFORE_SHA256,
            'after_sha256': AFTER_SHA256,
            'patch': 'pickleable-exhausted-ContentTooShortError',
            'retry_count_changed': False,
            'payload_hash_checks_changed': False,
            'download_failure_remains_fatal': True,
        }, report, indent=2)
        report.write('\n')


def main():
    if len(sys.argv) != 1:
        raise ValueError('No VS downloader patch argument overrides are supported')
    run_mach_bootstrap.require_source()
    apply_patch(Path.cwd() / RELATIVE_SOURCE, Path(r'D:\pb\evidence'))


if __name__ == '__main__':
    main()
