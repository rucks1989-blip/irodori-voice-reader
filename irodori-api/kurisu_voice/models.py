from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class SynthesisResult:
    wav_path: Path
    chunks: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


