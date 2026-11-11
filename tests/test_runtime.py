"""Private Git bootstrap regression tests; fixtures and temporary data stay in E:."""
import hashlib
import io
import http.client
import re
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend import git_runtime as rt

REQUIRED = ['cmd/git.exe', 'ucrt64/bin/git-lfs.exe',
            'ucrt64/bin/git-credential-manager.exe', 'usr/bin/ssh.exe']


class Response(io.BytesIO):
    url = 'https://release-assets.githubusercontent.com/example'


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.area = ROOT / 'runtime' / 'private-git-tests'
        self.area.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=self.area)
        self.root = Path(self.temp.name)
        self.cancel = threading.Event()

    def tearDown(self):
        self.temp.cleanup()

    def package(self, installed=True):
        p = (self.root / 'LXGit--1432888469' / 'versions' / '0.0.5'
             if installed else self.root / 'development')
        (p / '.lxtool').mkdir(parents=True)
        (p / '.lxtool/manifest.json').write_text(json.dumps({
            'kind': 'lx-tool', 'name': 'LXGit 游戏协作', 'version': '0.0.5',
            'panel': '.lxtool/panel.json'}), encoding='utf-8')
        if installed:
            (p / 'tool.json').write_text(json.dumps({
                'id': 'repo-1432888469', 'repository': 'gd-dd/LXGit',
                'version': '0.0.5'}), encoding='utf-8')
        else:
            for f in ['backend/worker.py', 'scripts/prepare_package.py']:
                q = p / f; q.parent.mkdir(exist_ok=True); q.write_bytes(b'# fixture')
        return p

    def payload(self, target):
        for i, rel in enumerate(REQUIRED):
            p = target / rel; p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(('component-' + str(i)).encode())
        marker = dict(version=rt.SPEC['version'], archiveSha256=rt.SPEC['sha256'],
                      files={p: rt.digest(target / p) for p in REQUIRED})
        (target / 'lxgit-ready.json').write_text(json.dumps(marker), encoding='utf-8')
        return marker

    def test_installed_container_shared_across_versions(self):
        p = self.package()
        runtime = rt.runtime_root(p)
        self.assertEqual(runtime, p.parent.parent / 'runtime/git')
        self.assertNotIn('/versions/', runtime.as_posix())
        self.assertFalse(runtime.exists(), 'root discovery must be read-only')

    def test_source_development_runtime_is_inside_repo(self):
        p = self.package(False)
        self.assertEqual(rt.runtime_root(p), p / 'runtime/git')

    def test_rejects_unrecognized_package_without_writing(self):
        p = self.package(False)
        (p / 'scripts/prepare_package.py').unlink()
        with self.assertRaises(ValueError): rt.runtime_root(p)
        self.assertFalse((p / 'runtime').exists())

    def test_rejects_forged_receipt_identity_repository_and_version(self):
        p = self.package(); receipt = p / 'tool.json'
        original = json.loads(receipt.read_text('utf-8'))
        for key, value in [('id', 'repo-1'), ('repository', 'attacker/LXGit'),
                           ('version', '../escape')]:
            with self.subTest(key=key):
                receipt.write_text(json.dumps(dict(original, **{key: value})), encoding='utf-8')
                with self.assertRaises(ValueError): rt.runtime_root(p)
        self.assertFalse((p.parent.parent / 'runtime').exists())

    def test_rejects_receipt_outside_expected_container(self):
        p = self.package()
        wrong = p.parent.parent.with_name('some-other-tool')
        p.parent.parent.rename(wrong)
        with self.assertRaises(ValueError): rt.runtime_root(wrong / 'versions/0.0.5')

    def test_no_links_rejects_reparse_ancestor(self):
        p = self.root / 'junction' / 'child'
        real_exists = Path.exists
        real_lstat = Path.lstat
        def exists(path):
            return path == p.parent or real_exists(path)
        def lstat(path):
            return type('ReparseStat', (), {'st_file_attributes': 0x400})() if path == p.parent else real_lstat(path)
        with patch.object(Path, 'exists', exists), patch.object(Path, 'lstat', lstat), patch.object(Path, 'is_symlink', return_value=False):
            with self.assertRaises(ValueError): rt.no_links(p)

    def test_lock_excludes_second_preparer_and_releases_on_failure(self):
        if os.name != 'nt': self.skipTest('Windows runtime locking')
        folder = self.root / 'runtime'
        with self.assertRaisesRegex(RuntimeError, 'fixture'):
            with rt.locked(folder):
                with self.assertRaises(ValueError):
                    with rt.locked(folder): self.fail('second lock unexpectedly entered')
                raise RuntimeError('fixture')
        with rt.locked(folder): pass

    def test_redirect_rejects_http_unknown_origin_and_userinfo(self):
        for url in ['http://github.com/a', 'https://example.invalid/a',
                    'https://user@github.com/a', 'https://github.com.evil.invalid/a']:
            with self.subTest(url=url), self.assertRaises(ValueError): rt.validate_url(url)
        for url in ['https://github.com/git-for-windows/git/a',
                    'https://release-assets.githubusercontent.com/a']:
            rt.validate_url(url)

    def perform_download(self, body, spec_body=None, progress=None):
        expected = body if spec_body is None else spec_body
        spec = dict(rt.SPEC, size=len(expected), sha256=hashlib.sha256(expected).hexdigest())
        response = Response(body)
        opener = Mock(); opener.open.return_value = response
        path = self.root / 'download.exe'
        with patch.object(rt, 'SPEC', spec), patch.object(rt.urllib.request, 'build_opener', return_value=opener):
            rt.download(path, self.cancel, progress or (lambda *a: None))
        return path

    def test_download_accepts_exact_pinned_bytes_and_reports_progress(self):
        events = []
        path = self.perform_download(b'official fixture', progress=lambda *a: events.append(a))
        self.assertEqual(path.read_bytes(), b'official fixture')
        self.assertEqual(events[-1][1:], (16, 16))

    def test_download_rejects_truncated_oversized_and_same_size_tampering(self):
        for body in [b'abc', b'abcdef', b'abXd']:
            with self.subTest(body=body), self.assertRaises(ValueError):
                self.perform_download(body, b'abcd')

    def test_download_checks_cancel_between_blocks(self):
        def progress(*args): self.cancel.set()
        with self.assertRaises(rt.Cancelled):
            self.perform_download(b'x' * (300 * 1024), progress=progress)
        self.assertLess((self.root / 'download.exe').stat().st_size, 300 * 1024)

    def test_download_network_errors_are_actionable_chinese(self):
        for error in [OSError('connection reset'), http.client.IncompleteRead(b'partial', 80),
                      http.client.RemoteDisconnected('closed')]:
            with self.subTest(error=type(error).__name__), patch.object(rt, '_download', side_effect=error):
                with self.assertRaisesRegex(ValueError, '准备 / 重试') as failure:
                    rt.download(self.root / 'partial.exe', self.cancel, lambda *a: None)
                self.assertIs(failure.exception.__cause__, error)

    def test_download_cancellation_wins_over_network_failure(self):
        self.cancel.set()
        with patch.object(rt, '_download', side_effect=OSError('connection reset')):
            with self.assertRaises(rt.Cancelled):
                rt.download(self.root / 'partial.exe', self.cancel, lambda *a: None)

    def test_ready_requires_all_components_pinned_marker_and_matching_hashes(self):
        root = self.root / 'runtime'; target = root / rt.SPEC['version']
        marker = self.payload(target)
        with patch.object(rt, 'probe', return_value=['fixture']) as probe:
            self.assertEqual(rt.ready(root), str(target / 'cmd/git.exe'))
            probe.assert_called_once_with(target)
            (target / REQUIRED[2]).write_bytes(b'tampered')
            self.assertIsNone(rt.ready(root))
            marker = self.payload(target)
            marker['files'].pop(REQUIRED[3])
            (target / 'lxgit-ready.json').write_text(json.dumps(marker), encoding='utf-8')
            self.assertIsNone(rt.ready(root))
            marker = self.payload(target); marker['archiveSha256'] = '0' * 64
            (target / 'lxgit-ready.json').write_text(json.dumps(marker), encoding='utf-8')
            self.assertIsNone(rt.ready(root))

    def test_ready_rejects_malformed_marker_shapes_without_probe(self):
        root = self.root / 'runtime'; target = root / rt.SPEC['version']
        good = self.payload(target); marker = target / 'lxgit-ready.json'
        shapes = [None, [], 'marker', 12, {'files': []}, {'files': None},
                  dict(good, files=list(good['files'])), dict(good, files='files')]
        for shape in shapes:
            with self.subTest(shape=shape), patch.object(rt, 'probe') as probe:
                marker.write_text(json.dumps(shape), encoding='utf-8')
                self.assertIsNone(rt.ready(root)); probe.assert_not_called()

    def test_ready_rejects_oversized_or_invalid_json_marker_before_probe(self):
        root = self.root / 'runtime'; target = root / rt.SPEC['version']
        self.payload(target); marker = target / 'lxgit-ready.json'
        for content in ['{' + ' ' * 16384, '{invalid json']:
            with self.subTest(length=len(content)), patch.object(rt, 'probe') as probe:
                marker.write_text(content, encoding='utf-8')
                self.assertIsNone(rt.ready(root)); probe.assert_not_called()

    def test_ready_rejects_invalid_digest_types_formats_and_traversal(self):
        root = self.root / 'runtime'; target = root / rt.SPEC['version']
        good = self.payload(target); marker = target / 'lxgit-ready.json'
        for value in [None, [], 42, 'a' * 63, 'A' * 64, 'g' * 64]:
            with self.subTest(value=value), patch.object(rt, 'probe') as probe:
                data = dict(good, files=dict(good['files']))
                data['files'][REQUIRED[0]] = value
                marker.write_text(json.dumps(data), encoding='utf-8')
                self.assertIsNone(rt.ready(root)); probe.assert_not_called()
        data = dict(good, files=dict(good['files']))
        data['files']['../outside.exe'] = '0' * 64
        marker.write_text(json.dumps(data), encoding='utf-8')
        with patch.object(rt, 'probe') as probe:
            self.assertIsNone(rt.ready(root)); probe.assert_not_called()

    def test_ready_without_probe_still_rejects_tampered_component(self):
        root = self.root / 'runtime'; target = root / rt.SPEC['version']
        self.payload(target)
        with patch.object(rt, 'probe') as probe:
            self.assertEqual(rt.ready(root, verify=False), str(target / 'cmd/git.exe'))
            probe.assert_not_called()
            (target / REQUIRED[0]).write_bytes(b'tampered')
            self.assertIsNone(rt.ready(root, verify=False)); probe.assert_not_called()

    def test_long_path_preflight_refuses_before_download_or_execution(self):
        self.root = self.root / ('long-fixture-' * 8)
        p = self.package(); root = rt.runtime_root(p)
        with patch.object(rt, 'download') as download, patch.object(rt, 'signature') as signature, \
             patch.object(rt.subprocess, 'Popen') as process:
            with self.assertRaisesRegex(ValueError, '路径过长'): rt.prepare(p, self.cancel)
        download.assert_not_called(); signature.assert_not_called(); process.assert_not_called()
        self.assertEqual(list(root.glob('.stage-*')), [])
        self.assertFalse((root / rt.SPEC['version']).exists())

    def test_ready_probe_failure_is_not_usable(self):
        root = self.root / 'runtime'; self.payload(root / rt.SPEC['version'])
        with patch.object(rt, 'probe', side_effect=subprocess.TimeoutExpired('git', 30)):
            self.assertIsNone(rt.ready(root))

    def test_prepare_reuses_ready_runtime_offline(self):
        p = self.package(); root = rt.runtime_root(p)
        self.payload(root / rt.SPEC['version'])
        with patch.object(rt, 'probe', return_value=['fixture']), patch.object(rt, 'download') as download, \
             patch.object(rt, 'signature', side_effect=AssertionError('offline reuse must not authenticate an archive')):
            result = rt.prepare(p, self.cancel)
        self.assertTrue(result['reused']); download.assert_not_called()

    def test_prepare_download_failure_preserves_existing_target_and_cleans_staging(self):
        p = self.package(); root = rt.runtime_root(p)
        target = root / rt.SPEC['version']; target.mkdir(parents=True)
        (target / 'keep.txt').write_bytes(b'previous runtime')
        stale = root / '.stage-abandoned'; stale.mkdir(); (stale / 'partial').write_bytes(b'partial')
        with patch.object(rt, 'download', side_effect=ValueError('network broken')):
            with self.assertRaisesRegex(ValueError, 'network broken'): rt.prepare(p, self.cancel)
        self.assertEqual((target / 'keep.txt').read_bytes(), b'previous runtime')
        self.assertEqual(list(root.glob('.stage-*')), [])

    def test_prepare_cancellation_cleans_staging_without_publishing_marker(self):
        p = self.package(); root = rt.runtime_root(p)
        def cancelled_download(path, cancel, progress):
            path.write_bytes(b'partial'); cancel.set(); rt.check_cancel(cancel)
        with patch.object(rt, 'download', side_effect=cancelled_download):
            with self.assertRaises(rt.Cancelled): rt.prepare(p, self.cancel)
        self.assertEqual(list(root.glob('.stage-*')), [])
        self.assertFalse((root / rt.SPEC['version']).exists())

    def test_signature_failure_never_executes_archive(self):
        p = self.package(); root = rt.runtime_root(p)
        def fake_download(path, *args): path.write_bytes(b'fixture')
        with patch.object(rt, 'download', side_effect=fake_download), \
             patch.object(rt, 'signature', side_effect=ValueError('bad signature')), \
             patch.object(rt.subprocess, 'Popen') as popen:
            with self.assertRaisesRegex(ValueError, 'bad signature'): rt.prepare(p, self.cancel)
        popen.assert_not_called(); self.assertFalse((root / rt.SPEC['version']).exists())
        self.assertEqual(list(root.glob('.stage-*')), [])

    def test_no_links_rejects_dangling_symlink(self):
        p = self.root / 'dangling'
        with patch.object(Path, 'is_symlink', side_effect=lambda: True), patch.object(Path, 'exists', return_value=False):
            with self.assertRaises(ValueError): rt.no_links(p)

    def test_rejects_manifest_receipt_version_mismatch(self):
        p = self.package(); manifest = p / '.lxtool/manifest.json'
        data = json.loads(manifest.read_text('utf-8')); data['version'] = '9.9.9'
        manifest.write_text(json.dumps(data), encoding='utf-8')
        with self.assertRaises(ValueError): rt.runtime_root(p)

    def extracted_path(self, command, options):
        self.assertIsInstance(command, list)
        self.assertEqual(command[1:], ['-y', '-gm2'])
        self.assertFalse(options.get('shell', False), 'SFX must not be launched via a shell')
        archive = Path(command[0])
        self.assertEqual(archive.name, 'PortableGit.7z.exe')
        self.assertEqual(options['cwd'], archive.parent)
        return archive.parent / 'PortableGit'

    def test_prepare_success_publishes_only_verified_payload(self):
        self.root = self.root / '中文 空格'
        self.root.mkdir()
        p = self.package(); root = rt.runtime_root(p); events = []
        def fake_download(path, *args):
            path.write_bytes(b'fixture'); events.append('download')
        def fake_signature(path):
            self.assertEqual(path.read_bytes(), b'fixture'); events.append('signature'); return 'verified publisher'
        def fake_extract(args, **kwargs):
            self.assertEqual(events, ['download', 'signature'])
            payload = self.extracted_path(args, kwargs)
            self.assertTrue(payload.is_relative_to(root))
            self.payload(payload); events.append('extract')
            return Mock(poll=Mock(return_value=0), returncode=0)
        with patch.object(rt, 'download', side_effect=fake_download), \
             patch.object(rt, 'signature', side_effect=fake_signature), \
             patch.object(rt.subprocess, 'Popen', side_effect=fake_extract), \
             patch.object(rt, 'probe', return_value=['fixture']):
            result = rt.prepare(p, self.cancel)
        self.assertFalse(result['reused'])
        target = root / rt.SPEC['version']
        self.assertEqual(Path(result['git']), target / 'cmd/git.exe')
        self.assertEqual(json.loads((target / 'lxgit-ready.json').read_text('utf-8'))['signer'], 'verified publisher')
        self.assertFalse((target / 'PortableGit.7z.exe').exists())
        self.assertEqual(list(root.glob('.stage-*')), [])

    def test_failed_probe_preserves_old_version_and_never_publishes(self):
        p = self.package(); root = rt.runtime_root(p)
        old = root / 'older-version'; old.mkdir(parents=True)
        (old / 'keep').write_bytes(b'old runtime')
        def fake_extract(args, **kwargs):
            payload = self.extracted_path(args, kwargs)
            self.payload(payload)
            return Mock(poll=Mock(return_value=0), returncode=0)
        with patch.object(rt, 'download'), patch.object(rt, 'signature', return_value='signed'), \
             patch.object(rt.subprocess, 'Popen', side_effect=fake_extract), \
             patch.object(rt, 'probe', side_effect=ValueError('broken component')):
            with self.assertRaisesRegex(ValueError, 'broken component'): rt.prepare(p, self.cancel)
        self.assertEqual((old / 'keep').read_bytes(), b'old runtime')
        self.assertFalse((root / rt.SPEC['version']).exists())
        self.assertEqual(list(root.glob('.stage-*')), [])

    def test_git_env_prepends_private_tools_without_global_path_mutation(self):
        before = os.environ.get('PATH')
        env = rt.git_env(self.root / 'git')
        self.assertEqual(os.environ.get('PATH'), before)
        self.assertEqual(env['PATH'].split(os.pathsep)[0], str(self.root / 'git/cmd'))
        self.assertIn(str(self.root / 'git/ucrt64/bin'), env['PATH'].split(os.pathsep))
        self.assertIn(str(self.root / 'git/usr/bin'), env['PATH'].split(os.pathsep))

    def test_cancelled_extraction_kills_owned_process_and_cleans_staging(self):
        p = self.package(); root = rt.runtime_root(p)
        process = Mock(returncode=None, pid=424242)
        process.poll.return_value = None
        def fake_extract(*args, **kwargs):
            self.cancel.set()
            return process
        with patch.object(rt, 'download'), patch.object(rt, 'signature', return_value='signed'), \
             patch.object(rt.subprocess, 'Popen', side_effect=fake_extract), \
             patch.object(rt.subprocess, 'run') as taskkill:
            with self.assertRaises(rt.Cancelled): rt.prepare(p, self.cancel)
        taskkill.assert_called_once()
        args, options = taskkill.call_args
        self.assertEqual(Path(args[0][0]).name.lower(), 'taskkill.exe')
        self.assertEqual(args[0][1:], ['/PID', '424242', '/T', '/F'])
        self.assertEqual(options['timeout'], 15)
        process.kill.assert_not_called(); process.wait.assert_called_once_with(timeout=15)
        self.assertFalse((root / rt.SPEC['version']).exists())
        self.assertEqual(list(root.glob('.stage-*')), [])

    def test_remove_staging_never_deletes_sibling_or_runtime_version(self):
        root = self.root / 'runtime'; root.mkdir()
        for path in [root / rt.SPEC['version'], self.root / '.stage-outside']:
            path.mkdir(); (path / 'keep').write_bytes(b'keep')
            with self.assertRaises(ValueError): rt.remove_staging(root, path)
            self.assertTrue((path / 'keep').exists())


if __name__ == '__main__': unittest.main()
