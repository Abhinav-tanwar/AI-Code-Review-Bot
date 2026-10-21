# Validation · October 20, 2026

- 22 automated tests pass using deterministic provider and GitHub API test doubles.
- Ruff passes for application modules and tests.
- JavaScript syntax validation passes.
- A live GPT-4.1 request through the dashboard returned two findings for the synthetic payment diff: SQL injection and division by zero. Both findings cited valid added lines. No user repository code was used for this test.
- Browser verification covers loading the example, generating a live review, viewing severity and line references, and review history.
- GitHub publication payloads, stale-head rejection, signature verification, and duplicate markers are verified with mocked GitHub responses. A separate live GitHub Actions smoke check posted one advisory inline finding on [PR #1](https://github.com/Abhinav-tanwar/AI-Code-Review-Bot/pull/1#discussion_r4331760004), bound to added line 6 of the synthetic fixture. The PR was closed without merging. Automatic review on other repositories requires their own Actions configuration.

- Docker image build and smoke tests passed for HTML delivery, health readiness, unauthenticated rejection, and authenticated history access. The temporary test container was stopped.
