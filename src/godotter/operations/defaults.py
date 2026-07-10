from __future__ import annotations

from godotter.operations.analysis_ops import build_analysis_operations
from godotter.operations.diagnostic_ops import build_diagnostic_operations
from godotter.operations.file_ops import build_file_operations
from godotter.operations.git_ops import build_git_operations
from godotter.operations.patch_ops import build_patch_operations
from godotter.operations.run_ops import build_run_operations
from godotter.operations.registry import OperationRegistry
from godotter.operations.runtime_ops import build_runtime_operations
from godotter.operations.uid_ops import build_uid_operations


def build_default_operations() -> OperationRegistry:
    return OperationRegistry(
        [
            *build_analysis_operations(),
            *build_diagnostic_operations(),
            *build_file_operations(),
            *build_git_operations(),
            *build_patch_operations(),
            *build_runtime_operations(),
            *build_run_operations(),
            *build_uid_operations(),
        ]
    )
