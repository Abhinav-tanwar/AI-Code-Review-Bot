import hashlib
import hmac
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from reviewbot import app as server
from reviewbot.diff import parse_diff, prepare
from reviewbot.engine import SYSTEM, review_diff
from reviewbot.github import GitHub, GitHubError
from reviewbot.models import Finding, Review
from reviewbot.store import Store

PATCH = (Path(__file__).parents[1] / "examples/payment.diff").read_text()


def finding(**changes):
    data = dict(
        path="payments.py",
        line=4,
        severity="high",
        category="security",
        title="Untrusted email changes SQL syntax",
        explanation="An email containing a quote can change the query.",
        recommendation="Use a parameterized query.",
        confidence=0.96,
    )
    return Finding(**(data | changes))


def result(findings=None):
    return Review(
        summary="The change introduces input handling defects.",
        findings=findings if findings is not None else [finding()],
        limitations=[],
    )


class Provider:
    def __init__(self, review):
        self.review = review
        self.responses = self
        self.request = None

    def parse(self, **kwargs):
        self.request = kwargs
        return SimpleNamespace(output_parsed=self.review)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("ALLOWED_HOSTS", "localhost,127.0.0.1,testserver")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.delenv("API_TOKEN", raising=False)
    server.store.cache_clear()
    yield TestClient(server.app)
    server.store.cache_clear()


def test_added_lines_and_real_line_numbers():
    patches = parse_diff(PATCH)
    assert patches[0].path == "payments.py"
    assert list(patches[0].added) == [4, 5, 6, 7, 8, 9]
    assert "query =" in patches[0].added[4]


def test_multiple_hunks_and_header_like_code():
    diff = "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-old\n+++ real code\n@@ -20,0 +21,1 @@\n+other\n"
    assert parse_diff(diff)[0].added == {1: "++ real code", 21: "other"}


def test_ignored_files_and_deleted_files():
    assert parse_diff(PATCH.replace("payments.py", ".env")) == []
    assert parse_diff(PATCH.replace("+++ b/payments.py", "+++ /dev/null")) == []
    assert parse_diff(PATCH.replace("+++ b/payments.py", "+++ b/../secret.py")) == []


def test_whole_file_budget_never_truncates_hunks():
    assert prepare(PATCH, max_chars=5)[0] == []
    assert prepare(PATCH, max_files=0)[1] == ["payments.py"]


def test_model_findings_are_validated_and_deduplicated():
    provider = Provider(
        result(
            [
                finding(),
                finding(),
                finding(line=900),
                finding(confidence=0.3),
                finding(path="unknown.py"),
            ]
        )
    )
    response = review_diff(PATCH, client=provider)
    assert len(response["review"]["findings"]) == 1
    assert provider.request["store"] is False
    assert provider.request["text_format"] is Review
    assert "untrusted" in provider.request["instructions"]
    assert "never instructions" in SYSTEM


def test_context_lines_cannot_be_cited():
    assert (
        review_diff(PATCH, client=Provider(result([finding(line=1)])))["review"]["findings"] == []
    )


def test_empty_model_review_is_valid():
    assert review_diff(PATCH, client=Provider(result([])))["review"]["findings"] == []


def test_refusal_or_incomplete_output_is_not_success():
    with pytest.raises(RuntimeError):
        review_diff(PATCH, client=Provider(None))
    with pytest.raises(ValueError):
        review_diff("not a diff", client=Provider(result()))


def test_local_review_roundtrip_and_safe_history(client, monkeypatch):
    monkeypatch.setattr(
        server, "review_diff", lambda diff: review_diff(diff, client=Provider(result()))
    )
    created = client.post("/api/reviews", json={"diff": PATCH})
    assert created.status_code == 200
    key = created.json()["id"]
    assert client.get("/api/reviews/" + key).json()["review"]["findings"][0]["line"] == 4
    assert client.get("/api/reviews").json()[0]["id"] == key
    assert client.post("/api/reviews/" + key + "/publish").status_code == 422
    assert client.get("/api/reviews/missing").status_code == 404


def test_api_access_token_and_cross_origin(client, monkeypatch):
    monkeypatch.setenv("API_TOKEN", "test-token")
    assert client.get("/api/reviews").status_code == 401
    assert (
        client.get("/api/reviews", headers={"Authorization": "Bearer test-token"}).status_code
        == 200
    )
    assert (
        client.post(
            "/api/reviews", headers={"Origin": "https://untrusted.example"}, json={"diff": PATCH}
        ).status_code
        == 403
    )
    assert client.get("/api/health", headers={"Host": "evil.example"}).status_code == 400


def test_request_size_limit(client):
    assert client.post("/api/reviews", content=b"x" * 160001).status_code == 413


def signed(payload, secret="test-webhook-secret"):
    raw = json.dumps(payload).encode()
    return raw, {
        "X-GitHub-Event": "pull_request",
        "X-Hub-Signature-256": "sha256="
        + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest(),
        "Content-Type": "application/json",
    }


def test_webhook_signature_allowlist_and_head_dedup(client, monkeypatch):
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "test-webhook-secret")
    monkeypatch.setenv("ALLOWED_REPOSITORIES", "owner/repo")
    event = {
        "action": "opened",
        "repository": {"full_name": "owner/repo"},
        "pull_request": {"number": 7, "head": {"sha": "a" * 40}, "state": "open", "draft": False},
    }
    raw, headers = signed(event)
    assert client.post("/webhooks/github", content=raw, headers=headers).status_code == 202
    assert client.post("/webhooks/github", content=raw, headers=headers).status_code == 202
    assert len(server.store().jobs()) == 1
    assert (
        client.post(
            "/webhooks/github", content=raw, headers=headers | {"X-Hub-Signature-256": "sha256=bad"}
        ).status_code
        == 401
    )
    event["repository"]["full_name"] = "other/repo"
    raw, headers = signed(event)
    assert client.post("/webhooks/github", content=raw, headers=headers).status_code == 403


def test_draft_webhook_does_not_enqueue(client, monkeypatch):
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "test-webhook-secret")
    monkeypatch.setenv("ALLOWED_REPOSITORIES", "owner/repo")
    raw, headers = signed(
        {
            "action": "opened",
            "repository": {"full_name": "owner/repo"},
            "pull_request": {
                "number": 1,
                "head": {"sha": "a" * 40},
                "state": "open",
                "draft": True,
            },
        }
    )
    assert (
        client.post("/webhooks/github", content=raw, headers=headers).json()["status"] == "ignored"
    )
    assert not server.store().jobs()


def test_store_recovers_interrupted_jobs(tmp_path):
    store = Store(tmp_path / "reviews.db")
    key = store.enqueue("owner/repo", 1, "a" * 40)
    assert store.claim()["id"] == key
    assert store.claim() is None
    reopened = Store(tmp_path / "reviews.db")
    assert reopened.claim()["id"] == key
    reopened.finish(key, "completed", "result-id")
    assert reopened.jobs()[0]["review_id"] == "result-id"


def github_client(handler):
    return GitHub(
        client=httpx.Client(
            base_url="https://api.github.com", transport=httpx.MockTransport(handler)
        )
    )


SOURCE = {"repository": "owner/repo", "pull_number": 1, "head_sha": "a" * 40}


def test_github_publication_binds_commit_and_validated_line(monkeypatch):
    monkeypatch.setenv("ALLOWED_REPOSITORIES", "owner/repo")
    posted = []

    def handler(request):
        if request.method == "POST":
            posted.append(json.loads(request.content))
            return httpx.Response(
                200, json={"html_url": "https://github.com/owner/repo/pull/1#review"}
            )
        if request.url.path.endswith("/reviews"):
            return httpx.Response(200, json=[])
        return httpx.Response(200, json={"state": "open", "head": {"sha": "a" * 40}})

    output = github_client(handler).publish(SOURCE, {"review": result().model_dump()})
    assert output["duplicate"] is False
    assert posted[0]["event"] == "COMMENT" and posted[0]["commit_id"] == "a" * 40
    assert posted[0]["comments"][0]["line"] == 4 and posted[0]["comments"][0]["side"] == "RIGHT"


def test_stale_review_is_never_posted(monkeypatch):
    monkeypatch.setenv("ALLOWED_REPOSITORIES", "owner/repo")
    github = github_client(
        lambda request: httpx.Response(200, json={"state": "open", "head": {"sha": "b" * 40}})
    )
    with pytest.raises(ValueError, match="changed"):
        github.publish(SOURCE, {"review": result().model_dump()})


def test_existing_review_is_reused(monkeypatch):
    monkeypatch.setenv("ALLOWED_REPOSITORIES", "owner/repo")

    def handler(request):
        assert request.method == "GET"
        data = (
            [
                {
                    "body": "<!-- ai-code-review-bot:" + ("a" * 40) + ":v1 -->",
                    "commit_id": "a" * 40,
                    "html_url": "existing",
                }
            ]
            if request.url.path.endswith("/reviews")
            else {"state": "open", "head": {"sha": "a" * 40}}
        )
        return httpx.Response(200, json=data)

    assert github_client(handler).publish(SOURCE, {"review": result().model_dump()})["duplicate"]


def test_unapproved_repository_is_not_contacted(monkeypatch):
    monkeypatch.setenv("ALLOWED_REPOSITORIES", "")

    def fail(request):
        raise AssertionError("must not contact GitHub")

    with pytest.raises(ValueError):
        github_client(fail).publish(SOURCE, {"review": result().model_dump()})


def test_pull_fetch_includes_missing_patch_coverage():
    def handler(request):
        if request.url.path.endswith("/files"):
            return httpx.Response(
                200,
                json=[
                    {"filename": "a.py", "patch": "@@ -0,0 +1 @@\n+x", "status": "added"},
                    {"filename": "asset.png", "status": "added"},
                ],
            )
        return httpx.Response(
            200, json={"state": "open", "head": {"sha": "a" * 40}, "changed_files": 2}
        )

    source = github_client(handler).pull("owner/repo", 1)
    assert source["omitted"] == ["asset.png"]
    assert parse_diff(source["diff"])[0].added == {1: "x"}


def test_github_failure_does_not_expose_request_details():
    with pytest.raises(GitHubError, match="403"):
        github_client(lambda request: httpx.Response(403, json={})).pull("owner/repo", 1)


def test_queued_stale_head_is_superseded(client, monkeypatch):
    class FakeGitHub:
        def pull(self, *args):
            return {"head_sha": "b" * 40}

    monkeypatch.setattr(server, "GitHub", FakeGitHub)
    server.store().enqueue("owner/repo", 1, "a" * 40)
    server.process_job(server.store().claim())
    assert server.store().jobs()[0]["status"] == "superseded"


def test_failed_jobs_have_bounded_explicit_retries(tmp_path):
    store = Store(tmp_path / "jobs.db")
    key = store.enqueue("owner/repo", 1, "a" * 40)
    assert "/" not in key
    for attempt in range(3):
        assert store.claim()["id"] == key
        store.finish(key, "failed", error="ProviderError")
        assert store.retry(key) is (attempt < 2)
    assert store.jobs()[0]["attempts"] == 3
