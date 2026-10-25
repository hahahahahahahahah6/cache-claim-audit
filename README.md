# cache-claim-audit

Offline-audit of documented cache-reuse promises against Python source.

Version 0.1.0. Python 3.9 or later; standard library only at runtime.
This tool compares documentation promises with static source evidence. It
executes no audited code, imports no audited modules, makes no network requests,
and never opens `.env` contents. Symlink files and directories are skipped.

## Usage

Install a locally built distribution and run either entry point:

```sh
python3 -m pip install dist/cache_claim_audit-0.1.0-py3-none-any.whl
cache-claim-audit /path/to/project
python3 -m cache_claim_audit /path/to/project --json
cache-claim-audit /path/to/project --verbose
cache-claim-audit /path/to/project --ignore 'vendor/*' --ignore 'generated/*'
cache-claim-audit /path/to/project --fail-on HIGH
cache-claim-audit --version
```

The positional directory defaults to the current directory. `--json` emits a
single JSON object with `findings`, `verified_claims`, `claims`,
`production_evidence`, `test_only_evidence`, `production_source_count`, `warnings`,
and `version`. Findings contain `rule`, `severity`, `family`, `claims`, and
`evidence`. Claims record relative path, line, and sentence text; evidence
records relative path, line, and kind (or just `kind: no-source`). `--verbose`
adds scan details in text mode; JSON always contains these details.

Exit codes: **0** PASS, **2** FAIL, **1** usage/path error. Default
`--fail-on MEDIUM` fails for any finding. `--fail-on HIGH` still prints MEDIUM
findings but returns 0 for a MEDIUM-only report. `--ignore` is repeatable and
uses case-sensitive glob matching against slash-separated relative paths and
their directory ancestors; quote globs to prevent shell expansion.

## Claim extraction

Only root `README.md` and `docs/**/*.md` supply claims. Backtick and tilde fenced
blocks are excluded; inline code spans count as prose. Matching ignores case
and normalizes whitespace in sentences, including wrapped lines. These are the
complete phrase tables; no optional middle words or extra hyphen variants are
accepted:

```text
cached family:
  cached (whole word), caching (whole word)
  uses a cache, use a cache, with a cache, with caching
  result cache, response cache

no-cache family:
  no caching, no cache, does not cache, doesn't cache, never caches
  not cached, uncached, without caching, without a cache, no memoization

The spec also explicitly identifies "does not use a cache" as a no-cache claim.
```

Negation asymmetry is intentional: a sentence containing `no`, `not`, `never`,
`without`, `doesn't`, `don't`, or `isn't` suppresses the positive family only.
The reverse family uses negation as its claim phrasing, so it is still counted.
This differs from R26/R27, which suppressed negation-phrased reverse claims.
A bare `cache` noun is not a claim: path names, settings, and instructions such
as `clear the cache directory` do not promise reuse. Multiple phrases collapse
to one finding per family, while retaining all claim locations.

## Rules

| Rule | Severity | Condition |
| --- | --- | --- |
| `documented-cached-claim-false` | HIGH | Positive claim; no production or test evidence |
| `documented-cached-claim-false-test-only` | MEDIUM | Positive claim; evidence only in tests/fixtures |
| `documented-no-cache-claim-false` | HIGH | Reverse claim; production evidence exists |
| `documented-no-cache-claim-false-test-only` | MEDIUM | Reverse claim; evidence only in tests/fixtures |

No production Python source overrides these conditions: each documented family
gets one HIGH finding with `no-source` evidence. Contradictory families are
audited independently; both can fail when there is no source. Otherwise,
positive claims with production evidence are verified, and reverse claims
with no evidence anywhere are verified. No claim means PASS even if evidence
exists. All relevant evidence sites are listed in each finding.

## Named evidence table

| Static site | Counts when |
| --- | --- |
| Decorator or call | Final function name is `cache` or `lru_cache`, bare or qualified, with or without decorator arguments |
| Import | Root module is `cachetools`, `diskcache`, `requests_cache`, or `joblib`; aliases and unused imports count |
| Subscript store | Assignment target receiver name contains `cache`, case-insensitively; e.g. `self._cache[key] = value` |
| Store method call | Attribute is `set`, `put`, or `__setitem__` and receiver name contains `cache` |
| Memoization call | Final function name is `memoize`, or the past-tense form of `cache`, with any qualifier |
| Disk store call | `shelve.open`, `dbm.open`, or `sqlite3.connect` with a literal filename containing `cache` |

Evidence is deduplicated by relative file, line, and kind. Production excludes
`tests/`, `fixtures/`, `test_*.py`, `*_test.py`, `docs/`, and `examples/`.
Test and fixture evidence is collected separately, while docs and examples
are excluded entirely as source. Symlink files/directories are skipped.
Repository metadata, virtual environments, build output, and bytecode folders
are also skipped.

## Differentiation

| Neighbour | Its question; this tool's distinction |
| --- | --- |
| retry-claim-audit | Attempt counts; never reuse of results |
| config-claim-audit | Documented default values; never presence of a reuse mechanism |
| streaming-claim-audit | How much is read at once; never reuse of results |
| determinism-claim-audit | Run-to-run stability; never reuse of results |
| bandit / ruff | Code issues; never documentation promises |
| functools docs / cachetools / diskcache docs | Helper usage; never docs-versus-code truth |

## Honest limitations

This is static, project-wide evidence, not proof of runtime hits, correct
invalidation, scope, capacity, expiry, or reuse by the documented operation.
Exact phrase matching misses paraphrases, other languages, and non-Markdown
docs. Sentence splitting is punctuation-based and does not interpret natural
language, abbreviations, Markdown tables, or scoped promises semantically.

Unused imports count intentionally: the four named library imports are an
over-approximation. Dead code counts too, including primitives in uncalled
functions. Bare `import functools` does not count. Only the stdlib-plus-named
evidence table above is supported; other third-party libraries and dynamic
alias resolution are out of scope. Disk store calls require the listed exact
qualified names and a literal filename, not computed paths.

Dict stores require a write site: a bare read such as `cache[key]` or
`cache.get(key)` alone does not count, even if the variable name suggests reuse.
A store that is only cleared, including `cache_clear()` without a decorator or
write, does not count. HTTP Cache-Control headers and ETag handling alone do
not count: they are protocol behaviour rather than local reuse evidence.
Subscript stores and name-based heuristics may also match unrelated objects.

Unreadable, non-UTF-8, or syntactically invalid source is skipped with a warning
in JSON/verbose output; its production file still counts as source. Review
warnings before relying on verified claims. No dependency inspection or source
execution occurs. `.env` contents are never read; a marker-only fixture and an
open guard test pin this behaviour.

## Build and test

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -v
python3 -m build
```

The standard build uses isolation. If build dependencies are unavailable
offline, with setuptools and wheel already installed, use:

```sh
python3 -m build --no-isolation
```

Artifacts are a wheel and sdist in `dist/`. `MANIFEST.in` includes tests and all
fixtures in the sdist. Each of the good fixtures exits 0; each bad fixture exits
2 with one HIGH finding; the test-only fixture has one MEDIUM finding and exits
0 under `--fail-on HIGH`.

```sh
PYTHONPATH=src python3 -m cache_claim_audit tests/fixtures/good/cached --json
PYTHONPATH=src python3 -m cache_claim_audit tests/fixtures/good/nocache --json
PYTHONPATH=src python3 -m cache_claim_audit tests/fixtures/bad/cached --json
PYTHONPATH=src python3 -m cache_claim_audit tests/fixtures/bad/nocache --json
PYTHONPATH=src python3 -m cache_claim_audit tests/fixtures/testonly --fail-on HIGH --json
```

MIT licensed. This project is intended for local use.
