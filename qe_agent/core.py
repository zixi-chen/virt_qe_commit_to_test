from __future__ import annotations

import json
import os
import re
import csv
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple, NamedTuple
from urllib import error as urllib_error
from urllib import request as urllib_request

from .config import (
    DEFAULT_RETRIEVAL_RULES,
    HEAVY_KEYWORDS,
    HEAVY_TRIGGER_PATTERNS,
    MAX_TEST_SOURCE_CHARS,
    MAX_TEST_SOURCE_LINES,
    PRIORITY_KEYWORDS,
    PROTECTED_MARKERS,
    SYSTEM_PROMPT,
)
from .models import CommitRecord
from .utils import (
    is_str, is_list, is_dict, is_tuple,
    normalize_string, normalize_path, get_stem_lower,
    safe_json_load, deduplicate_list, validate_api_key
)


# Global constants
MAX_DIFF_CONTEXT_CHARS = 60000
MAX_DIFF_FILES = 6
MAX_HUNKS_PER_FILE = 2
MAX_HUNK_LINES = 80


# Data class for configuration to avoid global state
class AnalysisConfig:
    def __init__(self, mapping_index: Dict[str, str] = None, retrieval_rules: Dict = None):
        self.mapping_index = mapping_index or {}
        self.retrieval_rules = retrieval_rules or dict(DEFAULT_RETRIEVAL_RULES)

    def get_mapping(self) -> Dict[str, str]:
        return self.mapping_index

    def get_rules(self) -> Dict[str, object]:
        return self.retrieval_rules

    def load_mapping_from_file(self, mapping_path: Path) -> Dict[str, str]:
        data = safe_json_load(mapping_path)
        clean_mapping = {}
        if data:
            for test_type, script_path in data.items():
                if is_str(test_type) and is_str(script_path) and script_path.endswith(".py"):
                    clean_mapping[test_type] = script_path
        return clean_mapping

    def load_retrieval_rules_from_file(self, rules_path: Path) -> Dict[str, object]:
        rules: Dict[str, object] = dict(DEFAULT_RETRIEVAL_RULES)
        data = safe_json_load(rules_path)
        if not data:
            return rules

        path_map = data.get("path_subsystem_map")
        if is_list(path_map):
            normalized_map = []
            for item in path_map:
                if is_tuple(item) and len(item) == 2:
                    normalized_map.append((str(item[0]), str(item[1])))
            if normalized_map:
                rules["path_subsystem_map"] = normalized_map

        for key in (
            "kvm_banned_test_keywords",
            "arch_x86_kvm_test_allow_keywords",
            "tdx_domain_path_markers",
            "tdx_domain_subject_keywords",
            "tdp_mmu_association_keywords",
            "uapi_path_markers",
            "uapi_test_keywords",
            "refactor_subject_keywords",
            "optional_tool_tests_for_refactor",
        ):
            values = data.get(key)
            if is_list(values):
                rules[key] = [str(v) if key == "optional_tool_tests_for_refactor" else normalize_string(v) for v in values]

        forced = data.get("forced_domain_tests")
        if is_dict(forced):
            normalized = {}
            for domain, tests in forced.items():
                if is_list(tests):
                    normalized[normalize_string(domain)] = [str(t) for t in tests]
            if normalized:
                rules["forced_domain_tests"] = normalized
        return rules


def load_runtime_env(project_root: Path) -> None:
    """Load environment variables from .env file."""
    try:
        from dotenv import load_dotenv
    except Exception:
        return
    load_dotenv(project_root / ".env", override=True)
    if not get_env_var("DEEPSEEK_API_KEY"):
        load_dotenv(project_root / ".env.example", override=False)


def normalize_tokens(value: str) -> List[str]:
    return [tok for tok in re.split(r"[^a-zA-Z0-9]+", normalize_string(value)) if tok]


def get_relevant_tests(module_name: str, config: AnalysisConfig, top_k: int = 12) -> List[Tuple[str, str, int]]:
    mapping_index = config.get_mapping()
    module_tokens = set(normalize_tokens(module_name))
    module_compact = normalize_string(module_name)
    scored: List[Tuple[str, str, int]] = []

    for test_type, script_path in mapping_index.items():
        t_tokens = set(normalize_tokens(test_type))
        p_tokens = set(normalize_tokens(Path(script_path).stem))
        overlap = module_tokens & (t_tokens | p_tokens)
        score = len(overlap) * 2

        test_type_lower = normalize_string(test_type)
        stem_lower = get_stem_lower(script_path)

        if test_type_lower in module_compact:
            score += 4
        if stem_lower in module_compact:
            score += 3
        if module_tokens and any(tok in test_type_lower for tok in module_tokens):
            score += 2
        if module_tokens and any(tok in stem_lower for tok in module_tokens):
            score += 2
        if score > 0:
            scored.append((test_type, script_path, score))

    scored.sort(key=lambda x: (-x[2], x[1]))
    return scored[:top_k]


def extract_changed_files(diff_text: str) -> List[str]:
    files = [m.group(2) for m in re.finditer(r"^diff --git a/(.+?) b/(.+)$", diff_text, flags=re.M)]
    return list(dict.fromkeys(files))


def parse_patch_text(patch_text: str, source_name: str) -> List[CommitRecord]:
    from_matches = list(re.finditer(r"^From ([0-9a-f]{7,40}) ", patch_text, flags=re.M))
    commits: List[CommitRecord] = []
    if not from_matches:
        subject_match = re.search(r"^Subject:\s*(.+)$", patch_text, flags=re.M)
        commits.append(
            CommitRecord(
                commit_id="unknown",
                subject=subject_match.group(1).strip() if subject_match else "Unknown Subject",
                changed_files=extract_changed_files(patch_text),
                diff_text=patch_text,
                source_name=source_name,
            )
        )
        return commits

    for idx, match in enumerate(from_matches):
        start = match.start()
        end = from_matches[idx + 1].start() if idx + 1 < len(from_matches) else len(patch_text)
        chunk = patch_text[start:end]
        subject_match = re.search(r"^Subject:\s*(.+)$", chunk, flags=re.M)
        commits.append(
            CommitRecord(
                commit_id=match.group(1),
                subject=subject_match.group(1).strip() if subject_match else "Unknown Subject",
                changed_files=extract_changed_files(chunk),
                diff_text=chunk,
                source_name=source_name,
            )
        )
    return commits


def collect_commits(input_path: Path) -> List[CommitRecord]:
    if input_path.is_file():
        return parse_patch_text(read_text(input_path), input_path.name)
    if input_path.is_dir():
        records: List[CommitRecord] = []
        for patch_file in sorted(input_path.glob("*.patch")):
            records.extend(parse_patch_text(read_text(patch_file), patch_file.name))
        return records
    raise FileNotFoundError(f"Input path does not exist: {input_path}")


def is_heavy_needed(commit: CommitRecord, config: AnalysisConfig) -> bool:
    rules = config.get_rules()
    heavy_patterns = rules.get("heavy_trigger_patterns", HEAVY_TRIGGER_PATTERNS)
    text = f"{normalize_string(commit.subject)}\n{normalize_string(commit.diff_text)}"
    return any(re.search(pat, text) for pat in heavy_patterns)


def is_priority_test(path: str, config: AnalysisConfig) -> bool:
    rules = config.get_rules()
    priority_keywords = rules.get("priority_keywords", PRIORITY_KEYWORDS)
    return any(keyword in get_stem_lower(path) for keyword in priority_keywords)


def is_heavy_test(path: str, config: AnalysisConfig) -> bool:
    rules = config.get_rules()
    heavy_keywords = rules.get("heavy_keywords", HEAVY_KEYWORDS)
    return any(keyword in get_stem_lower(path) for keyword in heavy_keywords)


def is_refactor_commit(commit: CommitRecord, config: AnalysisConfig) -> bool:
    rules = config.get_rules()
    refactor_keywords = rules.get("refactor_subject_keywords", [])
    return any(keyword in normalize_string(commit.subject) for keyword in refactor_keywords)


def has_explicit_specialized_calls(commit: CommitRecord, domain: str) -> bool:
    text = normalize_string(commit.diff_text)
    if domain == "tdx":
        return bool(re.search(r"is_tdx_vm\(|\btdx_", text))
    if domain == "sev_snp":
        return bool(re.search(r"is_sev|is_snp|\bsev_|\bsnp_", text))
    return False


def is_tdx_related_commit(commit: CommitRecord, config: AnalysisConfig) -> bool:
    rules = config.get_rules()
    subject = normalize_string(commit.subject)
    changed = [normalize_path(p) for p in commit.changed_files]
    tdx_keywords = rules.get("tdx_domain_subject_keywords", [])
    tdx_markers = rules.get("tdx_domain_path_markers", [])

    if any(keyword in subject for keyword in tdx_keywords):
        return True
    return any(marker in path for path in changed for marker in tdx_markers)


def map_path_to_subsystem(changed_path: str, config: AnalysisConfig) -> str:
    normalized = normalize_path(changed_path)
    rules = config.get_rules()
    path_map = rules.get("path_subsystem_map", [])

    for prefix, subsystem in path_map:
        if normalized.startswith(normalize_string(prefix)):
            return subsystem
    return "General"


def commit_subsystems(commit: CommitRecord, config: AnalysisConfig) -> List[str]:
    subsystems = [map_path_to_subsystem(path, config) for path in commit.changed_files]
    unique = deduplicate_list(subsystems)
    return unique or ["General"]


def needs_core_arch_fallback(commit: CommitRecord, config: AnalysisConfig) -> bool:
    return any(normalize_path(path).startswith("arch/x86") for path in commit.changed_files)


def apply_core_arch_fallback(commit: CommitRecord, tests: List[str], config: AnalysisConfig) -> Tuple[List[str], List[str]]:
    notes: List[str] = []
    if not needs_core_arch_fallback(commit, config):
        return tests, notes

    mapping_index = config.get_mapping()
    fallback_candidates = []
    if "boot" in mapping_index:
        fallback_candidates.append(mapping_index["boot"])
    fallback_candidates.append("qemu/tests/boot.py")
    if "smp" in mapping_index:
        fallback_candidates.append(mapping_index["smp"])
    fallback_candidates.append("qemu/tests/smp.py")

    for candidate in fallback_candidates:
        if candidate and candidate not in tests:
            tests.append(candidate)
            notes.append(f"Added core-arch baseline regression test: {candidate}")
            break

    return deduplicate_list(tests), notes


def apply_forced_domain_tests(commit: CommitRecord, tests: List[str], config: AnalysisConfig) -> Tuple[List[str], List[str]]:
    notes: List[str] = []
    if is_tdx_related_commit(commit, config):
        rules = config.get_rules()
        forced = rules.get("forced_domain_tests", {}).get("tdx", [])
        added = [test_path for test_path in forced if test_path not in tests]
        tests.extend(added)
        if added:
            notes.append(f"Added forced TDX domain tests: {', '.join(added)}")

    return deduplicate_list(tests), notes


def gather_candidate_tests(commit: CommitRecord, config: AnalysisConfig) -> List[Tuple[str, int]]:
    score_by_path: Dict[str, int] = {}
    matched_types_by_path: Dict[str, List[str]] = defaultdict(list)

    for changed_file in commit.changed_files:
        file_hits = get_relevant_tests(changed_file, config)
        for test_type, test_path, score in file_hits:
            score_by_path[test_path] = max(score_by_path.get(test_path, 0), score)
            matched_types_by_path[test_path].append(test_type)

    diff_lower = normalize_string(commit.diff_text)
    is_refactor = is_refactor_commit(commit, config)
    has_tdx_domain = is_tdx_related_commit(commit, config)
    has_sev_snp_domain = any(normalize_path(path) in ("svm/sev", "svm/snp") for path in commit.changed_files) or any(
        normalize_string(keyword) in ("sev", "snp") for keyword in [commit.subject]
    )

    rules = config.get_rules()
    mapping_index = config.get_mapping()

    explicit_type_boosts = (
        "sev_basic_config",
        "snp_basic_config",
        "tdx_pccs",
        "tdx_multi_vms",
        "virtio_mem_dynamic_memslots",
        "virtio_mem_dynamic_memslots_with_migration",
    )

    # Apply explicit type boosts
    if any(marker in diff_lower for marker in ("readonly", "memslot", "protected", "sev", "snp", "tdx")):
        for test_type in explicit_type_boosts:
            if test_type in mapping_index:
                path = mapping_index[test_type]
                score_by_path[path] = max(score_by_path.get(path, 0), 5)
                matched_types_by_path[path].append(test_type)

    # Domain filtering
    domain_filtered: Dict[str, int] = {}
    for path, score in score_by_path.items():
        path_lower = normalize_string(path)
        if "tdx" in path_lower and not has_tdx_domain:
            continue
        if any(x in path_lower for x in ("sev", "snp")) and not has_sev_snp_domain:
            continue
        domain_filtered[path] = score
    score_by_path = domain_filtered

    # Refactor filtering
    if is_refactor:
        threshold_filtered: Dict[str, int] = {}
        tdx_explicit = has_explicit_specialized_calls(commit, "tdx")
        sev_snp_explicit = has_explicit_specialized_calls(commit, "sev_snp")

        for path, score in score_by_path.items():
            path_lower = normalize_string(path)
            if "tdx" in path_lower and not tdx_explicit:
                continue
            if any(x in path_lower for x in ("sev", "snp")) and not sev_snp_explicit:
                continue
            threshold_filtered[path] = score
        score_by_path = threshold_filtered

    # TDPU MMU association
    has_tdp_mmu = "tdp_mmu" in diff_lower or any("tdp_mmu" in normalize_string(p) for p in commit.changed_files)
    if has_tdp_mmu:
        tdp_keywords = rules.get("tdp_mmu_association_keywords", [])
        for test_type, script_path in mapping_index.items():
            text = f"{normalize_string(test_type)} {normalize_string(script_path)}"
            if any(keyword in text for keyword in tdp_keywords):
                score_by_path[script_path] = max(score_by_path.get(script_path, 0), 6)
                matched_types_by_path[script_path].append(test_type)

    # UAPI changes
    uapi_markers = rules.get("uapi_path_markers", [])
    has_uapi_change = any(any(marker in normalize_string(p) for marker in uapi_markers) for p in commit.changed_files)
    if has_uapi_change:
        uapi_keywords = rules.get("uapi_test_keywords", [])
        for test_type, script_path in mapping_index.items():
            text = f"{normalize_string(test_type)} {normalize_string(script_path)}"
            if any(keyword in text for keyword in uapi_keywords):
                score_by_path[script_path] = max(score_by_path.get(script_path, 0), 6)
                matched_types_by_path[script_path].append(test_type)

    # Forced domain tests
    forced_tests = rules.get("forced_domain_tests", {})
    if has_tdx_domain and "tdx" in forced_tests:
        for forced in forced_tests["tdx"]:
            score_by_path[forced] = max(score_by_path.get(forced, 0), 6)

    candidates = sorted(score_by_path.items(), key=lambda x: (-x[1], x[0]))

    # KVM filtering
    if any(normalize_path(p).startswith("arch/x86/kvm") for p in commit.changed_files):
        allow_keywords = rules.get("arch_x86_kvm_test_allow_keywords", [])
        allow_filtered = []
        for path, score in candidates:
            type_names = " ".join(matched_types_by_path.get(path, []))
            search_text = f"{normalize_string(path)} {normalize_string(type_names)}"
            if has_tdx_domain and "tdx" in search_text:
                allow_filtered.append((path, score))
                continue
            if any(keyword in search_text for keyword in allow_keywords):
                allow_filtered.append((path, score))
        candidates = allow_filtered

    # General KVM filtering
    if "KVM" in commit_subsystems(commit, config):
        banned_keywords = rules.get("kvm_banned_test_keywords", [])
        candidates = [(path, score) for path, score in candidates
                     if not any(keyword in normalize_string(path) for keyword in banned_keywords)]

    # Heavy and priority filtering
    if not is_heavy_needed(commit, config):
        candidates = [(path, score) for path, score in candidates if not is_heavy_test(path, config)]

    candidates.sort(key=lambda x: (not is_priority_test(x[0], config), -x[1], x[0]))

    if is_refactor:
        optional = set(rules.get("optional_tool_tests_for_refactor", []))
        candidates = [(path, score) for path, score in candidates if path not in optional]
    return candidates


def infer_subsystem(changed_files: List[str]) -> str:
    if not changed_files:
        return "misc"
    first = normalize_path(changed_files[0])
    return first.split("/", 1)[0] if "/" in first else "misc"


def cluster_commits(commits: List[CommitRecord]) -> List[Tuple[str, CommitRecord, List[str]]]:
    grouped: Dict[str, List[CommitRecord]] = defaultdict(list)
    for commit in commits:
        grouped[infer_subsystem(commit.changed_files)].append(commit)
    tdx_commits: List[CommitRecord] = []
    for subsystem, items in list(grouped.items()):
        retained = []
        for commit in items:
            if is_tdx_related_commit(commit):
                tdx_commits.append(commit)
            else:
                retained.append(commit)
        grouped[subsystem] = retained
    if tdx_commits:
        existing_ids = {c.commit_id for c in grouped.get("arch", [])}
        merged_arch = list(grouped.get("arch", []))
        for commit in tdx_commits:
            if commit.commit_id not in existing_ids:
                merged_arch.append(commit)
                existing_ids.add(commit.commit_id)
        grouped["arch"] = merged_arch

    clustered = []
    for subsystem in sorted(grouped):
        items = grouped[subsystem]
        if not items:
            continue
        combined_files = list(dict.fromkeys([f for c in items for f in c.changed_files]))
        combined_diff = "\n\n".join(f"# Commit {c.commit_id}\nSubject: {c.subject}\n{c.diff_text}" for c in items)
        cluster_record = CommitRecord(
            commit_id=f"cluster-{subsystem}",
            subject=f"{subsystem} ({len(items)} commits)",
            changed_files=combined_files,
            diff_text=combined_diff,
            source_name="cluster",
        )
        clustered.append((subsystem, cluster_record, [c.commit_id for c in items]))
    return clustered


def needs_dual_path_validation(commit: CommitRecord, config: AnalysisConfig) -> bool:
    text = f"{normalize_string(commit.subject)}\n{normalize_string(commit.diff_text)}"
    if "if (" in text and any(marker in text for marker in PROTECTED_MARKERS):
        return True
    return any(key in text for key in ("protected state", "disallow", "readonly memslots"))


def apply_dual_path_validation(commit: CommitRecord, tests: List[str], config: AnalysisConfig) -> Tuple[List[str], List[str]]:
    notes: List[str] = []
    if not needs_dual_path_validation(commit):
        return tests, notes

    mapping_index = config.get_mapping()
    protected_tests = [t for t in tests if any(k in normalize_string(t) for k in ("sev", "snp", "tdx"))]
    normal_tests = [t for t in tests if not any(k in normalize_string(t) for k in ("sev", "snp", "tdx"))]

    if not protected_tests:
        for fallback_type in ("snp_basic_config", "sev_basic_config", "tdx_pccs"):
            if fallback_type in mapping_index:
                tests.append(mapping_index[fallback_type])
        notes.append("Added protected-guest negative path tests.")

    if not normal_tests and "virtio_mem_dynamic_memslots" in mapping_index:
        tests.append(mapping_index["virtio_mem_dynamic_memslots"])
        notes.append("Added non-protected positive path regression test (dual-path validation).")

    return deduplicate_list(tests), notes


def resolve_test_script_path(test_path: str, tests_root: Path) -> Path:
    # Try to resolve relative to tests_root first
    candidate = tests_root / test_path
    if candidate.exists():
        return candidate

    # Try as absolute path
    if Path(test_path).exists():
        return Path(test_path)

    # Return the candidate as a default
    return candidate


def _extract_key_logic_snippets(file_text: str, file_path: Path) -> str:
    lines = file_text.splitlines()
    key_blocks: List[str] = []
    patterns = (r"^\s*def\s+run\s*\(", r"^\s*class\s+.+\(", r"^\s*def\s+test_.+\(")
    for idx, line in enumerate(lines, start=1):
        if any(re.search(pat, line) for pat in patterns):
            start = max(1, idx - 5)
            end = min(len(lines), idx + 35)
            snippet = "\n".join(f"{ln:04d}: {lines[ln - 1]}" for ln in range(start, end + 1))
            key_blocks.append(
                f"### Key logic snippet {file_path.as_posix()}:{idx}\n"
                f"```python\n{snippet}\n```"
            )
        if len(key_blocks) >= 3:
            break
    return "\n\n".join(key_blocks)


def build_test_source_context(selected_tests: List[str], tests_root: Path) -> str:
    sections: List[str] = []
    total_chars = 0

    for test_path in selected_tests:
        resolved_path = resolve_test_script_path(test_path, tests_root)
        if not resolved_path.exists():
            section = f"## {test_path}\n\nSource not found at `{resolved_path}`."
            sections.append(section)
            total_chars += len(section)
            continue

        # Read only the needed portion of the file
        try:
            with resolved_path.open('r', encoding='utf-8', errors='ignore') as f:
                # Read just the first MAX_TEST_SOURCE_LINES lines to save memory
                lines = []
                for i, line in enumerate(f):
                    lines.append(line)
                    if i >= MAX_TEST_SOURCE_LINES:
                        break
                    total_chars += len(line)
                    if total_chars > MAX_TEST_SOURCE_CHARS:
                        sections.append("## Context truncated\n\nReached max context size for test source injection.")
                        return "\n\n".join(sections)

                head_content = "\n".join(f"{idx:04d}: {line}" for idx, line in enumerate(lines, start=1))

                section_parts = [
                    f"## {test_path}",
                    f"Resolved path: `{resolved_path}`",
                    f"Included lines: 1-{len(lines)}",
                    "```python",
                    head_content,
                    "```",
                ]

                # If file has more lines, extract key snippets
                lines_read = len(lines)
                if lines_read < MAX_TEST_SOURCE_LINES:
                    # File might be smaller, check actual size
                    file_size = resolved_path.stat().st_size
                    if file_size > MAX_TEST_SOURCE_CHARS:
                        key_snippets = _extract_key_logic_snippets_from_path(resolved_path, lines_read)
                        if key_snippets:
                            section_parts.append("Additional key snippets:")
                            section_parts.append(key_snippets)

                section = "\n".join(section_parts)
                sections.append(section)
        except OSError as e:
            section = f"## {test_path}\n\nError reading file: {e}"
            sections.append(section)
            continue

    return "\n\n".join(sections) if sections else "No test source context available."


def _extract_key_logic_snippets_from_path(file_path: Path, lines_read: int) -> str:
    """Extract key logic snippets from file with streaming read."""
    try:
        key_blocks: List[str] = []
        patterns = (r"^\s*def\s+run\s*\(", r"^\s*class\s+.+\(", r"^\s*def\s+test_.+\(")

        with file_path.open('r', encoding='utf-8', errors='ignore') as f:
            # Skip to where we left off
            for _ in range(lines_read):
                next(f)

            for line_num, line in enumerate(f, start=lines_read + 1):
                if any(re.search(pat, line) for pat in patterns):
                    start = max(1, line_num - 5)
                    end = min(line_num + 35, 1000)  # Limit scope
                    snippet = []
                    # Re-read with line numbers
                    with file_path.open('r', encoding='utf-8', errors='ignore') as f2:
                        for i, l in enumerate(f2, start=1):
                            if i >= start and i <= end:
                                snippet.append(f"{i:04d}: {l}")
                    key_snippets = "\n".join(snippet)
                    key_blocks.append(
                        f"### Key logic snippet {file_path.as_posix()}:{line_num}\n"
                        f"```python\n{key_snippets}\n```"
                    )
                    if len(key_blocks) >= 3:
                        break

        return "\n\n".join(key_blocks)
    except OSError:
        return ""


def _split_diff_by_file(diff_text: str) -> List[Tuple[str, str]]:
    matches = list(re.finditer(r"^diff --git a/(.+?) b/(.+)$", diff_text, flags=re.M))
    if not matches:
        return []
    blocks: List[Tuple[str, str]] = []
    for idx, match in enumerate(matches):
        start = match.start()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(diff_text)
        path = match.group(2)
        blocks.append((path, diff_text[start:end]))
    return blocks


def _score_hunk_lines(hunk_lines: List[str]) -> int:
    changed = 0
    keyword_bonus = 0
    # Pre-compute keyword set for faster lookup
    keywords = {"kvm", "tdx", "sev", "snp", "memslot", "readonly", "ioctl", "qmp"}

    # Check each line for changes and keywords
    for line in hunk_lines:
        if (line.startswith("+") or line.startswith("-")) and not (
            line.startswith("+++") or line.startswith("---")
        ):
            changed += 1

        # Check for keywords in the line (more efficient than joining all)
        line_lower = line.lower()
        keyword_bonus += sum(1 for keyword in keywords if keyword in line_lower)

    return changed + keyword_bonus


def _extract_hunks_for_file(file_block: str) -> List[Tuple[int, str]]:
    hunk_markers = list(re.finditer(r"^@@ .* @@.*$", file_block, flags=re.M))
    if not hunk_markers:
        lines = file_block.splitlines()
        preview = "\n".join(lines[:MAX_HUNK_LINES])
        return [(_score_hunk_lines(lines[:MAX_HUNK_LINES]), preview)] if preview else []

    ranked: List[Tuple[int, str]] = []
    # Process hunks without loading entire block in memory
    for idx, marker in enumerate(hunk_markers):
        start = marker.start()
        end = hunk_markers[idx + 1].start() if idx + 1 < len(hunk_markers) else len(file_block)

        # Extract hunk text with bounds checking
        hunk_text = file_block[start:end]
        hunk_lines = hunk_text.splitlines()

        # Score and truncate if needed
        score = _score_hunk_lines(hunk_lines)
        clipped = "\n".join(hunk_lines[:MAX_HUNK_LINES])
        if len(hunk_lines) > MAX_HUNK_LINES:
            clipped += "\n... (hunk truncated)"

        ranked.append((score, clipped))

        # Early exit if we have enough hunks
        if len(ranked) >= MAX_HUNKS_PER_FILE:
            break

    ranked.sort(key=lambda item: item[0], reverse=True)
    return ranked[:MAX_HUNKS_PER_FILE]


def build_diff_hunk_context(commit: CommitRecord) -> str:
    file_blocks = _split_diff_by_file(commit.diff_text)
    if not file_blocks:
        return "No structured diff hunks found."
    order = {path: idx for idx, path in enumerate(commit.changed_files)}
    ranked_files: List[Tuple[int, int, str, List[Tuple[int, str]]]] = []
    for path, block in file_blocks:
        hunks = _extract_hunks_for_file(block)
        if not hunks:
            continue
        file_score = sum(score for score, _ in hunks)
        ranked_files.append((file_score, -order.get(path, 10**6), path, hunks))
    ranked_files.sort(key=lambda item: (item[0], item[1]), reverse=True)
    selected_files = ranked_files[:MAX_DIFF_FILES]

    sections: List[str] = []
    total_chars = 0
    for _, __, path, hunks in selected_files:
        for index, (score, hunk_text) in enumerate(hunks, start=1):
            section = (
                f"## Diff hunk {path} #{index} (score={score})\n"
                "```diff\n"
                f"{hunk_text}\n"
                "```"
            )
            if total_chars + len(section) > MAX_DIFF_CONTEXT_CHARS:
                sections.append("## Diff context truncated\n\nReached max context size for diff hunks.")
                return "\n\n".join(sections)
            sections.append(section)
            total_chars += len(section)
    return "\n\n".join(sections) if sections else "No structured diff hunks found."


def build_prompt(
    commit: CommitRecord,
    cursorrules_text: str,
    experience_text: str,
    selected_tests: List[str],
    dedup_notes: List[str],
    diff_hunk_context: str,
    test_source_context: str,
) -> str:
    changed_files_md = "\n".join(f"- {p}" for p in commit.changed_files) or "- (none detected)"
    selected_tests_md = "\n".join(f"- {t}" for t in selected_tests) or "- (none)"
    dedup_notes_md = "\n".join(f"- {n}" for n in dedup_notes) or "- No extra notes."
    return f"""
You are a RHEL Virt QE expert agent. Follow the expert rules EXACTLY.

=== Core Rules from .cursorrules (must be directly followed) ===
{cursorrules_text}

=== Additional hard constraints ===
1) Test priority and dedup:
   - Prioritize test scripts containing basic/smoke/standard.
   - Exclude multi_vm/stress/parallel unless commit explicitly involves locking,
     resource contention, or performance bottleneck.
2) Dual-path validation:
   - If code introduces branch logic for a specific mode (e.g. protected guest),
     include BOTH negative tests for that mode and positive regression for non-mode path.

=== Expert Knowledge Base ===
{experience_text}

=== Commit Input ===
Commit ID: {commit.commit_id}
Subject: {commit.subject}
Changed files:
{changed_files_md}

Pre-selected candidate regression tests:
{selected_tests_md}

Selection notes:
{dedup_notes_md}

=== Key Diff Hunks (trimmed, high-priority) ===
{diff_hunk_context}

=== Test Source Context (must use for coverage judgment) ===
{test_source_context}

When deciding "existing test coverage", you must use the source context above and
state whether test logic truly covers the patch behavior. Also ground your patch reasoning in
the provided diff hunk context instead of guessing only from file paths.
In "回归测试", include all pre-selected candidate regression tests unless explicitly marked
as optional with a clear reason.

Return in Chinese with this format:
[子系统名称]
提交信息:
分析结论:
经验匹配:
回归测试:
新用例设计:
""".strip()


def call_openai_analysis(model: str, prompt: str, timeout: int = 120) -> str:
    api_key = validate_api_key("DEEPSEEK_API_KEY")
    base_url = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").strip().rstrip("/")
    if not base_url.endswith("/v1"):
        base_url = f"{base_url}/v1"
    resolved_model = model or os.getenv("MODEL_NAME", "deepseek-v4-flash")
    model_alias = {"DeepSeek-V4-Flash": "deepseek-v4-flash", "DeepSeek-V4-Pro": "deepseek-v4-pro"}
    resolved_model = model_alias.get(resolved_model, resolved_model)
    messages = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": prompt}]

    # Prefer OpenAI-compatible SDK if available; fallback to raw HTTP for DeepSeek-only environments.
    try:
        from openai import OpenAI  # type: ignore

        client = OpenAI(api_key=api_key, base_url=base_url)
        response = client.chat.completions.create(
            model=resolved_model,
            messages=messages,
            temperature=0.2,
            timeout=timeout,
        )
        return (response.choices[0].message.content if response.choices else "" or "").strip()
    except ModuleNotFoundError:
        payload = json.dumps(
            {
                "model": resolved_model,
                "messages": messages,
                "temperature": 0.2,
            }
        ).encode("utf-8")
        req = urllib_request.Request(
            url=f"{base_url}/chat/completions",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
            method="POST",
        )
        try:
            with urllib_request.urlopen(req, timeout=120) as resp:
                body = resp.read().decode("utf-8", errors="ignore")
        except urllib_error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="ignore")
            raise RuntimeError(f"DeepSeek HTTP {exc.code}: {detail}") from exc
        except urllib_error.URLError as exc:
            raise RuntimeError(f"DeepSeek request failed: {exc}") from exc

        data = json.loads(body)
        choices = data.get("choices") or []
        if not choices:
            raise RuntimeError(f"DeepSeek response missing choices: {data}")
        content = (choices[0].get("message") or {}).get("content", "")
        return str(content).strip()


def heuristic_section(commit: CommitRecord, selected_tests: List[str], notes: List[str]) -> str:
    tests_md = "\n".join(f"- {t}" for t in selected_tests) if selected_tests else "- (none)"
    notes_md = "\n".join(f"- {n}" for n in notes) if notes else "- Used local heuristic mode (no LLM output)."
    return (
        "[KVM/General]\n"
        "提交信息:\n"
        f"{commit.commit_id} - {commit.subject}\n\n"
        "分析结论:\n"
        "基于提交改动路径与关键字完成了回归边界筛选，应用了用例去重与双向路径验证规则。\n\n"
        "经验匹配:\n"
        "(空)\n\n"
        "回归测试:\n"
        f"{tests_md}\n\n"
        "新用例设计:\n"
        f"{notes_md}\n"
    )


def _normalize_section_title(title: str) -> str:
    normalized = title.strip().lower()
    normalized = re.sub(r"[*`#\[\]（）()\s]", "", normalized)
    normalized = normalized.replace("：", ":")
    return normalized


def _split_sections(content: str) -> Dict[str, str]:
    """
    Parse report sections from LLM output and support format drift:
    - Chinese/English headings
    - Half/full-width colon (: / ：)
    - Markdown heading styles (**标题:**, ### 标题)
    """
    title_aliases = {
        "提交信息": "commit_subject",
        "commitid&subject": "commit_subject",
        "commitidandsubject": "commit_subject",
        "分析结论": "analysis",
        "经验匹配": "experience",
        "回归测试": "regression_tests",
        "新用例设计": "new_case_design",
    }
    sections: Dict[str, List[str]] = {
        "commit_subject": [],
        "analysis": [],
        "experience": [],
        "regression_tests": [],
        "new_case_design": [],
    }
    current_key: Optional[str] = None
    lines = content.splitlines()
    header_pattern = re.compile(r"^\s*(?:[*#\-\s]*)?(.+?)\s*[:：]\s*(.*)$")
    for raw in lines:
        line = raw.rstrip()
        stripped = line.strip()
        match = header_pattern.match(stripped)
        if match:
            title_raw = match.group(1)
            first_payload = match.group(2)
            normalized = _normalize_section_title(title_raw)
            matched_key = None
            for alias, target in title_aliases.items():
                if _normalize_section_title(alias) == normalized:
                    matched_key = target
                    break
            if matched_key:
                current_key = matched_key
                if first_payload:
                    sections[current_key].append(first_payload.strip())
                continue
        if current_key:
            sections[current_key].append(line)
    return {key: "\n".join(value).strip() for key, value in sections.items()}


def parse_report_sections(llm_output: str) -> Dict[str, str]:
    return _split_sections(llm_output)


def extract_subsystem_label(llm_output: str) -> str:
    first_line = llm_output.strip().splitlines()[0].strip() if llm_output.strip() else ""
    match = re.match(r"^\[(.+)\]$", first_line)
    if match:
        return match.group(1).strip()
    return "Unknown"


def parse_regression_tests_block(block: str) -> List[str]:
    tests: List[str] = []
    for raw_line in block.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("-"):
            line = line[1:].strip()
        if not line or line == "(none)":
            continue
        tests.append(line)
    return tests


def write_csv_report(rows: List[Dict[str, str]], csv_path: Path) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    overall_tests: List[str] = []
    seen = set()
    for row in rows:
        for test_name in parse_regression_tests_block(row.get("regression_tests", "")):
            if test_name not in seen:
                seen.add(test_name)
                overall_tests.append(test_name)
    overall_tests_text = "\n".join(f"- {name}" for name in overall_tests) if overall_tests else ""

    grouped_rows: Dict[str, List[Dict[str, str]]] = defaultdict(list)
    for row in rows:
        grouped_rows[row.get("subsystem", "Unknown")].append(row)

    output_rows: List[Dict[str, str]] = []
    for subsystem in sorted(grouped_rows.keys()):
        output_rows.append(
            {
                "commit_subject": f"## [{subsystem}]",
                "subsystem": subsystem,
                "mode": "",
                "group": "",
                "分析结论": "",
                "经验匹配": "",
                "回归测试": "",
                "新用例设计": "",
                "overall_tests": "",
            }
        )
        for row in grouped_rows[subsystem]:
            output_rows.append(
                {
                    "commit_subject": row.get("commit_subject", ""),
                    "subsystem": row.get("subsystem", ""),
                    "mode": row.get("mode", ""),
                    "group": row.get("group", ""),
                    "分析结论": row.get("analysis", ""),
                    "经验匹配": row.get("experience", ""),
                    "回归测试": row.get("regression_tests", ""),
                    "新用例设计": row.get("new_case_design", ""),
                    "overall_tests": "",
                }
            )
        output_rows.append(
            {
                "commit_subject": "",
                "subsystem": "",
                "mode": "",
                "group": "",
                "分析结论": "",
                "经验匹配": "",
                "回归测试": "",
                "新用例设计": "",
                "overall_tests": "",
            }
        )
    output_rows.append(
        {
            "commit_subject": "OVERALL",
            "subsystem": "OVERALL",
            "mode": "",
            "group": "",
            "分析结论": "",
            "经验匹配": "",
            "回归测试": "",
            "新用例设计": "",
            "overall_tests": overall_tests_text,
        }
    )

    with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "commit_subject",
                "subsystem",
                "mode",
                "group",
                "分析结论",
                "经验匹配",
                "回归测试",
                "新用例设计",
                "overall_tests",
            ],
        )
        writer.writeheader()
        writer.writerows(output_rows)


def generate_report(
    input_path: Path,
    mapping_path: Path,
    experience_path: Path,
    cursorrules_path: Path,
    tests_root: Path,
    output_path: Path,
    excel_output_path: Optional[Path],
    model: str,
    mode: str,
) -> None:
    # Initialize configuration
    config = AnalysisConfig()
    config.mapping_index = config.load_mapping_from_file(mapping_path)
    config.retrieval_rules = config.load_retrieval_rules_from_file(mapping_path.parent / "retrieval_rules.json")

    experience_text = read_text(experience_path)
    cursorrules_text = read_text(cursorrules_path)
    commits = collect_commits(input_path)
    if not commits:
        output_path.write_text("# Test Plan Report\n\nNo commits found.\n", encoding="utf-8")
        return

    report_sections = [
        "# Test Plan Report",
        "",
        f"Generated at: {datetime.now().isoformat(timespec='seconds')}",
        f"Input: `{input_path}`",
        "",
    ]
    tabular_rows: List[Dict[str, str]] = []
    targets = [("single", c, [c.commit_id]) for c in commits] if mode == "single" else cluster_commits(commits, config)
    for group_name, commit, member_ids in targets:
        candidates = gather_candidate_tests(commit, config)
        selected = [path for path, _ in candidates[:8]]
        selected, forced_notes = apply_forced_domain_tests(commit, selected, config)
        selected, notes = apply_dual_path_validation(commit, selected, config)
        notes.extend(forced_notes)
        selected, fallback_notes = apply_core_arch_fallback(commit, selected, config)
        diff_hunk_context = build_diff_hunk_context(commit)
        test_source_context = build_test_source_context(selected, tests_root)
        prompt = build_prompt(
            commit,
            cursorrules_text,
            experience_text,
            selected,
            notes,
            diff_hunk_context,
            test_source_context,
        )
        try:
            llm_output = call_openai_analysis(model=model, prompt=prompt, timeout=120)
        except Exception as exc:
            llm_output = heuristic_section(commit, selected, [f"LLM call failed: {exc}"] + notes)
        structured = parse_report_sections(llm_output)
        tabular_rows.append(
            {
                "commit_short_id": commit.commit_id[:12],
                "subsystem": extract_subsystem_label(llm_output),
                "mode": mode,
                "group": group_name,
                "member_commits": ", ".join(member_ids),
                "commit_subject": structured["commit_subject"],
                "analysis": structured["analysis"],
                "experience": structured["experience"],
                "regression_tests": structured["regression_tests"],
                "new_case_design": structured["new_case_design"],
                "raw_llm_output": llm_output,
            }
        )
        report_sections.extend(
            [
                f"## {commit.commit_id[:12]}",
                "",
                f"- Mode: `{mode}`",
                f"- Group: `{group_name}`",
                f"- Member Commits: {', '.join(member_ids)}",
                "",
                llm_output,
                "",
            ]
        )
    output_path.write_text("\n".join(report_sections).rstrip() + "\n", encoding="utf-8")
    if excel_output_path:
        write_csv_report(tabular_rows, excel_output_path)
