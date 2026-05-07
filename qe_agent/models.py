from dataclasses import dataclass
from typing import List


@dataclass
class CommitRecord:
    commit_id: str
    subject: str
    changed_files: List[str]
    diff_text: str
    source_name: str
