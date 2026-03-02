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
    TranslationHeader,
    scan_vault,
    declared_translation_path,
    build_translation_index,
    is_ignored,
    WIKI_LINK_RE,
    TRANSLATION_MARKER,
    STANDBY_MARKER,
)
from .status import compute_status, VaultStatus, TranslationRecord
from .alignment import check_alignment, check_link_universe, LinkUniverseIssue

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
        "BAD LINKS": "bold red",
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
                    f"(matches an ignore pattern in {CONFIG_FILENAME}).[/dim]"
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
    *,
    show_panel: bool = True,
    show_table: bool = True,
) -> None:
    """Print link universe coaching for a given original.

    show_panel: show the ⚠ LINK UNIVERSE PRINCIPLE explanation box.
    show_table: show the per-link target table.
    Both default to True (full output). Pass show_panel=False when the agent
    already knows the rule but needs the target table. Pass neither True to
    suppress entirely (or just don't call the function).
    """
    internal = original.all_internal_links()
    if not internal or (not show_panel and not show_table):
        return

    console.print()
    if show_panel:
        console.print(Panel(LINK_UNIVERSE_REMINDER, border_style="yellow"))
    if not show_table:
        return

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
    👅 tongues — Obsidian vault translation coordinator.

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
    """Create a tongues-config.md config file in the current directory."""
    config_path = Path.cwd() / CONFIG_FILENAME
    if config_path.exists() and not force:
        console.print(
            f"[yellow]{CONFIG_FILENAME} already exists.[/yellow] "
            "Use --force to overwrite."
        )
        sys.exit(1)

    config_path.write_text(DEFAULT_CONFIG_CONTENT, encoding="utf-8")
    console.print(f"👅 [green]Created[/green] {config_path}")
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
        f"👅 [bold]Language coverage:[/bold]  "
        f"[{pct_style}]{pct:.1f}%[/{pct_style}]  "
        f"[dim]({vs.completed} of {vs.total_expected} language versions exist)[/dim]"
    )
    console.print(
        f"Vault: [dim]{config.vault_root}[/dim]  "
        f"Documents: {n_orig}  "
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
        console.print("[dim]No documents found. Create some .md files outside the translations folder.[/dim]")
        return

    needs_work = vs.needs_work()

    if not needs_work:
        console.print("👅 [bold green]✓ All language versions are complete and valid.[/bold green]")
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
                    f"    [dim]No {r.language.name} version linked in header — "
                    f"add one when you create it: "
                    f"• [[{r.language.name}-title|{r.language.name}]] •  "
                    f"(line 1 of the {r.language.name} version: {TRANSLATION_MARKER} {r.language.translated_from}: [[original-note-name]])[/dim]"
                )
                continue

            exp_rel = r.expected_path.relative_to(config.vault_root)

            if r.is_ok:
                console.print(f"  [{style}]{label:12}[/{style}] [{r.language.code}] {exp_rel}")
                continue

            console.print(f"  [{style}]{label:12}[/{style}] [{r.language.code}] → {exp_rel}")

            if r.status == "missing":
                console.print(
                    f"    [dim]Create the {r.language.name} version. Line 1:[/dim] "
                    f"{r.language.translated_from}: [[{orig_rel.stem}]]"
                )
            elif r.status == "missing_header":
                console.print(
                    f"    [dim]File exists but has no valid "
                    f"'{r.language.translated_from}: [[...]]' header.[/dim]"
                )
            elif r.status == "invalid_links":
                for issue in r.link_issues[:3]:
                    console.print(f"    [dim]Line {issue.line_num}:[/dim] {issue.issue_type.replace('_', ' ')}: [[{issue.target}]]")
                if len(r.link_issues) > 3:
                    console.print(
                        f"    [dim]… and {len(r.link_issues) - 3} more. "
                        f"Run: tongues check {orig_rel}[/dim]"
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
        f"[dim]{len(needs_work)} language version(s) need attention. "
        "Run [bold]tongues check <file>[/bold] for per-file detail.[/dim]"
    )


# ---------------------------------------------------------------------------
# tongues check
# ---------------------------------------------------------------------------

@main.command()
@click.argument("file")
@click.option("--verbose", "-v", is_flag=True,
              help="Show all issues and always display the link target table.")
def check(file: str, verbose: bool) -> None:
    """
    Check all translations for a specific original file.

    FILE may be the path to an original file or to any of its translations —
    passing a translation path will automatically redirect to its original.

    By default shows up to 3 issues per translation with a count of the rest.
    Use --verbose to see all issues and always display the link target table.
    """
    config = _load_or_exit()
    all_files = scan_vault(config)
    index = build_translation_index(all_files)

    # Resolve the file path (cwd-relative, vault-relative, or absolute)
    target = Path(file)
    if not target.is_absolute():
        cwd_path = Path.cwd() / target
        target = (cwd_path if cwd_path.exists() else config.vault_root / target).resolve()
    else:
        target = target.resolve()

    # Check ignore patterns before anything else
    if target.exists() and config.ignore_patterns:
        try:
            rel = target.relative_to(config.vault_root)
            if is_ignored(rel, config.ignore_patterns):
                console.print(
                    f"[dim]{file} is excluded from translation tracking "
                    f"(matches an ignore pattern in {CONFIG_FILENAME}).[/dim]"
                )
                sys.exit(0)
        except ValueError:
            pass

    vault_file = index.get(target)
    if vault_file is None:
        err_console.print(f"[red]File not found in vault:[/red] {file}")
        sys.exit(1)

    # Redirect translation paths to their original
    if vault_file.is_translation:
        if not isinstance(vault_file.header, TranslationHeader):
            err_console.print(f"[red]{file}[/red] is a translation file with no valid header.")
            sys.exit(1)
        original = _find_by_note_name(all_files, vault_file.header.original_note_name)
        if original is None:
            err_console.print(
                f"[red]Original '{vault_file.header.original_note_name}' not found in vault.[/red]"
            )
            sys.exit(1)
        console.print(f"[dim](translation of {original.rel_path} — checking original)[/dim]")
    elif not vault_file.is_original:
        err_console.print(f"[red]{file}[/red] is not a tracked vault file.")
        sys.exit(1)
    else:
        original = vault_file

    console.print()
    console.print(
        f"👅 [bold]Checking:[/bold] {original.rel_path}  "
        f"[dim]({len(original.content_lines)} content lines)[/dim]"
    )

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
        console.print(
            f"  Header: [dim]none yet[/dim] — "
            f"add a bullet link each time you create a language version: • [[title|lang]] •"
        )
    console.print()

    declared = [
        (lang, declared_translation_path(config, original, lang))
        for lang in config.languages
        if declared_translation_path(config, original, lang) is not None
    ]

    if not declared:
        console.print("[dim]No other-language versions declared yet.[/dim]")
        _print_link_universe_reminder(config, original, all_files,
                                      show_panel=verbose, show_table=True)
        console.print()
        return

    max_issues = None if verbose else 3
    any_missing = False
    has_link_violations = False

    for lang, exp_path in declared:
        console.rule(f"[bold]{lang.name}[/bold] [{lang.code}]", style="dim")
        exp_rel = exp_path.relative_to(config.vault_root)

        if not exp_path.exists():
            any_missing = True
            console.print(f"  [bold red]MISSING[/bold red]  {exp_rel}")
            console.print(f"  Create the {lang.name} version of this document with this 3-line header block:")
            console.print(f"    [bold]{TRANSLATION_MARKER} {lang.translated_from}: [[{original.path.stem}]][/bold]")
            console.print(f"    [bold](blank line)[/bold]")
            console.print(f"    [bold]---[/bold]")
            console.print(
                f"  Then {len(original.content_lines)} lines of body content "
                f"structured identically to {original.path.stem}."
            )
            console.print(
                f"  [dim]Links must point to {lang.name} versions of linked documents, "
                f"or to originals as stand-ins with the {STANDBY_MARKER} marker: "
                f"[[original-note|{STANDBY_MARKER} display name]][/dim]"
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
                f"'{TRANSLATION_MARKER} {lang.translated_from}: [[original note name]]'"
            )
            continue

        result = check_alignment(original, trans_file)
        link_issues = check_link_universe(trans_file, all_files, config)
        if link_issues:
            has_link_violations = True

        if result.is_valid and not link_issues:
            console.print(f"  [bold green]OK[/bold green]  {exp_rel}")
        else:
            if not result.line_count_match:
                console.print(
                    f"  [bold yellow]LINE COUNT MISMATCH[/bold yellow]  {exp_rel}\n"
                    f"  original={result.original_content_lines} lines, "
                    f"translation={result.translation_content_lines} lines"
                )
            elif result.issues:
                console.print(
                    f"  [bold yellow]MISALIGNED[/bold yellow]  {exp_rel}  "
                    f"({len(result.issues)} issue(s))"
                )
            if link_issues:
                console.print(
                    f"  [bold red]BAD LINKS[/bold red]  {exp_rel}  "
                    f"({len(link_issues)} issue(s))"
                )

            shown = result.issues[:max_issues] if max_issues is not None else result.issues
            for issue in shown:
                console.print(f"    Line {issue.line_num}: {issue.describe()}")
            if max_issues is not None and len(result.issues) > max_issues:
                console.print(
                    f"    [dim]… and {len(result.issues) - max_issues} more. "
                    f"Run: tongues inspect {original.rel_path} {lang.code}[/dim]"
                )

            shown_links = link_issues[:max_issues] if max_issues is not None else link_issues
            for issue in shown_links:
                console.print(f"    Line {issue.line_num}: {issue.describe()}")
            if max_issues is not None and len(link_issues) > max_issues:
                console.print(f"    [dim]… and {len(link_issues) - max_issues} more link issues[/dim]")

    # Link universe reminder — panel only when there are violations; table when
    # translations are missing (agent needs the target list) or verbose mode.
    if verbose or has_link_violations:
        _print_link_universe_reminder(config, original, all_files,
                                      show_panel=True, show_table=True)
    elif any_missing:
        _print_link_universe_reminder(config, original, all_files,
                                      show_panel=False, show_table=True)
    # all OK → no reminder

    console.print()


# ---------------------------------------------------------------------------
# tongues inspect
# ---------------------------------------------------------------------------

@main.command()
@click.argument("file")
@click.argument("lang")
@click.option("--max-issues", default=None, type=int, metavar="N",
              help="Show at most N issues in the alignment table (default: all).")
def inspect(file: str, lang: str, max_issues: int | None) -> None:
    """
    Full alignment diff for one original/language pair.

    FILE is the original file. LANG is the language code (e.g. es, zh).
    Use --max-issues N to cap the table when there are many issues.
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
        console.print(f"👅 [bold]Inspecting:[/bold] {original.rel_path}  →  [{lang}] (no path declared)")
        console.print()
        console.print(f"[bold red]No {lang_obj.name} version declared for this document.[/bold red]")
        console.print(
            f"Add a bullet link to line 1 of {original.rel_path}:\n"
            f"  [bold]• [[{lang_obj.name}-title|{lang_obj.name}]] •[/bold]"
        )
        console.print(
            f"Replace '{lang_obj.name}-title' with the actual title in {lang_obj.name}, "
            f"then create the {lang_obj.name} version with line 1:\n"
            f"  [bold]{TRANSLATION_MARKER} {lang_obj.translated_from}: [[{original.path.stem}]][/bold]"
        )
        all_files = scan_vault(config)
        _print_link_universe_reminder(config, original, all_files)
        return

    exp_rel = exp_path.relative_to(config.vault_root)
    console.print(f"👅 [bold]Inspecting:[/bold] {original.rel_path}  →  [{lang}] {exp_rel}")
    console.print()

    if not exp_path.exists():
        console.print(f"[bold red]The {lang_obj.name} version of this document does not exist yet.[/bold red]")
        console.print(f"Expected at: {exp_rel}")
        console.print(
            f"\nCreate it with this 3-line header block:\n"
            f"  [bold]{TRANSLATION_MARKER} {lang_obj.translated_from}: [[{original.path.stem}]][/bold]\n"
            f"  [bold](blank line)[/bold]\n"
            f"  [bold]---[/bold]"
        )
        console.print(
            f"\nThen {len(original.content_lines)} lines of translated content."
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
            f"Header block of {exp_rel} must be:\n"
            f"  [bold]{TRANSLATION_MARKER} {lang_obj.translated_from}: [[{original.path.stem}]][/bold]\n"
            f"  [bold](blank line)[/bold]\n"
            f"  [bold]---[/bold]"
        )
        _print_link_universe_reminder(config, original, all_files)
        return

    result = check_alignment(original, trans_file)

    if result.is_valid:
        console.print("👅 [bold green]✓ Alignment is valid.[/bold green]")
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
        shown = result.issues[:max_issues] if max_issues is not None else result.issues
        hidden = len(result.issues) - len(shown)
        console.print(f"[bold]Structural issues[/bold] ({len(result.issues)}):")
        table = Table(box=box.SIMPLE_HEAD, show_header=True, header_style="bold", expand=True)
        table.add_column("Line", style="dim", width=5)
        table.add_column("Issue")
        table.add_column("Original", overflow="fold")
        table.add_column("Translation", overflow="fold")

        for issue in shown:
            table.add_row(
                str(issue.line_num),
                issue.issue_type.replace("_", " "),
                issue.original_line.strip(),
                (issue.translation_line or "").strip(),
            )

        console.print(table)
        if hidden:
            console.print(
                f"[dim]… and {hidden} more issue(s) not shown "
                f"(remove --max-issues to see all)[/dim]"
            )

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
            f"• [[translated-title|{lang_obj.name}]] •"
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
    console.print("👅 [bold]Configured languages:[/bold]")
    for lang in config.languages:
        console.print(
            f"  [{lang.code}]  {lang.name}  "
            f"[dim]version header: \"{TRANSLATION_MARKER} {lang.translated_from}: [[document note name]]\"[/dim]"
        )
    console.print(
        f"\n[dim]Translations folder: {config.translations_folder}[/dim]"
    )
