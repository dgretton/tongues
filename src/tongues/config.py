"""
Configuration parsing for tongues.

Config lives in tongues.md at the vault root — a Markdown file with YAML
frontmatter, so it's Obsidian-syncable and human-readable.
"""

from __future__ import annotations

import yaml
from dataclasses import dataclass, field
from pathlib import Path

CONFIG_FILENAME = "tongues-config.md"

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
  # ignore:
  #   - "Daily Notes/**"
  #   - "Templates/**"
  #   - "Archive/**"
---

# Tongues — Translation Configuration

This vault uses **tongues** to ensure every document exists in every configured
language. There is no "source language" or "target language" — any document may
be written in any language, and tongues tracks whether a structurally consistent
copy exists in each of the other configured languages.

## For AI agents and terminal users

**Install** (if not present): `uv tool install git+https://github.com/dgretton/tongues.git`
**Always update before use**: `uv tool upgrade tongues`
**Check status**: `tongues status`  — coverage % = (documents + valid versions) / (documents × languages); starts at 1/L and reaches 100% when every document has a valid version in every configured language
**Check a specific file**: `tongues check <path/to/file.md>`
**Inspect alignment detail**: `tongues inspect <path/to/file.md> <lang-code>`
**Get translation file path**: `tongues where <path/to/file.md> <lang-code>`

## How translations work

- **Documents** are any `.md` files outside the translations folder (and not ignored).
  Each document is assumed to be written in some language and must eventually exist
  in every configured language.
- **Language versions** live flat inside the translations folder (default: `.translations/`).
  They are found via links, not the file tree — the whole folder can be hidden or deleted cleanly.
- Language version filenames are chosen by whoever creates them — they should be the
  title of the document in that language.
- Names must be unique within the translations folder (across all documents
  for the same language). `tongues status` reports naming conflicts.
- **Ignored files** are excluded from translation tracking entirely. Add glob patterns
  under `ignore:` in the YAML frontmatter above. Patterns are matched against the
  path relative to the vault root (e.g. `Daily Notes/**`, `Templates/**`, `scratch.md`).

### Document header (add incrementally as language versions are created)

Each time you create a language version, add a wiki-link on line 1 of the document.
Use `•` as separator and bookend. A blank line and then `---` must follow the
header (they render as a horizontal rule). Create the header block if absent,
or insert the new link before the trailing `•` if the line already exists:

```
• [[Mi Título de Nota|español]] • [[我的笔记标题|中文]] •

---
# Note Title
...
```

The wiki-link target (before `|`) is the note name Obsidian uses to find the
translation. No path is needed — Obsidian resolves it by name. In rendered
view this line reads simply: `• español • 中文 •`

### Language version header (required)

Line 1 must be `※` followed by the `translated_from` phrase and a wiki-link to
the original. A blank line and then `---` must follow.

```
※ Traducido de: [[Original Note Name]]

---
# Título de la nota
...
```

## What makes a language version valid

Line counts and structure are compared **excluding headers on both sides**.
YAML frontmatter (`---...---`) at the top of a document is excluded and need
not appear in the language version. The full 3-line header block on each side
(header line + blank line + `---`) is stripped before any comparison.
Only the body content is compared. Trailing blank lines are ignored.

1. Same number of body lines as the original (headers excluded on both sides).
2. Heading levels match at every line position.
3. Bullet/list structure matches at every line position.
4. Checkbox states (`[ ]` vs `[x]` etc.) match at every line position.
5. Lines that contain links in the original also contain links in the translation
   (same count per line).
6. Every wiki-link in the translation points to a same-language translation, or
   to an original as a stand-in with `⍰` in the display text. Broken links,
   wrong-language links, and stand-ins without `⍰` make the translation invalid.

## The link universe principle

**This is the most important thing to get right.**

Within a language, all internal links must point to other files in the same language.
If original A links to original B, the Spanish translation of A must link to the
Spanish translation of B (not to B itself). This creates parallel universes of links —
a reader clicks their language once and stays in it as they follow every subsequent link.

When B's translation does not exist yet, link to the original B as a stand-in, using
`⍰` in the display text to signal that the translation is absent:

```
[[Original Note Name|⍰ Nombre en español]]
```

Links that point to non-existent notes, to translations in a different language, or to
originals without the `⍰` marker make the translation **invalid** in tongues' eyes.

Use `tongues check <file>` to see required link targets and validate existing links.
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
    languages: list[Language]           # languages to translate into
    translations_folder: str            # relative to vault_root
    ignore_patterns: list[str] = field(default_factory=list)  # glob patterns to skip


def find_config(start: Path) -> tuple[Path, Path] | None:
    """Walk up from start searching each ancestor's subtree for tongues-config.md.

    Returns (vault_root, config_path) where vault_root is the ancestor directory
    that contains the config file (directly or in a subdirectory).

    Raises ValueError if multiple config files are found under the same ancestor.
    """
    current = start.resolve()
    while True:
        matches = sorted(current.rglob(CONFIG_FILENAME))
        if matches:
            if len(matches) > 1:
                rel_paths = "\n  ".join(str(m.relative_to(current)) for m in matches)
                raise ValueError(
                    f"Multiple {CONFIG_FILENAME} files found under {current}:\n  {rel_paths}\n"
                    "Remove all but one before using tongues."
                )
            return current, matches[0]
        parent = current.parent
        if parent == current:
            return None
        current = parent


def parse_config(vault_root: Path, config_path: Path) -> TonguesConfig:
    """Parse a tongues-config.md file and return a TonguesConfig."""
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
        vault_root=vault_root,
        config_path=config_path,
        languages=languages,
        translations_folder=cfg.get("translations_folder", ".translations"),
        ignore_patterns=cfg.get("ignore", []),
    )


def load_config(start: Path | None = None) -> TonguesConfig:
    """Find and parse the nearest tongues-config.md, raising clearly if absent."""
    if start is None:
        start = Path.cwd()
    result = find_config(start)
    if result is None:
        raise FileNotFoundError(
            f"No {CONFIG_FILENAME} found in {start} or any parent directory.\n"
            "Run 'tongues init' to create one."
        )
    vault_root, config_path = result
    return parse_config(vault_root, config_path)
