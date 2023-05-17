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
Like a filegroup but for a single label.
"""

def _file_impl(ctx):
    return DefaultInfo(depset(ctx.files.src))

file = rule(
    doc = "Like a filegroup but for a single label.",
    implementation = _file_impl,
    attr = {
        "src": attr.label(allow_files = True),
    },
)
