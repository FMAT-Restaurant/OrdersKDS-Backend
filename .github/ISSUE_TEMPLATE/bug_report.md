---
name: Bug report
about: Report a reproducible defect in the Orders & KDS backend
title: "fix(<scope>): <short description>"
labels: ["bug", "needs-triage"]
assignees: []
---

## Describe the bug

<!-- A clear and concise description of what the bug is.
     State the actual behaviour and how it differs from the expected one. -->

**Actual behaviour:**

**Expected behaviour:**

## Related requirement

<!-- Which RF/RNF is violated?  Check the catalogue in docs/VyV_OrdenesKDS.md §3. -->

Violates: <!-- RF-XX / RNF-XX -->

## Steps to reproduce

1. <!-- First step -->
2. <!-- Second step -->
3. <!-- ... -->

## Environment

- **Branch:** `develop` / `main` / `<branch-name>`
- **Commit SHA:** <!-- git rev-parse --short HEAD -->
- **Python version:** 3.12
- **Docker / Compose version:** <!-- docker --version && docker compose version -->

## Logs / error output

```
<!-- Paste relevant log lines here (scrub any credentials before pasting). -->
```

## Regression test

<!-- Describe (or paste) the test that would catch this bug. -->
<!-- Every bug fix must ship with a regression test (CONTRIBUTING §8). -->

## Additional context

<!-- Screenshots, sequence diagrams, or any other helpful information. -->
