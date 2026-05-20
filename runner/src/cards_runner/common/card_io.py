"""Card frontmatter read and write.

Cards are YAML-frontmatter Markdown. The runner reads the file, edits
the frontmatter dict, and rewrites the file atomically. The body is
preserved byte-for-byte through the round trip; the planner cares
about its line breaks and section headings.

Avoiding `yaml.dump` for the round trip is deliberate: `yaml.dump`
reorders keys, drops anchors and comments, and rewrites multi-line
strings in ways that produce noisy git diffs. The runner uses a
targeted in-place rewrite for the small set of fields it owns.

For chunk 1 the runner only touches: `status`, `claimed_by`,
`started_at`, `finished_at`, `last_heartbeat`, `attempt_trace_id`,
`model_used`. Anything else is left untouched.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import yaml  # type: ignore[import-untyped]

from .atomic import atomic_write_text
from .types import CardSnapshot


# A card looks like:
#
# ---
# id: b001-03-add-rate-limit-middleware
# status: backlog
# ...
# ---
#
# ## Context
# ...
#
# We split on the first two `---` lines.
_FRONTMATTER_RE = re.compile(
    r"^---\s*\n(?P<fm>.*?)\n---\s*\n(?P<body>.*)\Z",
    re.DOTALL,
)


# Field names whose value is a plain scalar the runner sets to a
# string, null, or number. Keep narrow on purpose: we do not want the
# in-place rewriter accidentally touching list-typed fields like
# `cascade_history` or `touches`.
_SCALAR_FIELDS_FOR_INPLACE_REWRITE: frozenset[str] = frozenset({
    "status",
    "claimed_by",
    "started_at",
    "finished_at",
    "last_heartbeat",
    "attempt_trace_id",
    "model_used",
})


class CardParseError(Exception):
    """Raised when a card file is not a valid YAML-frontmatter Markdown doc."""


@dataclass(frozen=True)
class _Match:
    fm_text: str
    body: str


def _split_card(text: str) -> _Match:
    m = _FRONTMATTER_RE.match(text)
    if m is None:
        raise CardParseError(
            "card file missing YAML frontmatter fenced by '---' lines"
        )
    return _Match(fm_text=m.group("fm"), body=m.group("body"))


def parse_card_file(path: Path) -> CardSnapshot:
    """Read and parse a card file. Returns a snapshot.

    `card_id` is taken from the `id:` field if present; otherwise the
    filename stem is used as a fallback.
    """
    text = path.read_text(encoding="utf-8")
    split = _split_card(text)
    fm: dict[str, Any] = yaml.safe_load(split.fm_text) or {}
    if not isinstance(fm, dict):
        raise CardParseError(
            f"frontmatter of {path} parsed to {type(fm).__name__}, "
            "expected mapping"
        )
    card_id = str(fm.get("id") or path.stem)
    return CardSnapshot(
        card_id=card_id,
        frontmatter=fm,
        body=split.body,
        raw_frontmatter_text=split.fm_text,
    )


def write_card_file(path: Path, snapshot: CardSnapshot) -> None:
    """Write a snapshot back atomically.

    Uses targeted in-place line rewrites for the scalar fields the
    runner owns. Any field the runner does not own is left exactly
    as the planner wrote it. Fields added since the original read
    (which chunk 1 should not be doing) get appended at the end of
    the frontmatter block.
    """
    new_fm_text = _rewrite_scalar_fields(
        snapshot.raw_frontmatter_text,
        snapshot.frontmatter,
    )
    rebuilt = f"---\n{new_fm_text}\n---\n{snapshot.body}"
    atomic_write_text(path, rebuilt)


def _rewrite_scalar_fields(
    fm_text: str,
    new_values: dict[str, Any],
) -> str:
    """Return `fm_text` with the runner-owned scalar fields rewritten.

    Only the fields in `_SCALAR_FIELDS_FOR_INPLACE_REWRITE` are
    touched. Other fields in `new_values` are ignored; the caller is
    not supposed to ask us to mutate them through this path.

    Fields present in `new_values` but not in the source frontmatter
    are appended to the bottom of the block. This is rare in chunk 1
    but happens for `attempt_trace_id` on the first claim.
    """
    lines = fm_text.split("\n")
    seen: set[str] = set()
    out: list[str] = []
    for line in lines:
        stripped = line.lstrip()
        if not stripped or stripped.startswith("#"):
            out.append(line)
            continue
        # Top-level scalar lines start at column 0 with `key:` (no
        # leading whitespace). List entries and nested keys are
        # indented; we leave them alone.
        if line.startswith(" ") or line.startswith("\t"):
            out.append(line)
            continue
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\s*:(?:\s|$)", line)
        if m is None:
            out.append(line)
            continue
        key = m.group(1)
        if key in _SCALAR_FIELDS_FOR_INPLACE_REWRITE and key in new_values:
            out.append(f"{key}: {_format_scalar(new_values[key])}")
            seen.add(key)
        else:
            out.append(line)
    # Append any owned fields that were not present in the source.
    appended: list[str] = []
    for key in _SCALAR_FIELDS_FOR_INPLACE_REWRITE:
        if key in new_values and key not in seen:
            appended.append(f"{key}: {_format_scalar(new_values[key])}")
    if appended:
        # Trim trailing blank line(s) so we glue cleanly.
        while out and out[-1] == "":
            out.pop()
        out.extend(appended)
    return "\n".join(out)


def _format_scalar(value: Any) -> str:
    """Format a scalar for YAML output.

    None -> `null`. Strings get a single layer of quoting only when
    they contain YAML-significant punctuation. UUID-shaped and ISO
    timestamps are common runner values and render unquoted, matching
    what the planner does.
    """
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value)
    if text == "":
        return '""'
    # Quote when the value collides with YAML syntax. Conservative.
    bad = set(":#&*!|>'\"%@`,{}[]")
    needs_quote = (
        text[0] in "-?" + " "
        or any(c in bad for c in text)
        or text.lower() in {"null", "true", "false", "yes", "no", "on", "off"}
    )
    if needs_quote:
        # Single quotes; double any inner single quote.
        return "'" + text.replace("'", "''") + "'"
    return text


def append_completion_notes(snapshot: CardSnapshot, notes_markdown: str) -> None:
    """Append a `## Completion notes` section to the card body.

    Idempotent for the section header: if the body already contains a
    `## Completion notes` line, the new content is concatenated under
    it. Otherwise the section is added at the end of the body.
    """
    header = "## Completion notes"
    body = snapshot.body.rstrip("\n")
    if header in body:
        snapshot.body = body + "\n\n" + notes_markdown.rstrip("\n") + "\n"
    else:
        snapshot.body = (
            body
            + "\n\n"
            + header
            + "\n\n"
            + notes_markdown.rstrip("\n")
            + "\n"
        )


def scan_card_dir(directory: Path) -> Iterable[Path]:
    """Yield card files in a subfolder, sorted by mtime then name.

    Sorted by mtime so the daemon picks the oldest queued card first
    (FIFO within the eligibility set). Name secondary sort keeps the
    order stable when the filesystem returns mtime ties.
    """
    if not directory.is_dir():
        return iter(())
    entries = [p for p in directory.iterdir() if p.is_file() and p.suffix == ".md"]
    entries.sort(key=lambda p: (p.stat().st_mtime_ns, p.name))
    return iter(entries)
