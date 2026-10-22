"""ICE 2 – DevOps Bios (one shared repo for the whole class).

Two kinds of pull requests, told apart by branch prefix:
  bio/<purdue-username>     ICE 2 (graded): add bios/<purdue-username>.md, 2+ commits, open a PR
  readme/<purdue-username>  Hands-On 2.2 (ungraded): add one line to the README's Team Directory

The bot keeps one checklist comment on each pull request. It never reviews or merges
from here; the instructor-triggered merge bot (merge_ready.py) does that, and reuses
the readiness checks below.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from checks import (ENTRY_RE, bio_problems, box, branch_suffix, changed_lines,  # noqa: E402
                    explain)
from ghclient import BOT_LOGIN, upsert_marked_comment  # noqa: E402

MARK = "<!-- ice2_checklist -->"
BIO_PREFIX = "bio/"
README_PREFIX = "readme/"
WRONG_BRANCH = ("This repo expects a branch named `bio/<your-purdue-username>` (ICE 2) or "
                "`readme/<your-purdue-username>` (Hands-On 2.2).")


def evaluate_pr(repo, pr, default_branch=None, merged_bios=()):
    """Read-only snapshot of one pull request. `merged_bios`: bio suffixes to count as already on main."""
    ref = pr["head_ref"]
    if ref.startswith(BIO_PREFIX):
        return _evaluate_bio(repo, pr)
    if ref.startswith(README_PREFIX):
        return _evaluate_readme(repo, pr, default_branch, merged_bios)
    return {"kind": "other", "pr": pr, "github_touched": False, "ready": False}


def _evaluate_bio(repo, pr):
    suffix = branch_suffix(pr["head_ref"], BIO_PREFIX)
    names = [f["filename"] for f in repo.pr_files(pr["number"])]
    expected = f"bios/{suffix}.md" if suffix else None
    text = repo.file_text(expected, pr["head_sha"]) if expected in names else None
    others = [n for n in names if n != expected]
    e = {
        "kind": "bio", "pr": pr, "suffix": suffix, "expected": expected,
        "branch_ok": suffix is not None,
        "file_ok": expected in names,
        "only_own_file": expected in names and not others,
        "other_files": others,
        "readme_touched": "README.md" in others,
        "github_touched": any(n.startswith(".github/") for n in names),
        "problems": bio_problems(text) if expected in names else ["missing_file"],
        "commits": len(repo.pr_commits(pr["number"])),
    }
    e["ready"] = bool(e["branch_ok"] and e["file_ok"] and e["only_own_file"] and not e["problems"]
                      and e["commits"] >= 2 and not e["github_touched"])
    return e


def _evaluate_readme(repo, pr, default_branch, merged_bios):
    suffix = branch_suffix(pr["head_ref"], README_PREFIX)
    files = repo.pr_files(pr["number"])
    names = [f["filename"] for f in files]
    readme = next((f for f in files if f["filename"] == "README.md"), None)
    added, removed = changed_lines(readme.get("patch") if readme else None)
    match = ENTRY_RE.match(added[0]) if len(added) == 1 else None
    on_main = bool(suffix) and (
        suffix in merged_bios
        or repo.file_text(f"bios/{suffix}.md", default_branch or repo.default_branch()) is not None)
    e = {
        "kind": "readme", "pr": pr, "suffix": suffix,
        "branch_ok": suffix is not None,
        "only_readme": names == ["README.md"],
        "other_files": [n for n in names if n != "README.md"],
        "github_touched": any(n.startswith(".github/") for n in names),
        "added": added, "removed": removed,
        "one_line": len(added) == 1 and not removed,
        "line_format_ok": match is not None,
        "link_is_own": bool(match and suffix and match.group("suffix").lower() == suffix.lower()),
        "bio_on_main": on_main,
    }
    e["ready"] = bool(e["branch_ok"] and e["only_readme"] and e["one_line"] and e["line_format_ok"]
                      and e["link_is_own"] and e["bio_on_main"] and not e["github_touched"])
    return e


def missing_items(e):
    """What still stands between this PR and being merged by the merge bot (empty = ready)."""
    pr = e["pr"]
    if e["kind"] == "other":
        return [WRONG_BRANCH]
    out = []
    if e["github_touched"]:
        out.append("This PR changes files under `.github/`. Remove those changes; such PRs are never merged by the bot.")
    if not e["branch_ok"]:
        out.append(f"Name your branch `{'bio' if e['kind'] == 'bio' else 'readme'}/<your-purdue-username>`.")
    if e["kind"] == "bio":
        if not e["file_ok"]:
            out.append(f"Add `{e['expected'] or 'bios/<your-purdue-username>.md'}` (the file name must match your branch).")
        if e["file_ok"] and not e["only_own_file"]:
            out.append("Change only your own bio file (also changed: "
                       + ", ".join(f"`{n}`" for n in e["other_files"]) + ").")
        if e["readme_touched"]:
            out.append("Leave `README.md` alone for now; you'll add your link in Hands-On 2.2.")
        if e["file_ok"] and e["problems"]:
            out.append("Finish your bio:\n" + "\n".join("  " + l for l in explain(e["problems"]).splitlines()))
        if e["commits"] < 2:
            out.append(f"Make at least two commits (you have {e['commits']}).")
    else:
        if not e["only_readme"]:
            out.append("Change only `README.md`.")
        if not e["one_line"]:
            out.append("Add exactly one line to the Team Directory and remove nothing "
                       f"(added {len(e['added'])}, removed {len(e['removed'])}).")
        elif not e["line_format_ok"]:
            out.append("Use the exact format `- [Your Name](bios/your-purdue-username.md)`.")
        elif not e["link_is_own"]:
            out.append(f"Link to your own bio: `bios/{e['suffix']}.md` (it must match your branch name).")
        if not e["bio_on_main"]:
            out.append(f"Your bio `bios/{e['suffix']}.md` isn't on `main` yet; your link would point nowhere.")
    return out


def checklist(e):
    pr = e["pr"]
    head = [f"### ICE 2 checklist for @{pr['user']}" if e["kind"] != "readme"
            else f"### Hands-On 2.2 checklist for @{pr['user']}",
            "_This comment updates itself on every push and merge._", ""]
    if e["kind"] == "other":
        return "\n".join(head + [WRONG_BRANCH + f" (yours: `{pr['head_ref']}`)"])
    if e["kind"] == "bio":
        rows = [
            f"- {box(e['branch_ok'])} Branch is named `bio/<your-purdue-username>` (yours: `{pr['head_ref']}`)",
            f"- {box(e['file_ok'])} Adds `{e['expected'] or 'bios/<your-purdue-username>.md'}` (file name matches the branch)",
            f"- {box(e['only_own_file'])} Changes only your own bio file",
            f"- {box(not e['problems'])} Every field in your bio is filled in",
            f"- {box(e['commits'] >= 2)} At least two commits (you have {e['commits']})",
            f"- {box(pr['merged'])} Merged into `main` (your instructor does this)",
        ]
    else:
        rows = [
            f"- {box(e['branch_ok'])} Branch is named `readme/<your-purdue-username>` (yours: `{pr['head_ref']}`)",
            f"- {box(e['only_readme'])} Changes only `README.md`",
            f"- {box(e['one_line'])} Adds exactly one line and removes none",
            f"- {box(e['line_format_ok'] and e['link_is_own'])} The line looks like "
            f"`- [Your Name](bios/{e['suffix'] or 'your-purdue-username'}.md)` and links to your own bio",
            f"- {box(e['bio_on_main'])} Your bio is on `main`",
            f"- {box(pr['merged'])} Merged into `main` (your instructor does this)",
        ]
    lines = head + rows
    if e["github_touched"]:
        lines += ["", "**Not ready:** this PR changes files under `.github/`, so it will never be "
                  "merged by the bot. Remove those changes."]
    if e["kind"] == "bio":
        if e["readme_touched"]:
            lines += ["", "**Heads up:** Leave `README.md` alone for now; you'll add your link in Hands-On 2.2."]
        other = [n for n in e["other_files"] if n != "README.md" and not n.startswith(".github/")]
        if other:
            lines += ["", "**Heads up:** this PR also changes " + ", ".join(f"`{n}`" for n in other)
                      + ". Only add your own bio file, so you don't collide with classmates."]
        if e["problems"] and e["file_ok"]:
            lines += ["", "**To finish your bio:**", explain(e["problems"])]
    elif not e["bio_on_main"] and e["branch_ok"]:
        lines += ["", f"**Heads up:** `bios/{e['suffix']}.md` isn't on `main` yet. "
                  "Pull the latest `main` once your bio has been merged."]
    return "\n".join(lines)


def bio_points(e):
    """(points out of 5, [what's missing]) for one bio PR."""
    pts, notes = 0, []
    if e["branch_ok"]:
        pts += 1
    else:
        notes.append("branch not named bio/<name>")
    if e["file_ok"] and e["only_own_file"]:
        pts += 1
    else:
        notes.append("file name doesn't match the branch" if not e["file_ok"]
                     else "PR changes files other than the bio")
    if e["file_ok"] and not e["problems"]:
        pts += 2
    else:
        notes.append("bio file missing" if not e["file_ok"] else "bio fields not all filled in")
    if e["commits"] >= 2:
        pts += 1
    else:
        notes.append("fewer than two commits")
    return pts, notes


def score_student(login, evaluations):
    """Points out of 5 from the student's best bio/ PR, merged or not. readme/ PRs are ungraded."""
    best = None
    for e in evaluations:
        if e["kind"] == "bio" and e["branch_ok"] and e["pr"]["user"].lower() == login.lower():
            pts, notes = bio_points(e)
            if best is None or pts > best[0]:
                best = (pts, notes)
    if best is None:
        return 0, "no bio PR"
    return best[0], "; ".join(best[1]) or "complete"


def act(repo, pr_number=None):
    pulls = repo.list_pulls()
    targets = [p for p in pulls if p["number"] == pr_number] if pr_number else \
        [p for p in pulls if p["state"] == "open"]
    default = None
    for pr in targets:
        if pr["user"] == BOT_LOGIN:
            continue
        if pr["head_ref"].startswith(README_PREFIX) and default is None:
            default = repo.default_branch()
        upsert_marked_comment(repo, pr["number"], MARK, checklist(evaluate_pr(repo, pr, default)))


def main():
    import json
    from ghclient import Repo
    event = {}
    path = os.environ.get("GITHUB_EVENT_PATH")
    if path and os.path.exists(path):
        with open(path) as fh:
            event = json.load(fh)
    number = (event.get("pull_request") or {}).get("number")
    act(Repo(os.environ["GITHUB_REPOSITORY"]), number)


if __name__ == "__main__":
    main()
