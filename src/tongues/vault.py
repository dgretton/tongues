"""
Vault scanning and file classification.

Every .md file in the vault is either:
  - An original  — lives anywhere outside the translations folder
  - A translation — lives inside the translations folder

Translation filenames are deterministic:
  {original-stem}-{sha256(rel_path)[:8]}-{lang_code}.md

This means `tongues where` can always tell you exactly where a translation
should live without reading the vault at all.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

from .config import TonguesConfig, Language

# Matches Markdown inline links: [text](target)
LINK_RE = re.compile(r'\[([^\]]*)\]\(([^)]*)\)')

# Heading: one or more # at start of line
HEADING_RE = re.compile(r'^(#{1,6})\s')

# Unordered or ordered bullet
BULLET_RE = re.compile(r'^\s*([-*+]|\d+\.)\s')


# ---------------------------------------------------------------------------
# Filename / path helpers
# ---------------------------------------------------------------------------

def translation_stem(original_rel_path: Path, lang_code: str) -> str:
    """Return the stem (no extension) for a translation file."""
    path_str = str(original_rel_path).replace("\\", "/")
    hash_id = hashlib.sha256(path_str.encode()).hexdigest()[:8]
    return f"{original_rel_path.stem}-{hash_id}-{lang_code}"


def translation_filename(original_rel_path: Path, lang_code: str) -> str:
    return translation_stem(original_rel_path, lang_code) + ".md"


def translation_path(config: TonguesConfig, original_rel_path: Path, lang_code: str) -> Path:
    """Absolute path where a translation should (or does) live."""
    return config.vault_root / config.translations_folder / translation_filename(original_rel_path, lang_code)


def translation_rel_path(config: TonguesConfig, original_rel_path: Path, lang_code: str) -> Path:
    """Vault-relative path where a translation lives."""
    return Path(config.translations_folder) / translation_filename(original_rel_path, lang_code)


# ---------------------------------------------------------------------------
# Header parsing
# ---------------------------------------------------------------------------

@dataclass
class OriginalHeader:
    """Language-link line at the top of an original file."""
    # lang_code -> relative path string as written in the file
    language_links: dict[str, str]


@dataclass
class TranslationHeader:
    """'Translated from' line at the top of a translation file."""
    translated_from_phrase: str
    original_link_text: str     # display text of the backlink
    original_link_target: str   # path as written in the file (relative to translation)
    language: Language


def _parse_original_header(lines: list[str]) -> tuple[OriginalHeader | None, int]:
    """
    If the first line looks like a language-link bar, parse it.
    Returns (header_or_None, content_start_index).
    """
    if not lines:
        return None, 0

    first = lines[0].strip()
    links = LINK_RE.findall(first)
    if not links:
        return None, 0

    # Line must contain ONLY links and separators (|, spaces)
    cleaned = LINK_RE.sub("", first).replace("|", "").strip()
    if cleaned:
        return None, 0

    language_links: dict[str, str] = {}
    for _text, target in links:
        stem = Path(target).stem          # e.g. "memory-a1b2c3d4-es"
        parts = stem.rsplit("-", 1)
        if len(parts) == 2:
            lang_code = parts[-1]
            language_links[lang_code] = target

    content_start = 2 if (len(lines) > 1 and lines[1].strip() == "") else 1
    return OriginalHeader(language_links=language_links), content_start


def _parse_translation_header(
    lines: list[str],
    languages: list[Language],
) -> tuple[TranslationHeader | None, int]:
    """
    If the first line matches a known 'translated from' phrase, parse it.
    Returns (header_or_None, content_start_index).
    """
    if not lines:
        return None, 0

    first = lines[0].strip()

    for lang in languages:
        phrase = lang.translated_from
        if not first.lower().startswith(phrase.lower()):
            continue
        links = LINK_RE.findall(first)
        if not links:
            continue
        link_text, link_target = links[0]
        header = TranslationHeader(
            translated_from_phrase=phrase,
            original_link_text=link_text,
            original_link_target=link_target,
            language=lang,
        )
        content_start = 2 if (len(lines) > 1 and lines[1].strip() == "") else 1
        return header, content_start

    return None, 0


# ---------------------------------------------------------------------------
# VaultFile
# ---------------------------------------------------------------------------

@dataclass
class VaultFile:
    path: Path                              # absolute
    rel_path: Path                          # relative to vault root
    is_original: bool
    is_translation: bool
    header: OriginalHeader | TranslationHeader | None
    lines: list[str]                        # full file, splitlines
    content_start: int                      # index of first content line (after header)

    @property
    def content_lines(self) -> list[str]:
        return self.lines[self.content_start:]

    def links_on_line(self, content_line_index: int) -> list[tuple[str, str]]:
        """Return (text, target) pairs for all links on a content line."""
        line = self.content_lines[content_line_index]
        return LINK_RE.findall(line)

    def all_internal_links(self) -> list[tuple[int, str, str]]:
        """
        Return (content_line_index, text, target) for every internal link
        in the content (skips http/https links).
        """
        results = []
        for i, line in enumerate(self.content_lines):
            for text, target in LINK_RE.findall(line):
                if not target.startswith("http://") and not target.startswith("https://"):
                    results.append((i, text, target))
        return results


# ---------------------------------------------------------------------------
# Vault scanning
# ---------------------------------------------------------------------------

def scan_vault(config: TonguesConfig) -> list[VaultFile]:
    """
    Walk the entire vault and classify every .md file as original or translation.
    Skips the config file itself.
    """
    translations_abs = (config.vault_root / config.translations_folder).resolve()
    vault_files: list[VaultFile] = []

    for md_path in sorted(config.vault_root.rglob("*.md")):
        if md_path.resolve() == config.config_path.resolve():
            continue

        rel = md_path.relative_to(config.vault_root)
        try:
            lines = md_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue

        in_translations = md_path.resolve().is_relative_to(translations_abs)

        if in_translations:
            header, cs = _parse_translation_header(lines, config.languages)
            vault_files.append(VaultFile(
                path=md_path,
                rel_path=rel,
                is_original=False,
                is_translation=True,
                header=header,
                lines=lines,
                content_start=cs,
            ))
        else:
            header, cs = _parse_original_header(lines)
            vault_files.append(VaultFile(
                path=md_path,
                rel_path=rel,
                is_original=True,
                is_translation=False,
                header=header,
                lines=lines,
                content_start=cs,
            ))

    return vault_files


def build_translation_index(
    vault_files: list[VaultFile],
) -> dict[Path, VaultFile]:
    """Map absolute path -> VaultFile for quick lookup."""
    return {f.path.resolve(): f for f in vault_files}
