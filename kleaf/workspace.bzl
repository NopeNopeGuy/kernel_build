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

"""
Defines repositories in a Kleaf workspace.
"""

load("//build/bazel_common_rules/workspace:external.bzl", "import_external_repositories")
load("//build/kernel/kleaf:key_value_repo.bzl", "key_value_repo")
load(
    "//build/kernel/kleaf/impl:kernel_prebuilt_repo.bzl",
    "kernel_prebuilt_repo",
)
load(
    "//build/kernel/kleaf/impl:kernel_prebuilt_utils.bzl",
    "CI_TARGET_MAPPING",
)
load("//build/kernel/kleaf/impl:kleaf_host_tools_repo.bzl", "kleaf_host_tools_repo")
load("//prebuilts/clang/host/linux-x86/kleaf:register.bzl", "register_clang_toolchains")

# buildifier: disable=unnamed-macro
def define_kleaf_workspace(
        common_kernel_package = None,
        include_remote_java_tools_repo = False,
        artifact_url_fmt = None,
        kleaf_repo_name = None):
    """Common macro for defining repositories in a Kleaf workspace.

    **This macro must only be called from `WORKSPACE` or `WORKSPACE.bazel`
    files, not `BUILD` or `BUILD.bazel` files!**

    If [`define_kleaf_workspace_epilog`](#define_kleaf_workspace_epilog) is
    called, it must be called after `define_kleaf_workspace` is called.

    Args:
      common_kernel_package: Default is `"@//common"`. The package to the common
        kernel source tree.

        As a legacy behavior, if the provided string does not start with
        `@` or `//`, it is prepended with `@//`.

        Do not provide the trailing `/`.
      include_remote_java_tools_repo: Default is `False`. Whether to vendor two extra
        repositories: remote_java_tools and remote_java_tools_linux.

        These respositories should exist under `//prebuilts/bazel/`
      artifact_url_fmt: API endpoint for Android CI artifacts.
        The format may include anchors for the following properties:
          * {build_number}
          * {target}
          * {filename}
      kleaf_repo_name: Name of the Kleaf repository. Default is "@".
    """
    if kleaf_repo_name == None:
        kleaf_repo_name = "@"
    if not kleaf_repo_name.startswith("@"):
        fail("Invalid kleaf_repo_name. It must start with @")

    if common_kernel_package == None:
        common_kernel_package = "{}//common".format(kleaf_repo_name)
    if not common_kernel_package.startswith("@") and not common_kernel_package.startswith("//"):
        common_kernel_package = kleaf_repo_name + "//" + common_kernel_package

        # buildifier: disable=print
        print("""
WARNING: define_kleaf_workspace() should be called with common_kernel_package={}.
    This will become an error in the future.""".format(
            repr(common_kernel_package),
        ))

    import_external_repositories(
        # keep sorted
        bazel_skylib = True,
        io_abseil_py = True,
        io_bazel_stardoc = True,
    )

    # Superset of all tools we need from host.
    # For the subset of host tools we typically use for a kernel build,
    # see //build/kernel:hermetic-tools.
    kleaf_host_tools_repo(
        name = "kleaf_host_tools",
        host_tools = [
            "bash",
            "perl",
            "rsync",
            "sh",
            # For BTRFS (b/292212788)
            "find",
        ],
    )

    # The prebuilt NDK does not support Bazel.
    # https://docs.bazel.build/versions/main/external.html#non-bazel-projects
    native.new_local_repository(
        name = "prebuilt_ndk",
        path = "prebuilts/ndk-r23",
        build_file = "build/kernel/kleaf/ndk.BUILD",
    )

    key_value_repo(
        name = "kernel_toolchain_info",
        srcs = ["{}:build.config.constants".format(common_kernel_package)],
        additional_values = {
            "common_kernel_package": common_kernel_package,
        },
    )

    for repo_name in CI_TARGET_MAPPING:
        kernel_prebuilt_repo(
            name = repo_name,
            artifact_url_fmt = artifact_url_fmt,
        )

    # TODO(b/200202912): Re-route this when rules_python is pulled into AOSP.
    native.local_repository(
        name = "rules_python",
        path = "build/bazel_common_rules/rules/python/stubs",
    )

    # The following 2 repositories contain prebuilts that are necessary to the Java Rules.
    # They are vendored locally to avoid the need for CI bots to download them.
    if include_remote_java_tools_repo:
        native.local_repository(
            name = "remote_java_tools",
            path = "prebuilts/bazel/common/remote_java_tools",
        )

        native.local_repository(
            name = "remote_java_tools_linux",
            path = "prebuilts/bazel/linux-x86_64/remote_java_tools_linux",
        )

    # Use checked-in JDK from prebuilts as local_jdk
    #   Needed for stardoc
    # Note: This was not added directly to avoid conflicts with roboleaf,
    #   see https://android-review.googlesource.com/c/platform/build/bazel/+/2457390
    #   for more details.
    native.new_local_repository(
        name = "local_jdk",
        path = "prebuilts/jdk/jdk11/linux-x86",
        build_file = "build/kernel/kleaf/jdk11.BUILD",
    )

    # Fake rules_cc to avoid fetching it for any py_binary targets.
    native.local_repository(
        name = "rules_cc",
        path = "build/kernel/kleaf/impl/fake_rules_cc",
    )

    # Stub out @remote_coverage_tools required for testing.
    native.local_repository(
        name = "remote_coverage_tools",
        path = "build/bazel_common_rules/rules/coverage/remote_coverage_tools",
    )

    # Use checked-in JDK from prebuilts as local_jdk
    #   Needed for stardoc
    native.register_toolchains(
        "@local_jdk//:all",
    )

    native.register_toolchains(
        "{}//prebuilts/build-tools:py_toolchain".format(kleaf_repo_name),
        "{}//build/kernel:hermetic_tools_toolchain".format(kleaf_repo_name),
    )

    register_clang_toolchains(kleaf_repo_name = kleaf_repo_name,)
