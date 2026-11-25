# Naming

Why the action carries the names it does.

## The action is called Pull Request Policy Gate, while its identifiers stay pr-policy-gate

**Id:** c8d7b6ff-a80f-4bed-be2d-d62d3cbb0ca5
**Type:** decision
**Type:** constraint
**Status:** active
**Evidence:** confirmed
**Source:** conversation 2026-11-25

`action.yml`, the report title on every pull request, `README.md`, `AGENTS.md` and the schema title say "Pull Request Policy Gate", while the repository, the configuration file `.github/pr-policy-gate.yml`, the comment marker and the commit status keep `pr-policy-gate`.

**Reason:** GitHub Marketplace refuses an action name that matches an existing action, and "PR Policy Gate" matches `rodny90/pr-policy`, listed since October 2026 as "pr-policy gate" under the slug `pr-policy-gate`.

**Rejected alternative:** Renaming only `action.yml`'s `name`, which lists the action under a name its reports and `README.md` do not use.

**Rejected alternative:** Keeping "PR Policy Gate" and leaving the action off the Marketplace.
