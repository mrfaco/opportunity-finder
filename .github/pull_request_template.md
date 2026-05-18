<!--
Keep the description tight. The bullets below are what a reviewer (human or
AI) will look at first. Delete sections that genuinely don't apply rather
than leaving them blank.
-->

## What

<!-- One paragraph: what does this PR change and why now? -->

## Test plan

<!-- How did you verify this works? Cite specific tests, manual steps, or
admin URLs you exercised. "CI is green" is not a test plan. -->

- [ ] New behavior has a test (or this PR adds no new behavior)
- [ ] Bug fixes have a regression test that fails on the pre-fix code

## Discipline checklist (AGENTS.md)

- [ ] No exception swallowed, logged-and-continued, or replaced with a
      fallback value. New `# allow: suppress-exception` annotations are
      called out below if any.
- [ ] If models changed, migrations are in the same commit
- [ ] If prompts changed, the new hashes are intentional and the bodies
      still describe the actual contract
- [ ] If a tool's input/output schema changed, `schema_version` bumped

## Anything reviewers should know

<!-- Surprises, follow-ups, deferred work, links to context. -->
