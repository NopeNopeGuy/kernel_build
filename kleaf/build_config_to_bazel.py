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

# A script that converts existing build.config to a skeleton Bazel BUILD rules
# for starters.
#
# Requires buildozer: Install at
#   https://github.com/bazelbuild/buildtools/blob/master/buildozer/README.md
import argparse
import collections
import json
import logging
import os
import subprocess
import sys
import tempfile
from typing import Tuple, Callable, Sequence, Optional, TextIO, Any, Iterable

BUILD_CONFIG_PREFIX = "build.config."
BUILDOZER_NO_CHANGES_MADE = 3
DEFAULT_KERNEL_BUILD_SRCS = """glob(["**"],\\ exclude=["**/.*",\\ "**/.*/**",\\ "**/BUILD.bazel",\\ "**/*.bzl",])"""

_IGNORED_BUILD_CONFIGS = (
    "OUT_DIR",
    "MAKE_GOALS",
    "LD",
    "HERMETIC_TOOLCHAIN",
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

_NOT_SUPPORTED_BUILD_CONFIGS = (
    "EXT_MODULES_MAKEFILE",
    "COMPRESS_MODULES",
    "ADDITIONAL_HOST_TOOLS",
    "POST_KERNEL_BUILD_CMDS",
    "TAGS_CONFIG",
    "EXTRA_CMDS",
    "DIST_CMDS",
    "VENDOR_RAMDISK_CMDS",
    "STOP_SHIP_TRACEPRINTK",
)


def fail(msg):
    logging.error("%s", msg)
    sys.exit(1)


def check_isinstance(obj, clazz):
    if not isinstance(obj, clazz):
        fail(f"Object {obj} is not an instance of {clazz}")
    return obj


def order_dict_by_key(d):
    return collections.OrderedDict(sorted(d.items()))


def find_buildozer() -> str:
    gopath = os.environ.get("GOPATH", os.path.join(os.environ["HOME"], "go"))
    buildozer = os.path.join(gopath, "bin", "buildozer")
    if not os.path.isfile(buildozer):
        fail("Can't find buildozer. Install with instructions at "
             "https://github.com/bazelbuild/buildtools/blob/master/buildozer/README.md")
    return buildozer


def readlink_if_link(path) -> str:
    # if [[ -l $x ]]; then readlink $x; else echo $x; fi
    if os.path.islink(path):
        return os.readlink(path)
    return path


def find_build_config(env: dict[str, str]) -> str:
    # Set by either environment or _setup_env.sh
    if env.get("BUILD_CONFIG"):
        return readlink_if_link(env["BUILD_CONFIG"])
    fail("$BUILD_CONFIG is not set, and top level build.config file is not found.")


def infer_target_name(args, build_config: str) -> str:
    if args.target:
        return args.target
    build_config_base = os.path.basename(build_config)
    if build_config_base.startswith(
            BUILD_CONFIG_PREFIX) and build_config_base != BUILD_CONFIG_PREFIX:
        return build_config_base[len(BUILD_CONFIG_PREFIX):]
    fail("Fail to infer target name. Specify with --target.")


def ensure_build_file(package: str):
    if os.path.isabs(package):
        fail(f"$BUILD_CONFIG must be a relative path.")
    if not os.path.exists(os.path.join(package, "BUILD.bazel")) and not os.path.exists(
            os.path.join(package, "BUILD")):
        build_file = os.path.join(package, "BUILD.bazel")
        logging.info(f"Creating {build_file}")
        with open(os.path.join(build_file), "w"):
            pass


class InfoKey(object):
    """The key of the dictionary storing information for existing BUILD files."""

    def __init__(self, target: str):
        """
        Args:
            target: full label of the target.
        """
        self.target = target

    def __hash__(self):
        return hash((self.target))

    def __eq__(self, other):
        return (self.target) == (other.target)


class TargetKey(InfoKey):
    def __str__(self):
        return f"{self.target}"

    def __repr__(self):
        return f"TargetKey({repr(self.target)})"


class AttributeKey(InfoKey):
    """The key of the dictionary storing information for existing BUILD files."""

    def __init__(self, target: str, attribute: Optional[str]):
        """
        Args:
            target: full label of the target.
            attribute: If representing a target, None.
              If representing an attribute, name of the attribute.
        """
        super().__init__(target)
        self.attribute = attribute

    def __hash__(self):
        return hash((self.target, self.attribute))

    def __eq__(self, other):
        return (self.target, self.attribute) == (other.target, other.attribute)

    def __str__(self):
        return f"{self.target} {self.attribute}"

    def __repr__(self):
        return f"InfoKey({repr(self.target)}, {repr(self.attribute)})"


class InfoValue(object):
    """The value of the dictionary storing information for existing BUILD files."""

    # Attribute value is None.
    NONE = "None"

    # Attribute value is not set, or target does not exist.
    MISSING = None
    pass


class AttributeValue(InfoValue):
    def __init__(self,
                 value: Optional[str | list[Any]] = InfoValue.MISSING,
                 comment: Optional[str] = InfoValue.MISSING):
        """
        Args:
            value: string-representation of the attribute value.
              - If attribute value is None, this is the string "None".
              - If attribute value is not set, this is the value None
              - If attribute value is a list, the program will try to parse
                the Starlark list and store it with a Python list with the
                best effort. If that fails, fall back to the string
                representation.
            comment: string that contains the comment.
              - If comment is not found, this is the value None.
        """
        self.value = value
        self.comment = comment

    def __str__(self):
        return self.value


class TargetValue(InfoValue):
    def __init__(self, kind: Optional[str] = InfoValue.MISSING):
        self.kind = kind

    def __str__(self):
        return self.kind


class BuildozerCommandBuilder(object):
    def __init__(self, args):
        """
        Args:
             args: Namespace containing command-line arguments
        """

        self.old_env: dict[str, str] = order_dict_by_key(os.environ)

        # Test overrides sys.std* and os.environ
        self.new_env: dict[str, str] = order_dict_by_key(json.loads(subprocess.check_output(
            "source build/kernel/_setup_env.sh > /dev/null && build/kernel/kleaf/dump_env.py",
            shell=True, stderr=sys.stderr, env=os.environ)))
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

        self.buildozer = find_buildozer()

        # set elsewhere
        self.existing: Optional[dict[InfoKey, InfoValue]] = None
        self.dist_targets: Optional[set[str]] = None
        self.out_file: Optional[TextIO] = None

    def __enter__(self):
        self.out_file = tempfile.NamedTemporaryFile("w+")
        self.out_file.__enter__()
        return self

    def __exit__(self, exc, value, tb):
        self.out_file.__exit__(exc, value, tb)
        self.out_file = None

    def _get_all_info(self, keys: Iterable[InfoKey]) -> dict[InfoKey, InfoValue]:
        """Get all interesting information of existing BUILD files.

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
        """Execute a buildozer print command."""
        value = InfoValue.MISSING

        try:
            # Test overrides sys.std* and os.environ
            value = subprocess.check_output(
                [self.buildozer, f"{print_command} {attribute}", target],
                text=True, stderr=sys.stderr, env=os.environ).strip()
        except subprocess.CalledProcessError:
            pass

        return value

    def _get_target(self, target: str) -> Tuple[InfoKey, InfoValue]:
        """Get information of a single target from existing BUILD files.

        Args:
            target: full label of target.
        """
        kind = self._buildozer_print(target, "print", "kind")
        return TargetKey(target), TargetValue(kind)

    def _get_attr(self, key: AttributeKey) -> Tuple[InfoKey, InfoValue]:
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
        """Write a buildozer command that creates a target."""
        if package is None:
            package = self.package
        ensure_build_file(package)
        new_target_pkg = f"//{package}:__pkg__"
        new_target = f"//{package}:{name}"
        key = TargetKey(new_target)

        existing_kind = InfoValue.MISSING
        if key in self.existing:
            existing_kind = check_isinstance(self.existing[key], TargetValue).kind

        if existing_kind is InfoValue.MISSING:
            self.out_file.write(f"""
                fix movePackageToTop|{new_target_pkg}
                new_load {load_from} {kind}|{new_target_pkg}
                new {kind} {name}|{new_target_pkg}
""")
            self.existing[key] = TargetValue(kind=kind)
        elif existing_kind != kind:
            logging.warning(f"Forcifully setting {new_target} from {existing_kind} to {kind}")
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
        """Add comment to attribute of the given target.

        If the attribute does not exist (assuming that it is queried
        with _get_all_info), it is set to None.
        """
        # comments can only be set to existing attributes. Set it to None if the
        # attribute does not already exist.
        self._set_attr(target, attribute, InfoValue.NONE, command="set_if_absent")

        attr_val = check_isinstance(self.existing[AttributeKey(target, attribute)], AttributeValue)
        if should_set_comment_pred(attr_val):
            logging.info(f"pred passes: {attr_val.comment}")
            if attr_val.comment is InfoValue.MISSING or \
                    expected_comment not in attr_val.comment:
                esc_comment = expected_comment.replace(" ", "\\ ")
                self.out_file.write(f"""comment {attribute} {esc_comment}|{target}\n""")
                attr_val.comment = expected_comment

    def _add_target_comment(self, target: str, comment_lines: Sequence[str]):
        """Add comment to a given target."""

        # "comment" command on targets will override existing comments,
        # so there is no need to check existing comments.
        content = "\\n".join(comment_lines)
        content = content.replace(" ", "\\ ")
        if content:
            self.out_file.write(f"""comment {content}|{target}\n""")

    def _set_attr(self, target, attribute, value, quote=False, command="set"):
        """Write a buildozer command that sets an attribute.

        Args:
            target: full label of target
            attribute: attribute name
            value: value of attribute
            quote: if value should be quoted in the buildozer command
            command: buildozer command. Either "set" or "set_if_absent"
        """
        if command not in ("set", "set_if_absent"):
            fail(f"Unknown command {command} for _set_attr")

        command_value = f'"{value}"' if quote else value
        self.out_file.write(f"""{command} {attribute} {command_value}|{target}\n""")

        # set value in self.existing
        key = AttributeKey(target, attribute)
        if key not in self.existing:
            self.existing[key] = AttributeValue()
        attr_val: AttributeValue = check_isinstance(self.existing[key], AttributeValue)
        if command == "set" or (command == "set_if_absent" and attr_val.value is InfoValue.MISSING):
            attr_val.value = value

    def _add_attr(self, target, attribute, value, quote=False):
        """Write a buildozer command that adds to an attribute.

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
        attr_val: AttributeValue = check_isinstance(self.existing[key], AttributeValue)
        if attr_val.value is InfoValue.MISSING:
            attr_val.value = f"[{command_value}]"
        else:
            # We could flatten this list, but we don't care about the value for now.
            attr_val.value += f" + [{command_value}]"

    def _create_buildozer_commands(self) -> None:
        """Filled in self.out_file."""
        common = self.args.ack

        self.dist_targets = set()

        target = self._new("kernel_build", self.target_name)
        dist = self._new("copy_to_dist_dir", self.dist_name,
                         load_from="//build/bazel_common_rules/dist:dist.bzl", add_to_dist=False)
        self._set_attr(dist, "flat", True)

        images = None
        modules_install = None

        target_comment = []
        unknowns = []  # List of unknown build configs

        for key, value in self.new_env.items():
            esc_value = value.replace(" ", "\\ ")
            if type(self)._is_ignored_build_config(key):
                continue
            elif key in _NOT_SUPPORTED_BUILD_CONFIGS and value:
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
                    f"FIXME: PRE_DEFCONFIG_CMDS: Don't forget to modify to write to $OUT_DIR: https://android.googlesource.com/kernel/build/+/refs/heads/master/kleaf/docs/errors.md#defconfig-readonly")
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
                # TODO(b/241162984): Fix MODULES_OPTIONS; it should be a string
                images = self._new("kernel_images", self.images_name)
                self._add_comment(images, "module_options",
                                  f"TODO(b/241162984): Support MODULE_OPTIONS")
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
            elif key in self.old_env:
                if self.old_env[key] == value:
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
                                  f"FIXME: kernel_modules should include the list of kernel_module()s")

            self._set_attr(images, "kernel_build", target, quote=True)
            self._set_attr(images, "kernel_modules_install", modules_install, quote=True)

        if "KERNEL_DIR" in self.new_env and self.new_env["KERNEL_DIR"] != self.package:
            if self.new_env["KERNEL_DIR"].removesuffix("/") == common:
                self._set_attr(target, "srcs", DEFAULT_KERNEL_BUILD_SRCS, quote=False,
                               command="set_if_absent")
                self._add_attr(target, "srcs", f"//{common}:kernel_aarch64_sources", quote=True)
            else:
                self._add_comment(target, "srcs",
                                  f"""FIXME: add files from KERNEL_DIR {self.new_env["KERNEL_DIR"]}""")

        self._add_comment(target, "base_kernel",
                          f"FIXME: base_kernel should be migrated to //{common}:kernel_aarch64.",
                          lambda attr_val: attr_val.value not in (
                              f"//{common}:kernel_aarch64", f"//{common}:kernel"))

        self._add_comment(target, "module_outs",
                          f"FIXME: set to the list of in-tree modules. You may run `tools/bazel build {target}` and follow the instructions in the error message.",
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
            # Test overrides sys.std* and os.environ
            subprocess.check_call(buildozer_args, stdout=sys.stdout, stderr=sys.stderr,
                                  env=os.environ)
        except subprocess.CalledProcessError as e:
            if e.returncode == BUILDOZER_NO_CHANGES_MADE:
                logging.info("No files were changed.")
            else:
                raise

    def run(self):
        # Dry run to see what attributes / targets will be added
        with self:
            self.existing = dict()
            # This modifies self.existing
            self._create_buildozer_commands()

            # self.existing.keys() = things we would change.
            # Get the existing information of these things in BUILD files
            self.existing = self._get_all_info(self.existing.keys())

            # The buildozer command file is deleted.

        # Create another buildozer command file. This time, actually run buildozer with it.
        with self:
            self._create_buildozer_commands()
            self._run_buildozer()


def parse_args(argv):
    parser = argparse.ArgumentParser()
    parser.add_argument("-t", "--target",
                        help="Name of target. Otherwise, infer from the name of the build.config file.")
    parser.add_argument("--log", help="log level", default="warning")
    parser.add_argument("-k",
                        help="buildozer keep going. Use when targets are already defined. There may be duplicated FIXME comments.",
                        action="store_true")
    parser.add_argument("--stdout",
                        help="buildozer write changed BUILD file to stdout (dry run)",
                        action="store_true")
    parser.add_argument("--ack", help="path to ACK source tree", default="common")
    return parser.parse_args(argv)


def config_logging(args):
    numeric_level = getattr(logging, args.log.upper(), None)
    if not isinstance(numeric_level, int):
        raise ValueError('Invalid log level: %s' % args.log)
    logging.basicConfig(level=numeric_level, format='%(levelname)s: %(message)s')


def main(argv):
    args = parse_args(argv)
    config_logging(args)
    BuildozerCommandBuilder(args=args).run()


if __name__ == "__main__":
    main(sys.argv[1:])
