# ChatGPT + GitHub autonomous UI workflow

## Operating model
The owner supplies scientific requirements and accepts the final
scientific scope. ChatGPT may choose routine design details without
serial approval: layout, typography, spacing, component grouping,
icons, colors, responsive behavior and implementation refactoring
that does not alter scientific meaning.

Tooling: connected GitHub read/write, PRs, GitHub Actions, Qt UI
regression tests and recorded screenshots. Codex, Figma and paid
design services are **optional**, not required.

This is **session-driven autonomy**. GitHub Actions runs on configured
events automatically. ChatGPT cannot continue working or review new
artifacts after a chat ends unless a separate supported task/agent
is explicitly configured. Report this distinction honestly.

## Work stages
1. READ: fetch current branch SHA, source, docs, open PRs, test logs.
2. AUDIT: classify confirmed defects vs hypotheses; capture source
   evidence and screenshots where possible.
3. DESIGN: independently propose and select a coherent solution that
   fits PRODUCT_BRIEF and DESIGN_SYSTEM; preserve science truth.
4. IMPLEMENT: make small reviewable commits on a branch based on the
   correct scientific parent branch.
5. VALIDATE: execute deterministic tests and obtain GitHub Actions
   status; capture offline screenshots and logs.
6. REVIEW: inspect **actual** screenshot images when tools can retrieve
   them; record what was observed and what remains unverified.
7. REPAIR: correct failures/regressions and repeat checks.
8. HANDOFF: leave a draft PR with objective evidence, unresolved gaps
   and explicit user acceptance boundary. Never merge automatically.

## Branch discipline
For the current Survey Explorer UI work, branch from
`feature/survey-explorer-v04`, not unmerged v0.3 nor `main`.
The draft PR should initially target the Survey Explorer branch so
unrelated science work is not swept into the diff. Do not merge it,
or alter the science PRs, without explicit approval.

## Reproducibility contract
- Offline tests must not query the survey archive or remote Viewer.
- Separate `preview`, `archive candidate`, `HEAD evidence`,
  `downloaded`, `diagnostic` and `science-adopted` statuses.
- Publish screen-resolution, state, screenshot filenames, test logs
  and the exact branch commit with the CI job.
- Document rendering limitations of offscreen QtWebEngine;
  a blank placeholder must never be described as remote imagery.
- Do not claim success from a workflow that has not actually completed.
- If connector cannot expose image bytes, label screenshot visual
  review `unverified` and retain artifact links for later inspection.

## Review deliverable template
Branch and base | changes | test command / run | passed / failed /
not run | screenshot artifacts captured? | screenshots **inspected**? |
scope exclusions | remaining defects | PR link.
