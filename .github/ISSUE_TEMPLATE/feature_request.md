---
name: Feature request / Task
about: Propose a new feature, endpoint, event or improvement
title: "feat(<scope>): <short description>"
labels: ["enhancement", "needs-triage"]
assignees: []
---

## Summary

<!-- One paragraph: what is being proposed and why it is needed. -->

## Related requirement

<!-- Link to the RF/RNF this feature satisfies.
     If there is no matching requirement, explain why and get explicit approval
     before implementation (DEVELOPMENT_GUIDELINES §3.3). -->

Satisfies: <!-- RF-XX / RNF-XX -->
Open point: <!-- OP-XX if applicable -->

## Proposed solution

<!-- Describe the implementation approach:
     - Which layer(s) change (router / service / domain / repository / messaging)?
     - New endpoint or event?  New database columns?
     - Contracts that change (Pydantic model, JSON Schema, Postman collection)? -->

## Acceptance criteria

<!-- List testable conditions that must be true for this feature to be
     considered done.  Use the "Given / When / Then" format if applicable. -->

- [ ] ...
- [ ] ...

## Definition of Done checklist

<!-- This section mirrors the PR template checklist; fill it in when the
     feature is ready for review. -->

- [ ] Code passes `ruff check . && ruff format --check . && mypy app`
- [ ] Unit tests cover success, error and boundary paths
- [ ] Integration test added if infrastructure is involved
- [ ] Contracts updated (Pydantic + JSON Schema + Postman)
- [ ] Open points not resolved silently

## Alternatives considered

<!-- Why is this approach preferred over alternatives? -->

## Additional context

<!-- Sequence diagrams, sketches, references to architecture docs, etc. -->
