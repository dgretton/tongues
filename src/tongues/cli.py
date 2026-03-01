"""
tongues CLI — the main entry point.

Primary audience: AI agents (Claude Code, etc.) running in a vault directory.
Output is designed to be unambiguous, actionable, and rich in coaching.
"""

from __future__ import annotations

import sys
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table
from rich import box
from rich.panel import Panel

from .config import load_config, TonguesConfig, Language, DEFAULT_CONFIG_CONTENT, CONFIG_FILENAME
from .vault import (
    VaultFile,
    scan_vault,
    declared_translation_path,
    build_translation_index,
    is_ignored,
    WIKI_LINK_RE,
)
from .status import compute_status, VaultStatus, TranslationRecord
from .alignment import check_alignment

console = Console()
err_console = Console(stderr=True)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

LINK_UNIVERSE_REMINDER = """\
[bold yellow]⚠  LINK UNIVERSE PRINCIPLE[/bold yellow]
Every internal link in a translation must point to the [italic]same-language[/italic] version
of the linked file — not to the original. This creates parallel link universes:
a reader clicks their language once and stays in it through every subsequent link.

Use [bold]tongues check <file>[/bold] to see the expected translation targets for every
link in a file before you write or edit a translation.\
"""


def _status_style(label: str) -> str:
    return {
        "OK": "bold green",
        "MISSING": "bold red",
        "NO PATH": "bold red",
        "NO HEADER": "bold red",
        "LINE COUNT": "bold yellow",
        "MISALIGNED": "bold yellow",
        "ERROR": "bold red",
    }.get(label, "white")


def _load_or_exit(path: Path | None = None) -> TonguesConfig:
    try:
        return load_config(path)
    except FileNotFoundError as e:
        err_console.print(f"[red]{e}[/red]")
        sys.exit(1)
    except Exception as e:
        err_console.print(f"[red]Error loading config:[/red] {e}")
        sys.exit(1)


def _resolve_original(config: TonguesConfig, file_arg: str) -> VaultFile:
    """
    Resolve a CLI file argument to a VaultFile (must be an original).
    Accepts vault-relative or absolute paths.
    """
    target = Path(file_arg)
    if not target.is_absolute():
        # Try relative to cwd first, then vault root
        if (Path.cwd() / target).exists():
            target = (Path.cwd() / target).resolve()
        else:
            target = (config.vault_root / target).resolve()
    else:
        target = target.resolve()

    # Check ignore patterns before scanning — gives a clear message instead of "not found"
    if target.exists() and config.ignore_patterns:
        try:
            rel = target.relative_to(config.vault_root)
            if is_ignored(rel, config.ignore_patterns):
                console.print(
                    f"[dim]{file_arg} is excluded from translation tracking "
                    f"(matches an ignore pattern in tongues.md).[/dim]"
                )
                sys.exit(0)
        except ValueError:
            pass  # target is not under vault root

    all_files = scan_vault(config)
    for f in all_files:
        if f.path.resolve() == target:
            if not f.is_original:
                err_console.print(
                    f"[red]{file_arg}[/red] is a translation file, not an original. "
                    "Pass the original file."
                )
                sys.exit(1)
            return f

    err_console.print(f"[red]File not found in vault:[/red] {file_arg}")
    sys.exit(1)


def _find_by_note_name(all_files: list[VaultFile], note_name: str) -> VaultFile | None:
    """Find a VaultFile whose stem matches the given note name."""
    for f in all_files:
        if f.path.stem == note_name:
            return f
    return None


def _print_link_universe_reminder(
    config: TonguesConfig,
    original: VaultFile,
    all_files: list[VaultFile],
) -> None:
    """Print coaching about link universe correctness for a given original."""
    internal = original.all_internal_links()
    if not internal:
        return

    console.print()
    console.print(Panel(LINK_UNIVERSE_REMINDER, border_style="yellow"))

    table = Table(
        box=box.SIMPLE_HEAD,
        show_header=True,
        header_style="bold",
        expand=True,
    )
    table.add_column("Line", style="dim", width=5)
    table.add_column("Links to")
    for lang in config.languages:
        table.add_column(f"→ {lang.name}", overflow="fold")

    index = build_translation_index(all_files)

    for line_idx, _text, target in internal:
        # Try to find the linked VaultFile — by path (markdown) or by note name (wiki-link)
        linked: VaultFile | None = None
        orig_dir = original.path.parent
        try:
            linked_abs = (orig_dir / target).resolve()
            linked = index.get(linked_abs)
        except (ValueError, OSError):
            pass
        if linked is None:
            linked = _find_by_note_name(all_files, target)

        lang_cells = []
        for lang in config.languages:
            if linked is not None and linked.is_original:
                trans_path = declared_translation_path(config, linked, lang)
                if trans_path is not None:
                    lang_cells.append(f"[[{trans_path.stem}|{lang.name}]]")
                else:
                    lang_cells.append(f"(no {lang.name} path declared)")
            else:
                lang_cells.append("(not found)")

        table.add_row(str(line_idx + 1), target, *lang_cells)

    console.print(table)


# ---------------------------------------------------------------------------
# CLI group
# ---------------------------------------------------------------------------

@click.group()
@click.version_option(package_name="tongues")
def main() -> None:
    """
    tongues — Obsidian vault translation coordinator.

    \b
    Quick start for AI agents:
      uv tool install git+https://github.com/dgretton/tongues.git  # install if absent
      uv tool upgrade tongues          # always upgrade before use
      tongues status                   # see what needs work
      tongues check <file.md>          # inspect a specific original
      tongues inspect <file.md> <lang> # detailed alignment diff
    """


# ---------------------------------------------------------------------------
# tongues init
# ---------------------------------------------------------------------------

@main.command()
@click.option("--force", is_flag=True, help="Overwrite existing config.")
def init(force: bool) -> None:
    """Create a tongues.md config file in the current directory."""
    config_path = Path.cwd() / CONFIG_FILENAME
    if config_path.exists() and not force:
        console.print(
            f"[yellow]{CONFIG_FILENAME} already exists.[/yellow] "
            "Use --force to overwrite."
        )
        sys.exit(1)

    config_path.write_text(DEFAULT_CONFIG_CONTENT, encoding="utf-8")
    console.print(f"[green]Created[/green] {config_path}")
    console.print(
        "Edit the YAML frontmatter to set your target languages, then run "
        "[bold]tongues status[/bold]."
    )


# ---------------------------------------------------------------------------
# tongues status
# ---------------------------------------------------------------------------

@main.command()
@click.option("--all", "show_all", is_flag=True, help="Show OK files too.")
def status(show_all: bool) -> None:
    """Show overall translation completion and list files that need work."""
    config = _load_or_exit()
    vs = compute_status(config)

    # Summary bar
    pct = vs.percentage
    pct_style = "green" if pct == 100 else ("yellow" if pct >= 50 else "red")
    console.print()
    ok_translations = sum(1 for r in vs.records if r.is_ok)
    n_orig = len(vs.originals)
    n_lang = len(config.languages)
    console.print(
        f"[bold]Translation status:[/bold]  "
        f"[{pct_style}]{pct:.1f}%[/{pct_style}]  "
        f"[dim]({ok_translations}/{n_orig * n_lang} translations × {n_lang} languages)[/dim]"
    )
    console.print(
        f"Vault: [dim]{config.vault_root}[/dim]  "
        f"Originals: {n_orig}  "
        f"Languages: {', '.join(l.name for l in config.languages)}"
    )
    console.print()

    if vs.collisions:
        console.print(Panel(
            "[bold red]NAMING CONFLICT[/bold red]\n"
            "Multiple originals declare the same translation note name. "
            "Edit the language-link header of all but one to use a unique translated title.",
            border_style="red",
        ))
        for lang, path, files in vs.collisions:
            console.print(f"  [{lang.code}] [bold red]{path.stem}[/bold red] ← claimed by:")
            for f in files:
                console.print(f"    {f.rel_path}")
        console.print()

    if vs.total_expected == 0:
        console.print("[dim]No originals found. Create some .md files outside the translations folder.[/dim]")
        return

    needs_work = vs.needs_work()

    if not needs_work:
        console.print("[bold green]✓ All translations are complete and valid.[/bold green]")
        return

    # Group by original
    by_original: dict[Path, list[TranslationRecord]] = {}
    all_records = vs.records if show_all else needs_work
    for r in all_records:
        by_original.setdefault(r.original.path, []).append(r)

    for orig_path, records in sorted(by_original.items()):
        orig_rel = records[0].original.rel_path
        console.rule(f"[bold]{orig_rel}[/bold]", style="dim")

        for r in records:
            label = r.status_label
            style = _status_style(label)

            if r.status == "unmapped":
                console.print(f"  [{style}]{label:12}[/{style}] [{r.language.code}]")
                console.print(
                    f"    [dim]No {r.language.name} link in header — "
                    f"add one when you create the translation: "
                    f"[[translated-title-{r.language.code}|{r.language.name}]][/dim]"
                )
                continue

            exp_rel = r.expected_path.relative_to(config.vault_root)

            if r.is_ok:
                console.print(f"  [{style}]{label:12}[/{style}] [{r.language.code}] {exp_rel}")
                continue

            console.print(f"  [{style}]{label:12}[/{style}] [{r.language.code}] → {exp_rel}")

            if r.status == "missing":
                console.print(
                    f"    [dim]Create this file. Line 1:[/dim] "
                    f"{r.language.translated_from}: [[{orig_rel.stem}]]"
                )
            elif r.status == "missing_header":
                console.print(
                    f"    [dim]File exists but has no valid "
                    f"'{r.language.translated_from}: [[...]]' header.[/dim]"
                )
            elif r.alignment:
                if not r.alignment.line_count_match:
                    console.print(
                        f"    [dim]Line count: original={r.alignment.original_content_lines}, "
                        f"translation={r.alignment.translation_content_lines}[/dim]"
                    )
                if r.alignment.issues:
                    shown = r.alignment.issues[:3]
                    for issue in shown:
                        console.print(
                            f"    [dim]Line {issue.line_num}:[/dim] {issue.issue_type.replace('_', ' ')}"
                        )
                    if len(r.alignment.issues) > 3:
                        console.print(
                            f"    [dim]… and {len(r.alignment.issues) - 3} more. "
                            f"Run: tongues inspect {orig_rel} {r.language.code}[/dim]"
                        )

    console.print()
    console.print(
        f"[dim]{len(needs_work)} pair(s) need attention. "
        "Run [bold]tongues check <file>[/bold] for per-file detail.[/dim]"
    )


# ---------------------------------------------------------------------------
# tongues check
# ---------------------------------------------------------------------------

@main.command()
@click.argument("file")
def check(file: str) -> None:
    """
    Check all translations for a specific original file.

    Prints the status of each language translation, lists alignment issues,
    and shows which wiki-link targets internal links should use in each language.
    """
    config = _load_or_exit()
    original = _resolve_original(config, file)
    all_files = scan_vault(config)

    console.print()
    console.print(f"[bold]Checking:[/bold] {original.rel_path}")

    # Header status
    n_configured = len(config.languages)
    if original.header and original.header.language_links:
        linked_names = [
            lang.name for lang in config.languages
            if lang.code in original.header.language_links
        ]
        n_linked = len(linked_names)
        names_str = ", ".join(linked_names)
        style = "green" if n_linked == n_configured else "yellow"
        console.print(
            f"  Header: [{style}]{n_linked} of {n_configured} languages[/{style}]"
            f" — {names_str}"
        )
    else:
        console.print(f"  Header: [dim]none yet[/dim] — add a link each time you create a translation")

    console.print(f"  Content lines: {len(original.content_lines)}")
    console.print()

    index = build_translation_index(all_files)

    declared = [
        (lang, declared_translation_path(config, original, lang))
        for lang in config.languages
        if declared_translation_path(config, original, lang) is not None
    ]

    if not declared:
        console.print("[dim]No translations declared yet.[/dim]")
        _print_link_universe_reminder(config, original, all_files)
        console.print()
        return

    for lang, exp_path in declared:
        console.rule(f"[bold]{lang.name}[/bold] [{lang.code}]", style="dim")

        exp_rel = exp_path.relative_to(config.vault_root)

        if not exp_path.exists():
            console.print(f"  [bold red]MISSING[/bold red]  {exp_rel}")
            console.print(f"  Create this file. Line 1:")
            console.print(f"    [bold]{lang.translated_from}: [[{original.path.stem}]][/bold]")
            console.print(
                f"  Then a blank line, then {len(original.content_lines)} lines of body content "
                f"(matching the original's structure)."
            )
            continue

        trans_file = index.get(exp_path.resolve())

        if trans_file is None:
            console.print(f"  [red]Could not read:[/red] {exp_rel}")
            continue

        if trans_file.header is None:
            console.print(f"  [bold red]NO HEADER[/bold red]  {exp_rel}")
            console.print(
                f"  File exists but first line doesn't match "
                f"'{lang.translated_from}: [[original note name]]'"
            )
            continue

        result = check_alignment(original, trans_file)

        if result.is_valid:
            console.print(f"  [bold green]OK[/bold green]  {exp_rel}")
        else:
            if not result.line_count_match:
                console.print(
                    f"  [bold yellow]LINE COUNT MISMATCH[/bold yellow]  {exp_rel}\n"
                    f"  original={result.original_content_lines} lines, "
                    f"translation={result.translation_content_lines} lines"
                )
            else:
                console.print(
                    f"  [bold yellow]MISALIGNED[/bold yellow]  {exp_rel}  "
                    f"({len(result.issues)} issue(s))"
                )

            for issue in result.issues[:5]:
                console.print(f"    Line {issue.line_num}: {issue.describe()}")
            if len(result.issues) > 5:
                console.print(
                    f"    … {len(result.issues) - 5} more. "
                    f"Run: tongues inspect {original.rel_path} {lang.code}"
                )

    # Link universe coaching
    _print_link_universe_reminder(config, original, all_files)
    console.print()


# ---------------------------------------------------------------------------
# tongues inspect
# ---------------------------------------------------------------------------

@main.command()
@click.argument("file")
@click.argument("lang")
def inspect(file: str, lang: str) -> None:
    """
    Full alignment diff for one original/language pair.

    FILE is the original file. LANG is the language code (e.g. es, zh).
    """
    config = _load_or_exit()
    original = _resolve_original(config, file)

    lang_obj = next((l for l in config.languages if l.code == lang), None)
    if lang_obj is None:
        err_console.print(
            f"[red]Unknown language code:[/red] {lang!r}. "
            f"Configured: {', '.join(l.code for l in config.languages)}"
        )
        sys.exit(1)

    exp_path = declared_translation_path(config, original, lang_obj)

    console.print()

    if exp_path is None:
        console.print(f"[bold]Inspecting:[/bold] {original.rel_path}  →  [{lang}] (no path declared)")
        console.print()
        console.print(f"[bold red]No translation path declared for {lang_obj.name}.[/bold red]")
        console.print(
            f"Add a wiki-link to line 1 of {original.rel_path}:\n"
            f"  [bold][[{original.path.stem}-{lang}|{lang_obj.name}]][/bold]"
        )
        console.print(
            f"Replace '{original.path.stem}-{lang}' with the translated title, "
            f"then create the translation file."
        )
        all_files = scan_vault(config)
        _print_link_universe_reminder(config, original, all_files)
        return

    exp_rel = exp_path.relative_to(config.vault_root)
    console.print(f"[bold]Inspecting:[/bold] {original.rel_path}  →  [{lang}] {exp_rel}")
    console.print()

    if not exp_path.exists():
        console.print(f"[bold red]Translation does not exist.[/bold red]")
        console.print(f"Expected at: {exp_rel}")
        console.print(
            f"\nCreate it with this header on line 1:\n"
            f"  [bold]{lang_obj.translated_from}: [[{original.path.stem}]][/bold]"
        )
        console.print(
            f"\nThen a blank line, then {len(original.content_lines)} lines of translated content."
        )
        all_files = scan_vault(config)
        _print_link_universe_reminder(config, original, all_files)
        return

    all_files = scan_vault(config)
    index = build_translation_index(all_files)
    trans_file = index.get(exp_path.resolve())

    if trans_file is None or trans_file.header is None:
        console.print(f"[bold red]Translation header missing.[/bold red]")
        console.print(
            f"Line 1 of {exp_rel} must be:\n"
            f"  [bold]{lang_obj.translated_from}: [[{original.path.stem}]][/bold]"
        )
        _print_link_universe_reminder(config, original, all_files)
        return

    result = check_alignment(original, trans_file)

    if result.is_valid:
        console.print("[bold green]✓ Alignment is valid.[/bold green]")
        console.print(f"  {result.original_content_lines} content lines, all structure checks pass.")
        _print_link_universe_reminder(config, original, all_files)
        return

    # Line count
    if not result.line_count_match:
        console.print(
            f"[bold yellow]Line count mismatch[/bold yellow]: "
            f"original body has {result.original_content_lines} lines, "
            f"translation body has {result.translation_content_lines} "
            f"(headers excluded on both sides)."
        )
        diff = result.translation_content_lines - result.original_content_lines
        if diff > 0:
            console.print(f"  Translation body has [yellow]{diff} extra[/yellow] line(s). Remove them.")
        else:
            console.print(f"  Translation body is [yellow]{-diff} line(s) short[/yellow]. Add missing lines.")
        console.print()

    # Issues table
    if result.issues:
        console.print(f"[bold]Structural issues[/bold] ({len(result.issues)}):")
        table = Table(box=box.SIMPLE_HEAD, show_header=True, header_style="bold", expand=True)
        table.add_column("Line", style="dim", width=5)
        table.add_column("Issue")
        table.add_column("Original", overflow="fold")
        table.add_column("Translation", overflow="fold")

        for issue in result.issues:
            table.add_row(
                str(issue.line_num),
                issue.issue_type.replace("_", " "),
                issue.original_line.strip(),
                (issue.translation_line or "").strip(),
            )

        console.print(table)

    _print_link_universe_reminder(config, original, all_files)
    console.print()


# ---------------------------------------------------------------------------
# tongues where
# ---------------------------------------------------------------------------

@main.command()
@click.argument("file")
@click.argument("lang")
def where(file: str, lang: str) -> None:
    """
    Print the path where a translation file should (or does) live.

    Requires the original to have a language-link header entry for the given
    language. Returns a non-zero exit code if no path is declared.
    """
    config = _load_or_exit()
    original = _resolve_original(config, file)

    lang_obj = next((l for l in config.languages if l.code == lang), None)
    if lang_obj is None:
        err_console.print(
            f"[red]Unknown language code:[/red] {lang!r}. "
            f"Configured: {', '.join(l.code for l in config.languages)}"
        )
        sys.exit(1)

    exp_path = declared_translation_path(config, original, lang_obj)

    if exp_path is None:
        err_console.print(
            f"[red]No {lang_obj.name} link in header[/red] of {original.rel_path}.\n"
            f"Add one when you create the translation: "
            f"[[translated-title-{lang}|{lang_obj.name}]]"
        )
        sys.exit(1)

    exp_rel = exp_path.relative_to(config.vault_root)
    exists = "exists" if exp_path.exists() else "does not exist"
    console.print(f"{exp_rel}  [dim]({exists})[/dim]")


# ---------------------------------------------------------------------------
# tongues languages
# ---------------------------------------------------------------------------

@main.command()
def languages() -> None:
    """List configured languages."""
    config = _load_or_exit()

    console.print()
    console.print("[bold]Configured languages:[/bold]")
    for lang in config.languages:
        console.print(
            f"  [{lang.code}]  {lang.name}  "
            f"[dim]header phrase: \"{lang.translated_from}: [[original note name]]\"[/dim]"
        )
    console.print(
        f"\n[dim]Translations folder: {config.translations_folder}[/dim]"
    )
