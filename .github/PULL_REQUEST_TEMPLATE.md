## Summary

<!-- One or two sentences: what does this PR do and why?
     Start with an imperative verb: "Add ...", "Fix ...", "Refactor ..." -->

## Related requirements

<!-- List every RF/RNF this PR satisfies, e.g.: RF-24, RF-25, RNF-01.
     If the change cannot be linked to a requirement, explain why and get
     explicit approval in the comments before merging. -->

Satisfies: <!-- RF-XX, RNF-XX -->
Closes / Refs: <!-- #issue-number (if applicable) -->

## Type of change

<!-- Check the box that applies (one per PR; if two apply, split the PR). -->

- [ ] `feat` — new feature or endpoint
- [ ] `fix` — bug fix (include a regression test)
- [ ] `refactor` — code restructuring without behaviour change
- [ ] `test` — tests only (no production code change)
- [ ] `docs` — documentation only
- [ ] `build` — dependencies, Dockerfile, requirements
- [ ] `ci` — CI/CD configuration (workflows, sonar, thresholds)
- [ ] `chore` — maintenance that does not touch `app/` or `tests/`

## Description

<!-- Explain the *why*, not the *what* (the diff explains the what).
     Mention open points (OP-XX) you resolved and the assumption you made.
     Link the corresponding frontend PR if contracts changed (CONTRIBUTING §9). -->

## Checklist

<!-- CONTRIBUTING §8 — run these locally before opening the PR. -->

### Code quality
- [ ] `ruff check . && ruff format --check . && mypy app` passes with no errors
- [ ] All code, comments, logs, and test names are in English (GUIDELINES §2)
- [ ] No `Any` (Python) or `any` (TypeScript) introduced
- [ ] Function length ≤ 40 lines; file length ≤ 400 lines

### Business rules
- [ ] Every state transition goes through `ensure_transition_allowed`; no direct `order.status = …`
- [ ] Every RabbitMQ publication goes through the `outbox` table in the same transaction
- [ ] Every consumer verifies idempotency before touching the database
- [ ] `user_id` / `role` come from the JWT, never from the request body
- [ ] `VOIDED` orders never emit a charge event; `WASTED` orders do

### Tests
- [ ] `pytest tests/unit` passes locally
- [ ] `pytest tests/integration` passes locally (requires Docker)
- [ ] New behaviour is covered by at least one unit test (success path + error path)
- [ ] Bug fixes include a regression test (red → green)

### Security
- [ ] No secrets, tokens, or internal paths in code, tests, logs, or Docker images
- [ ] Role-based authorization is enforced in the router **and** double-checked in the service
- [ ] All client-facing inputs are validated with Pydantic at the router boundary

### Contracts (if endpoints or events changed)
- [ ] Pydantic models updated
- [ ] JSON Schema in `contracts/events/` updated
- [ ] Postman collection updated (`tests/api/postman/`)
- [ ] `app/core/error_codes.py` updated (and mirrored in the frontend if applicable)
- [ ] Frontend PR opened and linked (same branch name if possible — CONTRIBUTING §9)

### Open points
- [ ] No open point (OP-01 … OP-07) was resolved silently — any assumption is
      stated explicitly in the description above

## Screenshots / recordings (if UI-facing)

<!-- Drop a screenshot or a short screen recording if the change affects a
     user-facing screen.  Not required for backend-only changes. -->

## Deployment notes

<!-- List any manual steps needed after merging:
     - New environment variables to add to GitHub Secrets
     - New Alembic migration to apply: `alembic upgrade head`
     - New RabbitMQ exchanges or queues to declare
     - Leave empty if none. -->
