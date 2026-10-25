"""Static docs and AST analysis. Audited modules are never imported."""

import ast
import fnmatch
import re
from pathlib import Path

CACHED = re.compile(
    r"\b(?:cached|caching|uses a cache|use a cache|with a cache|with caching|"
    r"result cache|response cache)\b", re.I
)
NO_CACHE = re.compile(
    r"\b(?:no caching|no cache|does not cache|doesn't cache|never caches|"
    r"not cached|uncached|without caching|without a cache|no memoization|"
    r"does not use a cache)\b", re.I
)
NEGATION = re.compile(r"\b(?:no|not|never|without|doesn't|don't|isn't)\b", re.I)
LIBRARIES = {"cachetools", "diskcache", "requests_cache", "joblib"}
EXCLUDED = {"docs", "examples"}
TEST_DIRS = {"tests", "fixtures"}


def ignored(path, patterns):
    """Match portable relative paths, including ignored directory ancestors."""
    parts = path.split("/")
    candidates = ["/".join(parts[:i]) for i in range(1, len(parts) + 1)]
    return any(fnmatch.fnmatchcase(candidate, pattern.rstrip("/"))
               for candidate in candidates for pattern in patterns)


def files(root, patterns):
    """Walk without following symlink files or directories; never open .env."""
    import os
    for directory, dirs, names in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d not in {".git", ".venv", "venv", "__pycache__", "build", "dist"}
                         and not (Path(directory) / d).is_symlink()
                         and not ignored((Path(directory) / d).relative_to(root).as_posix(), patterns))
        for name in sorted(names):
            path = Path(directory) / name
            rel = path.relative_to(root).as_posix()
            if not path.is_symlink() and not ignored(rel, patterns):
                yield path, rel


def claims_from_text(text, path):
    """Extract sentence claims outside fenced code; retain original line numbers."""
    lines = []
    fence = None
    for number, line in enumerate(text.splitlines(), 1):
        match = re.match(r"^\s{0,3}(`{3,}|~{3,})", line)
        if fence:
            if match and match[1][0] == fence[0] and len(match[1]) >= len(fence) and not line[match.end():].strip():
                fence = None
            lines.append((number, ""))
        elif match:
            fence = match[1]
            lines.append((number, ""))
        else:
            lines.append((number, line))
    # Blank lines delimit paragraphs, while wrapped prose remains one sentence.
    clean = "\n".join(line for _, line in lines)
    found = []
    for sentence in re.finditer(r"[^.!?\n]+(?:\n(?!\s*\n)[^.!?\n]+)*[.!?]?", clean):
        raw = sentence.group()
        prose = " ".join(re.sub(r"`+", "", raw).split())
        families = []
        if NO_CACHE.search(prose):
            families.append("no-cache")
        if CACHED.search(prose) and not NEGATION.search(prose):
            families.append("cached")
        for family in families:
            found.append({"family": family, "path": path,
                          "line": clean.count("\n", 0, sentence.start()) + 1,
                          "text": " ".join(raw.split())})
    return found


def name(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = name(node.value)
        return parent + "." + node.attr if parent else node.attr
    return ""


def evidence_from_text(text, path):
    tree = ast.parse(text, filename=path)
    evidence = {}

    def add(node, kind):
        key = (path, node.lineno, kind)
        evidence[key] = {"path": path, "line": node.lineno, "kind": kind}

    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            modules = [alias.name for alias in node.names] if isinstance(node, ast.Import) else [node.module or ""]
            if any(module.split(".")[0] in LIBRARIES for module in modules):
                add(node, "cache-library-import")
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            for decorator in node.decorator_list:
                if not isinstance(decorator, ast.Call) and name(decorator).split(".")[-1] in {"cache", "lru_cache"}:
                    add(decorator, "cache-primitive")
        if isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Store) and "cache" in name(node.value).lower():
            add(node, "cache-store-write")
        if isinstance(node, ast.Call):
            qualified = name(node.func)
            leaf = qualified.split(".")[-1]
            if leaf in {"cache", "lru_cache"}:
                add(node, "cache-primitive")
            if leaf in {"memoize", "cached"}:
                add(node, "memoize-call")
            if isinstance(node.func, ast.Attribute) and leaf in {"set", "put", "__setitem__"} and "cache" in name(node.func.value).lower():
                add(node, "cache-store-write")
            if qualified in {"shelve.open", "dbm.open", "sqlite3.connect"}:
                args = list(node.args[:1]) + [kw.value for kw in node.keywords
                                             if kw.arg in {"filename", "database", "file"}]
                if any(isinstance(arg, ast.Constant) and isinstance(arg.value, str) and "cache" in arg.value.lower() for arg in args):
                    add(node, "disk-cache-store")
    return sorted(evidence.values(), key=lambda item: (item["path"], item["line"], item["kind"]))


def audit(root, ignore=()):
    root = Path(root).resolve()
    if not root.is_dir():
        raise ValueError("audit path must be an existing directory")
    claims, production, testonly, warnings = [], [], [], []
    source_count = 0
    for path, rel in files(root, ignore):
        parts = rel.split("/")
        is_doc = rel == "README.md" or (parts[0] == "docs" and path.suffix == ".md")
        is_source = path.suffix == ".py" and not any(p in EXCLUDED for p in parts[:-1])
        if not is_doc and not is_source:
            continue
        is_test = any(p in TEST_DIRS for p in parts[:-1]) or path.name.startswith("test_") or path.name.endswith("_test.py")
        if is_source and not is_test:
            source_count += 1
        try:
            text = path.read_text(encoding="utf-8-sig")
            if is_doc:
                claims.extend(claims_from_text(text, rel))
            if is_source:
                items = evidence_from_text(text, rel)
                (testonly if is_test else production).extend(items)
        except (OSError, UnicodeError, SyntaxError) as error:
            warnings.append({"path": rel, "message": type(error).__name__})
    findings, verified = [], []
    for family in ("cached", "no-cache"):
        family_claims = [claim for claim in claims if claim["family"] == family]
        if not family_claims:
            continue
        severity, suffix, evidence = "HIGH", "", []
        if not source_count:
            evidence = [{"kind": "no-source"}]
        elif family == "cached" and production:
            verified.append({"family": family, "claims": family_claims, "evidence": production})
            continue
        elif family == "no-cache" and production:
            evidence = production
        elif testonly:
            severity, suffix, evidence = "MEDIUM", "-test-only", testonly
        elif family == "no-cache":
            verified.append({"family": family, "claims": family_claims, "evidence": []})
            continue
        findings.append({"rule": "documented-" + family + "-claim-false" + suffix,
                         "severity": severity, "family": family,
                         "claims": family_claims, "evidence": evidence})
    return {"version": "0.1.0", "findings": findings, "verified_claims": verified,
            "claims": claims, "production_evidence": production,
            "test_only_evidence": testonly, "warnings": warnings,
            "production_source_count": source_count}
