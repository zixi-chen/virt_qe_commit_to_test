"""Utility functions for Virt QE Agent.

This module contains shared utilities to reduce code duplication across the codebase.
"""

import os
import re
import json
from pathlib import Path
from typing import Dict, List, Tuple, Optional, Any
from urllib import error as urllib_error
from urllib import request as urllib_request

# Type guards for cleaner code
def is_str(value) -> bool:
    return isinstance(value, str)

def is_list(value) -> bool:
    return isinstance(value, list)

def is_dict(value) -> bool:
    return isinstance(value, dict)

def is_tuple(value) -> bool:
    return isinstance(value, tuple)

def normalize_string(value: str) -> str:
    """Normalize string by lowercasing and stripping whitespace."""
    return value.lower().strip()

def normalize_path(path: str) -> str:
    """Normalize path string by lowercasing and removing leading './'."""
    return normalize_string(path).lstrip("./")

def get_stem_lower(path: str) -> str:
    """Get the stem of a path and normalize it."""
    return normalize_string(Path(path).stem)

def resolve_path_argument(path_arg: str, base_dir: Path) -> Path:
    """Resolve path argument, handling relative vs absolute paths."""
    path = Path(path_arg).expanduser()
    return path if path.is_absolute() else (base_dir / path).resolve()

def safe_json_load(path: Path) -> Optional[Dict]:
    """Safely load JSON from file, returning None on failure."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None

def deduplicate_list(items: List[Any]) -> List[Any]:
    """Remove duplicates from a list while preserving order."""
    seen = set()
    return [x for x in items if not (x in seen or seen.add(x))]

def get_env_var(name: str, default: str = "") -> str:
    """Get environment variable with optional default."""
    return os.getenv(name, "").strip() or default

def validate_api_key(key_name: str = "DEEPSEEK_API_KEY") -> str:
    """Validate and return API key from environment."""
    api_key = get_env_var(key_name)
    if not api_key:
        raise RuntimeError(f"{key_name} is not set.")
    return api_key