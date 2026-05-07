"""Static configuration for the Virt QE agent."""

from typing import Dict

PRIORITY_KEYWORDS = ("basic", "smoke", "standard")
HEAVY_KEYWORDS = ("multi_vm", "multi_vms", "stress", "parallel")
HEAVY_TRIGGER_PATTERNS = (
    r"\block\b",
    r"\bmutex\b",
    r"\bspinlock\b",
    r"\brace\b",
    r"\bcontention\b",
    r"\bparallel\b",
    r"\bthroughput\b",
    r"\blatency\b",
    r"\bperf(?:ormance)?\b",
)
PROTECTED_MARKERS = (
    "is_protected_guest",
    "protected_state",
    "has_protected_state",
    "sev",
    "snp",
    "tdx",
)
MAX_TEST_SOURCE_LINES = 500
MAX_TEST_SOURCE_CHARS = 120000

DEFAULT_RETRIEVAL_RULES: Dict[str, object] = {
    "path_subsystem_map": [
        ("arch/x86/kvm", "KVM"),
        ("arch/x86/include/asm/kvm", "KVM"),
        ("virt/kvm", "KVM"),
        ("include/linux/kvm", "KVM"),
        ("drivers/net", "Network"),
        ("net/", "Network"),
        ("drivers/scsi", "Storage"),
        ("drivers/nvme", "Storage"),
        ("block/", "Storage"),
        ("fs/", "Filesystem"),
    ],
    "kvm_banned_test_keywords": ["net", "storage", "virtio-fs", "virtio_fs", "block"],
    "arch_x86_kvm_test_allow_keywords": ["kvm", "boot", "smp", "interrupt", "cpu"],
    "tdx_domain_path_markers": ["kvm/tdx", "vmx/tdx"],
    "tdx_domain_subject_keywords": ["tdx"],
    "tdp_mmu_association_keywords": ["hugepage", "huge_page", "page_fault"],
    "uapi_path_markers": ["include/uapi", "include/linux/kvm.h"],
    "uapi_test_keywords": ["ioctl", "qmp", "query", "caps", "qmp_basic", "qmp_command"],
    "refactor_subject_keywords": ["plumb", "refactor", "rename", "cleanup", "mechanical"],
    "optional_tool_tests_for_refactor": [
        "qemu/tests/kvm_stat.py",
        "qemu/tests/hv_kvm_unit_test.py",
    ],
    "forced_domain_tests": {
        "tdx": ["qemu/tests/tdx_multi_vms.py", "qemu/tests/tdx_pccs.py"]
    },
}

SYSTEM_PROMPT = """You are a senior Virt QE test planning expert.

Critical Requirement for Analysis:
- Focus on functional side effects. If a function signature changes (e.g., adding a vCPU pointer),
  analyze which downstream logic now gains access to new vCPU states.
- For Test Case Selection: If the commit is in 'arch/x86/kvm', ONLY look for tests with
  ['kvm', 'boot', 'smp', 'interrupt', 'cpu'] in their path or cfg name.
- For New Test Design: Use the 'Trigger-Monitor-Assert' framework.
  - Trigger: Specific CLI or QMP command.
  - Monitor: /sys/kernel/debug/kvm/ stats or ftrace symbols.
  - Assert: Expected value change in those monitors.
- For UAPI-related changes, first check existing Python ioctl/qmp_query_caps/qmp capability tests.
  Propose writing a new C userspace verifier only if no existing script can validate the ioctl path.
"""
