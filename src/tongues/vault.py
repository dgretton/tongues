"""
Vault scanning and file classification.

Every .md file in the vault is either:
  - An original  — lives anywhere outside the translations folder
  - A translation — lives inside the translations folder

Originals carry a language-link header on line 1 (with a leading space):

  [[Mi Nota Traducida-es|español]] | [[我的翻译笔记-zh|中文]]

Each wiki-link target is the note name of the translation (no path, no
extension — Obsidian finds it by name). The display text is the language name
as configured. The translation file lives in the translations folder under that
exact name + ".md".

Translation filenames are chosen by the translator — they should be the
translated title of the original in the target language. There is no generated
fallback; a translation is "unmapped" until the original's header declares it.
"""

from __future__ import annotations

import fnmatch
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from .config import TonguesConfig, Language

# Matches Markdown inline links: [text](target)
LINK_RE = re.compile(r'\[([^\]]*)\]\(([^)]*)\)')

# Matches Obsidian wiki-links: [[note_name]] or [[note_name|display text]]
# Groups: (note_name, display_text_or_empty_string)
WIKI_LINK_RE = re.compile(r'\[\[([^\]|]+)(?:\|([^\]]*))?\]\]')

# Heading: one or more # at start of line
HEADING_RE = re.compile(r'^(#{1,6})\s')

# Unordered or ordered bullet
BULLET_RE = re.compile(r'^\s*([-*+]|\d+\.)\s')

# Checkbox item: - [ ], - [x], - [/], etc. — captures the state character
CHECKBOX_RE = re.compile(r'^\s*[-*+]\s+\[(.)\]')


# ---------------------------------------------------------------------------
# Header parsing
# ---------------------------------------------------------------------------

@dataclass
class OriginalHeader:
    """Language-link line at the top of an original file.

    language_links maps lang_code -> note_name (the wiki-link target, i.e.
    the translation file's stem as Obsidian knows it, without extension).
    """
    language_links: dict[str, str]


@dataclass
class TranslationHeader:
    """'Translated from' line at the top of a translation file."""
    translated_from_phrase: str
    original_note_name: str     # wiki-link target pointing back to the original
    language: Language


def _parse_original_header(
    lines: list[str],
    languages: list[Language],
) -> tuple[OriginalHeader | None, int]:
    """
    If the first line looks like a language-link bar, parse it.
    Returns (header_or_None, content_start_index).

    Expected format:
      • [[Mi Título de Nota|español]] • [[我的笔记标题|中文]] •

    Line must start with '•', contain at least one wiki-link, and contain
    nothing besides wiki-links, '•' bullets, and whitespace.

    The display text of each wiki-link is matched against configured language
    names (case-insensitive) to determine which language it corresponds to.
    Falls back to extracting a lang code from the last dash-segment of the
    note name if the display text doesn't match any configured language.
    """
    if not lines:
        return None, 0

    first = lines[0].strip()

    if not first.startswith("•"):
        return None, 0

    wiki_links = WIKI_LINK_RE.findall(first)  # [(note_name, display), ...]
    if not wiki_links:
        return None, 0

    # Line must contain ONLY wiki-links, '•' bullets, and whitespace
    cleaned = WIKI_LINK_RE.sub("", first).replace("•", "").strip()
    if cleaned:
        return None, 0

    lang_by_name = {lang.name.lower(): lang for lang in languages}
    language_links: dict[str, str] = {}  # lang_code -> note_name
    for note_name, display in wiki_links:
        note_name = note_name.strip()
        display = display.strip()
        lang = lang_by_name.get(display.lower())
        if lang is not None:
            language_links[lang.code] = note_name
        else:
            # Fallback: extract lang code from last dash-segment of note name
            parts = note_name.rsplit("-", 1)
            if len(parts) == 2 and parts[-1] not in language_links:
                language_links[parts[-1]] = note_name

    if len(lines) < 2 or lines[1].strip() != "":
        return None, 0
    return OriginalHeader(language_links=language_links), 2


def _parse_translation_header(
    lines: list[str],
    languages: list[Language],
) -> tuple[TranslationHeader | None, int]:
    """
    If the first line matches a known 'translated from' phrase, parse it.
    Returns (header_or_None, content_start_index).

    Expected format:
      Traducido de: [[Original Note Name]]
    """
    if not lines:
        return None, 0

    first = lines[0].strip()

    for lang in languages:
        phrase = lang.translated_from
        if not first.lower().startswith(phrase.lower()):
            continue
        wiki_links = WIKI_LINK_RE.findall(first)
        if not wiki_links:
            continue
        note_name, _display = wiki_links[0]
        header = TranslationHeader(
            translated_from_phrase=phrase,
            original_note_name=note_name.strip(),
            language=lang,
        )
        if len(lines) < 2 or lines[1].strip() != "":
            return None, 0
        return header, 2

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

    def all_internal_links(self) -> list[tuple[int, str, str]]:
        """
        Return (content_line_index, display_text, target) for every internal
        link in the content.

        For markdown links [text](target): skips http/https targets.
        For wiki-links [[note_name|display]]: target is the note name.
        """
        results = []
        for i, line in enumerate(self.content_lines):
            for text, target in LINK_RE.findall(line):
                if not target.startswith("http://") and not target.startswith("https://"):
                    results.append((i, text, target))
            for note_name, display in WIKI_LINK_RE.findall(line):
                results.append((i, display or note_name, note_name.strip()))
        return results


# ---------------------------------------------------------------------------
# Vault scanning
# ---------------------------------------------------------------------------

def is_ignored(rel_path: Path, patterns: list[str]) -> bool:
    """
    Return True if rel_path matches any ignore pattern.

    Patterns are matched against the POSIX string of the path relative to the
    vault root using fnmatch, where * matches across directory separators.
    Common examples:
      "Daily Notes/**"  — excludes everything under Daily Notes/
      "Templates/**"    — excludes everything under Templates/
      "scratch.md"      — excludes a specific file at any depth
    """
    if not patterns:
        return False
    path_str = rel_path.as_posix()
    return any(fnmatch.fnmatch(path_str, pattern) for pattern in patterns)


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
            if is_ignored(rel, config.ignore_patterns):
                continue
            header, cs = _parse_original_header(lines, config.languages)
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


def declared_translation_path(
    config: TonguesConfig, original: VaultFile, lang: Language
) -> Path | None:
    """
    Return the absolute path where a translation lives or should live, or None
    if the original's header doesn't declare a translation for this language.

    The path is: {translations_folder}/{note_name}.md, where note_name is the
    wiki-link target from the original's language-link header.

    Returns None when no header entry exists — the translation is "unmapped".
    """
    if original.header is not None:
        note_name = original.header.language_links.get(lang.code)
        if note_name is not None:
            return config.vault_root / config.translations_folder / f"{note_name}.md"
    return None


def find_translation_collisions(
    config: TonguesConfig, originals: list[VaultFile]
) -> list[tuple[Language, Path, list[VaultFile]]]:
    """
    Return (language, path, [originals]) triples where two or more originals
    declare the same translation path. Each triple is a naming conflict that
    must be resolved by giving at least one translation a different note name.
    """
    result = []
    for lang in config.languages:
        by_path: dict[Path, list[VaultFile]] = defaultdict(list)
        for orig in originals:
            path = declared_translation_path(config, orig, lang)
            if path is not None:
                by_path[path.resolve()].append(orig)
        for path, files in by_path.items():
            if len(files) > 1:
                result.append((lang, path, files))
    return result
