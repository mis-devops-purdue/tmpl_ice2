"""ICE 2 merge bot. Runs only when the instructor triggers merge_ready.yml.

Processes the open, non-bot pull requests one at a time: every bio/ PR first (oldest
first), then every readme/ PR (oldest first), re-fetching main before each one.

  ready bio/ PR     -> approve, merge (merge commit)
  ready readme/ PR  -> approve, merge; if it conflicts on README.md only, merge origin/main
                       into the student's branch, keep EVERYONE's Team Directory line
                       (union, no duplicates, sorted by name), push, approve, merge
  anything else     -> left open with one comment listing exactly what is missing

Safe to run any number of times: merged PRs drop out of the open list, and a run picks up
PRs fixed since the last one. dry_run changes nothing and only reports.
"""
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ice2  # noqa: E402
from checks import has_conflict_markers, merge_directory  # noqa: E402
from ghclient import BOT_LOGIN, GitHubError, upsert_marked_comment  # noqa: E402

STATUS_MARK = "<!-- ice2_merge_status -->"
APPROVAL_BODY = "Approved by Teammate Bot (instructor-triggered merge)"
RESOLVE_MESSAGE = "Resolve README conflict (Teammate Bot)"
FOOTER = "Fix these and your instructor will run the merge again."
IDENTITY = ["-c", "user.name=Teammate Bot", "-c", "user.email=teammate-bot@users.noreply.github.com"]


class Git:
    """The few git commands the merge bot needs, run in a full clone of the repo."""

    def __init__(self, cwd, remote="origin"):
        self.cwd, self.remote = cwd, remote

    def run(self, *args, check=True):
        proc = subprocess.run(["git", *IDENTITY, *args], cwd=self.cwd, capture_output=True, text=True)
        if check and proc.returncode != 0:
            raise RuntimeError(f"git {' '.join(args)}: {(proc.stderr or proc.stdout).strip()[:300]}")
        return proc

    def fetch(self):
        self.run("fetch", self.remote, f"+refs/heads/*:refs/remotes/{self.remote}/*", "--prune")

    def clean(self):
        self.run("merge", "--abort", check=False)
        self.run("reset", "--hard", check=False)
        self.run("clean", "-fdq", check=False)

    def unmerged(self):
        return sorted(self.run("diff", "--name-only", "--diff-filter=U").stdout.split())

    def conflict_files(self, branch, base):
        """Files that conflict when origin/<base> is merged into origin/<branch> ([] = clean). Changes nothing."""
        self.clean()
        try:
            self.run("checkout", "-q", "--detach", f"{self.remote}/{branch}")
            proc = self.run("merge", "--no-commit", "--no-ff", f"{self.remote}/{base}", check=False)
            return [] if proc.returncode == 0 else (self.unmerged() or ["(merge failed)"])
        finally:
            self.clean()

    def resolve_readme(self, branch, base):
        """Merge origin/<base> into the student's branch, resolve README.md, push.

        Returns (ok, detail). On any doubt the merge is aborted and nothing is pushed.
        """
        self.clean()
        try:
            self.run("checkout", "-q", "--detach", f"{self.remote}/{branch}")
            proc = self.run("merge", "--no-ff", "--no-edit", f"{self.remote}/{base}", check=False)
            if proc.returncode == 0:
                return False, "no conflict found locally; nothing resolved"
            files = self.unmerged()
            if files != ["README.md"]:
                return False, "conflicts in " + ", ".join(files or ["(unknown)"])
            main_text = self.run("show", ":3:README.md").stdout      # theirs = origin/<base>
            branch_text = self.run("show", ":2:README.md").stdout    # ours = the student's branch
            try:
                merged = merge_directory(main_text, branch_text)
            except ValueError as exc:
                return False, str(exc)
            if has_conflict_markers(merged):
                return False, "resolved README still has conflict markers"
            with open(os.path.join(self.cwd, "README.md"), "w", encoding="utf-8", newline="") as fh:
                fh.write(merged)
            self.run("add", "README.md")
            if self.unmerged():
                return False, "conflicts remain after resolving README.md"
            self.run("commit", "-q", "-m", RESOLVE_MESSAGE)
            self.run("push", "-q", self.remote, f"HEAD:refs/heads/{branch}")
            return True, "resolved"
        except RuntimeError as exc:
            return False, str(exc)
        finally:
            self.clean()


# ---------------------------------------------------------------- helpers
def is_bot(login):
    return login == BOT_LOGIN or (login or "").endswith("[bot]")


def ordered(pulls):
    """bio/ PRs first, then readme/ PRs (each oldest first), then anything else."""
    def rank(p):
        ref = p["head_ref"]
        return (0 if ref.startswith(ice2.BIO_PREFIX) else 1 if ref.startswith(ice2.README_PREFIX) else 2,
                p.get("created_at") or "", p["number"])
    return sorted(pulls, key=rank)


def find_comment(repo, number, marker):
    return next((c for c in repo.issue_comments(number) if c["user"] == BOT_LOGIN and marker in c["body"]), None)


def say(repo, number, lines, footer=FOOTER, dry_run=False):
    if dry_run:
        return
    body = "### Not merged yet\nThe merge bot skipped this pull request:\n" + "\n".join(
        l if l.startswith(("  ", "- ")) else f"- {l}" for l in lines)
    if footer:
        body += f"\n\n{footer}"
    upsert_marked_comment(repo, number, STATUS_MARK, body)


def approve_and_merge(repo, pr):
    repo.create_review(pr["number"], "APPROVE", APPROVAL_BODY)
    repo.merge_pull(pr["number"], sha=repo.get_pull(pr["number"])["head_sha"])


def after_merge(repo, number, default, merged_bios):
    """Merges by the workflow token don't trigger teammate_bot.yml, so refresh the comments here."""
    pr = dict(repo.get_pull(number), merged=True, state="closed")
    upsert_marked_comment(repo, number, ice2.MARK, ice2.checklist(ice2.evaluate_pr(repo, pr, default, merged_bios)))
    old = find_comment(repo, number, STATUS_MARK)
    if old:
        repo.update_comment(old["id"], "### Merged\nMerged by Teammate Bot (instructor-triggered merge).\n\n" + STATUS_MARK)


# ---------------------------------------------------------------- the run
def run(repo, git, dry_run=False):
    """Returns a list of result dicts: number, branch, user, result, detail."""
    default = repo.default_branch()
    results, merged_bios, readme_merges = [], set(), 0
    open_prs = [p for p in repo.list_pulls() if p["state"] == "open" and not is_bot(p["user"])]

    for listed in ordered(open_prs):
        git.fetch()                                   # main changes after every merge
        pr = repo.get_pull(listed["number"])
        n, branch = pr["number"], pr["head_ref"]
        row = {"number": n, "branch": branch, "user": pr["user"]}
        results.append(row)
        if pr["state"] != "open":
            row.update(result="skipped", detail="no longer open")
            continue

        e = ice2.evaluate_pr(repo, pr, default, merged_bios)
        missing = ice2.missing_items(e)
        if missing:
            say(repo, n, missing, dry_run=dry_run)
            row.update(result="would skip" if dry_run else "skipped", detail="; ".join(" ".join(m.split()) for m in missing))
            continue

        kind = e["kind"]
        mergeable = repo.get_mergeable(n, tries=5)
        files = []
        if mergeable is None or mergeable is False:   # unknown or conflicting: ask git directly
            files = git.conflict_files(branch, default)
            mergeable = not files if mergeable is None else False

        if mergeable and dry_run:
            note = " (another README PR merges first, so this may need a conflict resolved)" \
                if kind == "readme" and readme_merges else ""
            row.update(result="would merge", detail=f"ready{note}")
            readme_merges += kind == "readme"
            if kind == "bio":
                merged_bios.add(e["suffix"])
            continue

        if mergeable:
            try:
                approve_and_merge(repo, pr)
            except GitHubError as exc:
                # GitHub's mergeable flag can lag right after another merge; look at git ourselves.
                files = git.conflict_files(branch, default) if kind == "readme" else []
                if files != ["README.md"]:
                    say(repo, n, [f"GitHub refused the merge: {str(exc)[:200]}"], footer="Your instructor will take a look.")
                    row.update(result="skipped", detail=f"GitHub refused the merge: {str(exc)[:120]}")
                    continue
                mergeable = False
            else:
                if kind == "bio":
                    merged_bios.add(e["suffix"])
                after_merge(repo, n, default, merged_bios)
                row.update(result="merged", detail="")
                continue

        if kind == "readme" and files == ["README.md"]:
            if dry_run:
                row.update(result="would merge", detail="would resolve the README.md conflict first")
                readme_merges += 1
                continue
            ok, detail = git.resolve_readme(branch, default)
            if not ok:
                say(repo, n, [f"Couldn't resolve the README.md conflict automatically: {detail}"],
                    footer="Your instructor will take a look.")
                row.update(result="skipped", detail=f"conflict not resolved: {detail}")
                continue
            git.fetch()
            if repo.get_mergeable(n, tries=8) is False:
                say(repo, n, ["README.md was resolved, but GitHub still reports a conflict."],
                    footer="Your instructor will take a look.")
                row.update(result="skipped", detail="still conflicting after resolving")
                continue
            try:
                approve_and_merge(repo, pr)
            except GitHubError as exc:
                say(repo, n, [f"README.md was resolved, but GitHub refused the merge: {str(exc)[:200]}"],
                    footer="Your instructor will take a look.")
                row.update(result="skipped", detail=f"GitHub refused the merge: {str(exc)[:120]}")
                continue
            after_merge(repo, n, default, merged_bios)
            row.update(result="merged after resolving conflict", detail="README.md: kept everyone's line")
            continue

        why = ("conflicts in " + ", ".join(f"`{f}`" for f in files)) if files else \
            "GitHub reports a conflict, but git finds none; try again in a minute"
        if kind == "bio":
            why += " (unexpected: every bio is a separate file)"
        say(repo, n, [f"Merge conflict: {why}."], footer="Your instructor will take a look.", dry_run=dry_run)
        row.update(result="would skip" if dry_run else "skipped", detail=why.replace("`", ""))
    return results


def summary(results, dry_run=False):
    counts = {}
    for r in results:
        counts[r["result"]] = counts.get(r["result"], 0) + 1
    lines = ["## ICE 2 merge run" + (" (DRY RUN: nothing was changed)" if dry_run else ""), ""]
    lines.append(", ".join(f"{v} {k}" for k, v in sorted(counts.items())) or "No open pull requests.")
    if results:
        lines += ["", "| PR | Branch | Author | Result | Details |", "|---|---|---|---|---|"]
        for r in results:
            detail = (r["detail"] or "").replace("|", "\\|").replace("\n", " ")
            lines.append(f"| #{r['number']} | `{r['branch']}` | {r['user']} | {r['result']} | {detail} |")
    return "\n".join(lines) + "\n"


def main():
    from ghclient import Repo
    dry_run = os.environ.get("DRY_RUN", "").strip().lower() == "true"
    results = run(Repo(os.environ["GITHUB_REPOSITORY"]), Git(os.getcwd()), dry_run)
    text = summary(results, dry_run)
    print(text)
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(text)


if __name__ == "__main__":
    main()
