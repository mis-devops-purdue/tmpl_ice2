"""Pure text checks for ICE 2 (DevOps Bios) and Hands-On 2.2 (Team Directory). No network calls here."""
import re

# Bio fields every student fills in. Matched by keyword so small label
# edits ("Fun fact" vs "Fun fact about me") still count.
BIO_FIELDS = {
    "program": "Program / concentration",
    "background": "Background (one sentence)",
    "superpower": "DevOps superpower",
    "kryptonite": "DevOps kryptonite",
    "tool": "A tool I'd like to learn this term",
    "fun fact": "Fun fact",
}
TIMEZONE_KEYS = ("time zone", "timezone")

FIELD_RE = re.compile(r"^\s*[-*]\s*\*\*(?P<label>.+?)\*\*\s*:?\s*(?P<value>.*)$")
PLACEHOLDER_RE = re.compile(r"<[^>]*>")


def _filled(value):
    v = PLACEHOLDER_RE.sub("", value or "").strip(" .:-*")
    return bool(v)


def parse_bio(text):
    """Return {'name': str|None, 'fields': {key: value}, 'timezone': value|None}."""
    name, fields, tz = None, {}, None
    for line in (text or "").splitlines():
        if line.startswith("# ") and name is None:
            name = line[2:].strip()
            continue
        m = FIELD_RE.match(line)
        if not m:
            continue
        label = m.group("label").lower().rstrip(":")
        value = m.group("value").strip()
        if any(k in label for k in TIMEZONE_KEYS):
            tz = value
            continue
        for key in BIO_FIELDS:
            if key in label and key not in fields:
                fields[key] = value
                break
    return {"name": name, "fields": fields, "timezone": tz}


def bio_problems(text, need_timezone=False):
    """List of problem codes for a bio file. Empty list = complete."""
    if text is None:
        return ["missing_file"]
    bio = parse_bio(text)
    problems = []
    if not bio["name"] or not _filled(bio["name"]) or "your name" in bio["name"].lower():
        problems.append("name")
    for key in BIO_FIELDS:
        if not _filled(bio["fields"].get(key, "")):
            problems.append(f"field:{key}")
    if need_timezone and not _filled(bio["timezone"] or ""):
        problems.append("timezone")
    return problems


def branch_suffix(ref, prefix):
    """'bio/jdoe' -> 'jdoe' when prefix == 'bio/'; else None."""
    if ref and ref.startswith(prefix) and len(ref) > len(prefix):
        return ref[len(prefix):]
    return None


# ---- Team Directory (README.md) ------------------------------------------
DIRECTORY_HEADING = "## team directory"
ENTRY_RE = re.compile(r"^- \[(?P<name>[^\]]+)\]\(bios/(?P<suffix>[^)/\s]+)\.md\)\s*$")
CONFLICT_MARKERS = ("<<<<<<<", "=======", ">>>>>>>")


def changed_lines(patch):
    """(added, removed) line texts from a GitHub pull request file patch."""
    added, removed = [], []
    for line in (patch or "").splitlines():
        if line.startswith("@@") or line.startswith("\\"):
            continue
        if line.startswith("+"):
            added.append(line[1:])
        elif line.startswith("-"):
            removed.append(line[1:])
    return added, removed


def _directory_bounds(lines):
    start = next((i for i, l in enumerate(lines) if l.strip().lower() == DIRECTORY_HEADING), None)
    if start is None:
        return None
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    return start, end


def directory_entries(text):
    """The '- [Name](bios/x.md)' lines in the Team Directory section, in file order."""
    lines = (text or "").splitlines()
    bounds = _directory_bounds(lines)
    if not bounds:
        return []
    return [l.rstrip() for l in lines[bounds[0] + 1:bounds[1]] if l.startswith("- [")]


def merge_directory(main_text, other_text):
    """main_text with its Team Directory list replaced by the union of both sides' lines.

    Exact duplicates are dropped and the list is sorted by display name (case-insensitive).
    Everything outside the list stays exactly as in main_text. Raises ValueError when
    main_text has no Team Directory section.
    """
    lines = main_text.splitlines()
    bounds = _directory_bounds(lines)
    if not bounds:
        raise ValueError("README.md has no '## Team Directory' section")
    start, end = bounds
    body = lines[start + 1:end]
    union = []
    for line in directory_entries(main_text) + directory_entries(other_text):
        if line not in union:
            union.append(line)

    def key(line):
        m = ENTRY_RE.match(line)
        return ((m.group("name") if m else line).lower(), line.lower())

    union.sort(key=key)
    at = [i for i, l in enumerate(body) if l.startswith("- [")]
    if at:
        body = body[:at[0]] + union + body[at[-1] + 1:]
    else:  # empty list: put the lines right after the last non-blank line of the section
        last = max([i for i, l in enumerate(body) if l.strip()], default=-1)
        body = body[:last + 1] + union + body[last + 1:]
    out = "\n".join(lines[:start + 1] + body + lines[end:])
    return out + ("\n" if main_text.endswith("\n") else "")


def has_conflict_markers(text):
    return any(line.startswith(CONFLICT_MARKERS) for line in (text or "").splitlines())


def box(done):
    return "✅" if done else "⬜"


FRIENDLY = {
    "missing_file": "I couldn't find the file I expected.",
    "name": "Replace `<Your Name>` in the heading with your name.",
    "field:program": "Fill in **Program / concentration**.",
    "field:background": "Fill in **Background (one sentence)**.",
    "field:superpower": "Fill in **DevOps superpower**.",
    "field:kryptonite": "Fill in **DevOps kryptonite**.",
    "field:tool": "Fill in **A tool I'd like to learn this term**.",
    "field:fun fact": "Fill in **Fun fact**.",
}


def explain(problems):
    return "\n".join(f"- {FRIENDLY.get(p, p)}" for p in problems)
