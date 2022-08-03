#!/usr/bin/env python3
# Copyright (C) 2022 The Android Open Source Project
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

# This test requires buildozer installed in $HOME, which is not accessible
# via `bazel test`. Hence, execute this test with
#   build/kernel/kleaf/build_config_to_bazel_test.py
# TODO(b/241320850): Move this to bazel py_test, then use:
#   absl.testing.parameterized
#   absltest.main

import os
import sys
import tempfile
import unittest
import unittest.mock

import build_config_to_bazel

_TEST_DATA = 'build/kernel/kleaf/tests/build_config_to_bazel_test_data'


class BuildConfigToBazelTest(unittest.TestCase):

    def setUp(self) -> None:
        self.environ = os.environ.copy()

        self.stdout = tempfile.TemporaryFile('w+')
        self.addCleanup(self.stdout.close)

        self.stderr = tempfile.TemporaryFile('w+')
        self.addCleanup(self.stderr.close)

    def _run_test(self, name, expected_list, expected_files, argv=()):
        """
        Args:
            name: build.config file name under test data
            expected_list: list of texts exepcted in the final output
            expected_files: dict, where keys are file names and values are
              expected content of the file
            argv: argv to build_config_to_bazel
        """
        self.environ['BUILD_CONFIG'] = f'{_TEST_DATA}/{name}'
        argv = ['--stdout'] + list(argv)

        with unittest.mock.patch.object(build_config_to_bazel.BuildozerCommandBuilder,
                                        '_create_extra_file') as create_extra_file:
            try:
                args = build_config_to_bazel.parse_args(argv)
                builder = build_config_to_bazel.BuildozerCommandBuilder(
                    args=args,
                    stdout=self.stdout,
                    stderr=self.stderr,
                    environ=self.environ)
                builder.run()
            except Exception:
                self.stderr.seek(0)
                sys.__stderr__.write(self.stderr.read())
                raise

            self.stdout.seek(0)
            out = self.stdout.read()

            for expected in expected_list:
                with self.subTest('expect output', expected=expected):
                    self.assertTrue(expected in out, f"{repr(expected)} not found in:\n{out}")

            for filename, content in expected_files.items():
                with self.subTest('expect file', filename=filename):
                    create_extra_file.assert_called_with(filename, content)

    def test_simple(self):
        expected_list = [
            'name = "simple"',
            '''srcs = glob(
        ["**"],
        exclude = [
            "**/.*",
            "**/.*/**",
            "**/BUILD.bazel",
            "**/*.bzl",
        ],
    ) + ["//common:kernel_aarch64_sources"],''',
            'build_config = "build.config.simple"',
            'name = "simple_dist"',
        ]
        self._run_test('build.config.simple', expected_list, {})

    def test_override_target_name(self):
        expected_list = [
            'name = "mytarget"',
            'name = "mytarget_dist"',
        ]
        self._run_test('build.config.simple', expected_list, {}, argv=['--target=mytarget'])

    def test_override_ack(self):
        expected_list = [
            # check base_kernel comments contains these
            '//ack:kernel_aarch64',
        ]
        self._run_test('build.config.simple', expected_list, {}, argv=['--ack=ack'])

    def test_no_hermetic_tools(self):
        expected_list = [
            # check that comments contains these
            '# FIXME: HERMETIC_TOOLCHAIN=0 not supported'
        ]
        self._run_test('build.config.no_hermetic_toolchain', expected_list, {})

    def test_everything(self):
        # Check defined targets
        expected_list = [
            '"everything"',
            '"everything_dist"',
            '"everything_images"',
            '"everything_dts"',
            '"everything_modules_install"',

            # BUILD_CONFIG
            'build_config = "build.config.everything"',
            # BUILD_CONFIG_FRAGMENTS
            # check that comments contains these
            'build.config.fragment',
            'kernel_build_config',
            # FAST_BUILD
            # check that comments contains these
            '--config=fast',
            # LTO
            # check that comments contains these
            '--lto=thin',
            # FILES
            '"myfile/myfile1"',
            '"myfile/myfile2"',
            # KCONFIG_EXT_PREFIX
            f'kconfig_ext = "{_TEST_DATA}"',
            # UNSTRIPPED_MODULES
            'collect_unstripped_modules = True',
            # KMI_SYMBOL_LIST
            'kmi_symbol_list = "//common:android/abi_symbollist_mydevice"',
            # ADDITIONAL_KMI_SYMBOL_LISTS
            '''additional_kmi_symbol_lists = [
        "//common:android/abi_symbollist_additional1",
        "//common:android/abi_symbollist_additional2",
    ],''',
            # TRIM_NONLISTED_KMI
            'trim_nonlisted_kmi = True',
            # KMI_SYMBOL_LIST_STRICT_MODE
            'kmi_symbol_list_strict_mode = True',
            # KBUILD_SYMTYPES
            'kbuild_symtypes = True',
            # GENERATE_VMLINUX_BTF
            'generate_vmlinux_btf = True',

            # BUILD_BOOT_IMG
            'build_boot = True',
            # BUILD_VENDOR_BOOT_IMG
            'build_vendor_boot = True',
            # BUILD_DTBO_IMG
            'build_dtbo = True',
            # BUILD_VENDOR_KERNEL_BOOT
            'build_vendor_kernel_boot = True',
            # BUILD_INITRAMFS
            'build_initramfs = True',
            # MKBOOTIMG_PATH
            # check that comments contains these
            'mymkbootimg',
            # MODULES_OPTIONS
            f'modules_options = "//{_TEST_DATA}:modules.options.everything"',

            # MODULES_BLOCKLIST
            'modules_blocklist = "modules_blocklist"',
            # MODULES_LIST
            'modules_list = "modules_list"',
            # SYSTEM_DLKM_MODULES_BLOCKLIST
            'system_dlkm_modules_blocklist = "system_dlkm_modules_blocklist"',
            # SYSTEM_DLKM_MODULES_LIST
            'system_dlkm_modules_list = "system_dlkm_modules_list"',
            # SYSTEM_DLKM_PROPS
            'system_dlkm_props = "system_dlkm_props"',
            # VENDOR_DLKM_MODULES_BLOCKLIST
            'vendor_dlkm_modules_blocklist = "vendor_dlkm_modules_blocklist"',
            # VENDOR_DLKM_MODULES_LIST
            'vendor_dlkm_modules_list = "vendor_dlkm_modules_list"',
            # VENDOR_DLKM_PROPS
            'vendor_dlkm_props = "vendor_dlkm_props"',

            # GKI_BUILD_CONFIG
            'base_kernel = "//common:kernel_aarch64"',
            # GKI_PREBUILTS_DIR
            # check that comments contains these
            'prebuilts/gki',

            # DTS_EXT_DIR
            f'dtstree = "//{_TEST_DATA}:everything_dts"',

            # BUILD_GKI_CERTIFICATION_TOOLS
            f'"//build/kernel:gki_certification_tools"',

            # UNKNOWN_BUILD_CONFIG
            f'# FIXME: Unknown in build config: UNKNOWN_BUILD_CONFIG=1',

            # TODO(b/241320850): Support these variables in build_config_to_bazel
            'EXT_MODULES',
            'ABI_DEFINITION',
            'KMI_ENFORCED',
        ]

        expected_files = {
            f'{_TEST_DATA}/modules.options.everything': '''
option foo param=value
option bar param=value
''',
        }

        self._run_test('build.config.everything', expected_list, expected_files)


if __name__ == '__main__':
    unittest.main(verbosity=2)
