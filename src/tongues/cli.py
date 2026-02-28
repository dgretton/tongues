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
from rich.text import Text

from .config import load_config, TonguesConfig, Language, DEFAULT_CONFIG_CONTENT, CONFIG_FILENAME
from .vault import (
    VaultFile,
    scan_vault,
    translation_path,
    translation_rel_path,
    translation_filename,
    LINK_RE,
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

Use [bold]tongues check <file>[/bold] to see the expected translation paths for every
link in a file before you write or edit a translation.\
"""


def _status_style(label: str) -> str:
    return {
        "OK": "bold green",
        "MISSING": "bold red",
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


def _print_link_universe_reminder(
    config: TonguesConfig,
    original: VaultFile,
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
    table.add_column("Original links to")
    for lang in config.languages:
        table.add_column(f"→ {lang.name}", overflow="fold")

    for line_idx, _text, target in internal:
        # Try to resolve target relative to the original's directory
        orig_dir = original.path.parent
        try:
            linked_abs = (orig_dir / target).resolve()
            linked_rel = linked_abs.relative_to(config.vault_root)
            lang_cells = []
            for lang in config.languages:
                trans_rel = translation_rel_path(config, linked_rel, lang.code)
                lang_cells.append(str(trans_rel))
        except (ValueError, OSError):
            lang_cells = ["(external or unresolvable)"] * len(config.languages)

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
    console.print(
        f"[bold]Translation status:[/bold]  "
        f"[{pct_style}]{pct:.1f}%[/{pct_style}]  "
        f"({vs.completed}/{vs.total_expected} complete)"
    )
    console.print(
        f"Vault: [dim]{config.vault_root}[/dim]  "
        f"Originals: {len(vs.originals)}  "
        f"Languages: {', '.join(l.name for l in config.languages)}"
    )
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
            exp_rel = r.expected_path.relative_to(config.vault_root)

            if r.is_ok:
                console.print(f"  [{style}]{label:12}[/{style}] [{r.language.code}] {exp_rel}")
                continue

            console.print(f"  [{style}]{label:12}[/{style}] [{r.language.code}] → {exp_rel}")

            if r.status == "missing":
                console.print(
                    f"    [dim]Create this file with header:[/dim] "
                    f"{r.language.translated_from}: "
                    f"[original title]({orig_rel})"
                )
            elif r.status == "missing_header":
                console.print(
                    f"    [dim]File exists but has no valid '{r.language.translated_from}: ...' header.[/dim]"
                )
            elif r.alignment:
                if not r.alignment.line_count_match:
                    console.print(
                        f"    [dim]Line count: original={r.alignment.original_content_lines}, "
                        f"translation={r.alignment.translation_content_lines}[/dim]"
                    )
                if r.alignment.issues:
                    # Show first 3 issues inline; rest available via inspect
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
    and shows which translation paths internal links should resolve to.
    """
    config = _load_or_exit()
    original = _resolve_original(config, file)

    console.print()
    console.print(f"[bold]Checking:[/bold] {original.rel_path}")

    # Header status
    if original.header:
        console.print(f"  Header: [green]present[/green] ({len(original.header.language_links)} language link(s))")
    else:
        console.print(f"  Header: [bold yellow]ACTION NEEDED[/bold yellow] — no language-link line found on line 1.")
        console.print(f"  → Run [bold]tongues header {original.rel_path}[/bold] to generate it, then add it to the file.")

    console.print(f"  Content lines: {len(original.content_lines)}")
    console.print()

    for lang in config.languages:
        exp_path = translation_path(config, original.rel_path, lang.code)
        exp_rel = exp_path.relative_to(config.vault_root)

        console.rule(f"[bold]{lang.name}[/bold] [{lang.code}]", style="dim")

        if not exp_path.exists():
            console.print(f"  [bold red]MISSING[/bold red]  {exp_rel}")
            console.print(f"  Create with header:")
            console.print(
                f"    [bold]{lang.translated_from}: "
                f"[<original title>]({original.rel_path})[/bold]"
            )
            console.print(
                f"  Then a blank line, then the translated content "
                f"({len(original.content_lines)} lines to match)."
            )
            continue

        # Load the translation
        all_files = scan_vault(config)
        trans_file = next((f for f in all_files if f.path.resolve() == exp_path.resolve()), None)

        if trans_file is None:
            console.print(f"  [red]Could not read:[/red] {exp_rel}")
            continue

        if trans_file.header is None:
            console.print(f"  [bold red]NO HEADER[/bold red]  {exp_rel}")
            console.print(
                f"  File exists but first line doesn't match "
                f"'{lang.translated_from}: [title](path)'"
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
    _print_link_universe_reminder(config, original)
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

    exp_path = translation_path(config, original.rel_path, lang_obj.code)
    exp_rel = exp_path.relative_to(config.vault_root)

    console.print()
    console.print(f"[bold]Inspecting:[/bold] {original.rel_path}  →  [{lang}] {exp_rel}")
    console.print()

    if not exp_path.exists():
        console.print(f"[bold red]Translation does not exist.[/bold red]")
        console.print(f"Expected at: {exp_rel}")
        console.print(
            f"\nCreate it with this header on line 1:\n"
            f"  [bold]{lang_obj.translated_from}: [<title>]({original.rel_path})[/bold]"
        )
        console.print(
            f"\nThen a blank line, then {len(original.content_lines)} lines of translated content."
        )
        _print_link_universe_reminder(config, original)
        return

    all_files = scan_vault(config)
    trans_file = next((f for f in all_files if f.path.resolve() == exp_path.resolve()), None)

    if trans_file is None or trans_file.header is None:
        console.print(f"[bold red]Translation header missing.[/bold red]")
        console.print(
            f"Line 1 of {exp_rel} must be:\n"
            f"  [bold]{lang_obj.translated_from}: [<title>]({original.rel_path})[/bold]"
        )
        _print_link_universe_reminder(config, original)
        return

    result = check_alignment(original, trans_file)

    if result.is_valid:
        console.print("[bold green]✓ Alignment is valid.[/bold green]")
        console.print(f"  {result.original_content_lines} content lines, all structure checks pass.")
        _print_link_universe_reminder(config, original)
        return

    # Line count
    if not result.line_count_match:
        console.print(
            f"[bold yellow]Line count mismatch[/bold yellow]: "
            f"original has {result.original_content_lines} content lines, "
            f"translation has {result.translation_content_lines}."
        )
        diff = result.translation_content_lines - result.original_content_lines
        if diff > 0:
            console.print(f"  Translation has [yellow]{diff} extra[/yellow] line(s). Remove them.")
        else:
            console.print(f"  Translation is [yellow]{-diff} short[/yellow]. Add missing lines.")
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

    _print_link_universe_reminder(config, original)
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

    Useful for scripting: tongues where notes/foo.md es
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

    exp_path = translation_path(config, original.rel_path, lang_obj.code)
    exp_rel = exp_path.relative_to(config.vault_root)

    exists = "exists" if exp_path.exists() else "does not exist"
    console.print(f"{exp_rel}  [dim]({exists})[/dim]")


# ---------------------------------------------------------------------------
# tongues header
# ---------------------------------------------------------------------------

@main.command()
@click.argument("file")
def header(file: str) -> None:
    """
    Generate the language-link header line for an original file.

    Prints the exact text to place on line 1 of the original (followed by
    a blank line). The header links to all configured language translations.
    """
    config = _load_or_exit()
    original = _resolve_original(config, file)

    links = []
    for lang in config.languages:
        rel = translation_rel_path(config, original.rel_path, lang.code)
        links.append(f"[{lang.name}]({rel})")

    header_line = " " + " | ".join(links)  # leading space keeps Obsidian cursor off the link
    console.print()
    console.print("[bold]Paste this as line 1 of the original file, followed by a blank line:[/bold]")
    console.print("[dim](Note the single leading space — required to keep the Obsidian cursor off the link.)[/dim]")
    console.print()
    console.print(f"  {header_line}")
    console.print()
    console.print("[dim]Line 2 should be blank, then your content follows from line 3.[/dim]")


# ---------------------------------------------------------------------------
# tongues languages
# ---------------------------------------------------------------------------

@main.command()
def languages() -> None:
    """List configured languages."""
    config = _load_or_exit()

    console.print()
    console.print(
        f"[bold]Original language:[/bold] {config.original_language.name} "
        f"[dim]({config.original_language.code})[/dim]"
    )
    console.print("[bold]Target languages:[/bold]")
    for lang in config.languages:
        console.print(
            f"  [{lang.code}]  {lang.name}  "
            f"[dim]header phrase: \"{lang.translated_from}: [title](path)\"[/dim]"
        )
    console.print(
        f"\n[dim]Translations folder: {config.translations_folder}[/dim]"
    )
