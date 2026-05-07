import argparse
import os
from pathlib import Path

from .core import generate_report, load_runtime_env
from .tp_qemu_repo import DEFAULT_TP_QEMU_GIT_URL, ensure_tp_qemu_repo, should_sync_tp_qemu
from .utils import resolve_path_argument, get_env_var


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Virt QE patch analysis agent")
    parser.add_argument("--input", required=True, help="Patch file path or directory containing *.patch files.")
    parser.add_argument("--mapping", default=".agent_context/mapping_index.json", help="Path to mapping index JSON.")
    parser.add_argument("--experience", default=".agent_context/experience_base.md", help="Path to expert experience markdown.")
    parser.add_argument("--cursorrules", default=".cursorrules", help="Path to core rules file.")
    parser.add_argument("--output", default="test_plan_report.md", help="Output markdown report path.")
    parser.add_argument(
        "--mode",
        choices=("single", "cluster"),
        default="single",
        help="single: analyze per commit; cluster: analyze by subsystem cluster.",
    )
    parser.add_argument("--verbose", action="store_true", help="Print detailed retrieval and filtering process.")
    parser.add_argument("--tests-root", default="tp-qemu", help="Root directory that contains qemu/tests scripts.")
    parser.add_argument(
        "--model",
        default=os.getenv("MODEL_NAME", "deepseek-v4-flash"),
        help="Model/deployment name. Defaults to MODEL_NAME env var when set.",
    )
    parser.add_argument(
        "--excel-output",
        default="",
        help="Optional CSV output path for Excel-friendly table view.",
    )
    return parser


def main() -> None:
    project_root = Path.cwd()
    load_runtime_env(project_root)
    args = build_parser().parse_args()
    tests_root = resolve_path_argument(args.tests_root, project_root)

    if should_sync_tp_qemu():
        repo_url = get_env_var("TP_QEMU_GIT_URL", DEFAULT_TP_QEMU_GIT_URL)
        branch = get_env_var("TP_QEMU_BRANCH") or None
        ensure_tp_qemu_repo(tests_root, repo_url, branch=branch, verbose=args.verbose)

    generate_report(
        input_path=Path(args.input),
        mapping_path=Path(args.mapping),
        experience_path=Path(args.experience),
        cursorrules_path=Path(args.cursorrules),
        tests_root=tests_root,
        output_path=Path(args.output),
        excel_output_path=Path(args.excel_output) if args.excel_output else None,
        model=args.model,
        mode=args.mode,
    )
    print(f"Report generated: {args.output}")
    if args.excel_output:
        print(f"Excel-friendly CSV generated: {args.excel_output}")
