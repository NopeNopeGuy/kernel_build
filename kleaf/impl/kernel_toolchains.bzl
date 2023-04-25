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

"""Helper for `kernel_env` to get toolchains for different platforms."""

load("@bazel_skylib//lib:shell.bzl", "shell")
load("@bazel_skylib//rules:common_settings.bzl", "BuildSettingInfo")
load(
    ":common_providers.bzl",
    "KernelEnvToolchainsInfo",
    "KernelPlatformToolchainInfo",
)
load("//prebuilts/clang/host/linux-x86/kleaf:versions.bzl", _CLANG_VERSIONS = "VERSIONS")

def _quote_prepend_cwd(value):
    """Prepends $PWD to value.

    Returns:
        quoted shell value
    """
    if not value.startswith("/"):
        return "${PWD}/" + shell.quote(value)
    return shell.quote(value)

def _get_declared_toolchain_version(ctx):
    declared_toolchain_version = None
    for version in _CLANG_VERSIONS:
        attr = getattr(ctx.attr, "_clang_version_{}".format(version))
        if ctx.target_platform_has_constraint(attr[platform_common.ConstraintValueInfo]):
            declared_toolchain_version = version
    return declared_toolchain_version

def _check_toolchain_version(ctx, resolved_toolchain_info, declared_toolchain_version, platform_name):
    if resolved_toolchain_info.compiler_version != declared_toolchain_version:
        fail("{}: Resolved to incorrect toolchain for {} platform. Expected: {}, actual: {}".format(
            ctx.label,
            platform_name,
            declared_toolchain_version,
            resolved_toolchain_info.compiler_version,
        ))

def _get_target_arch(ctx):
    if ctx.target_platform_has_constraint(ctx.attr._platform_cpu_arm64[platform_common.ConstraintValueInfo]):
        return ctx.attr._platform_cpu_arm64.label.name
    elif ctx.target_platform_has_constraint(ctx.attr._platform_cpu_x86_64[platform_common.ConstraintValueInfo]):
        return ctx.attr._platform_cpu_x86_64.label.name
    elif ctx.target_platform_has_constraint(ctx.attr._platform_cpu_riscv64[platform_common.ConstraintValueInfo]):
        return ctx.attr._platform_cpu_riscv64.label.name
    fail("{}: Cannot determine target platform.".format(ctx.label))

def _quote_sanitize_flags(flags):
    """Turns paths into ones relative to $PWD for each flag.

    Kbuild executes the compiler in subdirectories, hence an absolute path is needed.

    Returns:
        quoted shell value
    """

    result_quoted_flags = []

    prev = None
    for _index, flag in enumerate(flags):
        if prev in ("--sysroot", "-I", "-iquote", "-isystem"):
            result_quoted_flags.append(_quote_prepend_cwd(flag))
        elif flag.startswith("--sysroot="):
            key, value = flag.split("=", 2)
            result_quoted_flags.append("{}={}".format(key, _quote_prepend_cwd(value)))
        elif flag.startswith("-I"):
            key, value = flag[:2], flag[2:]
            result_quoted_flags.append("{}{}".format(key, _quote_prepend_cwd(value)))
        else:
            result_quoted_flags.append(shell.quote(flag))
        prev = flag

    return "' '".join(result_quoted_flags)

def _kernel_toolchains_impl(ctx):
    # FIXME what about toolchain_version.startswith("//build/kernel/kleaf/tests/")
    exec = ctx.attr.exec_toolchain[KernelPlatformToolchainInfo]
    target = ctx.attr.target_toolchain[KernelPlatformToolchainInfo]

    declared_toolchain_version = _get_declared_toolchain_version(ctx)
    _check_toolchain_version(ctx, exec, declared_toolchain_version, "exec")
    _check_toolchain_version(ctx, target, declared_toolchain_version, "target")

    all_files = depset(transitive = [exec.all_files, target.all_files])
    target_arch = _get_target_arch(ctx)

    quoted_bin_paths = [
        _quote_prepend_cwd(exec.bin_path),
        _quote_prepend_cwd(target.bin_path),
    ]

    setup_env_var_cmd = """
        export PATH={quoted_bin_paths}:${{PATH}}
    """.format(
        quoted_bin_paths = ":".join(quoted_bin_paths)
    )
    if ctx.attr._kernel_use_resolved_toolchains[BuildSettingInfo].value:
        setup_env_var_cmd += """
            export HOSTCFLAGS={quoted_hostcflags}
            export USERCFLAGS={quoted_usercflags}
        """.format(
            quoted_hostcflags = _quote_sanitize_flags(exec.cflags),
            quoted_usercflags = _quote_sanitize_flags(target.cflags),
        )

    # Kleaf clang bins are under kleaf/parent, so CLANG_PREBUILT_BIN in
    # build.config.common is incorrect. Manually set additional PATH's.

    return KernelEnvToolchainsInfo(
        all_files = all_files,
        target_arch = target_arch,
        setup_env_var_cmd = setup_env_var_cmd,
    )

kernel_toolchains = rule(
    doc = """Helper for `kernel_env` to get toolchains for different platforms.""",
    implementation = _kernel_toolchains_impl,
    attrs = {
        "exec_toolchain": attr.label(
            cfg = "exec",
            providers = [KernelPlatformToolchainInfo],
        ),
        "target_toolchain": attr.label(
            providers = [KernelPlatformToolchainInfo],
        ),
        "_kernel_use_resolved_toolchains": attr.label(
            default = "//build/kernel/kleaf:experimental_kernel_use_resolved_toolchains",
        ),
        "_platform_cpu_arm64": attr.label(default = "@platforms//cpu:arm64"),
        "_platform_cpu_x86_64": attr.label(default = "@platforms//cpu:x86_64"),
        "_platform_cpu_riscv64": attr.label(default = "@platforms//cpu:riscv64"),
    } | {
        "_clang_version_{}".format(version): attr.label(default = "//prebuilts/clang/host/linux-x86/kleaf:{}".format(version))
        for version in _CLANG_VERSIONS
    },
)
