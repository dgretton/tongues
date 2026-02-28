"""
Vault-wide translation status.

Enumerates every (original × target_language) pair and checks whether
a valid translation exists. Drives the top-level `tongues status` output.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .config import TonguesConfig, Language
from .vault import (
    VaultFile,
    scan_vault,
    translation_path,
    build_translation_index,
)
from .alignment import check_alignment, AlignmentResult


# ---------------------------------------------------------------------------
# Per-pair record
# ---------------------------------------------------------------------------

@dataclass
class TranslationRecord:
    original: VaultFile
    language: Language
    expected_path: Path             # where the translation file should live
    translation: VaultFile | None   # None when file is absent
    alignment: AlignmentResult | None  # None when file is absent or header missing

    @property
    def status(self) -> str:
        if self.translation is None:
            return "missing"
        if self.translation.header is None:
            return "missing_header"
        if self.alignment is None:
            return "error"
        if not self.alignment.line_count_match:
            return "line_count_mismatch"
        if self.alignment.issues:
            return "misaligned"
        return "ok"

    @property
    def is_ok(self) -> bool:
        return self.status == "ok"

    @property
    def status_label(self) -> str:
        return {
            "missing": "MISSING",
            "missing_header": "NO HEADER",
            "error": "ERROR",
            "line_count_mismatch": "LINE COUNT",
            "misaligned": "MISALIGNED",
            "ok": "OK",
        }.get(self.status, self.status.upper())


# ---------------------------------------------------------------------------
# Vault-wide status
# ---------------------------------------------------------------------------

@dataclass
class VaultStatus:
    config: TonguesConfig
    originals: list[VaultFile]
    records: list[TranslationRecord]

    @property
    def total_expected(self) -> int:
        return len(self.originals) * len(self.config.languages)

    @property
    def completed(self) -> int:
        return sum(1 for r in self.records if r.is_ok)

    @property
    def percentage(self) -> float:
        if self.total_expected == 0:
            return 100.0
        return 100.0 * self.completed / self.total_expected

    def needs_work(self) -> list[TranslationRecord]:
        return [r for r in self.records if not r.is_ok]

    def records_for_original(self, original: VaultFile) -> list[TranslationRecord]:
        return [r for r in self.records if r.original.path == original.path]


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def compute_status(config: TonguesConfig) -> VaultStatus:
    """Scan the vault and build a complete translation status report."""
    all_files = scan_vault(config)
    originals = [f for f in all_files if f.is_original]
    index = build_translation_index(all_files)

    records: list[TranslationRecord] = []

    for original in originals:
        for lang in config.languages:
            exp_path = translation_path(config, original.rel_path, lang.code)
            trans_file = index.get(exp_path.resolve())

            if trans_file is None:
                alignment = None
            elif trans_file.header is None:
                alignment = None
            else:
                alignment = check_alignment(original, trans_file)

            records.append(TranslationRecord(
                original=original,
                language=lang,
                expected_path=exp_path,
                translation=trans_file,
                alignment=alignment,
            ))

    return VaultStatus(config=config, originals=originals, records=records)
