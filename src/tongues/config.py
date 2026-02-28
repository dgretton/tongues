"""
Configuration parsing for tongues.

Config lives in tongues.md at the vault root — a Markdown file with YAML
frontmatter, so it's Obsidian-syncable and human-readable.
"""

from __future__ import annotations

import yaml
from dataclasses import dataclass
from pathlib import Path

CONFIG_FILENAME = "tongues.md"

DEFAULT_CONFIG_CONTENT = """\
---
tongues:
  original_language:
    code: en
    name: English
  languages:
    - code: es
      name: español
      translated_from: "Traducido del inglés"
    - code: zh
      name: 中文
      translated_from: "从英语翻译"
  translations_folder: .translations
---

# Tongues — Translation Configuration

This vault uses **tongues** to coordinate translations into multiple languages.

## For AI agents and terminal users

**Install** (if not present): `uv tool install git+https://github.com/dgretton/tongues.git`
**Always update before use**: `uv tool upgrade tongues`
**Check status**: `tongues status`
**Check a specific file**: `tongues check <path/to/file.md>`
**Inspect alignment detail**: `tongues inspect <path/to/file.md> <lang-code>`
**Get translation file path**: `tongues where <path/to/file.md> <lang-code>`
**Generate original's header**: `tongues header <path/to/file.md>`

## How translations work

- **Originals** are any `.md` files outside the translations folder.
- **Translated files** live flat inside the translations folder (default: `.translations/`).
  They are found via links, not the file tree — the whole folder can be hidden or deleted cleanly.
- A translation filename is derived deterministically from the original's vault-relative path,
  so `tongues where` always tells you exactly where to put a translation.

### Original file header (add once translations exist)

```
 [español](.translations/notename-a1b2c3d4-es.md) | [中文](.translations/notename-a1b2c3d4-zh.md)

# Note Title
...
```

### Translation file header (required)

```
Traducido del inglés: [Note Title](../path/to/original.md)

# Título de la nota
...
```

## What counts as a valid translation

1. Same number of content lines as the original (after stripping headers).
2. Heading levels match at every line position.
3. Bullet/list structure matches at every line position.
4. Lines that contain links in the original also contain links in the translation
   (link targets may differ — they should point within the same language universe).

## The link universe principle

**This is the most important thing to get right.**

Within a language, all internal links must point to other files in the same language.
If original A links to original B, the Spanish translation of A must link to the
Spanish translation of B (not to B itself). This creates parallel universes of links —
a reader clicks their language once and stays in it as they follow every subsequent link.

Use `tongues check <file>` to see the expected translation paths for every link in a file.
"""


@dataclass
class Language:
    code: str
    name: str
    translated_from: str  # phrase used at top of translation files, e.g. "Traducido del inglés"


@dataclass
class TonguesConfig:
    vault_root: Path
    config_path: Path
    original_language: Language
    languages: list[Language]       # target languages (not including original)
    translations_folder: str        # relative to vault_root


def find_config(start: Path) -> Path | None:
    """Walk up from start looking for tongues.md."""
    current = start.resolve()
    while True:
        candidate = current / CONFIG_FILENAME
        if candidate.exists():
            return candidate
        parent = current.parent
        if parent == current:
            return None
        current = parent


def parse_config(config_path: Path) -> TonguesConfig:
    """Parse a tongues.md file and return a TonguesConfig."""
    text = config_path.read_text(encoding="utf-8")

    if not text.startswith("---"):
        raise ValueError(
            f"{config_path} must begin with YAML frontmatter (---). "
            "Run 'tongues init' to create a fresh config."
        )

    try:
        end = text.index("---", 3)
    except ValueError:
        raise ValueError(f"Unclosed YAML frontmatter in {config_path}.")

    yaml_text = text[3:end]
    data = yaml.safe_load(yaml_text) or {}
    cfg = data.get("tongues", {})

    orig_data = cfg.get("original_language", {"code": "en", "name": "English"})
    original_language = Language(
        code=orig_data["code"],
        name=orig_data["name"],
        translated_from="",  # originals don't use this field
    )

    languages = []
    for lang_data in cfg.get("languages", []):
        languages.append(Language(
            code=lang_data["code"],
            name=lang_data["name"],
            translated_from=lang_data.get(
                "translated_from",
                f"Translated from {original_language.name}",
            ),
        ))

    return TonguesConfig(
        vault_root=config_path.parent,
        config_path=config_path,
        original_language=original_language,
        languages=languages,
        translations_folder=cfg.get("translations_folder", ".translations"),
    )


def load_config(start: Path | None = None) -> TonguesConfig:
    """Find and parse the nearest tongues.md, raising clearly if absent."""
    if start is None:
        start = Path.cwd()
    config_path = find_config(start)
    if config_path is None:
        raise FileNotFoundError(
            f"No {CONFIG_FILENAME} found in {start} or any parent directory.\n"
            "Run 'tongues init' to create one."
        )
    return parse_config(config_path)
