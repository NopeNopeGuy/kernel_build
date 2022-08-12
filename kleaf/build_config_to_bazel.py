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

"""A script that converts existing build.config to a skeleton Bazel BUILD rules.

The skeleton Bazel BUILD file likely won't build properly. Manual intervention
is required after the skeleton file is created. Most instructions are presented
as "FIXME" comments in the generated file.

Running this script requires buildozer. Install it at
  https://github.com/bazelbuild/buildtools/blob/master/buildozer/README.md
"""

import argparse
import collections
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from typing import Callable, Optional, TextIO, Any, Iterable, Sequence, Mapping

_BUILD_CONFIG_PREFIX = "build.config."
_BUILDOZER_RETURN_CODE_NO_CHANGES_MADE = 3
# See kernel_build.bzl
_DEFAULT_KERNEL_BUILD_SRCS = \
    """glob(["**"],\\ exclude=["**/.*",\\ "**/.*/**",\\ "**/BUILD.bazel",\\ "**/*.bzl",])"""

# Variables set by build configs or _setup_env.sh that does not need to be
# translated into BUILD definitions. Ignore these variables.
_IGNORED_BUILD_CONFIGS = (
    "OUT_DIR",
    "MAKE_GOALS",
    "LD",
    "SKIP_MRPROPER",
    "SKIP_DEFCONFIG",
    "SKIP_IF_VERSION_MATCHES",
    "SKIP_EXT_MODULES",
    "SKIP_CP_KERNEL_HDR",
    "SKIP_UNPACKING_RAMDISK",
    "POST_DEFCONFIG_CMDS",
    "IN_KERNEL_MODULES",
    "DO_NOT_STRIP_MODULES",
    "AVB_SIGN_BOOT_IMG",
    "AVB_BOOT_PARTITION_SIZE",
    "AVB_BOOT_KEY",
    "AVB_BOOT_ALGORITHM",
    "AVB_BOOT_PARTITION_NAME",
    "MODULES_ORDER",
    "GKI_MODULES_LIST",
    "LZ4_RAMDISK",
    "LZ4_RAMDISK_COMPRESS_ARGS",
    "KMI_STRICT_MODE_OBJECTS",
    "GKI_DIST_DIR",
    "BUILD_GKI_ARTIFACTS",
    "GKI_KERNEL_CMDLINE",
    "AR",
    "ARCH",
    "BRANCH",
    "BUILDTOOLS_PREBUILT_BIN",
    "CC",
    "CLANG_PREBUILT_BIN",
    "CLANG_VERSION",
    "COMMON_OUT_DIR",
    "DECOMPRESS_GZIP",
    "DECOMPRESS_LZ4",
    "DEFCONFIG",
    "DEPMOD",
    "DTC",
    "HOSTCC",
    "HOSTCFLAGS",
    "HOSTCXX",
    "HOSTLDFLAGS",
    "KBUILD_BUILD_HOST",
    "KBUILD_BUILD_TIMESTAMP",
    "KBUILD_BUILD_USER",
    "KBUILD_BUILD_VERSION",
    "KCFLAGS",
    "KCPPFLAGS",
    "KERNEL_DIR",
    "KMI_GENERATION",
    "LC_ALL",
    "LLVM",
    "MODULES_ARCHIVE",
    "NDK_TRIPLE",
    "NM",
    "OBJCOPY",
    "OBJDUMP",
    "OBJSIZE",
    "PATH",
    "RAMDISK_COMPRESS",
    "RAMDISK_DECOMPRESS",
    "RAMDISK_EXT",
    "READELF",
    "ROOT_DIR",
    "SOURCE_DATE_EPOCH",
    "STRIP",
    "TOOL_ARGS",
    "TZ",
    "UNSTRIPPED_DIR",
    "UNSTRIPPED_MODULES_ARCHIVE",
    "USERCFLAGS",
    "USERLDFLAGS",
    "_SETUP_ENV_SH_INCLUDED",
)

# Variables not supported by Kleaf. Device owners will need to migrate away from these variables.
# Keys are variable names. Values are regular expressions.
# - If the value of the variable in the build config does not match the regular expression, the
#   variable is considered unsupported.
# - If the value does match the regular expression, it is considered ignored (i.e. the BUILD file
#   is not modified).
_NOT_SUPPORTED_BUILD_CONFIGS = {
    "EXT_MODULES_MAKEFILE": r".+",
    "COMPRESS_MODULES": r".+",
    "HERMETIC_TOOLCHAIN": r"[^1]",
    "ADDITIONAL_HOST_TOOLS": r".+",
    "POST_KERNEL_BUILD_CMDS": r".+",
    "TAGS_CONFIG": r".+",
    "EXTRA_CMDS": r".+",
    "DIST_CMDS": r".+",
    "VENDOR_RAMDISK_CMDS": r".+",
    "STOP_SHIP_TRACEPRINTK": r".+",
}


def die(msg):
    logging.error("%s", msg)
    sys.exit(1)


def isinstance_or_die(obj, clazz):
    if not isinstance(obj, clazz):
        die(f"Object {obj} is not an instance of {clazz}")
    return obj


def order_dict_by_key(d: Mapping[str, str]) -> Mapping[str, str]:
    return collections.OrderedDict(sorted(d.items()))


def find_build_config(env: Mapping[str, str]) -> str:
    # Set by either environment or _setup_env.sh
    if env.get("BUILD_CONFIG"):
        real_build_config = os.path.realpath(env["BUILD_CONFIG"])
        real_this = os.path.realpath(".")
        if os.path.commonpath([real_build_config, real_this]) != real_this:
            die(f"realpath $BUILD_CONFIG ({real_build_config}) is not under the repository root")
        return os.path.relpath(real_build_config, real_this)
    die("$BUILD_CONFIG is not set, and top level build.config file is not found.")


def infer_target_name(args, build_config: str) -> str:
    if args.target:
        return args.target
    build_config_base = os.path.basename(build_config)
    if build_config_base.startswith(
            _BUILD_CONFIG_PREFIX) and build_config_base != _BUILD_CONFIG_PREFIX:
        return build_config_base[len(_BUILD_CONFIG_PREFIX):]
    die("Fail to infer target name. Specify with --target.")


def ensure_build_file(package: str):
    if os.path.isabs(package):
        die(f"$BUILD_CONFIG must be a relative path.")
    if not os.path.exists(os.path.join(package, "BUILD.bazel")) and not os.path.exists(
            os.path.join(package, "BUILD")):
        build_file = os.path.join(package, "BUILD.bazel")
        logging.info(f"Creating {build_file}")
        with open(build_file, "w"):
            pass


@dataclass(frozen=True)
class InfoKey(object):
    """The key of the dictionary storing information for existing BUILD files."""

    # Full label of the target.
    target: str


class TargetKey(InfoKey):
    pass


@dataclass(frozen=True)
class AttributeKey(InfoKey):
    """The key of the dictionary storing information for existing BUILD files."""

    # Name of the attribute.
    attribute: Optional[str]


class InfoValue(object):
    """The value of the dictionary storing information for existing BUILD files."""

    # Attribute value is None.
    NONE = "None"

    # Attribute value is not set, or target does not exist.
    MISSING = None


@dataclass
class AttributeValue(InfoValue):
    # String-representation of the attribute value.
    # - If attribute value is None, this is the string "None" (InfoValue.NONE).
    # - If attribute value is not set, this is the value None (InfoValue.MISSING)
    value: Optional[str | list[Any]] = InfoValue.MISSING

    # String that contains the comment.
    # If comment is not found, this is the value None.
    comment: Optional[str] = InfoValue.MISSING


@dataclass
class TargetValue(InfoValue):
    # Kind of the declaration (e.g. kernel_build)
    kind: Optional[str] = InfoValue.MISSING


class BuildozerCommandBuilder(object):
    def __init__(self, args, stdout: Optional[TextIO] = None, stderr: Optional[TextIO] = None,
                 environ: Mapping[str, str] = None):
        """
        Args:
             args: Namespace containing command-line arguments
             stdout: Override stdout stream for subprocesses
             stderr: Override stderr stream for subprocesses
             environ: Override environment variables for subprocesses
        """

        self.stdout = stdout or sys.stdout
        self.stderr = stderr or sys.stderr
        self.environ = environ or os.environ

        self.new_env = order_dict_by_key(json.loads(subprocess.check_output(
            "source build/kernel/_setup_env.sh > /dev/null && build/kernel/kleaf/dump_env.py",
            shell=True, stderr=self.stderr, env=self.environ)))
        logging.info("Captured env: %s", json.dumps(self.new_env, indent=2))

        build_config = find_build_config(self.new_env)
        target_name = infer_target_name(args, build_config)

        self.package = os.path.dirname(build_config)
        self.target_name = target_name
        self.args = args

        self.pkg = f"//{self.package}:__pkg__"
        self.dist_name = f"{target_name}_dist"
        self.unstripped_modules_name = f"{target_name}_unstripped_modules_archive"
        self.images_name = f"{target_name}_images"
        self.dts_name = f"{target_name}_dts"
        self.modules_install_name = f"{target_name}_modules_install"

        self.buildozer = self._find_buildozer()

        # set elsewhere
        self.existing: Optional[dict[InfoKey, InfoValue]] = None
        self.dist_targets: Optional[set[str]] = None
        self.out_file: Optional[TextIO] = None

    def __enter__(self):
        self.out_file = tempfile.NamedTemporaryFile("w+")
        return self

    def __exit__(self, exc, value, tb):
        self.out_file.close()
        self.out_file = None

    def _find_buildozer(self) -> str:
        gopath = self.environ.get("GOPATH", os.path.join(self.environ["HOME"], "go"))
        buildozer = os.path.join(gopath, "bin", "buildozer")
        if not os.path.isfile(buildozer):
            die("Can't find buildozer. Install with instructions at "
                "https://github.com/bazelbuild/buildtools/blob/master/buildozer/README.md")
        return buildozer

    def _get_all_info(self, keys: Iterable[InfoKey]) -> dict[InfoKey, InfoValue]:
        """Gets all interesting information of existing BUILD files.

        Args:
            keys: The list of interesting information to get.
        """
        ret = dict()
        for key in keys:
            tup = None
            if isinstance(key, TargetKey):
                tup = self._get_target(key.target)
            elif isinstance(key, AttributeKey):
                tup = self._get_attr(key)
            ret[tup[0]] = tup[1]
        return ret

    def _buildozer_print(self, target, print_command, attribute) -> Optional[str | list[Any]]:
        """Executes a buildozer print command."""
        value = InfoValue.MISSING

        try:
            value = subprocess.check_output(
                [self.buildozer, f"{print_command} {attribute}", target],
                text=True, stderr=self.stderr, env=self.environ).strip()
        except subprocess.CalledProcessError:
            pass

        return value

    def _get_target(self, target: str) -> tuple[InfoKey, InfoValue]:
        """Gets information about a single target from existing BUILD files.

        Args:
            target: full label of target.
        """
        kind = self._buildozer_print(target, "print", "kind")
        return TargetKey(target), TargetValue(kind)

    def _get_attr(self, key: AttributeKey) -> tuple[InfoKey, InfoValue]:
        """Get a single attribute of existing BUILD files.

        Args:
            key: the InfoKey.
        """
        value = self._buildozer_print(key.target, "print", key.attribute)
        comment = self._buildozer_print(key.target, "print_comment", key.attribute)
        return key, AttributeValue(value=value, comment=comment)

    @staticmethod
    def _is_ignored_build_config(build_config: str) -> bool:
        if build_config in _IGNORED_BUILD_CONFIGS:
            return True
        if build_config.startswith("BASH_FUNC_") and build_config.endswith("%%"):
            return True
        if build_config == "_":
            return True
        return False

    def _new(self, kind: str, name: str, package=None, load_from="//build/kernel/kleaf:kernel.bzl",
             add_to_dist=True) -> str:
        """Writes a buildozer command that creates a target."""
        if package is None:
            package = self.package
        ensure_build_file(package)
        new_target_pkg = f"//{package}:__pkg__"
        new_target = f"//{package}:{name}"
        key = TargetKey(new_target)

        existing_kind = InfoValue.MISSING
        if key in self.existing:
            existing_kind = isinstance_or_die(self.existing[key], TargetValue).kind

        if existing_kind is InfoValue.MISSING:
            self.out_file.write(f"""
                fix movePackageToTop|{new_target_pkg}
                new_load {load_from} {kind}|{new_target_pkg}
                new {kind} {name}|{new_target_pkg}
""")
            self.existing[key] = TargetValue(kind=kind)
        elif existing_kind != kind:
            logging.warning(f"Forcefully setting {new_target} from {existing_kind} to {kind}")
            self.out_file.write(f"""
                fix movePackageToTop|{new_target_pkg}
                new_load {load_from} {kind}|{new_target_pkg}
                set kind {kind}|{new_target}
""")
            self.existing[key] = TargetValue(kind=kind)

        if add_to_dist:
            self.dist_targets.add(new_target)
        return new_target

    def _add_comment(self, target: str, attribute: str, expected_comment: str,
                     should_set_comment_pred: Callable[[AttributeValue], bool] = lambda e: True):
        """Adds comment to attribute of the given target.

        If the attribute does not exist (assuming that it is queried
        with _get_all_info), it is set to None.
        """
        # comments can only be set to existing attributes. Set it to None if the
        # attribute does not already exist.
        self._set_attr(target, attribute, InfoValue.NONE, command="set_if_absent")

        attr_val = isinstance_or_die(self.existing[AttributeKey(target, attribute)], AttributeValue)
        if should_set_comment_pred(attr_val):
            logging.info(f"pred passes: {attr_val.comment}")
            if attr_val.comment is InfoValue.MISSING or \
                    expected_comment not in attr_val.comment:
                esc_comment = expected_comment.replace(" ", "\\ ")
                self.out_file.write(f"""comment {attribute} {esc_comment}|{target}\n""")
                attr_val.comment = expected_comment

    def _add_target_comment(self, target: str, comment_lines: Iterable[str]):
        """Adds comment to a given target."""

        # "comment" command on targets will override existing comments,
        # so there is no need to check existing comments.
        content = "\\n".join(comment_lines)
        content = content.replace(" ", "\\ ")
        if content:
            self.out_file.write(f"""comment {content}|{target}\n""")

    def _set_attr(self, target: str, attribute: str, value: Any, quote: bool = False,
                  command: str = "set"):
        """Writes a buildozer command that sets an attribute.

        Args:
            target: full label of target
            attribute: attribute name
            value: value of attribute
            quote: if value should be quoted in the buildozer command
            command: buildozer command. Either "set" or "set_if_absent"
        """
        if command not in ("set", "set_if_absent"):
            die(f"Unknown command {command} for _set_attr")

        command_value = f'"{value}"' if quote else str(value)
        self.out_file.write(f"""{command} {attribute} {command_value}|{target}\n""")

        # set value in self.existing
        key = AttributeKey(target, attribute)
        if key not in self.existing:
            self.existing[key] = AttributeValue()
        attr_val: AttributeValue = isinstance_or_die(self.existing[key], AttributeValue)
        if command == "set" or (command == "set_if_absent" and attr_val.value is InfoValue.MISSING):
            attr_val.value = value

    def _add_attr(self, target: str, attribute: str, value: str, quote=False):
        """Writes a buildozer command that adds to an attribute.

        Args:
            target: full label of target
            attribute: attribute name
            value: value of attribute
            quote: if value should be quoted in the buildozer command
        """
        command_value = f'"{value}"' if quote else value
        self.out_file.write(f"""add {attribute} {command_value}|{target}\n""")

        # set value in self.existing
        key = AttributeKey(target, attribute)
        if key not in self.existing:
            self.existing[key] = AttributeValue()
        attr_val: AttributeValue = isinstance_or_die(self.existing[key], AttributeValue)
        if attr_val.value is InfoValue.MISSING:
            attr_val.value = f"[{command_value}]"
        else:
            # We could flatten this list, but we don't care about the value for now.
            attr_val.value += f" + [{command_value}]"

    def _create_extra_file(self, path: str, content: str):
        """Creates an extra file in the filesystem."""
        if self.args.stdout:
            logging.info(f"Dry-run: skipped creating file at {path}")
            return
        logging.info(f"Creating file at {path}")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(content)

    def _create_buildozer_commands(self) -> None:
        """Fills in self.out_file."""
        common = self.args.ack

        self.dist_targets = set()

        target = self._new("kernel_build", self.target_name)
        dist = self._new("copy_to_dist_dir", self.dist_name,
                         load_from="//build/bazel_common_rules/dist:dist.bzl", add_to_dist=False)
        self._set_attr(dist, "flat", True)

        images = None
        modules_install = None

        target_comment = []

        # List of build configs unknown to this script. They require attention from
        # the developers to be translated properly.
        unknowns = []

        for key, value in self.new_env.items():
            esc_value = value.replace(" ", "\\ ")
            if type(self)._is_ignored_build_config(key):
                continue
            elif key in _NOT_SUPPORTED_BUILD_CONFIGS and re.match(_NOT_SUPPORTED_BUILD_CONFIGS[key],
                                                                  value):
                target_comment.append(f"FIXME: {key}={esc_value} not supported")
            elif key == "BUILD_CONFIG":
                self._set_attr(target, "build_config", os.path.basename(value), quote=True)
            elif key == "BUILD_CONFIG_FRAGMENTS":
                target_comment.append(
                    f"FIXME: {key}={esc_value}: Please manually convert to kernel_build_config")
            elif key == "FAST_BUILD":
                if value:
                    target_comment.append(f"FIXME: {key}: Specify --config=fast in device.bazelrc")
            elif key == "LTO":
                if value:
                    target_comment.append(f"FIXME: {key}: Specify --lto={value} in device.bazelrc")
            elif key == "DIST_DIR":
                rel_dist_dir = os.path.relpath(value)
                self._add_comment(dist, "dist_dir",
                                  f'FIXME: or dist_dir = "{rel_dist_dir}"')
            elif key == "FILES":
                for elem in value.split():
                    self._add_attr(target, "outs", elem, quote=True)
            elif key == "EXT_MODULES":
                # FIXME(b/241320850): add kernel_modules_install (modules_install) to EXT_MODULES
                modules = value.split()
                if modules:
                    target_comment.append(
                        f"FIXME: {key}={esc_value}: Please manually convert to kernel_module")
            elif key == "KCONFIG_EXT_PREFIX":
                self._set_attr(target, "kconfig_ext", value, quote=True)
            elif key == "UNSTRIPPED_MODULES":
                self._set_attr(target, "collect_unstripped_modules", bool(value))
            elif key == "COMPRESS_UNSTRIPPED_MODULES":
                if value == "1":
                    unstripped_modules = self._new("kernel_unstripped_modules_archive",
                                                   self.unstripped_modules_name)
                    self._set_attr(unstripped_modules, "kernel_build", target, quote=True)
                    self._add_comment(unstripped_modules, "kernel_modules",
                                      f"FIXME: set kernel_modules to the list of kernel_module()s")
            elif key in ("ABI_DEFINITION", "KMI_ENFORCED"):
                # FIXME(b/241320850): also ABI monitoring
                target_comment.append(
                    f"FIXME: {key}={esc_value}: Please manually convert to kernel_build_abi")
            elif key == "KMI_SYMBOL_LIST":
                self._set_attr(target, "kmi_symbol_list", f"//{common}:{value}", quote=True)
            elif key == "ADDITIONAL_KMI_SYMBOL_LISTS":
                kmi_symbol_lists = value.split()
                for kmi_symbol_list in kmi_symbol_lists:
                    self._add_attr(target, "additional_kmi_symbol_lists",
                                   f"//{common}:{kmi_symbol_list}", quote=True)

            elif key in (
                    "TRIM_NONLISTED_KMI",
                    "GENERATE_VMLINUX_BTF",
                    "KMI_SYMBOL_LIST_STRICT_MODE",
                    "KBUILD_SYMTYPES",
            ):
                self._set_attr(target, key.lower(), bool(value == "1"))
            elif key == "PRE_DEFCONFIG_CMDS":
                target_comment.append(
                    "FIXME: PRE_DEFCONFIG_CMDS: Don't forget to modify PRE_DEFCONFIG_CMDS "
                    "so it writes to $OUT_DIR, not the source tree: "
                    "https://android.googlesource.com/kernel/build/+/refs/heads/master/kleaf/docs/errors.md#defconfig-readonly")
            elif key in (
                    "BUILD_BOOT_IMG",
                    "BUILD_VENDOR_BOOT_IMG",
                    "BUILD_DTBO_IMG",
                    "BUILD_VENDOR_KERNEL_BOOT",
                    "BUILD_INITRAMFS",
            ):
                images = self._new("kernel_images", self.images_name)
                # bool(value) checks if the string is empty or not
                self._set_attr(images, key.removesuffix("_IMG").lower(), bool(value))
            elif key == "SKIP_VENDOR_BOOT":
                images = self._new("kernel_images", self.images_name)
                self._set_attr(images, "build_vendor_boot", not bool(value))
            elif key == "MKBOOTIMG_PATH":
                images = self._new("kernel_images", self.images_name)
                self._add_comment(images, "mkbootimg",
                                  f"FIXME: set mkbootimg to label of {esc_value}")
            elif key == "MODULES_OPTIONS":
                images = self._new("kernel_images", self.images_name)
                modules_options_filename = f"modules.options.{self.target_name}"
                modules_options_path = os.path.join(self.package, modules_options_filename)
                self._create_extra_file(modules_options_path, value)
                self._set_attr(images, "modules_options",
                               f"//{self.package}:{modules_options_filename}",
                               quote=True)
            elif key in (
                    "MODULES_LIST",
                    "MODULES_BLOCKLIST",
                    "SYSTEM_DLKM_MODULES_LIST",
                    "SYSTEM_DLKM_MODULES_BLOCKLIST",
                    "SYSTEM_DLKM_PROPS",
                    "VENDOR_DLKM_MODULES_LIST",
                    "VENDOR_DLKM_MODULES_BLOCKLIST",
                    "VENDOR_DLKM_PROPS",
            ):
                images = self._new("kernel_images", self.images_name)
                if os.path.commonpath((value, self.package)) == self.package:
                    self._set_attr(images, key.lower(), os.path.relpath(value, start=self.package),
                                   quote=True)
                else:
                    self._add_comment(images, key.lower(),
                                      f"FIXME: set {key.lower()} to label of {esc_value}")
            elif key == "GKI_BUILD_CONFIG":
                if value == f"{common}/build.config.gki.aarch64":
                    self._set_attr(target, "base_kernel", f"//{common}:kernel_aarch64", quote=True)
                else:
                    self._add_comment(target, "base_kernel",
                                      f"FIXME: set base_kernel to kernel_build for {esc_value}")
            elif key == "GKI_PREBUILTS_DIR":
                target_comment.append(
                    f"FIXME: {key}={esc_value}: Please manually convert to kernel_filegroup")
            elif key == "DTS_EXT_DIR":
                dts = self._new("kernel_dtstree", self.dts_name, package=value,
                                add_to_dist=False)
                self._set_attr(target, "dtstree", dts, quote=True)
            elif key == "BUILD_GKI_CERTIFICATION_TOOLS":
                if value == "1":
                    self.dist_targets.add("//build/kernel:gki_certification_tools")
            elif key in self.environ:
                if self.environ[key] == value:
                    logging.info(f"Ignoring variable {key} in environment.")
                else:
                    target_comment.append(f"FIXME: Unknown in build config: {key}={esc_value}")
                    unknowns.append(key)
            else:
                target_comment.append(f"FIXME: Unknown in build config: {key}={esc_value}")
                unknowns.append(key)

        for dist_target in self.dist_targets:
            self._add_attr(dist, "data", dist_target, quote=True)

        if images:
            if not modules_install:
                modules_install = self._new("kernel_modules_install", self.modules_install_name)
                self._set_attr(modules_install, "kernel_build", target, quote=True)
                self._add_comment(modules_install, "kernel_modules",
                                  "FIXME: kernel_modules should include the list of "
                                  "kernel_module()s")

            self._set_attr(images, "kernel_build", target, quote=True)
            self._set_attr(images, "kernel_modules_install", modules_install, quote=True)

        if "KERNEL_DIR" in self.new_env and self.new_env["KERNEL_DIR"] != self.package:
            if self.new_env["KERNEL_DIR"].removesuffix("/") == common:
                self._set_attr(target, "srcs", _DEFAULT_KERNEL_BUILD_SRCS, quote=False,
                               command="set_if_absent")
                self._add_attr(target, "srcs", f"//{common}:kernel_aarch64_sources", quote=True)
            else:
                self._add_comment(target, "srcs",
                                  f"FIXME: add files from KERNEL_DIR {self.new_env['KERNEL_DIR']}")

        self._add_comment(target, "base_kernel",
                          f"FIXME: base_kernel should be migrated to //{common}:kernel_aarch64.",
                          lambda attr_val: attr_val.value not in (
                              f"//{common}:kernel_aarch64", f"//{common}:kernel"))

        self._add_comment(target, "module_outs",
                          f"FIXME: set to the list of in-tree modules. You may run "
                          f"`tools/bazel build {target}` and follow the instructions "
                          f"in the error message.",
                          lambda attr_val: attr_val.value is InfoValue.MISSING or
                                           attr_val.value == InfoValue.NONE)

        self._add_target_comment(target, target_comment)

        if unknowns:
            logging.info("Unknown variables:\n%s", ",\n".join(f'"{e}"' for e in unknowns))

        self.out_file.flush()

    def _run_buildozer(self) -> None:
        self.out_file.seek(0)
        logging.info("Executing buildozer with the following commands:\n%s", self.out_file.read())

        buildozer_args = [
            self.buildozer,
            "-shorten_labels",
            "-f",
            self.out_file.name,
        ]
        if self.args.k:
            buildozer_args.append("-k")
        if self.args.stdout:
            buildozer_args.append("-stdout")
        try:
            subprocess.check_call(buildozer_args, stdout=self.stdout, stderr=self.stderr,
                                  env=self.environ)
        except subprocess.CalledProcessError as e:
            if e.returncode == _BUILDOZER_RETURN_CODE_NO_CHANGES_MADE:
                logging.info("No files were changed.")
            else:
                raise

    def run(self):
        # Dry run to see what attributes / targets will be added
        self.existing = dict()
        with self:
            # This modifies self.existing
            self._create_buildozer_commands()
            # The buildozer command file is deleted.

        # self.existing.keys() = things we would change.
        # Get the existing information of these things in BUILD files
        self.existing = self._get_all_info(self.existing.keys())

        # Create another buildozer command file. This time, actually run buildozer with it.
        with self:
            self._create_buildozer_commands()
            self._run_buildozer()


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-t", "--target",
                        help="Name of target. Otherwise, infer from the name of the "
                             "build.config file.")
    parser.add_argument("--log", help="log level", default="warning")
    parser.add_argument("-k",
                        help="buildozer keeps going on errors. Use when targets are already "
                             "defined. There may be duplicated FIXME comments.",
                        action="store_true")
    parser.add_argument("--stdout",
                        help="buildozer writes changed BUILD file to stdout (dry run)",
                        action="store_true")
    parser.add_argument("--ack", help="path to ACK source tree; default is common.",
                        default="common")
    return parser.parse_args(argv)


def _config_logging(args: argparse.Namespace):
    numeric_level = getattr(logging, args.log.upper(), None)
    if not isinstance(numeric_level, int):
        raise ValueError("Invalid log level: %s" % args.log)
    logging.basicConfig(level=numeric_level, format="%(levelname)s: %(message)s")


def main(argv: Sequence[str]):
    args = parse_args(argv)
    _config_logging(args)
    BuildozerCommandBuilder(args=args).run()


if __name__ == "__main__":
    main(sys.argv[1:])
