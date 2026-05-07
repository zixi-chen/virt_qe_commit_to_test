#!/usr/bin/env python3
import argparse
import json
import os
import re
from pathlib import Path

from qe_agent.core import load_runtime_env
from qe_agent.tp_qemu_repo import DEFAULT_TP_QEMU_GIT_URL, ensure_tp_qemu_repo, should_sync_tp_qemu
from qe_agent.utils import resolve_path_argument, get_env_var

TYPE_PATTERN = re.compile(r"type\s*=\s*([^ \n]+)")


def build_mapping(cfg_root: Path) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for cfg_file in sorted(cfg_root.rglob("*.cfg")):
        content = cfg_file.read_text(encoding="utf-8", errors="ignore")
        for match in TYPE_PATTERN.findall(content):
            type_name = match.strip()
            if type_name:
                mapping[type_name] = f"qemu/tests/{type_name}.py"
    return mapping


def main() -> None:
    workspace_root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description="Build .agent_context/mapping_index.json from tp-qemu cfg files.")
    parser.add_argument(
        "--tests-root",
        default="tp-qemu",
        help="Directory that contains qemu/tests (same as qe_agent --tests-root).",
    )
    args = parser.parse_args()

    load_runtime_env(workspace_root)
    tests_root = resolve_path_argument(args.tests_root, workspace_root)

    if should_sync_tp_qemu():
        repo_url = get_env_var("TP_QEMU_GIT_URL", DEFAULT_TP_QEMU_GIT_URL)
        branch = get_env_var("TP_QEMU_BRANCH") or None
        ensure_tp_qemu_repo(tests_root, repo_url, branch=branch, verbose=True)

    cfg_root = tests_root / "qemu" / "tests" / "cfg"
    output_path = workspace_root / ".agent_context" / "mapping_index.json"

    output_path.parent.mkdir(parents=True, exist_ok=True)

    if not cfg_root.exists():
        output_path.write_text("{}\n", encoding="utf-8")
        print(f"Warning: cfg path does not exist: {cfg_root}")
        print(f"Empty mapping written to: {output_path}")
        return

    mapping = build_mapping(cfg_root)
    output_path.write_text(
        json.dumps(mapping, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"Indexed {len(mapping)} types from: {cfg_root}")
    print(f"Mapping output: {output_path}")


if __name__ == "__main__":
    main()
