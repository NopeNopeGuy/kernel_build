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

"""Utilities for handling `--cache_dir`."""

load("@bazel_skylib//lib:shell.bzl", "shell")
load("@bazel_skylib//rules:common_settings.bzl", "BuildSettingInfo")

def _get_step(ctx, common_config_tags, symlink_name):
    """Returns a step for caching the output directory.

    Args:
        ctx: ctx
        common_config_tags: from `kernel_env[KernelEnvAttrInfo]`
        symlink_name: name of the "last" symlink

    Returns:
      A struct with these fields:

      * inputs
      * tools
      * cmd
      * outputs
      * post_cmd
    """

    # Use a local cache directory for ${OUT_DIR} so that, even when this _kernel_build
    # target needs to be rebuilt, we are using $OUT_DIR from previous invocations. This
    # boosts --config=local builds. See (b/235632059).
    cache_dir_cmd = ""
    post_cmd = ""
    inputs = []
    tools = []
    if ctx.attr._config_is_local[BuildSettingInfo].value:
        if not ctx.attr._cache_dir[BuildSettingInfo].value:
            fail("--config=local requires --cache_dir.")

        tools.append(ctx.executable._cache_dir_config_tags)

        cache_dir_cmd = """
            KLEAF_CONFIG_TAGS_TMP=$(mktemp kleaf_config_tags.json.XXXXXX)
            # cache_dir_config_tags.py requires --dest to not exist, otherwise it compares
            # and fails.
            rm -f "${{KLEAF_CONFIG_TAGS_TMP}}"

            # Add label of this target.
            {cache_dir_config_tags} \\
                --base {common_config_tags} \\
                --target {label} \\
                --dest "${{KLEAF_CONFIG_TAGS_TMP}}"

            export OUT_DIR_SUFFIX=$(cat ${{KLEAF_CONFIG_TAGS_TMP}} | sha1sum -b | cut -c-8)

            KLEAF_CACHED_COMMON_OUT_DIR={cache_dir}/${{OUT_DIR_SUFFIX}}
            export OUT_DIR=${{KLEAF_CACHED_COMMON_OUT_DIR}}/${{KERNEL_DIR}}
            mkdir -p "${{OUT_DIR}}"

            # Reconcile differences between expected file and target file, if any,
            # to prevent hash collision.
            {cache_dir_config_tags} \\
                --base "${{KLEAF_CONFIG_TAGS_TMP}}" \\
                --dest "${{KLEAF_CACHED_COMMON_OUT_DIR}}/kleaf_config_tags.json"

            unset KLEAF_CACHED_COMMON_OUT_DIR

            rm -f "${{KLEAF_CONFIG_TAGS_TMP}}"
            unset KLEAF_CONFIG_TAGS_TMP
        """.format(
            label = shell.quote(str(ctx.label)),
            cache_dir_config_tags = ctx.executable._cache_dir_config_tags.path,
            cache_dir = ctx.attr._cache_dir[BuildSettingInfo].value,
            common_config_tags = common_config_tags.path,
        )

        post_cmd = """
            ln -sfT ${{OUT_DIR_SUFFIX}} {cache_dir}/last_{symlink_name}
        """.format(
            cache_dir = ctx.attr._cache_dir[BuildSettingInfo].value,
            symlink_name = symlink_name,
        )
    return struct(
        inputs = inputs,
        tools = tools,
        cmd = cache_dir_cmd,
        outputs = [],
        post_cmd = post_cmd,
    )

def _attrs():
    return {
        "_config_is_local": attr.label(default = "//build/kernel/kleaf:config_local"),
        "_cache_dir": attr.label(default = "//build/kernel/kleaf:cache_dir"),
        "_cache_dir_config_tags": attr.label(
            default = "//build/kernel/kleaf/impl:cache_dir_config_tags",
            executable = True,
            cfg = "exec",
        ),
    }

cache_dir = struct(
    get_step = _get_step,
    attrs = _attrs,
)
