import os
import subprocess
import tempfile
from pathlib import Path
from unittest import TestCase

ROOT = Path(__file__).resolve().parents[2]
HOOK = ROOT / '.platform/hooks/postdeploy/07_web_memory_guard.sh'

# Stubs log their arguments; `swapon --show` reports SWAP_ACTIVE, and
# activating a file fails unless mkswap has written a signature into it.
_STUBS = {
    'swapon': '''#!/bin/bash
echo "swapon $*" >>"$CALLS"
if [[ "$1" == --show* ]]; then printf '%s' "${SWAP_ACTIVE:-}"; exit 0; fi
grep -q SWAPSPACE2 "$1"
''',
    'mkswap': '#!/bin/bash\necho "mkswap $*" >>"$CALLS"\necho SWAPSPACE2 >"$1"\n',
    'dd': '#!/bin/bash\necho "dd $*" >>"$CALLS"\nfor a; do [[ $a == of=* ]] && : >"${a#of=}"; done\nexit 0\n',
    'sysctl': '#!/bin/bash\necho "sysctl $*" >>"$CALLS"\n',
    'systemctl': '#!/bin/bash\necho "systemctl $*" >>"$CALLS"\n',
}


class WebMemoryGuardHookTests(TestCase):
    def _run(self, *, mem_total_kb=1958912, swap_active='', prepare=None):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name) / 'root'
        bin_dir = Path(temp.name) / 'bin'
        for path in (root / 'proc', root / 'etc', root / 'var', bin_dir):
            path.mkdir(parents=True)
        (root / 'proc/meminfo').write_text(f'MemTotal:       {mem_total_kb} kB\n')
        for name, body in _STUBS.items():
            stub = bin_dir / name
            stub.write_text(body)
            stub.chmod(0o755)
        if prepare:
            prepare(root)
        calls = Path(temp.name) / 'calls'
        calls.touch()
        env = {
            **os.environ,
            'PATH': f'{bin_dir}:{os.environ["PATH"]}',
            'MEMORY_GUARD_ROOT': str(root),
            'CALLS': str(calls),
            'SWAP_ACTIVE': swap_active,
        }
        result = subprocess.run(['bash', str(HOOK)], env=env, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        return root, calls.read_text().splitlines(), result.stdout

    def test_fresh_instance_gets_swap_limit_and_persistent_settings(self):
        root, calls, stdout = self._run()

        swapfile = root / 'var/swapfile'
        self.assertIn('SWAPSPACE2', swapfile.read_text())
        self.assertEqual(swapfile.stat().st_mode & 0o777, 0o600)
        self.assertIn(f'swapon {swapfile}', calls)
        self.assertEqual(
            (root / 'etc/fstab').read_text(),
            '/var/swapfile none swap defaults,nofail 0 0\n',
        )
        self.assertEqual(
            (root / 'etc/sysctl.d/90-interoves-swappiness.conf').read_text(),
            'vm.swappiness = 10\n',
        )
        dropin = (root / 'etc/systemd/system/web.service.d/10-memory-guard.conf').read_text()
        # 1913 MB t3.small minus the 512 MB reserve.
        self.assertIn('MemoryMax=1401M\n', dropin)
        self.assertIn('MemorySwapMax=0\n', dropin)
        self.assertIn(
            'systemctl set-property --runtime web.service MemoryMax=1401M MemorySwapMax=0',
            calls,
        )
        self.assertLess(calls.index('systemctl daemon-reload'), len(calls) - 1)
        self.assertIn('MemoryMax=1401M', stdout)

    def test_rerun_with_active_swap_is_idempotent(self):
        def prepare(root):
            (root / 'var/swapfile').write_text('SWAPSPACE2\n')
            (root / 'etc/fstab').write_text('/var/swapfile none swap defaults,nofail 0 0\n')

        root, calls, _stdout = self._run(swap_active='/var/swapfile\n', prepare=prepare)

        self.assertFalse(any(call.startswith(('dd ', 'mkswap ')) for call in calls))
        self.assertEqual((root / 'etc/fstab').read_text().count('/var/swapfile'), 1)

    def test_existing_valid_swapfile_is_reactivated_without_rewrite(self):
        def prepare(root):
            (root / 'var/swapfile').write_text('SWAPSPACE2\n')

        _root, calls, _stdout = self._run(prepare=prepare)

        self.assertFalse(any(call.startswith(('dd ', 'mkswap ')) for call in calls))

    def test_interrupted_swapfile_without_signature_is_recreated(self):
        def prepare(root):
            (root / 'var/swapfile').write_text('')

        root, calls, _stdout = self._run(prepare=prepare)

        self.assertTrue(any(call.startswith('mkswap ') for call in calls))
        self.assertIn('SWAPSPACE2', (root / 'var/swapfile').read_text())

    def test_limit_scales_with_ram_and_keeps_a_floor_on_small_instances(self):
        root, _calls, _stdout = self._run(mem_total_kb=4 * 1024 * 1024)
        dropin = root / 'etc/systemd/system/web.service.d/10-memory-guard.conf'
        self.assertIn('MemoryMax=3584M\n', dropin.read_text())

        root, _calls, _stdout = self._run(mem_total_kb=1024 * 1024)
        dropin = root / 'etc/systemd/system/web.service.d/10-memory-guard.conf'
        self.assertIn('MemoryMax=614M\n', dropin.read_text())

    def test_green_bundle_ships_and_requires_the_hook(self):
        bundle = (ROOT / 'scripts/prepare_eb_bundle.sh').read_text()
        manifest = (ROOT / 'scripts/eb_config_manifest.py').read_text()
        hook = '.platform/hooks/postdeploy/07_web_memory_guard.sh'

        self.assertEqual(bundle.count(f'"$ROOT/{hook}"'), 1)
        self.assertEqual(manifest.count(f'"{hook}"'), 2)
        self.assertTrue(os.access(HOOK, os.X_OK))
