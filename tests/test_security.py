from fastapi import HTTPException
import pytest

from app.config import WORKSPACE
from app.security import resolve_workspace_path


def test_resolves_relative_path_inside_workspace():
    resolved = resolve_workspace_path("project/file.txt")

    assert resolved == WORKSPACE / "project" / "file.txt"


def test_rejects_parent_directory_escape():
    with pytest.raises(HTTPException) as exc:
        resolve_workspace_path("../outside.txt")

    assert exc.value.status_code == 403


def test_rejects_absolute_path_escape():
    outside = WORKSPACE.parent / "outside.txt"

    with pytest.raises(HTTPException) as exc:
        resolve_workspace_path(str(outside))

    assert exc.value.status_code == 403
