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

sign_module_deps = [
    # Kernel build time module signining utility and keys
    # Only available if build_config has CONFIG_MODULE_SIG=y and
    # CONFIG_MODULE_SIG_PROTECT=y
    # android13-5.10+ and android-mainline
    "scripts/sign-file",
    "certs/signing_key.pem",
    "certs/signing_key.x509",
]
