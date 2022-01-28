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

import functools
import os
import subprocess
import sys


def exec_silent(fn):
    """
    Execute fn. On CalledProcessError, exit with return code immediately, but
    don't print Python stack traces.
    """
    try:
        return fn()
    except subprocess.CalledProcessError as e:
        sys.exit(e.returncode)


def check_output(*args, **kwargs):
    return exec_silent(functools.partial(subprocess.check_output, *args, **kwargs))


def check_call(*args, **kwargs):
    return exec_silent(functools.partial(subprocess.check_call, *args, **kwargs))


def main(root_dir, bazel_args, env):
    env = env.copy()

    source_date_epoch = check_output(
        ['{root_dir}/build/kernel/kleaf/source_date_epoch.sh'.format(root_dir=root_dir)],
        text=True).strip()
    if not source_date_epoch:
        sys.stderr.write("Unable to determine SOURCE_DATE_EPOCH, fallback to 0\n")
        source_date_epoch = "0"
    env["SOURCE_DATE_EPOCH"] = source_date_epoch

    bazel_path = "{root_dir}/prebuilts/bazel/linux-x86_64/bazel".format(root_dir=root_dir)
    bazel_jdk_path = "{root_dir}/prebuilts/jdk/jdk11/linux-x86".format(root_dir=root_dir)
    bazelrc_name = "build/kleaf/common.bazelrc"

    absolute_out_dir = "{root_dir}/out".format(root_dir=root_dir)

    command_args = [
        bazel_path,
        "--server_javabase={}".format(bazel_jdk_path),
        "--output_user_root={}/bazel/output_user_root".format(absolute_out_dir),
        "--host_jvm_args=-Djava.io.tmpdir={}/bazel/javatmp".format(
            absolute_out_dir),
        "--bazelrc={root_dir}/{bazelrc_name}".format(
            root_dir=root_dir,
            bazelrc_name=bazelrc_name)
    ]
    command_args += bazel_args

    check_call(command_args, env=env)


if __name__ == "__main__":
    main(root_dir=sys.argv[1], bazel_args=sys.argv[2:], env=os.environ)
