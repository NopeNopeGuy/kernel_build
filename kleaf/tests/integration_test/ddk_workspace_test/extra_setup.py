# Copyright (C) 2024 The Android Open Source Project
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

# TODO: This functionality should be moved into init_ddk.py

"""Script that fix-ups files generated by init_ddk.py for testing."""

import argparse
import dataclasses
import pathlib
import textwrap


@dataclasses.dataclass
class DdkExtraSetup:
    """Additional fix-ups after init_ddk.py for integration tests."""

    # path to @kleaf. Its value will be used as-is.
    kleaf_repo_rel: pathlib.Path

    # path to DDK workspace. Its value will be used as-is.
    ddk_workspace: pathlib.Path

    def _generate_device_bazelrc(self):
        # TODO(b/338439996): Use offline flag in init_ddk.py instead.
        path = self.ddk_workspace / "device.bazelrc"
        with path.open("a") as out_file:
            print("common --config=no_internet", file=out_file)

    def _generate_module_bazel(self):
        path = self.ddk_workspace / "MODULE.bazel"
        with path.open("a") as out_file:
            print(textwrap.dedent("""\
                bazel_dep(name = "bazel_skylib")
            """), file=out_file)

            # Copy local_path_override() from @kleaf because we do not
            # have Internet on CI.
            # TODO(b/338439996): Use offline flag in init_ddk.py instead.
            with (self.ddk_workspace / self.kleaf_repo_rel /
                  "MODULE.bazel").open() as src:
                self._copy_local_path_override(src, out_file)

    def _copy_local_path_override(self, src, dst):
        """Naive algorithm to parse src and copy local_path_override() to dst"""
        section = []
        path_attr_prefix = 'path = "'

        # Modify path so it is relative to the current DDK workspace.
        # TODO(b/338439996): Use offline flag in init_ddk.py instead.
        for line in src:
            if line.startswith("local_path_override("):
                section.append(line)
                continue
            if section:
                if line.lstrip().startswith(path_attr_prefix):
                    line = line.strip()
                    line = line.removeprefix(
                        path_attr_prefix).removesuffix('",')
                    line = f'    path = "{self.kleaf_repo_rel / line}",\n'
                section.append(line)

                if line.strip() == ")":
                    print("".join(section), file=dst)
                    section.clear()

    def run(self):
        self._generate_device_bazelrc()
        self._generate_module_bazel()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kleaf_repo_rel",
                        type=pathlib.Path,
                        help="If relative, it is against ddk_workspace",
                        )
    parser.add_argument("--ddk_workspace",
                        type=pathlib.Path,
                        help="If relative, it is against cwd",)
    args = parser.parse_args()
    DdkExtraSetup(**vars(args)).run()
