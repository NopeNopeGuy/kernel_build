# Copyright (C) 2021 The Android Open Source Project
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

load("@bazel_skylib//lib:dicts.bzl", "dicts")
load("@bazel_skylib//lib:paths.bzl", "paths")
load("@bazel_skylib//rules:common_settings.bzl", "bool_flag")
load(
    ":kernel.bzl",
    "kernel_build",
    "kernel_build_abi",
    "kernel_build_abi_dist",
    "kernel_compile_commands",
    "kernel_filegroup",
    "kernel_images",
    "kernel_kythe",
    "kernel_modules_install",
    "kernel_unstripped_modules_archive",
    "merged_kernel_uapi_headers",
)
load("//build/bazel_common_rules/dist:dist.bzl", "copy_to_dist_dir")
load("//build/kernel/kleaf/artifact_tests:kernel_test.bzl", "initramfs_modules_options_test")
load("//build/kernel/kleaf/artifact_tests:device_modules_test.bzl", "device_modules_test")
load("//build/kernel/kleaf/impl:gki_artifacts.bzl", "gki_artifacts")
load("//build/kernel/kleaf/impl:out_headers_allowlist_archive.bzl", "out_headers_allowlist_archive")
load("//build/kernel/kleaf/impl:utils.bzl", "utils")
load(
    "//build/kernel/kleaf/impl:constants.bzl",
    "MODULE_OUTS_FILE_OUTPUT_GROUP",
    "MODULE_OUTS_FILE_SUFFIX",
    "TOOLCHAIN_VERSION_FILENAME",
)
load(
    ":constants.bzl",
    "CI_TARGET_MAPPING",
    "GKI_DOWNLOAD_CONFIGS",
    "aarch64_outs",
    "x86_64_outs",
)
load(":print_debug.bzl", "print_debug")
load("@kernel_toolchain_info//:dict.bzl", "BRANCH", "common_kernel_package")

_ARCH_CONFIGS = {
    "kernel_aarch64": {
        "arch": "arm64",
        "build_config": "build.config.gki.aarch64",
        "outs": aarch64_outs,
    },
    "kernel_aarch64_16k": {
        "arch": "arm64",
        "build_config": "build.config.gki.aarch64.16k",
        "outs": aarch64_outs,
    },
    "kernel_aarch64_interceptor": {
        "arch": "arm64",
        "build_config": "build.config.gki.aarch64",
        "outs": aarch64_outs,
        "enable_interceptor": True,
    },
    "kernel_aarch64_debug": {
        "arch": "arm64",
        "build_config": "build.config.gki-debug.aarch64",
        "outs": aarch64_outs,
    },
    "kernel_x86_64": {
        "arch": "x86_64",
        "build_config": "build.config.gki.x86_64",
        "outs": x86_64_outs,
    },
    "kernel_x86_64_debug": {
        "arch": "x86_64",
        "build_config": "build.config.gki-debug.x86_64",
        "outs": x86_64_outs,
    },
}

# Subset of _TARGET_CONFIG_VALID_KEYS for kernel_build_abi.
_KERNEL_BUILD_ABI_VALID_KEYS = [
    "kmi_symbol_list",
    "additional_kmi_symbol_lists",
    "trim_nonlisted_kmi",
    "kmi_symbol_list_strict_mode",
    "abi_definition",
    "kmi_enforced",
    "module_implicit_outs",
]

# Valid configs of the value of the target_config argument in
# `define_common_kernels`
_TARGET_CONFIG_VALID_KEYS = _KERNEL_BUILD_ABI_VALID_KEYS + [
    "build_gki_artifacts",
    "gki_boot_img_sizes",
]

# Always collect_unstripped_modules for common kernels.
_COLLECT_UNSTRIPPED_MODULES = True

# glob() must be executed in a BUILD thread, so this cannot be a global
# variable.
def _default_target_configs():
    """Return the default value of `target_configs` of [`define_common_kernels()`](#define_common_kernels).
    """
    aarch64_kmi_symbol_list = native.glob(["android/abi_gki_aarch64"])
    aarch64_kmi_symbol_list = aarch64_kmi_symbol_list[0] if aarch64_kmi_symbol_list else None
    aarch64_additional_kmi_symbol_lists = native.glob(
        ["android/abi_gki_aarch64*"],
        exclude = ["**/*.xml", "android/abi_gki_aarch64"],
    )
    aarch64_trim_and_check = bool(aarch64_kmi_symbol_list) or len(aarch64_additional_kmi_symbol_lists) > 0
    aarch64_abi_definition = native.glob(["android/abi_gki_aarch64.xml"])
    aarch64_abi_definition = aarch64_abi_definition[0] if aarch64_abi_definition else None

    # Common configs for aarch64 and aarch64_debug
    aarch64_common = {
        # Assume the value for KMI_SYMBOL_LIST, ADDITIONAL_KMI_SYMBOL_LISTS, ABI_DEFINITION, and KMI_ENFORCED
        # for build.config.gki.aarch64
        "kmi_symbol_list": aarch64_kmi_symbol_list,
        "additional_kmi_symbol_lists": aarch64_additional_kmi_symbol_lists,
        "abi_definition": aarch64_abi_definition,
        "kmi_enforced": bool(aarch64_abi_definition),
        # Assume BUILD_GKI_ARTIFACTS=1
        "build_gki_artifacts": True,
        "gki_boot_img_sizes": {
            # Assume BUILD_GKI_BOOT_IMG_SIZE is the following
            "": "67108864",
            # Assume BUILD_GKI_BOOT_IMG_LZ4_SIZE is the following
            "lz4": "53477376",
            # Assume BUILD_GKI_BOOT_IMG_GZ_SIZE is the following
            "gz": "47185920",
        },
    }

    # Common configs for x86_64 and x86_64_debug
    x86_64_common = {
        # Assume BUILD_GKI_ARTIFACTS=1
        "build_gki_artifacts": True,
        "gki_boot_img_sizes": {
            # Assume BUILD_GKI_BOOT_IMG_SIZE is the following
            "": "67108864",
        },
    }

    return {
        "kernel_aarch64": dicts.add(aarch64_common, {
            # In build.config.gki.aarch64:
            # - If there are symbol lists: assume TRIM_NONLISTED_KMI=${TRIM_NONLISTED_KMI:-1}
            # - If there aren't:           assume TRIM_NONLISTED_KMI unspecified
            "trim_nonlisted_kmi": aarch64_trim_and_check,
            "kmi_symbol_list_strict_mode": aarch64_trim_and_check,
        }),
        "kernel_aarch64_16k": {
            # Assume TRIM_NONLISTED_KMI="" in build.config.gki.aarch64.16k
            "trim_nonlisted_kmi": False,
        },
        "kernel_aarch64_debug": dicts.add(aarch64_common, {
            # Assume TRIM_NONLISTED_KMI="" in build.config.gki-debug.aarch64
            "trim_nonlisted_kmi": False,
        }),
        "kernel_x86_64": x86_64_common,
        "kernel_x86_64_debug": dicts.add(x86_64_common, {
            # Assume TRIM_NONLISTED_KMI="" in build.config.gki-debug.x86_64
            "trim_nonlisted_kmi": False,
        }),
    }

def _filter_keys(d, valid_keys, what = "", allow_unknown_keys = False):
    """Remove keys from `d` if the key is not in `valid_keys`.

    Fail if there are unknown keys in `d`.
    """
    ret = {key: value for key, value in d.items() if key in valid_keys}
    if not allow_unknown_keys and sorted(ret.keys()) != sorted(d.keys()):
        fail("{what} contains invalid keys {invalid_keys}. Valid keys are: {valid_keys}".format(
            what = what,
            invalid_keys = [key for key in d.keys() if key not in valid_keys],
            valid_keys = valid_keys,
        ))
    return ret

def define_common_kernels(
        branch = None,
        target_configs = None,
        toolchain_version = None,
        visibility = None):
    """Defines common build targets for Android Common Kernels.

    This macro expands to the commonly defined common kernels (such as the GKI
    kernels and their variants. They are defined based on the conventionally
    used `BUILD_CONFIG` file and produce usual output files.

    Targets declared for kernel build (parent list item depends on child list item):
    - `kernel_aarch64_sources`
    - `kernel_aarch64_dist`
      - `kernel_aarch64`
      - `kernel_aarch64_uapi_headers`
      - `kernel_aarch64_additional_artifacts`
      - `kernel_aarch64_modules`
    - `kernel_aarch64_16k_dist`
      - `kernel_aarch64_16k`
      - `kernel_aarch64_modules`
    - `kernel_aarch64_debug_dist`
      - `kernel_aarch64_debug`
    - `kernel_x86_64_sources`
    - `kernel_x86_64_dist`
      - `kernel_x86_64`
      - `kernel_x86_64_uapi_headers`
      - `kernel_x86_64_additional_artifacts`
    - `kernel_x86_64_debug_dist`
      - `kernel_x86_64_debug`

    `<name>` (aka `kernel_{aarch64,x86}{_16k,_debug}`) targets build the
    main kernel build artifacts, e.g. `vmlinux`, etc.

    `<name>_sources` are convenience filegroups that refers to all sources required to
    build `<name>` and related targets.

    `<name>_uapi_headers` targets build `kernel-uapi-headers.tar.gz`.

    `<name>_additional_artifacts` contains additional artifacts that may be added to
    a distribution. This includes:
      - Images, including `system_dlkm`, etc.
      - `kernel-headers.tar.gz`

    `<name>_dist` targets can be run to obtain a distribution outside the workspace.

    Aliases are created to refer to the GKI kernel (`kernel_aarch64`) as
    "`kernel`" and the corresponding dist target (`kernel_aarch64_dist`) as
    "`kernel_dist`".

    Targets declared for cross referencing:
    - `kernel_aarch64_kythe_dist`
      - `kernel_aarch64_kythe`

    Targets declared for Bazel rules analysis for debugging purposes:
    - `kernel_aarch64_print_configs`
    - `kernel_aarch64_debug_print_configs`
    - `kernel_x86_64_print_configs`
    - `kernel_x86_64_debug_print_configs`

    **ABI monitoring**
    On branches with ABI monitoring turned on (aka KMI symbol lists are checked
    in; see argument `target_configs`), the following targets are declared:

    - `kernel_aarch64_abi`

    See [`kernel_build_abi()`](#kernel_build_abi) for details.

    **Prebuilts**

    You may set the argument `--use_prebuilt_gki` to a GKI prebuilt build number
    on [ci.android.com](http://ci.android.com). The format is:

    ```
    bazel <command> --use_prebuilt_gki=<build_number> <targets>
    ```

    For example, the following downloads GKI artifacts of build number 8077484 (assuming
    the current package is `//common`):

    ```
    bazel build --use_prebuilt_gki=8077484 //common:kernel_aarch64_download_or_build
    ```

    If you leave out the `--use_prebuilt_gki` argument, the command is equivalent to
    `bazel build //common:kernel_aarch64`, which builds kernel from source.

    `<name>_download_or_build` targets builds `<name>` from source if the `use_prebuilt_gki`
    is not set, and downloads artifacts of the build number from
    [ci.android.com](http://ci.android.com) if it is set. The build number is spe

    - `kernel_aarch64_download_or_build`
      - `kernel_aarch64_additional_artifacts_download_or_build`
      - `kernel_aarch64_uapi_headers_download_or_build`

    Note: If a device should build against downloaded prebuilts unconditionally, set
    `--//<package>:use_prebuilt_gki` and a fixed build number in `device.bazelrc`. For example:
    ```
    # device.bazelrc
    build --//common:use_prebuilt_gki
    build --action_env=KLEAF_DOWNLOAD_BUILD_NUMBER_MAP="gki_prebuilts=8077484"
    ```

    This is equivalent to specifying `--use_prebuilt_gki=8077484` for all Bazel commands.

    Args:
      branch: The value of `BRANCH` in `build.config`. If not set, it is loaded
        from `common/build.config.constants` **in `//{common_kernel_packgae}`**
        where `common_kernel_package` is supplied to `define_kleaf_workspace()`
        in the `WORKSPACE` file. Usually, `common_kernel_package = "common"`.
        Hence, if `define_common_kernels()` is called in a different package, it
        must be supplied.
      target_configs: A dictionary, where keys are target names, and
        values are a dictionary of configurations to override the default
        configuration for this target.

        The content of `target_configs` should match the following variables in
        `build.config.gki{,-debug}.{aarch64,x86_64}`:
        - `KMI_SYMBOL_LIST`
        - `ADDITIONAL_KMI_SYMBOL_LISTS`
        - `TRIM_NONLISTED_KMI`
        - `KMI_SYMBOL_LIST_STRICT_MODE`
        - `GKI_MODULES_LIST` (corresponds to [`kernel_build.module_implicit_outs`](#kernel_build-module_implicit_outs))
        - `BUILD_GKI_ARTIFACTS`
        - `BUILD_GKI_BOOT_IMG_SIZE` and `BUILD_GKI_BOOT_IMG_{COMPRESSION}_SIZE`

        The keys of the `target_configs` may be one of the following:
        - `kernel_aarch64`
        - `kernel_aarch64_16k`
        - `kernel_aarch64_debug`
        - `kernel_x86_64`
        - `kernel_x86_64_debug`

        The values of the `target_configs` should be a dictionary, where keys
        are one of the following, and values are passed to the corresponding
        argument in [`kernel_build`](#kernel_build):
        - `kmi_symbol_list`
        - `additional_kmi_symbol_lists`
        - `trim_nonlisted_kmi`
        - `kmi_symbol_list_strict_mode`
        - `module_implicit_outs` (corresponds to `GKI_MODULES_LIST`)

        In addition, the values of `target_configs` may contain the following keys:
        - `build_gki_artifacts`
        - `gki_boot_img_sizes` (corresponds to `BUILD_GKI_BOOT_IMG_SIZE` and `BUILD_GKI_BOOT_IMG_{COMPRESSION}_SIZE`)
          - This is a dictionary where keys are lower-cased compression algorithm (e.g. `"lz4"`)
            and values are sizes (e.g. `BUILD_GKI_BOOT_IMG_LZ4_SIZE`).
            The empty-string key `""` corresponds to `BUILD_GKI_BOOT_IMG_SIZE`.

        A target is configured as follows. A configuration item for this target
        is determined by the following, in the following order:

        1. `target_configs[target_name][configuration_item]`, if it exists;
        2. `default_target_configs[target_name][configuration_item]`, if it exists, where
           `default_target_configs` contains sensible defaults. See below.
        3. `None`

        For example, to determine the value of `kmi_symbol_list` of `kernel_aarch64`:

        ```
        if "kernel_aarch64" in target_configs and "kmi_symbol_list" in target_configs["kernel_aarch64"]:
            value = target_configs["kernel_aarch64"]["kmi_symbol_list"]
            # Note: if `target_configs["kernel_aarch64"]["kmi_symbol_list"] == None`, it'll be passed
            # as None, regardless of value in default_target_configs
        elif "kernel_aarch64" in default_target_configs and "kmi_symbol_list" in default_target_configs["kernel_aarch64"]:
            value = default_target_configs["kernel_aarch64"]["kmi_symbol_list"]
        else:
            value = None

        kernel_build(..., kmi_symbol_list = value)
        ```

        The `default_target_configs` above contains sensible defaults:
        - `kernel_aarch64`:
          - `kmi_symbol_list = "android/abi_gki_aarch64"` if the file exist, else `None`
          - `additional_kmi_symbol_list = glob(["android/abi_gki_aarch64*"])` excluding `kmi_symbol_list` and XMLs
          - `TRIM_NONLISTED_KMI=${TRIM_NONLISTED_KMI:-1}` in `build.config` if there are symbol lists, else empty
          - `KMI_SYMBOL_LIST_STRICT_MODE=${KMI_SYMBOL_LIST_STRICT_MODE:-1}` in `build.config` if there are symbol lists, else empty
        - `kernel_aarch64_16k`:
          - No `kmi_symbol_list` nor `additional_kmi_symbol_lists`
          - `TRIM_NONLISTED_KMI` is not specified in `build.config`
          - `KMI_SYMBOL_LIST_STRICT_MODE` is not specified in `build.config`
        - `kernel_aarch64_debug`:
          - `kmi_symbol_list = "android/abi_gki_aarch64"` if the file exist, else `None`
          - `additional_kmi_symbol_list = glob(["android/abi_gki_aarch64*"])` excluding `kmi_symbol_list` and XMLs
          - `TRIM_NONLISTED_KMI=""` in `build.config`
          - `KMI_SYMBOL_LIST_STRICT_MODE=""` in `build.config`
        - `kernel_x86_64`:
          - No `kmi_symbol_list` nor `additional_kmi_symbol_lists`
          - `TRIM_NONLISTED_KMI` is not specified in `build.config`
          - `KMI_SYMBOL_LIST_STRICT_MODE` is not specified in `build.config`
        - `kernel_x86_64_debug`:
          - No `kmi_symbol_list` nor `additional_kmi_symbol_lists`
          - `TRIM_NONLISTED_KMI=""` in `build.config`
          - `KMI_SYMBOL_LIST_STRICT_MODE` is not specified in `build.config`

        That is, the default value is:
        ```
        aarch64_kmi_symbol_list = glob(["android/abi_gki_aarch64"])
        aarch64_kmi_symbol_list = aarch64_kmi_symbol_list[0] if aarch64_kmi_symbol_list else None
        aarch64_additional_kmi_symbol_lists = glob(
            ["android/abi_gki_aarch64*"],
            exclude = ["**/*.xml", "android/abi_gki_aarch64"],
        )
        aarch64_trim_and_check = bool(aarch64_kmi_symbol_list) or len(aarch64_additional_kmi_symbol_lists) > 0
        default_target_configs = {
            "kernel_aarch64": {
                "kmi_symbol_list": aarch64_kmi_symbol_list,
                "additional_kmi_symbol_lists": aarch64_additional_kmi_symbol_lists,
                "trim_nonlisted_kmi": aarch64_trim_and_check,
                "kmi_symbol_list_strict_mode": aarch64_trim_and_check,
            },
            "kernel_aarch64_16k": {
            },
            "kernel_aarch64_debug": {
                "kmi_symbol_list": aarch64_kmi_symbol_list,
                "additional_kmi_symbol_lists": aarch64_additional_kmi_symbol_lists,
                "trim_nonlisted_kmi": False,
            },
            "kernel_x86_64": {
            },
            "kernel_x86_64_debug": {
                "trim_nonlisted_kmi": False,
            },
        }
        ```

        If `target_configs` is not set explicitly in `define_common_kernels()`:

        |                                   |trim?         |
        |-----------------------------------|--------------|
        |`kernel_aarch64`                   |TRIM          |
        |(with symbol lists)                |              |
        |(`trim_nonlisted_kmi=True`)        |              |
        |-----------------------------------|--------------|
        |`kernel_aarch64`                   |NO TRIM       |
        |(no symbol lists)                  |              |
        |(`trim_nonlisted_kmi=None`)        |              |
        |-----------------------------------|--------------|
        |`kernel_aarch64_16k`               |NO TRIM       |
        |(`trim_nonlisted_kmi=None`)        |              |
        |-----------------------------------|--------------|
        |`kernel_aarch64_debug`             |NO TRIM       |
        |(`trim_nonlisted_kmi=False`)       |              |
        |-----------------------------------|--------------|
        |`kernel_x86_64`                    |NO TRIM       |
        |(`trim_nonlisted_kmi=None`)        |              |
        |-----------------------------------|--------------|
        |`kernel_x86_64_debug`              |NO TRIM       |
        |(`trim_nonlisted_kmi=False`)       |              |

        To print the actual configurations for debugging purposes for e.g.
        `//common:kernel_aarch64`:

        ```
        bazel build //common:kernel_aarch64_print_configs
        ```

      toolchain_version: If not set, use default value in `kernel_build`.
      visibility: visibility of the `kernel_build` and targets defined for downloaded prebuilts.
        If unspecified, its value is `["//visibility:public"]`.

        See [`visibility`](https://docs.bazel.build/versions/main/visibility.html).
    """

    if branch == None and native.package_name() == common_kernel_package:
        branch = BRANCH
    if branch == None:
        fail("//{package}: define_common_kernels() must have branch argument because @kernel_toolchain_info reads value from //{common_kernel_package}".format(
            package = native.package_name(),
            common_kernel_package = common_kernel_package,
        ))

    if visibility == None:
        visibility = ["//visibility:public"]

    default_target_configs = None  # _default_target_configs is lazily evaluated.
    if target_configs == None:
        target_configs = {}
    for name in _ARCH_CONFIGS.keys():
        target_configs[name] = _filter_keys(
            target_configs.get(name, {}),
            valid_keys = _TARGET_CONFIG_VALID_KEYS,
            what = '//{package}:{name}: target_configs["{name}"]'.format(
                package = native.package_name(),
                name = name,
            ),
        )
        for key in _TARGET_CONFIG_VALID_KEYS:
            if key not in target_configs[name]:
                # Lazily evaluate default_target_configs
                if default_target_configs == None:
                    default_target_configs = _default_target_configs()
                target_configs[name][key] = default_target_configs.get(name, {}).get(key)

    native.filegroup(
        name = "common_kernel_sources",
        srcs = native.glob(
            ["**"],
            exclude = [
                "BUILD.bazel",
                "**/*.bzl",
                ".git/**",
            ],
        ),
    )

    for name, arch_config in _ARCH_CONFIGS.items():
        native.alias(
            name = name + "_sources",
            actual = ":common_kernel_sources",
        )

        target_config = target_configs[name]

        all_kmi_symbol_lists = target_config.get("additional_kmi_symbol_lists")
        all_kmi_symbol_lists = [] if all_kmi_symbol_lists == None else list(all_kmi_symbol_lists)
        if target_config.get("kmi_symbol_list"):
            all_kmi_symbol_lists.append(target_config.get("kmi_symbol_list"))
        native.filegroup(
            name = name + "_all_kmi_symbol_lists",
            srcs = all_kmi_symbol_lists,
        )

        print_debug(
            name = name + "_print_configs",
            content = json.encode_indent(target_config, indent = "    ").replace("null", "None"),
            tags = ["manual"],
        )

        kernel_build_abi_kwargs = _filter_keys(
            target_config,
            valid_keys = _KERNEL_BUILD_ABI_VALID_KEYS,
            allow_unknown_keys = True,
        )

        kernel_build_abi(
            name = name,
            srcs = [name + "_sources"],
            outs = arch_config["outs"],
            implicit_outs = [
                # Kernel build time module signining utility and keys
                # Only available during GKI builds
                # Device fragments need to add: '# CONFIG_MODULE_SIG_ALL is not set'
                "scripts/sign-file",
                "certs/signing_key.pem",
                "certs/signing_key.x509",
            ],
            build_config = arch_config["build_config"],
            enable_interceptor = arch_config.get("enable_interceptor"),
            visibility = visibility,
            define_abi_targets = bool(target_config.get("kmi_symbol_list")),
            # Sync with KMI_SYMBOL_LIST_MODULE_GROUPING
            module_grouping = None,
            collect_unstripped_modules = _COLLECT_UNSTRIPPED_MODULES,
            toolchain_version = toolchain_version,
            **kernel_build_abi_kwargs
        )

        if arch_config.get("enable_interceptor"):
            continue

        # A subset of headers in OUT_DIR that only contains scripts/. This is useful
        # for DDK headers interpolation.
        out_headers_allowlist_archive(
            name = name + "_script_headers",
            kernel_build = name,
            subdirs = ["scripts"],
        )

        kernel_modules_install(
            name = name + "_modules_install",
            # The GKI target does not have external modules. GKI modules goes
            # into the in-tree kernel module list, aka kernel_build.module_implicit_outs.
            # Hence, this is empty.
            kernel_modules = [],
            kernel_build = name,
        )

        kernel_unstripped_modules_archive(
            name = name + "_unstripped_modules_archive",
            kernel_build = name,
        )

        kernel_images(
            name = name + "_images",
            kernel_build = name,
            kernel_modules_install = name + "_modules_install",
            # Sync with GKI_DOWNLOAD_CONFIGS, "images"
            build_system_dlkm = True,
            # Keep in sync with build.config.gki* MODULES_LIST
            modules_list = "android/gki_system_dlkm_modules",
        )

        if target_config.get("build_gki_artifacts"):
            gki_artifacts(
                name = name + "_gki_artifacts",
                kernel_build = name,
                boot_img_sizes = target_config.get("gki_boot_img_sizes", {}),
                arch = arch_config["arch"],
            )
        else:
            native.filegroup(
                name = name + "_gki_artifacts",
                srcs = [],
            )

        # toolchain_version from <name>
        native.filegroup(
            name = name + "_" + TOOLCHAIN_VERSION_FILENAME,
            srcs = [name],
            output_group = TOOLCHAIN_VERSION_FILENAME,
        )

        # module_staging_archive from <name>
        native.filegroup(
            name = name + "_modules_staging_archive",
            srcs = [name],
            output_group = "modules_staging_archive",
        )

        # All GKI modules
        native.filegroup(
            name = name + "_modules",
            srcs = [
                "{}/{}".format(name, module)
                for module in (kernel_build_abi_kwargs["module_implicit_outs"] or [])
            ],
        )

        # The purpose of this target is to allow device kernel build to include reasonable
        # defaults of artifacts from GKI. Hence, this target includes everything in name + "_dist",
        # excluding the following:
        # - UAPI headers, because device-specific external kernel modules may install different
        #   headers.
        # - DDK; see _ddk_artifacts below.
        # - toolchain_version: avoid conflict with device kernel's kernel_build() in dist dir.
        native.filegroup(
            name = name + "_additional_artifacts",
            srcs = [
                # Sync with additional_artifacts_items
                name + "_headers",
                name + "_images",
                name + "_kmi_symbol_list",
                name + "_gki_artifacts",
            ],
        )

        # Everything in name + "_dist" for the DDK.
        # These aren't in DIST_DIR for build.sh-style builds, but necessary for driver
        # development. Hence they are also added to kernel_*_dist so they can be downloaded.
        # Note: This poke into details of kernel_build!
        native.filegroup(
            name = name + "_ddk_artifacts",
            srcs = [
                name + "_modules_prepare",
                name + "_modules_staging_archive",
            ],
        )

        dist_targets = [
            name,
            name + "_uapi_headers",
            name + "_unstripped_modules_archive",
            name + "_additional_artifacts",
            name + "_ddk_artifacts",
            name + "_modules",
            name + "_modules_install",
            name + "_" + TOOLCHAIN_VERSION_FILENAME,
            # BUILD_GKI_CERTIFICATION_TOOLS=1 for all kernel_build defined here.
            "//build/kernel:gki_certification_tools",
        ]

        copy_to_dist_dir(
            name = name + "_dist",
            data = dist_targets,
            flat = True,
            dist_dir = "out/{branch}/dist".format(branch = BRANCH),
            log = "info",
        )

        kernel_build_abi_dist(
            name = name + "_abi_dist",
            kernel_build_abi = name,
            data = dist_targets,
            flat = True,
            dist_dir = "out_abi/{branch}/dist".format(branch = BRANCH),
            log = "info",
        )

        _define_common_kernels_additional_tests(
            name = name + "_additional_tests",
            kernel_build_name = name,
            kernel_modules_install = name + "_modules_install",
            modules = (target_config.get("module_outs") or []) +
                      (target_config.get("module_implicit_outs") or []),
            arch = arch_config["arch"],
        )

        native.test_suite(
            name = name + "_tests",
            tests = [
                name + "_additional_tests",
                name + "_test",
                name + "_modules_test",
            ],
        )

    native.alias(
        name = "kernel",
        actual = ":kernel_aarch64",
    )

    native.alias(
        name = "kernel_dist",
        actual = ":kernel_aarch64_dist",
    )

    kernel_compile_commands(
        name = "kernel_aarch64_compile_commands",
        kernel_build = ":kernel_aarch64_interceptor",
    )

    kernel_kythe(
        name = "kernel_aarch64_kythe",
        kernel_build = ":kernel_aarch64_interceptor",
        compile_commands = ":kernel_aarch64_compile_commands",
    )

    copy_to_dist_dir(
        name = "kernel_aarch64_kythe_dist",
        data = [
            ":kernel_aarch64_compile_commands",
            ":kernel_aarch64_kythe",
        ],
        flat = True,
    )

    _define_prebuilts(visibility = visibility)

def _define_prebuilts(**kwargs):
    # Build number for GKI prebuilts
    bool_flag(
        name = "use_prebuilt_gki",
        build_setting_default = False,
    )

    # Matches when --use_prebuilt_gki is set.
    native.config_setting(
        name = "use_prebuilt_gki_set",
        flag_values = {
            ":use_prebuilt_gki": "true",
        },
    )

    for name, value in CI_TARGET_MAPPING.items():
        repo_name = value["repo_name"]
        main_target_outs = value["outs"]  # outs of target named {name}

        native.filegroup(
            name = name + "_downloaded",
            srcs = ["@{}//{}".format(repo_name, filename) for filename in main_target_outs],
            tags = ["manual"],
        )

        native.filegroup(
            name = name + "_module_outs_file",
            srcs = [":" + name],
            output_group = MODULE_OUTS_FILE_OUTPUT_GROUP,
        )

        # A kernel_filegroup that:
        # - If --use_prebuilt_gki_num is set, use downloaded prebuilt of kernel_aarch64
        # - Otherwise build kernel_aarch64 from sources.
        kernel_filegroup(
            name = name + "_download_or_build",
            srcs = select({
                ":use_prebuilt_gki_set": [":" + name + "_downloaded"],
                "//conditions:default": [name],
            }),
            deps = select({
                ":use_prebuilt_gki_set": [
                    name + "_ddk_artifacts_downloaded",
                    name + "_unstripped_modules_archive_downloaded",
                    name + "_" + TOOLCHAIN_VERSION_FILENAME + "_downloaded",
                ],
                "//conditions:default": [
                    name + "_ddk_artifacts",
                    name + "_" + TOOLCHAIN_VERSION_FILENAME,
                    # unstripped modules come from {name} in srcs, KernelUnstrippedModulesInfo
                ],
            }),
            kernel_srcs = [name + "_sources"],
            kernel_uapi_headers = name + "_uapi_headers_download_or_build",
            collect_unstripped_modules = _COLLECT_UNSTRIPPED_MODULES,
            images = name + "_images_download_or_build",
            module_outs_file = select({
                ":use_prebuilt_gki_set": "@{}//{}{}".format(repo_name, name, MODULE_OUTS_FILE_SUFFIX),
                "//conditions:default": ":" + name + "_module_outs_file",
            }),
            **kwargs
        )

        for config in GKI_DOWNLOAD_CONFIGS:
            target_suffix = config["target_suffix"]
            suffixed_target_outs = config["outs"]  # outs of target named {name}_{target_suffix}

            native.filegroup(
                name = name + "_" + target_suffix + "_downloaded",
                srcs = ["@{}//{}".format(repo_name, filename) for filename in suffixed_target_outs],
                tags = ["manual"],
            )

            # A filegroup that:
            # - If --use_prebuilt_gki_num is set, use downloaded prebuilt of kernel_{arch}_{target_suffix}
            # - Otherwise build kernel_{arch}_{target_suffix}
            native.filegroup(
                name = name + "_" + target_suffix + "_download_or_build",
                srcs = select({
                    ":use_prebuilt_gki_set": [":" + name + "_" + target_suffix + "_downloaded"],
                    "//conditions:default": [name + "_" + target_suffix],
                }),
                **kwargs
            )

        additional_artifacts_items = [
            name + "_headers",
            name + "_images",
            name + "_kmi_symbol_list",
            name + "_gki_artifacts",
        ]

        native.filegroup(
            name = name + "_additional_artifacts_downloaded",
            srcs = [item + "_downloaded" for item in additional_artifacts_items],
        )

        native.filegroup(
            name = name + "_additional_artifacts_download_or_build",
            srcs = [item + "_download_or_build" for item in additional_artifacts_items],
        )

def _define_common_kernels_additional_tests(
        name,
        kernel_build_name,
        kernel_modules_install,
        modules,
        arch):
    fake_modules_options = "//build/kernel/kleaf/artifact_tests:fake_modules_options.txt"

    kernel_images(
        name = name + "_fake_images",
        kernel_modules_install = kernel_modules_install,
        build_initramfs = True,
        modules_options = fake_modules_options,
    )

    initramfs_modules_options_test(
        name = name + "_fake",
        kernel_images = name + "_fake_images",
        expected_modules_options = fake_modules_options,
    )

    native.genrule(
        name = name + "_empty_modules_options",
        outs = [name + "_empty_modules_options/modules.options"],
        cmd = ": > $@",
    )

    kernel_images(
        name = name + "_empty_images",
        kernel_modules_install = kernel_modules_install,
        build_initramfs = True,
        # Not specify module_options
    )

    initramfs_modules_options_test(
        name = name + "_empty",
        kernel_images = name + "_empty_images",
        expected_modules_options = name + "_empty_modules_options",
    )

    device_modules_test(
        name = name + "_device_modules_test",
        base_kernel_label = Label("//{}:{}".format(native.package_name(), kernel_build_name)),
        base_kernel_module = min(modules) if modules else None,
        arch = arch,
    )

    native.test_suite(
        name = name,
        tests = [
            name + "_empty",
            name + "_fake",
            name + "_device_modules_test",
        ],
    )

def define_db845c(
        name,
        outs,
        build_config = None,
        module_outs = None,
        define_abi_targets = None,
        kmi_symbol_list = None,
        kmi_symbol_list_add_only = None,
        module_grouping = None,
        unstripped_modules_archive = None,
        gki_modules_list = None,
        dist_dir = None):
    """Define target for db845c.

    Note: This is a mixed build.

    Requires [`define_common_kernels`](#define_common_kernels) to be called in the same package.

    Args:
        name: name of target. Usually `"db845c"`.
        build_config: See [kernel_build.build_config](#kernel_build-build_config). If `None`,
          default to `"build.config.db845c"`.
        outs: See [kernel_build.outs](#kernel_build-outs).
        module_outs: See [kernel_build.module_outs](#kernel_build-module_outs). The list of
          in-tree kernel modules.
        define_abi_targets: See [kernel_build_abi.define_abi_targets](#kernel_build_abi-define_abi_targets).
        kmi_symbol_list: See [kernel_build.kmi_symbol_list](#kernel_build-kmi_symbol_list).
        kmi_symbol_list_add_only: See [kernel_build_abi.kmi_symbol_list_add_only](#kernel_build_abi-kmi_symbol_list_add_only).
        module_grouping: See [kernel_build_abi.module_grouping](#kernel_build_abi-module_grouping).
        unstripped_modules_archive: See [kernel_build_abi.unstripped_modules_archive](#kernel_build_abi-unstripped_modules_archive).
        gki_modules_list: List of gki modules to be copied to the dist directory.
          If `None`, all gki kernel modules will be copied.
        dist_dir: Argument to `copy_to_dist_dir`. If `None`, default is `"out/{BRANCH}/dist"`.
    """

    if build_config == None:
        build_config = "build.config.db845c"

    if kmi_symbol_list == None:
        kmi_symbol_list = "//common:android/abi_gki_aarch64_db845c" if define_abi_targets else None

    if kmi_symbol_list_add_only == None:
        kmi_symbol_list_add_only = True if define_abi_targets else None

    if gki_modules_list == None:
        gki_modules_list = [":kernel_aarch64_modules"]

    if dist_dir == None:
        dist_dir = "out/{branch}/dist".format(branch = BRANCH)

    # Also refer to the list of ext modules for ABI monitoring targets
    _kernel_modules = []

    kernel_build_abi(
        name = name,
        outs = outs,
        srcs = [":common_kernel_sources"],
        # List of in-tree kernel modules.
        module_outs = module_outs,
        build_config = build_config,
        # Enable mixed build.
        base_kernel = ":kernel_aarch64",

        # enable ABI Monitoring
        # based on the instructions here:
        # https://android.googlesource.com/kernel/build/+/refs/heads/master/kleaf/docs/abi_device.md
        # https://android-review.googlesource.com/c/kernel/build/+/2308912
        define_abi_targets = define_abi_targets,
        kernel_modules = _kernel_modules,
        kmi_symbol_list = kmi_symbol_list,
        kmi_symbol_list_add_only = kmi_symbol_list_add_only,
        module_grouping = module_grouping,
        unstripped_modules_archive = unstripped_modules_archive,
    )

    kernel_modules_install(
        name = name + "_modules_install",
        kernel_build = name,
        # List of external modules.
        kernel_modules = _kernel_modules,
    )

    merged_kernel_uapi_headers(
        name = name + "_merged_kernel_uapi_headers",
        kernel_build = name,
        kernel_modules = _kernel_modules,
    )

    kernel_images(
        name = name + "_images",
        build_initramfs = True,
        kernel_build = name,
        kernel_modules_install = name + "_modules_install",
    )

    dist_targets = [
        name,
        name + "_images",
        name + "_modules_install",
        # Mixed build: Additional GKI artifacts.
        ":kernel_aarch64",
        ":kernel_aarch64_additional_artifacts",
        name + "_merged_kernel_uapi_headers",
    ]

    copy_to_dist_dir(
        name = name + "_dist",
        data = dist_targets + gki_modules_list,
        dist_dir = dist_dir,
        flat = True,
        log = "info",
    )

    kernel_build_abi_dist(
        name = name + "_abi_dist",
        kernel_build_abi = ":db845c",
        data = dist_targets + gki_modules_list,
        dist_dir = dist_dir,
        flat = True,
        log = "info",
    )
