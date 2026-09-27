# Frontend regression tests

Requires Node.js 22 or newer and npm. From the repository root:

```bash
npm --prefix tests/frontend ci --ignore-scripts
npm --prefix tests/frontend test
```

Dependencies are pinned in `package-lock.json` and are only needed for tests;
Kapowarr's runtime and Docker image do not require Node.js.

The suite uses Node's test runner and jsdom to load the real Pack Inbox and Activity queue templates
and JavaScript. API responses, confirmation dialogs and polling timers are
stubbed; tests do not start Kapowarr, contact providers, or alter library files.
Unexpected API routes fail the test.

Coverage includes visible-file selection and import requests, collapsed finished
jobs and release history, cleanup confirmation tokens, search pagination,
subscription controls, supported download choices, and review-only torrent discovery in Activity. Add
scenarios to `pack-inbox.test.cjs` or `queue-discovery.test.cjs` when changing
these interactions.

These are DOM interaction checks. They do not render Jinja, verify backend
behavior, or check browser layout and appearance. Continue testing those changes
with Python tests and an isolated running instance as appropriate.

The Frontend Tests GitHub Actions workflow runs this command for relevant pushes
and pull requests.
