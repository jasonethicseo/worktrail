"""공개 트리는 allowlist 밖의 새 파일을 자동으로 싣지 않는다."""
from __future__ import annotations

import pytest

from tools.public_manifest import build_manifest


def test_허용한_파일과_폴더만_가고_예외는_다시_빠진다():
    tracked = [
        "README.md",
        "casebook/core/worktrail.py",
        "casebook/private_note.py",
        "tests/test_worktrail.py",
        "tools/hooks/install_git_hook.sh",
        "tools/deploy.sh",
        "new-private-file.txt",
    ]
    includes = ["README.md", "casebook/", "tests/", "tools/hooks/"]
    excludes = ["casebook/private_note.py"]

    assert build_manifest(tracked, includes, excludes) == [
        "README.md",
        "casebook/core/worktrail.py",
        "tests/test_worktrail.py",
        "tools/hooks/install_git_hook.sh",
    ]


def test_추적되지_않은_allowlist_항목은_오타로_보고_거절한다():
    with pytest.raises(ValueError, match="missing/"):
        build_manifest(["README.md"], ["README.md", "missing/"], [])
