---
name: Bug report
about: Report a reproducible bug in workflows, scripts, docs, or automation outputs
title: '[Bug] '
labels: bug
assignees: ''

---

## Summary

Describe what is broken in one short paragraph.

## Impact

- Scope: Which part is affected? (workflow manager, worker wrapper, script, README rendering, etc.)
- Severity: low / medium / high
- User-visible effect: what fails or degrades?

## Reproduction Steps

1. 
2. 
3. 
4. 

## Expected Behavior

Describe what should happen.

## Actual Behavior

Describe what actually happened.

## Logs and Evidence

Provide as much as possible:

- Error message and stack trace
- GitHub Actions run URL
- Related task name(s)
- Relevant file snapshots, for example:
	- .github/manager/state/state.json
	- .github/manager/state/event-log.json
	- .github/manager/state/dead-letters.json

## Environment

- OS:
- Python version:
- Trigger mode: workflow_dispatch / schedule / local
- Branch or commit SHA:

## Regression Check

- [ ] This worked before and now fails
- [ ] I can provide the last known good commit
- [ ] I checked for duplicate existing issues

## Possible Root Cause (Optional)

If you already have a hypothesis, describe it briefly.

## Proposed Fix (Optional)

If you have a fix direction, describe it.

## Validation Checklist

- [ ] Repro is deterministic
- [ ] Expected behavior is clearly defined
- [ ] Included enough logs/artifacts to debug

## Additional Context

Anything else that helps triage.
