---
name: python-coding
description: Use this skill for any Python coding task — writing new code, debugging, refactoring, optimizing, or extending existing scripts, classes, or modules. Trigger when the user asks to write, fix, modify, or review Python code.
---

# Python Coding

This skill governs how to approach Python coding tasks: plan first, change minimally, stay concise.

## Workflow

**Before writing or modifying any code:**

1. **Read fully.** If existing code is provided, understand the full scope before touching anything.
2. **State the plan.** In 1–3 sentences, describe what will change and why. If the change is non-trivial, identify dependencies and side effects.
3. **Confirm scope.** Prefer the smallest change that satisfies the requirement. If a larger refactor seems warranted, flag it separately and do not implement it unless asked.

Only then write or modify code.

## Code Style

- **Concise over verbose.** Avoid boilerplate, docstrings, and type annotations unless asked. Exception: complex function signatures benefit from brief inline comments.
- **Idiomatic Python.** Prefer list/dict comprehensions, `zip`, `enumerate`, `itertools`, and `functools` over explicit loops where clarity is preserved.
- **NumPy/SciPy first.** For numerical work, default to vectorized array operations. Avoid Python-level loops over large arrays.
- **No unnecessary abstraction.** Don't introduce new classes, modules, or layers unless the existing structure clearly calls for it.
- **Naming.** Short, descriptive names. Single-letter variables only for loop indices and mathematical notation (e.g., `r`, `M`, `t`).

## Minimal-Change Principle

When modifying existing code:
- Change only what is necessary. Do not reformat unrelated lines.
- Do not rename variables or restructure logic unless that is the explicit goal.
- Prefer `str_replace`-style diffs in your response: show only the relevant before/after block, not the full file.
- If a fix touches more than ~20 lines, briefly explain why the scope is unavoidable.

## Debugging Protocol

1. Identify the failure mode precisely (wrong output, exception, silent error, performance).
2. State the likely root cause before proposing a fix.
3. Fix the root cause, not symptoms. Do not add defensive wrappers unless the input is genuinely untrusted.

## Performance

- Profile before optimizing. Do not micro-optimize unless a bottleneck is known.
- For large datasets: prefer `numpy` vectorization → `scipy` routines → Cython/Numba → C extensions, in that order.
- Avoid repeated memory allocation in tight loops.

## Scientific Computing Conventions

- Physical units in variable names or inline comments when ambiguous (e.g., `r_pc`, `mass_msun`).
- Constants from `astropy.constants` or `scipy.constants`; do not hard-code.
- Reproducibility: set seeds explicitly (`np.random.default_rng(seed)`), avoid global state.
- Plots: minimal, publication-ready by default — no titles unless asked, axis labels with units, tight layout.
