import os
import re

import httpx


class GitHubError(RuntimeError):
    pass


def escape(text):
    # Neutralize mentions and HTML in generated comments.
    return text.replace("@", "@\u200b").replace("<", "&lt;").replace(">", "&gt;")


class GitHub:
    def __init__(self, token=None, client=None):
        token = token if token is not None else os.getenv("GITHUB_TOKEN", "")
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "AI-Code-Review-Bot",
        }
        if token:
            headers["Authorization"] = "Bearer " + token
        self.client = client or httpx.Client(
            base_url="https://api.github.com", headers=headers, timeout=30, follow_redirects=False
        )

    def call(self, method, path, **kwargs):
        response = self.client.request(method, path, **kwargs)
        if response.status_code >= 300:
            raise GitHubError(
                f"GitHub returned HTTP {response.status_code}. Check repository access, token permissions, or rate limits."
            )
        return response.json()

    def pull(self, repo, number):
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
            raise ValueError("Invalid repository name.")
        pr = self.call("GET", f"/repos/{repo}/pulls/{number}")
        if pr["state"] != "open":
            raise ValueError("This pull request is not open.")
        if pr.get("draft"):
            raise ValueError("Draft pull requests are not reviewed.")
        if pr.get("changed_files", 0) > 100:
            raise ValueError("Split pull requests larger than 100 files before reviewing.")
        files = []
        for page in range(1, 3):
            batch = self.call(
                "GET", f"/repos/{repo}/pulls/{number}/files", params={"per_page": 100, "page": page}
            )
            files.extend(batch)
            if len(batch) < 100:
                break
        if len(files) > 100:
            raise ValueError("Pull request exceeds the 100-file limit.")
        patches = []
        omitted = []
        for f in files:
            if not f.get("patch") or f.get("status") == "removed":
                omitted.append(f["filename"])
                continue
            patches.append(
                f"diff --git a/{f['filename']} b/{f['filename']}\n--- a/{f['filename']}\n+++ b/{f['filename']}\n{f['patch']}\n"
            )
        current = self.call("GET", f"/repos/{repo}/pulls/{number}")
        if current["head"]["sha"] != pr["head"]["sha"]:
            raise ValueError("Pull request changed while its files were fetched. Retry.")
        return {
            "repository": repo,
            "pull_number": number,
            "head_sha": pr["head"]["sha"],
            "diff": "\n".join(patches),
            "omitted": omitted,
        }

    def publish(self, source, result):
        repo = source["repository"]
        number = source["pull_number"]
        head = source["head_sha"]
        allowed = {
            x.strip().lower() for x in os.getenv("ALLOWED_REPOSITORIES", "").split(",") if x.strip()
        }
        if repo.lower() not in allowed:
            raise ValueError("Repository is not in ALLOWED_REPOSITORIES.")
        current = self.call("GET", f"/repos/{repo}/pulls/{number}")
        if current["state"] != "open" or current["head"]["sha"] != head or current.get("draft"):
            raise ValueError(
                "The pull request changed, closed, or became a draft. Run a fresh review."
            )
        marker = f"<!-- ai-code-review-bot:{head}:v1 -->"
        # Marker deduplication survives a process crash after GitHub accepts a review.
        for page in range(1, 101):
            existing = self.call(
                "GET",
                f"/repos/{repo}/pulls/{number}/reviews",
                params={"per_page": 100, "page": page},
            )
            match = next(
                (
                    r
                    for r in existing
                    if marker in (r.get("body") or "") and r.get("commit_id") == head
                ),
                None,
            )
            if match:
                return {"url": match["html_url"], "duplicate": True}
            if len(existing) < 100:
                break
        else:
            raise ValueError("Review history exceeds the deduplication scan limit.")
        review = result["review"]
        findings = review["findings"]
        body = f"## AI Code Review Bot\n\n{escape(review['summary'])}\n\n{len(findings)} validated finding(s). This is an automated advisory review, not an approval.\n\n"
        if review["limitations"]:
            body += (
                "### Scope and limitations\n"
                + "\n".join("- " + escape(x) for x in review["limitations"])
                + "\n\n"
            )
        body += marker
        comments = [
            {
                "path": f["path"],
                "line": f["line"],
                "side": "RIGHT",
                "body": f"**[{f['severity'].upper()}] {escape(f['title'])}**\n\n{escape(f['explanation'])}\n\n**Recommendation:** {escape(f['recommendation'])}",
            }
            for f in findings
        ]
        latest = self.call("GET", f"/repos/{repo}/pulls/{number}")
        if latest["state"] != "open" or latest["head"]["sha"] != head or latest.get("draft"):
            raise ValueError(
                "Pull request changed immediately before publication. Run a fresh review."
            )
        posted = self.call(
            "POST",
            f"/repos/{repo}/pulls/{number}/reviews",
            json={"commit_id": head, "event": "COMMENT", "body": body, "comments": comments},
        )
        return {"url": posted["html_url"], "duplicate": False}
