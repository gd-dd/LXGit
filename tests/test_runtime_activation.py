"""Private-runtime activation regressions, including real Windows delete-sharing locks.

Only disposable directories below this repository's E: runtime are modified.
No Git executable, SFX, GUI, user installation, network or credentials are used.
"""
import ctypes
import errno
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend import git_runtime as rt

AREA = ROOT / 'runtime/bootstrap-fix-20261112/activation-tests'
TEMP_AREA = ROOT / 'runtime/a8-tests'
REQUIRED = ['cmd/git.exe', 'ucrt64/bin/git-lfs.exe',
            'ucrt64/bin/git-credential-manager.exe', 'usr/bin/ssh.exe']


class DirectoryLease:
    """A genuine Windows directory handle that denies FILE_SHARE_DELETE."""
    def __init__(self, directory):
        from ctypes import wintypes as w
        self.guard = threading.Lock()
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        self.kernel.CreateFileW.argtypes = [w.LPCWSTR, w.DWORD, w.DWORD,
            ctypes.c_void_p, w.DWORD, w.DWORD, w.HANDLE]
        self.kernel.CreateFileW.restype = w.HANDLE
        self.kernel.CloseHandle.argtypes = [w.HANDLE]
        self.kernel.CloseHandle.restype = w.BOOL
        self.handle = self.kernel.CreateFileW(str(directory), 1, 3, None, 3, 0x02000000, None)
        if self.handle in (None, ctypes.c_void_p(-1).value):
            raise ctypes.WinError(ctypes.get_last_error())

    def close(self):
        with self.guard:
            if self.handle is not None:
                handle, self.handle = self.handle, None
                if not self.kernel.CloseHandle(handle):
                    raise ctypes.WinError(ctypes.get_last_error())


@unittest.skipUnless(os.name == 'nt', 'Windows activation and locking contract')
class RuntimeActivationTests(unittest.TestCase):
    def setUp(self):
        AREA.mkdir(parents=True, exist_ok=True)
        TEMP_AREA.mkdir(parents=True, exist_ok=True)
        # Keep canonical package + stage below the real PortableGit path preflight.
        self.temp = tempfile.TemporaryDirectory(prefix='a-', dir=TEMP_AREA)
        self.root = Path(self.temp.name)
        self.cancel = threading.Event()

    def tearDown(self):
        self.temp.cleanup()

    def directory(self, name):
        path = self.root / name
        path.mkdir(parents=True)
        (path / 'keep.txt').write_bytes(name.encode())
        return path

    def fake_payload(self, path):
        for rel in REQUIRED:
            file = path / rel
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_bytes(('verified fixture: ' + rel).encode())
        marker = {'version': rt.SPEC['version'], 'archiveSha256': rt.SPEC['sha256'],
                  'files': {rel: hashlib.sha256((path / rel).read_bytes()).hexdigest() for rel in REQUIRED}}
        (path / 'lxgit-ready.json').write_text(json.dumps(marker), 'utf-8')

    def package(self):
        path = self.root / 'LXGit--1432888469/versions/0.0.7'
        (path / '.lxtool').mkdir(parents=True)
        (path / '.lxtool/manifest.json').write_text(json.dumps({
            'kind': 'lx-tool', 'name': 'LXGit 游戏协作', 'version': '0.0.7',
            'panel': '.lxtool/panel.json'}), 'utf-8')
        (path / 'tool.json').write_text(json.dumps({
            'id': 'repo-1432888469', 'repository': 'gd-dd/LXGit',
            'version': '0.0.7'}), 'utf-8')
        return path

    def extraction(self, args, **kwargs):
        self.fake_payload(Path(args[0]).parent / 'PortableGit')
        return Mock(poll=Mock(return_value=0), returncode=0)

    def test_real_directory_lock_first_denies_then_retry_succeeds_after_release(self):
        source = self.directory('PortableGit'); target = self.root / 'version'
        lease = DirectoryLease(source)
        try:
            with self.assertRaises(OSError) as first:
                source.rename(target)
            self.assertIn(first.exception.winerror, (5, 32, 33))
            first_error = first.exception.winerror
            rows = []
            timer = threading.Timer(.2, lease.close); timer.start()
            try:
                rt.rename_with_retry(source, target, self.cancel,
                                     lambda *row: rows.append(row), timeout=2)
            finally:
                timer.cancel(); timer.join()
            self.assertTrue((target / 'keep.txt').is_file())
            self.assertFalse(source.exists())
            self.assertEqual(len(rows), 1, 'One waiting notice, not a UI-message flood')
            (AREA / 'real-lock-retry-proof.json').write_text(json.dumps({
                'baselineWinerror': first_error, 'realWindowsDirectoryHandle': True,
                'releasedByOwnTimer': True, 'promotedAfterRelease': True,
                'progressEvents': rows}, ensure_ascii=False, indent=2), 'utf-8')
        finally:
            lease.close()

    def test_real_directory_lock_can_be_cancelled_without_promoting(self):
        source = self.directory('PortableGit'); target = self.root / 'version'
        lease = DirectoryLease(source); timer = threading.Timer(.15, self.cancel.set)
        started = time.monotonic(); timer.start()
        try:
            with self.assertRaises(rt.Cancelled):
                rt.rename_with_retry(source, target, self.cancel, timeout=2)
            self.assertLess(time.monotonic() - started, 1)
            self.assertTrue((source / 'keep.txt').exists())
            self.assertFalse(target.exists())
        finally:
            timer.cancel(); timer.join(); lease.close()

    def test_real_staging_root_lock_retries_cleanup_after_release(self):
        stage = self.directory('.stage-owned')
        lease = DirectoryLease(stage); timer = threading.Timer(.2, lease.close); timer.start()
        try:
            with patch.object(rt, 'RENAME_TIMEOUT', 2):
                rt.remove_staging(self.root, stage)
            self.assertFalse(stage.exists())
        finally:
            timer.cancel(); timer.join(); lease.close()

    def test_persistent_winerror5_is_bounded_and_never_publishes(self):
        source = self.directory('PortableGit'); target = self.root / 'version'
        started = time.monotonic()
        with patch.object(Path, 'rename', side_effect=ctypes.WinError(5)) as rename:
            with self.assertRaises(OSError) as error:
                rt.rename_with_retry(source, target, self.cancel, timeout=.25)
        self.assertEqual(error.exception.winerror, 5)
        self.assertGreater(rename.call_count, 1)
        self.assertLess(time.monotonic() - started, 1)
        self.assertTrue(source.exists()); self.assertFalse(target.exists())

    def test_unrelated_oserror_is_not_retried(self):
        source = self.directory('PortableGit'); target = self.root / 'version'
        with patch.object(Path, 'rename', side_effect=OSError(errno.EXDEV, 'different volume')) as rename:
            with self.assertRaises(OSError):
                rt.rename_with_retry(source, target, self.cancel, timeout=2)
        rename.assert_called_once()

    def test_existing_target_is_not_overwritten_or_retried(self):
        source = self.directory('PortableGit'); target = self.directory('version')
        with patch.object(Path, 'rename') as rename:
            with self.assertRaises(FileExistsError):
                rt.rename_with_retry(source, target, self.cancel, timeout=2)
        rename.assert_not_called()
        self.assertEqual((source / 'keep.txt').read_bytes(), b'PortableGit')
        self.assertEqual((target / 'keep.txt').read_bytes(), b'version')

    def test_cancelled_before_rename_does_not_touch_either_directory(self):
        source = self.directory('PortableGit'); target = self.root / 'version'
        self.cancel.set()
        with patch.object(Path, 'rename') as rename:
            with self.assertRaises(rt.Cancelled):
                rt.rename_with_retry(source, target, self.cancel)
        rename.assert_not_called(); self.assertTrue(source.exists())

    def test_failed_activation_restores_previous_target(self):
        payload = self.directory('PortableGit'); target = self.directory(rt.SPEC['version'])
        original = Path.rename
        def rename(source, destination):
            if source == payload: raise ctypes.WinError(5)
            return original(source, destination)
        with patch.object(Path, 'rename', rename), patch.object(rt, 'RENAME_TIMEOUT', .2):
            with self.assertRaisesRegex(ValueError, '无法切换'):
                rt.activate_payload(payload, target, self.cancel, lambda *a: None)
        self.assertEqual((target / 'keep.txt').read_bytes(), rt.SPEC['version'].encode())
        self.assertTrue(payload.exists())
        self.assertEqual(list(self.root.glob('invalid-*')), [])

    def test_cancelled_activation_restores_previous_target_despite_cancel_event(self):
        payload = self.directory('PortableGit'); target = self.directory(rt.SPEC['version'])
        original = Path.rename
        def rename(source, destination):
            if source == payload:
                self.cancel.set(); raise ctypes.WinError(32)
            return original(source, destination)
        with patch.object(Path, 'rename', rename):
            with self.assertRaises(rt.Cancelled):
                rt.activate_payload(payload, target, self.cancel, lambda *a: None)
        self.assertEqual((target / 'keep.txt').read_bytes(), rt.SPEC['version'].encode())
        self.assertTrue(payload.exists()); self.assertEqual(list(self.root.glob('invalid-*')), [])

    def test_failed_rollback_preserves_backup_and_names_it_in_error(self):
        payload = self.directory('PortableGit'); target = self.directory(rt.SPEC['version'])
        original = Path.rename
        def rename(source, destination):
            if source == payload or source.name.startswith('invalid-'):
                raise ctypes.WinError(5)
            return original(source, destination)
        with patch.object(Path, 'rename', rename), patch.object(rt, 'RENAME_TIMEOUT', .15):
            with self.assertRaisesRegex(ValueError, '旧目录保留') as error:
                rt.activate_payload(payload, target, self.cancel, lambda *a: None)
        backups = list(self.root.glob('invalid-*'))
        self.assertEqual(len(backups), 1)
        self.assertIn(str(backups[0]), str(error.exception))
        self.assertEqual((backups[0] / 'keep.txt').read_bytes(), rt.SPEC['version'].encode())
        self.assertTrue(payload.exists()); self.assertFalse(target.exists())

    def test_successful_activation_preserves_previous_directory_as_backup(self):
        payload = self.directory('PortableGit'); target = self.directory(rt.SPEC['version'])
        rt.activate_payload(payload, target, self.cancel, lambda *a: None)
        self.assertEqual((target / 'keep.txt').read_bytes(), b'PortableGit')
        backups = list(self.root.glob('invalid-*'))
        self.assertEqual(len(backups), 1)
        self.assertEqual((backups[0] / 'keep.txt').read_bytes(), rt.SPEC['version'].encode())

    def test_failed_prepare_does_not_publish_ready_and_restores_old_target(self):
        package = self.package(); root = rt.runtime_root(package)
        target = root / rt.SPEC['version']; target.mkdir(parents=True)
        (target / 'previous.txt').write_bytes(b'old target must survive')
        original = Path.rename
        def rename(source, destination):
            if source.name == 'PortableGit': raise ctypes.WinError(5)
            return original(source, destination)
        with patch.object(rt, 'download'), patch.object(rt, 'signature', return_value='fixture signer'), \
             patch.object(rt.subprocess, 'Popen', side_effect=self.extraction), \
             patch.object(rt, 'probe', return_value=['fixture']), \
             patch.object(Path, 'rename', rename), patch.object(rt, 'RENAME_TIMEOUT', .15):
            with self.assertRaisesRegex(ValueError, '无法切换'):
                rt.prepare(package, self.cancel)
        self.assertEqual((target / 'previous.txt').read_bytes(), b'old target must survive')
        self.assertIsNone(rt.ready(root, verify=False))
        self.assertEqual(list(root.glob('.stage-*')), [])

    def test_cleanup_error_does_not_mask_original_prepare_error(self):
        package = self.package(); root = rt.runtime_root(package)
        with patch.object(rt, 'download', side_effect=ValueError('primary transport failed')), \
             patch.object(rt, 'remove_staging', side_effect=ctypes.WinError(5)):
            with self.assertRaisesRegex(ValueError, 'primary transport failed') as error:
                rt.prepare(package, self.cancel)
        self.assertFalse((root / rt.SPEC['version']).exists())
        self.assertTrue(getattr(error.exception, '__notes__', []), 'Cleanup failure must remain diagnosable')
        self.assertIn('临时目录清理失败', error.exception.cleanup_warning)

    def test_successful_prepare_reports_cleanup_warning_without_losing_ready_runtime(self):
        package = self.package(); root = rt.runtime_root(package)
        with patch.object(rt, 'download'), patch.object(rt, 'signature', return_value='fixture signer'), \
             patch.object(rt.subprocess, 'Popen', side_effect=self.extraction), \
             patch.object(rt, 'probe', return_value=['fixture']), \
             patch.object(rt, 'remove_staging', side_effect=ctypes.WinError(5)):
            result = rt.prepare(package, self.cancel)
        self.assertIn('cleanupWarning', result)
        self.assertFalse(result['reused'])
        self.assertEqual(rt.ready(root, verify=False), str(root / rt.SPEC['version'] / 'cmd/git.exe'))
        self.assertEqual(len(list(root.glob('.stage-*'))), 1)
        with patch.object(rt, 'probe', return_value=['fixture']), patch.object(rt, 'download') as download, \
             patch.object(rt, 'remove_staging') as cleanup, patch.object(Path, 'glob') as scan:
            reused = rt.prepare(package, self.cancel)
        self.assertTrue(reused['reused']); self.assertNotIn('cleanupWarning', reused)
        download.assert_not_called(); cleanup.assert_not_called(); scan.assert_not_called()
        self.assertEqual(len(list(root.glob('.stage-*'))), 1)

    def test_ready_reuse_never_scans_or_cleans_staging_and_never_redownloads(self):
        package = self.package(); root = rt.runtime_root(package)
        self.fake_payload(root / rt.SPEC['version'])
        stage = root / '.stage-abandoned'; stage.mkdir(); (stage / 'partial').write_bytes(b'partial')
        with patch.object(rt, 'probe', return_value=['fixture']), patch.object(rt, 'download') as download, \
             patch.object(rt, 'remove_staging', side_effect=AssertionError('ready fast path must not clean')) as cleanup, \
             patch.object(Path, 'glob', side_effect=AssertionError('ready fast path must not scan')) as scan:
            result = rt.prepare(package, self.cancel)
        self.assertTrue(result['reused']); self.assertNotIn('cleanupWarning', result)
        self.assertIsNotNone(rt.ready(root, verify=False)); download.assert_not_called()
        cleanup.assert_not_called(); scan.assert_not_called()
        self.assertEqual((stage / 'partial').read_bytes(), b'partial')

    def test_staging_cleanup_has_its_own_shorter_bound(self):
        stage = self.directory('.stage-owned'); started = time.monotonic()
        with patch.object(rt, 'CLEANUP_TIMEOUT', .15), patch.object(rt, 'RENAME_TIMEOUT', 12), \
             patch.object(rt.shutil, 'rmtree', side_effect=ctypes.WinError(5)) as remove:
            with self.assertRaises(OSError): rt.remove_staging(self.root, stage)
        self.assertGreater(remove.call_count, 1)
        self.assertLess(time.monotonic() - started, 1)
        self.assertTrue((stage / 'keep.txt').exists())

    def test_cancellation_stays_primary_when_cleanup_remains_locked(self):
        package = self.package(); root = rt.runtime_root(package)
        def cancelled_download(path, cancel, progress):
            path.write_bytes(b'partial'); cancel.set(); rt.check_cancel(cancel)
        started = time.monotonic()
        with patch.object(rt, 'download', side_effect=cancelled_download), \
             patch.object(rt, 'CLEANUP_TIMEOUT', .15), \
             patch.object(rt.shutil, 'rmtree', side_effect=ctypes.WinError(5)):
            with self.assertRaises(rt.Cancelled) as error: rt.prepare(package, self.cancel)
        self.assertLess(time.monotonic() - started, 1)
        self.assertTrue(getattr(error.exception, '__notes__', []))
        self.assertIn('临时目录清理失败', error.exception.cleanup_warning)
        self.assertFalse((root / rt.SPEC['version']).exists())

    def test_retry_cleanup_refuses_non_stage_and_outside_paths(self):
        for path in [self.directory(rt.SPEC['version']), self.directory('outside/.stage-other')]:
            with self.assertRaises(ValueError): rt.remove_staging(self.root, path)
            self.assertTrue((path / 'keep.txt').exists())


if __name__ == '__main__': unittest.main()
