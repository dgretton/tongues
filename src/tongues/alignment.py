"""
Structural alignment checking between an original and its translation,
and link-universe validation for translation files.

A translation is considered valid when:
  1. Content line count matches the original.
  2. Every heading has the same level (# count) at the same line position.
  3. Bullet/list markers appear at the same line positions.
  4. Checkbox states ([ ] vs [x]) match at every line position.
  5. Lines that contain links in the original also contain links in the
     translation (same count).
  6. Every wiki-link in the translation's content points to either a
     same-language translation file or an original used as a stand-in
     (stand-ins must include the ⍰ marker in their display text).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .config import TonguesConfig
from .vault import (
    VaultFile, TranslationHeader,
    HEADING_RE, BULLET_RE, CHECKBOX_RE, LINK_RE, WIKI_LINK_RE,
    STANDBY_MARKER,
)


def _count_links(line: str) -> int:
    return len(LINK_RE.findall(line)) + len(WIKI_LINK_RE.findall(line))


@dataclass
class AlignmentIssue:
    line_num: int           # 1-indexed within content (after header)
    issue_type: str         # see constants below
    original_line: str
    translation_line: str | None

    # issue_type values:
    MISSING_HEADING = "missing_heading"         # orig has heading, trans does not
    SPURIOUS_HEADING = "spurious_heading"       # trans has heading, orig does not
    HEADING_LEVEL = "heading_level_mismatch"    # both headings but different depth
    MISSING_BULLET = "missing_bullet"           # orig has bullet, trans does not
    SPURIOUS_BULLET = "spurious_bullet"         # trans has bullet, orig does not
    MISSING_LINKS = "missing_links"             # orig has links, trans has none
    SPURIOUS_LINKS = "spurious_links"           # trans has links, orig has none
    LINK_COUNT = "link_count_mismatch"          # both have links but different count
    MISSING_CHECKBOX = "missing_checkbox"       # orig has checkbox, trans has plain bullet
    SPURIOUS_CHECKBOX = "spurious_checkbox"     # trans has checkbox, orig has plain bullet
    CHECKBOX_STATE = "checkbox_state_mismatch"  # both have checkboxes but different states

    def describe(self) -> str:
        t = self.issue_type
        orig = repr(self.original_line.strip())
        trans = repr(self.translation_line.strip()) if self.translation_line else "—"
        if t == self.MISSING_HEADING:
            return f"original is a heading, translation is not\n    orig:  {orig}\n    trans: {trans}"
        if t == self.SPURIOUS_HEADING:
            return f"translation has unexpected heading\n    orig:  {orig}\n    trans: {trans}"
        if t == self.HEADING_LEVEL:
            orig_level = len(HEADING_RE.match(self.original_line).group(1))
            trans_level = len(HEADING_RE.match(self.translation_line).group(1))
            return f"heading level mismatch (original H{orig_level}, translation H{trans_level})\n    orig:  {orig}\n    trans: {trans}"
        if t == self.MISSING_BULLET:
            return f"original is a list item, translation is not\n    orig:  {orig}\n    trans: {trans}"
        if t == self.SPURIOUS_BULLET:
            return f"translation has unexpected list item\n    orig:  {orig}\n    trans: {trans}"
        if t == self.MISSING_LINKS:
            return f"original has link(s), translation has none\n    orig:  {orig}\n    trans: {trans}"
        if t == self.SPURIOUS_LINKS:
            return f"translation has link(s), original has none\n    orig:  {orig}\n    trans: {trans}"
        if t == self.LINK_COUNT:
            n_orig = _count_links(self.original_line)
            n_trans = _count_links(self.translation_line)
            return f"link count mismatch (original {n_orig}, translation {n_trans})\n    orig:  {orig}\n    trans: {trans}"
        if t == self.MISSING_CHECKBOX:
            return f"original has checkbox, translation has plain bullet\n    orig:  {orig}\n    trans: {trans}"
        if t == self.SPURIOUS_CHECKBOX:
            return f"translation has unexpected checkbox\n    orig:  {orig}\n    trans: {trans}"
        if t == self.CHECKBOX_STATE:
            o_state = CHECKBOX_RE.match(self.original_line).group(1)
            t_state = CHECKBOX_RE.match(self.translation_line).group(1)
            o_label = "checked" if o_state.lower() == "x" else f"[{o_state}]"
            t_label = "checked" if t_state.lower() == "x" else f"[{t_state}]"
            return f"checkbox state mismatch (original {o_label}, translation {t_label})\n    orig:  {orig}\n    trans: {trans}"
        return f"{t}\n    orig:  {orig}\n    trans: {trans}"


@dataclass
class AlignmentResult:
    original: VaultFile
    translation: VaultFile
    original_content_lines: int
    translation_content_lines: int
    issues: list[AlignmentIssue] = field(default_factory=list)

    @property
    def line_count_match(self) -> bool:
        return self.original_content_lines == self.translation_content_lines

    @property
    def is_valid(self) -> bool:
        return self.line_count_match and len(self.issues) == 0

    @property
    def issue_count(self) -> int:
        return len(self.issues) + (0 if self.line_count_match else 1)


def check_alignment(original: VaultFile, translation: VaultFile) -> AlignmentResult:
    """Return a full alignment report for one original/translation pair."""
    orig_content = original.content_lines
    trans_content = translation.content_lines

    result = AlignmentResult(
        original=original,
        translation=translation,
        original_content_lines=len(orig_content),
        translation_content_lines=len(trans_content),
    )

    min_lines = min(len(orig_content), len(trans_content))

    for i in range(min_lines):
        o = orig_content[i]
        t = trans_content[i]
        line_num = i + 1

        # --- headings ---
        o_head = HEADING_RE.match(o)
        t_head = HEADING_RE.match(t)

        if o_head and not t_head:
            result.issues.append(AlignmentIssue(line_num, AlignmentIssue.MISSING_HEADING, o, t))
        elif not o_head and t_head:
            result.issues.append(AlignmentIssue(line_num, AlignmentIssue.SPURIOUS_HEADING, o, t))
        elif o_head and t_head and o_head.group(1) != t_head.group(1):
            result.issues.append(AlignmentIssue(line_num, AlignmentIssue.HEADING_LEVEL, o, t))

        # --- bullets ---
        o_bullet = BULLET_RE.match(o)
        t_bullet = BULLET_RE.match(t)

        if o_bullet and not t_bullet:
            result.issues.append(AlignmentIssue(line_num, AlignmentIssue.MISSING_BULLET, o, t))
        elif not o_bullet and t_bullet:
            result.issues.append(AlignmentIssue(line_num, AlignmentIssue.SPURIOUS_BULLET, o, t))

        # --- checkboxes (subset of bullets — checked independently) ---
        o_check = CHECKBOX_RE.match(o)
        t_check = CHECKBOX_RE.match(t)

        if o_check and not t_check:
            result.issues.append(AlignmentIssue(line_num, AlignmentIssue.MISSING_CHECKBOX, o, t))
        elif not o_check and t_check:
            result.issues.append(AlignmentIssue(line_num, AlignmentIssue.SPURIOUS_CHECKBOX, o, t))
        elif o_check and t_check and o_check.group(1).lower() != t_check.group(1).lower():
            result.issues.append(AlignmentIssue(line_num, AlignmentIssue.CHECKBOX_STATE, o, t))

        # --- links (markdown + wiki-links) ---
        o_n = _count_links(o)
        t_n = _count_links(t)

        if o_n > 0 and t_n == 0:
            result.issues.append(AlignmentIssue(line_num, AlignmentIssue.MISSING_LINKS, o, t))
        elif o_n == 0 and t_n > 0:
            result.issues.append(AlignmentIssue(line_num, AlignmentIssue.SPURIOUS_LINKS, o, t))
        elif o_n > 0 and t_n > 0 and o_n != t_n:
            result.issues.append(AlignmentIssue(line_num, AlignmentIssue.LINK_COUNT, o, t))

    return result


# ---------------------------------------------------------------------------
# Link-universe validation
# ---------------------------------------------------------------------------

@dataclass
class LinkUniverseIssue:
    line_num: int       # 1-indexed within content
    target: str         # note name as written in the wiki-link
    display: str        # display text as written (may be empty)
    issue_type: str
    found_lang: str | None = None   # for WRONG_LANGUAGE: the language code found

    BROKEN = "broken_link"              # target not found in vault
    WRONG_LANGUAGE = "wrong_language"   # target is a translation in a different language
    MISSING_STANDBY = "missing_standby" # target is an original but ⍰ is absent from display

    def describe(self) -> str:
        t = self.issue_type
        if t == self.BROKEN:
            return (
                f"[[{self.target}]] not found in vault — "
                f"if the translation doesn't exist yet, link to the original as a stand-in: "
                f"[[original-note-name|{STANDBY_MARKER} {self.target}]]"
            )
        if t == self.WRONG_LANGUAGE:
            return (
                f"[[{self.target}]] is a [{self.found_lang}] translation — "
                f"links must stay within the same language universe"
            )
        if t == self.MISSING_STANDBY:
            display = self.display or self.target
            return (
                f"[[{self.target}]] links to an original without the {STANDBY_MARKER} stand-in marker — "
                f"use [[{self.target}|{STANDBY_MARKER} {display}]]"
            )
        return f"{t}: [[{self.target}]]"


def check_link_universe(
    translation: VaultFile,
    all_files: list[VaultFile],
    config: TonguesConfig,
) -> list[LinkUniverseIssue]:
    """
    Validate that every wiki-link in a translation's content follows the
    link universe convention:
      - Points to a translation in the SAME language, OR
      - Points to an original as a stand-in, with ⍰ in the display text.
    Anything else (broken link, wrong-language translation, original without ⍰)
    is an issue that makes the translation invalid.
    """
    issues = []
    if not isinstance(translation.header, TranslationHeader):
        return issues

    this_lang_code = translation.header.language.code
    by_stem: dict[str, VaultFile] = {f.path.stem: f for f in all_files}

    for i, line in enumerate(translation.content_lines):
        for note_name, display in WIKI_LINK_RE.findall(line):
            note_name = note_name.strip()
            linked = by_stem.get(note_name)

            if linked is None:
                issues.append(LinkUniverseIssue(
                    line_num=i + 1, target=note_name, display=display,
                    issue_type=LinkUniverseIssue.BROKEN,
                ))
            elif linked.is_translation:
                if (isinstance(linked.header, TranslationHeader)
                        and linked.header.language.code != this_lang_code):
                    issues.append(LinkUniverseIssue(
                        line_num=i + 1, target=note_name, display=display,
                        issue_type=LinkUniverseIssue.WRONG_LANGUAGE,
                        found_lang=linked.header.language.code,
                    ))
                # same language (or unrecognised header) → OK
            else:
                # Points to an original — stand-in, must have ⍰ in display
                if STANDBY_MARKER not in display:
                    issues.append(LinkUniverseIssue(
                        line_num=i + 1, target=note_name, display=display,
                        issue_type=LinkUniverseIssue.MISSING_STANDBY,
                    ))

    return issues
