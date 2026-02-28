"""
Structural alignment checking between an original and its translation.

A translation is considered valid when:
  1. Content line count matches the original.
  2. Every heading has the same level (# count) at the same line position.
  3. Bullet/list markers appear at the same line positions.
  4. Lines that contain links in the original also contain links in the
     translation (link targets may differ — they should point to the same
     language universe, but that is a coaching concern, not a validity check).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .vault import VaultFile, HEADING_RE, BULLET_RE, LINK_RE


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
            n_orig = len(LINK_RE.findall(self.original_line))
            n_trans = len(LINK_RE.findall(self.translation_line))
            return f"link count mismatch (original {n_orig}, translation {n_trans})\n    orig:  {orig}\n    trans: {trans}"
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

        # --- links ---
        o_links = LINK_RE.findall(o)
        t_links = LINK_RE.findall(t)

        if o_links and not t_links:
            result.issues.append(AlignmentIssue(line_num, AlignmentIssue.MISSING_LINKS, o, t))
        elif not o_links and t_links:
            result.issues.append(AlignmentIssue(line_num, AlignmentIssue.SPURIOUS_LINKS, o, t))
        elif o_links and t_links and len(o_links) != len(t_links):
            result.issues.append(AlignmentIssue(line_num, AlignmentIssue.LINK_COUNT, o, t))

    return result
