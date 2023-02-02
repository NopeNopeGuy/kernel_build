# Copyright (C) 2023 The Android Open Source Project
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#       http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Integration tests for Kleaf.

The rest of the arguments are passed to absltest.

Example:

    bazel run //build/kernel/kleaf/tests/integration_test

    bazel run //build/kernel/kleaf/tests/integration_test \\
      -- --bazel_arg=--verbose_failures --bazel_arg=--announce_rc

    bazel run //build/kernel/kleaf/tests/integration_test \\
      -- KleafIntegrationTest.test_simple_incremental

    bazel run //build/kernel/kleaf/tests/integration_test \\
      -- --bazel_arg=--verbose_failures --bazel_arg=--announce_rc \\
         KleafIntegrationTest.test_simple_incremental \\
         --verbosity=2
"""

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import pathlib
import tempfile
import textwrap
import unittest

from absl.testing import absltest
from build.kernel.kleaf.analysis.inputs import analyze_inputs

_BAZEL = pathlib.Path("tools/bazel")

# See local.bazelrc
_NOLOCAL = ["--no//build/kernel/kleaf:config_local"]
_LOCAL = ["--//build/kernel/kleaf:config_local"]

_LTO_NONE = [
    "--lto=none",
    "--nokmi_symbol_list_strict_mode",
]

# Handy arguments to build as fast as possible.
_FASTEST = _LOCAL + _LTO_NONE


def load_arguments():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("--bazel-arg", action="append", dest="bazel_args", default=[],
                        help="arg to recursive bazel calls")
    return parser.parse_known_args()


arguments = None


class Exec(object):
    @staticmethod
    def check_call(args: list[str], **kwargs) -> None:
        """Executes a shell command."""
        kwargs.setdefault("text", True)
        sys.stderr.write(f"+ {' '.join(args)}\n")
        subprocess.check_call(args, **kwargs)

    @staticmethod
    def check_output(args: list[str], **kwargs) -> str:
        """Returns output of a shell command"""
        kwargs.setdefault("text", True)
        sys.stderr.write(f"+ {' '.join(args)}\n")
        return subprocess.check_output(args, **kwargs)


class Bazel(object):
    @staticmethod
    def check_call(args: list[str], **kwargs) -> None:
        """Executes a bazel command."""
        Exec.check_call([str(_BAZEL)] + args + arguments.bazel_args, **kwargs)

    @staticmethod
    def check_output(args: list[str], **kwargs) -> str:
        """Returns output of a bazel command."""
        return Exec.check_output([str(_BAZEL)] + args + arguments.bazel_args, **kwargs)


class KleafIntegrationTest(unittest.TestCase, Bazel):
    def setUp(self) -> None:
        self.assertTrue(os.environ.get("BUILD_WORKSPACE_DIRECTORY"),
            "BUILD_WORKSPACE_DIRECTORY is not set")
        os.chdir(os.environ["BUILD_WORKSPACE_DIRECTORY"])
        sys.stderr.write(f"BUILD_WORKSPACE_DIRECTORY={os.environ['BUILD_WORKSPACE_DIRECTORY']}\n")

        self.assertTrue(_BAZEL.is_file())

        Bazel.check_call(["clean"])

    def _sha256(self, path: pathlib.Path | str) -> str:
        """Gets the hash for a file."""
        hash = hashlib.sha256()
        with open(path, "rb") as file:
            chunk = None
            while chunk != b'':
                chunk = file.read(4096)
                hash.update(chunk)
        return hash.hexdigest()

    def _touch(self, path: pathlib.Path | str, append_text="\n") -> None:
        """Modifies a file so it (may) trigger a rebuild for certain targets."""
        with open(path) as file:
            old_content = file.read()

        def cleanup():
            with open(path, "w") as new_file:
                new_file.write(old_content)

        self.addCleanup(cleanup)

        with open(path, "a") as file:
            file.write(append_text)

    def _touch_core_kernel_file(self):
        """Modifies a core kernel file."""
        self._touch(f"{self._common()}/kernel/sched/core.c")

    def _common(self) -> str:
        """Returns the common package."""
        return "common"

    def test_simple_incremental(self):
        Bazel.check_call(["build", f"//{self._common()}:kernel_dist"] + _FASTEST)
        Bazel.check_call(["build", f"//{self._common()}:kernel_dist"] + _FASTEST)

    def test_incremental_core_kernel_file_modified(self):
        """Tests incremental build with a core kernel file modified."""
        Bazel.check_call(["build", f"//{self._common()}:kernel_dist"] + _FASTEST)
        self._touch_core_kernel_file()
        Bazel.check_call(["build", f"//{self._common()}:kernel_dist"] + _FASTEST)

    def test_change_to_core_kernel_does_not_affect_modules_prepare(self):
        """Tests that, with a small change to the core kernel, modules_prepare does not change.

        See b/254357038.
        """
        modules_prepare_archive = \
            f"bazel-bin/{self._common()}/kernel_aarch64_modules_prepare/modules_prepare_outdir.tar.gz"
        Bazel.check_call(["build", f"//{self._common()}:kernel_aarch64_modules_prepare"] + _FASTEST)
        first_hash = self._sha256(modules_prepare_archive)

        old_modules_archive = tempfile.NamedTemporaryFile()
        shutil.copyfile(modules_prepare_archive, old_modules_archive.name)

        self._touch_core_kernel_file()

        Bazel.check_call(["build", f"//{self._common()}:kernel_aarch64_modules_prepare"] + _FASTEST)
        second_hash = self._sha256(modules_prepare_archive)

        if first_hash != second_hash:
            old_modules_archive.delete = False

        self.assertEqual(first_hash, second_hash,
                         textwrap.dedent(f"""\
                             Check their content here:
                             old: {old_modules_archive.name}
                             new: {modules_prepare_archive}"""))

    def test_module_does_not_depend_on_vmlinux(self):
        """Tests that, the inputs for building a module does not include vmlinux and System.map.

        See b/254357038."""
        vd_modules = Bazel.check_output([
            "query",
            'kind("^_kernel_module rule$", //common-modules/virtual-device/...)'
        ]).splitlines()
        self.assertTrue(vd_modules)

        print(f"+ build/kernel/kleaf/analysis/inputs.py 'mnemonic(\"KernelModule.*\", {vd_modules[0]})'")
        input_to_module = analyze_inputs(aquery_args=[
                                                         f'mnemonic("KernelModule.*", {vd_modules[0]})'
                                                     ] + _FASTEST).keys()
        self.assertFalse(
            [path for path in input_to_module if pathlib.Path(path).name == "vmlinux"],
            "An external module must not depend on vmlinux")
        self.assertFalse(
            [path for path in input_to_module if pathlib.Path(path).name == "System.map"],
            "An external module must not depend on System.map")

    def test_incremental_switch_to_local(self):
        """Tests that switching from non-local to local works."""
        Bazel.check_call(["build", f"//{self._common()}:kernel_dist"] + _LTO_NONE)
        Bazel.check_call(["build", f"//{self._common()}:kernel_dist"] + _LTO_NONE + _LOCAL)

    def test_incremental_switch_to_non_local(self):
        """Tests that switching from local to non-local works."""
        Bazel.check_call(["build", f"//{self._common()}:kernel_dist"] + _LTO_NONE + _LOCAL)
        Bazel.check_call(["build", f"//{self._common()}:kernel_dist"] + _LTO_NONE)

    def test_change_lto_to_thin_when_local(self):
        """Tests that, with --config=local, changing from --lto=none to --lto=thin works.

        See b/257288175."""
        Bazel.check_call(["build", f"//{self._common()}:kernel_dist"] + _LOCAL + _LTO_NONE)
        Bazel.check_call(["build", f"//{self._common()}:kernel_dist"] + _LOCAL + [
            "--lto=thin"
        ])

    def test_change_lto_to_none_when_local(self):
        """Tests that, with --config=local, changing from --lto=thin to --lto=local works.

        See b/257288175."""
        Bazel.check_call(["build", f"//{self._common()}:kernel_dist"] + _LOCAL + [
            "--lto=thin"
        ])
        Bazel.check_call(["build", f"//{self._common()}:kernel_dist"] + _LOCAL + _LTO_NONE)


if __name__ == "__main__":
    arguments, unknown = load_arguments()
    sys.argv[1:] = unknown
    absltest.main()
