# tongues

Coordinates continuous translation of [Obsidian](https://obsidian.md/) vaults into multiple languages.

## Install

```sh
uv tool install git+https://github.com/dgretton/tongues.git
```

**Always upgrade before use:**

```sh
uv tool upgrade tongues
```

## Quick start

```sh
cd /path/to/your/vault
tongues init          # creates tongues.md — edit it to set your languages
tongues status        # see overall completion and what needs work
tongues check <file>  # inspect a specific original and all its translations
```

## How it works

- **Originals** are every `.md` file outside the translations folder.
- **Translations** live flat inside a configurable folder (default: `.translations/`).
  You navigate to them only via links — the folder can be hidden or deleted cleanly.
- Translation filenames are deterministic (`stem-hash8-langcode.md`), so `tongues where` always tells you exactly where a translation should go.

### Original file header

Once translations exist, add a language-link bar as line 1:

```
 [español](.translations/notename-a1b2c3d4-es.md) | [中文](.translations/notename-a1b2c3d4-zh.md)

# Note Title
...
```

Run `tongues header <file>` to generate the correct text.

### Translation file header (required)

Line 1 of every translation:

```
Traducido de: [Note Title](../path/to/original.md)

# Título de la nota
...
```

### What counts as a valid translation

Headers are stripped from both files before any comparison: the original's language-link
line (+ following blank) and the translation's `translated_from` line (+ following blank)
are excluded. Only the body content is compared.

1. Same number of body lines as the original (headers excluded on both sides).
2. Heading levels match at every line position.
3. Bullet/list structure matches at every line position.
4. Lines that contain links in the original also contain links in the translation
   (link targets may differ — they should route within the same language universe).

### The link universe principle

When translating a file, **every internal link must point to the same-language version** of the linked file. This creates parallel link universes: a reader clicks their language once and stays in it through every subsequent link.

`tongues check <file>` shows the expected translation paths for every link in a file.

## Commands

| Command | Description |
|---|---|
| `tongues status` | Overall completion percentage and files needing work |
| `tongues check <file>` | All translations for one original, with link universe map |
| `tongues inspect <file> <lang>` | Full alignment diff for one original/language pair |
| `tongues where <file> <lang>` | Print the path where a translation should live |
| `tongues header <file>` | Generate the language-link header for an original |
| `tongues languages` | List configured languages |
| `tongues init` | Create a `tongues.md` config in the current directory |

## Configuration

`tongues.md` at the vault root — Markdown with YAML frontmatter (Obsidian-syncable):

```yaml
---
tongues:
  original_language:
    code: en
    name: English
  languages:
    - code: es
      name: español
      translated_from: "Traducido de"
    - code: zh
      name: 中文
      translated_from: "从英语翻译"
  translations_folder: .translations
---
```

## For AI agents

```sh
# Standard workflow
uv tool install git+https://github.com/dgretton/tongues.git  # install if absent
uv tool upgrade tongues        # always upgrade first
tongues status                 # get the full picture
tongues check notes/foo.md     # drill into a specific file
tongues inspect notes/foo.md es  # full diff for one pair
tongues where notes/foo.md es  # where to put the translation
tongues header notes/foo.md    # what to put on line 1 of the original
```
