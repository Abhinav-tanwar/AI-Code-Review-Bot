# AI Code Review Bot

![AI Code Review Bot — charcoal and amber project header](assets/readme-header.svg)

Turn pull request diffs into focused, severity-ranked findings. The review engine checks each finding against an actual added line before it can be published as an advisory GitHub comment.

**Python / FastAPI · OpenAI · GitHub REST API · GitHub Actions · SQLite · Docker**

![Code review dashboard](screenshots/workspace.png)
*Actual local dashboard; live review evidence is linked below.*

## Verified results

| Check | Observed result |
| --- | --- |
| Automated validation | **22 tests passed**, using provider and GitHub API doubles |
| Live model check | **2 seeded defects** surfaced: SQL injection and division by zero |
| GitHub integration | Actions posted **1 inline advisory finding** on a separate smoke-test PR |

[Validation record](VALIDATION.md) · [Live inline review](https://github.com/Abhinav-tanwar/AI-Code-Review-Bot/pull/1#discussion_r4331760004). These synthetic examples verify the integration; they do not measure defect-detection accuracy.

## Run locally

Requires **Python 3.12+** and an OpenAI API key with API credit.

```sh
git clone https://github.com/Abhinav-tanwar/AI-Code-Review-Bot.git
cd AI-Code-Review-Bot
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
cp .env.example .env
```

Set `OPENAI_API_KEY` privately in `.env`, then:

```sh
.venv/bin/python -m uvicorn reviewbot.app:app --host 127.0.0.1 --port 8020
```

Open **http://127.0.0.1:8020**; API docs: **/docs**. Choose **Load sample diff → Run code review**. This makes a billable model request. Inspect the findings and line references before publishing anything.

## Enable PR reviews

1. Add the repository Actions secret `OPENAI_API_KEY`.
2. Set the Actions variable `AI_REVIEW_ENABLED` to `true`.
3. Open or update a non-draft PR from a branch in the same repository.

The workflow uses the short-lived `GITHUB_TOKEN`. Fork PRs require a maintainer-triggered manual workflow. [Workflow, CLI and webhook setup](docs/guide.md).

## How it works

`Diff / PR → parse changed lines → structured GPT review → validate locations → preview → advisory comment`

- Filter invalid locations, low-confidence findings and duplicates.
- Recheck the PR head before publication and bind comments to the reviewed commit.
- Retain review history and process signed webhook jobs through a persistent queue.

## Verify

```sh
.venv/bin/python -m pytest -q
.venv/bin/ruff check reviewbot tests
```

## Scope and data

Reviews are advisory `COMMENT` reviews; the bot does not approve or merge PRs. Reviewed code is never executed. Diff text is sent to OpenAI. Model confidence is not calibrated, and a clean review does not establish correctness or security. Remote service access requires the authentication and hosting configuration in the guide. Keep credentials and runtime storage out of Git.

[Architecture, deployment, configuration, limits and API guide](docs/guide.md)

<sub>macOS/Linux commands shown. On Windows, use `py -3.12 -m venv .venv` and replace `.venv/bin/python` with `.venv\Scripts\python.exe`.</sub>
