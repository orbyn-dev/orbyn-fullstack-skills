"""Exercise the documented PGO preflight with a disposable Rust toolchain."""
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

from test_tools import ROOT

SHELL = shutil.which('sh') if os.name == 'posix' else None
GUIDE = ROOT/'skills/rust-migration/references/blueprint-and-pgo.md'


@unittest.skipUnless(SHELL, 'The PGO recipe uses a POSIX shell')
class PgoPreflightTests(unittest.TestCase):
    host = 'x86_64-unknown-linux-gnu'

    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='pgo test ')
        self.addCleanup(temp.cleanup)
        self.workspace = Path(temp.name)
        self.sysroot = self.workspace/'rust toolchain'
        self.bin = self.workspace/'bin'
        self.bin.mkdir()
        self.make_executable(self.bin/'rustc', r"""#!/bin/sh
case "$*" in
    -vV) printf 'rustc 1.90.0\nhost: %s\n' "$TEST_HOST" ;;
    '--print sysroot') printf '%s\n' "$TEST_SYSROOT" ;;
    *) exit 2 ;;
esac
""")
        guide = GUIDE.read_text(encoding='utf-8')
        pgo = guide.partition('\n## LLVM PGO\n')[2]
        block = re.search(r'^ *```sh\n(.*?)^ *```$', pgo, re.S | re.M)
        self.assertIsNotNone(block, 'Expected an executable PGO preflight in the guide')
        self.preflight = block.group(1)

    def make_executable(self, path, content='#!/bin/sh\nexit 0\n'):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding='utf-8')
        path.chmod(0o755)
        return path

    def bundled_tool(self, target):
        return self.sysroot/'lib/rustlib'/target/'bin/llvm-profdata'

    def run_preflight(self, **overrides):
        env = {
            'PATH': os.pathsep.join((str(self.bin), os.defpath)),
            'TEST_HOST': self.host,
            'TEST_SYSROOT': str(self.sysroot),
            **overrides,
        }
        # The subsequent PGO step consumes this path; print it without running LLVM.
        return subprocess.run(
            [SHELL, '-c', self.preflight + '\nprintf "%s\\n" "$LLVM_PROFDATA"'],
            cwd=self.workspace, env=env, capture_output=True, text=True, timeout=5,
        )

    def assert_selected(self, result, tool):
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), [str(tool)])

    def test_uses_host_toolchain_by_default_even_with_a_system_llvm(self):
        self.make_executable(self.bin/'llvm-profdata')
        bundled = self.make_executable(self.bundled_tool(self.host))
        self.assert_selected(self.run_preflight(), bundled)

    def test_explicit_target_overrides_the_host_triple(self):
        target = 'aarch64-unknown-linux-gnu'
        self.make_executable(self.bundled_tool(self.host))
        bundled = self.make_executable(self.bundled_tool(target))
        self.assert_selected(self.run_preflight(TARGET=target), bundled)

    def test_missing_or_non_executable_tool_stops_without_using_system_llvm(self):
        self.make_executable(self.bin/'llvm-profdata')
        bundled = self.bundled_tool(self.host)
        for installed in (False, True):
            with self.subTest(installed=installed):
                if installed:
                    self.make_executable(bundled).chmod(0o644)
                result = self.run_preflight()
                self.assertEqual(result.returncode, 1, result)
                self.assertEqual(result.stdout, '')
                self.assertIn(self.host, result.stderr)
                self.assertIn('llvm-tools-preview', result.stderr)
