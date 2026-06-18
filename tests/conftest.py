# Copyright 2026 Logan Fick
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Shared pytest hooks for the test suite."""

import pytest


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(
    config: pytest.Config,
    items: list[pytest.Item],
) -> None:
    """Apply and enforce primary test category markers by directory."""
    tests_root = config.rootpath / "tests"
    category_roots = (
        (tests_root / "unit", pytest.mark.unit),
        (tests_root / "integration", pytest.mark.integration),
    )
    uncategorized: list[str] = []

    for item in items:
        for category_root, marker in category_roots:
            if item.path.is_relative_to(category_root):
                item.add_marker(marker)
                break
        else:
            uncategorized.append(str(item.path.relative_to(config.rootpath)))

    if uncategorized:
        formatted_paths = "\n".join(f"  - {path}" for path in uncategorized)
        msg = (
            "Tests must live under tests/unit or tests/integration.\n"
            f"Uncategorized tests:\n{formatted_paths}"
        )
        raise pytest.UsageError(msg)
