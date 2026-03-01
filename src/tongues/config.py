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
  languages:
    - code: es
      name: español
      translated_from: "Traducido de"
    - code: zh
      name: 中文
      translated_from: "译自"
  translations_folder: .translations
---

# Tongues — Translation Configuration

This vault uses **tongues** to coordinate translations into multiple languages.
There is no "original language" — documents may be written in any language and
each one simply needs a translation into every other configured language.

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
- Translation note names are chosen by the translator — they should be the
  translated title of the document in the target language. Conventionally add a
  `-{lang-code}` suffix (e.g. `Mi Nota-es`) to keep languages distinguishable.
- Note names must be unique across all originals for the same language.
  `tongues status` reports naming conflicts.

### Original file header (add once translations exist)

Run `tongues header <file>` to generate a template, then paste it as line 1.
Replace each placeholder note name with the translated title of this note:

```
 [[Mi Título de Nota-es|español]] | [[我的笔记标题-zh|中文]]

# Note Title
...
```

The wiki-link target (before `|`) is the note name Obsidian uses to find the
translation. No path is needed — Obsidian resolves it by name.

### Translation file header (required)

Line 1 must be the `translated_from` phrase followed by a wiki-link to the original.

```
Traducido de: [[Original Note Name]]

# Título de la nota
...
```

## What counts as a valid translation

Line counts and structure are compared **excluding headers on both sides**:
the original's language-link line (and its following blank line) is stripped,
and the translation's `translated_from` line (and its following blank line) is
stripped, before any comparison is made. Only the body content is compared.

1. Same number of body lines as the original (headers excluded on both sides).
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

Use `tongues check <file>` to see the expected wiki-link targets for every link in a file.
"""


@dataclass
class Language:
    code: str
    name: str
    translated_from: str  # phrase used at top of translation files, e.g. "Traducido de", "译自"


@dataclass
class TonguesConfig:
    vault_root: Path
    config_path: Path
    languages: list[Language]       # languages to translate into
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

    languages = []
    for lang_data in cfg.get("languages", []):
        languages.append(Language(
            code=lang_data["code"],
            name=lang_data["name"],
            translated_from=lang_data.get("translated_from", "Translated from"),
        ))

    return TonguesConfig(
        vault_root=config_path.parent,
        config_path=config_path,
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
