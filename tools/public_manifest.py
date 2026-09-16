"""추적 파일에서 공개 allowlist를 적용하고 예외를 뺀다."""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Iterable


def read_rules(path: Path) -> list[str]:
    """빈 줄과 주석을 빼고 파일·폴더 규칙을 읽는다."""
    return [
        line
        for raw in path.read_text(encoding="utf-8").splitlines()
        if (line := raw.strip()) and not line.startswith("#")
    ]


def matches(path: str, rule: str) -> bool:
    """끝에 /가 붙은 규칙은 폴더, 그 밖은 정확한 파일 경로다."""
    return path.startswith(rule) if rule.endswith("/") else path == rule


def build_manifest(
    tracked: Iterable[str], includes: Iterable[str], excludes: Iterable[str]
) -> list[str]:
    paths = list(dict.fromkeys(tracked))
    include_rules = list(dict.fromkeys(includes))
    exclude_rules = list(dict.fromkeys(excludes))

    missing = [rule for rule in include_rules if not any(matches(path, rule) for path in paths)]
    if missing:
        raise ValueError("추적 파일과 맞지 않는 공개 항목: " + ", ".join(missing))

    return [
        path
        for path in paths
        if any(matches(path, rule) for rule in include_rules)
        and not any(matches(path, rule) for rule in exclude_rules)
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tracked", required=True, type=Path)
    parser.add_argument("--include", required=True, type=Path)
    parser.add_argument("--exclude", required=True, type=Path)
    args = parser.parse_args()

    try:
        manifest = build_manifest(
            args.tracked.read_text(encoding="utf-8").splitlines(),
            read_rules(args.include),
            read_rules(args.exclude),
        )
    except ValueError as exc:
        parser.error(str(exc))

    for path in manifest:
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
