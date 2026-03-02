"""
Vault-wide translation status.

Enumerates every (original × configured_language) pair and checks whether
a valid translation exists. Drives the top-level `tongues status` output.

Progress formula:
  (originals + ok_translations) / (originals × languages)

Originals count in the numerator because each one represents content that already
exists. The formula starts at 1/T (e.g. 50% with 2 languages) and reaches 100%
when every original has a valid translation in every configured language.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .config import TonguesConfig, Language
from .vault import (
    VaultFile,
    scan_vault,
    declared_translation_path,
    build_translation_index,
    find_translation_collisions,
)
from .alignment import check_alignment, AlignmentResult, check_link_universe, LinkUniverseIssue


# ---------------------------------------------------------------------------
# Per-pair record
# ---------------------------------------------------------------------------

@dataclass
class TranslationRecord:
    original: VaultFile
    language: Language
    expected_path: Path | None          # None when no header entry declares a path
    translation: VaultFile | None       # None when file is absent or unmapped
    alignment: AlignmentResult | None   # None when file is absent or header missing
    link_issues: list[LinkUniverseIssue] = field(default_factory=list)

    @property
    def status(self) -> str:
        if self.expected_path is None:
            return "unmapped"       # original has no header entry for this language
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
        if self.link_issues:
            return "invalid_links"
        return "ok"

    @property
    def is_ok(self) -> bool:
        return self.status == "ok"

    @property
    def status_label(self) -> str:
        return {
            "unmapped": "NO PATH",
            "missing": "MISSING",
            "missing_header": "NO HEADER",
            "error": "ERROR",
            "line_count_mismatch": "LINE COUNT",
            "misaligned": "MISALIGNED",
            "invalid_links": "BAD LINKS",
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
    collisions: list[tuple[Language, Path, list[VaultFile]]]  # naming conflicts

    @property
    def total_expected(self) -> int:
        return len(self.originals) * len(self.config.languages)

    @property
    def completed(self) -> int:
        return len(self.originals) + sum(1 for r in self.records if r.is_ok)

    @property
    def percentage(self) -> float:
        if self.total_expected == 0:
            return 100.0
        return min(100.0, 100.0 * self.completed / self.total_expected)

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
            exp_path = declared_translation_path(config, original, lang)
            trans_file = index.get(exp_path.resolve()) if exp_path is not None else None

            if trans_file is None:
                alignment = None
                link_issues = []
            elif trans_file.header is None:
                alignment = None
                link_issues = []
            else:
                alignment = check_alignment(original, trans_file)
                link_issues = check_link_universe(trans_file, all_files, config)

            records.append(TranslationRecord(
                original=original,
                language=lang,
                expected_path=exp_path,
                translation=trans_file,
                alignment=alignment,
                link_issues=link_issues,
            ))

    collisions = find_translation_collisions(config, originals)
    return VaultStatus(config=config, originals=originals, records=records, collisions=collisions)
