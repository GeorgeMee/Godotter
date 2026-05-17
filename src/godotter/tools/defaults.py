from godotter.tools.files import ListFiles, ReadFile, SearchCode
from godotter.tools.git import GitBranchTool, GitDiffTool, GitLogTool, GitStatusTool
from godotter.tools.memory import SaveMemory
from godotter.tools.patch import ApplyPatch, GeneratePatch
from godotter.tools.runtime import (
    HeadlessRunTool,
    ProjectInfoTool,
    RuntimeDoctorTool,
    SceneCreateTool,
    SceneInspectTool,
    SceneValidateTool,
    ScriptLintTool,
    UidFixTool,
)
from godotter.tools.validate import ValidateProject


def build_default_tools() -> list[object]:
    return [
        ReadFile(),
        ListFiles(),
        SearchCode(),
        GitStatusTool(),
        GitDiffTool(),
        GitLogTool(),
        GitBranchTool(),
        SaveMemory(),
        GeneratePatch(),
        ApplyPatch(),
        ValidateProject(),
        ProjectInfoTool(),
        SceneCreateTool(),
        SceneInspectTool(),
        SceneValidateTool(),
        ScriptLintTool(),
        HeadlessRunTool(),
        RuntimeDoctorTool(),
        UidFixTool(),
    ]
