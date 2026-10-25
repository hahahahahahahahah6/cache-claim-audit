import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from cache_claim_audit.audit import audit, claims_from_text, evidence_from_text
from cache_claim_audit.__main__ import main


class Claims(unittest.TestCase):
    pass


def claim_case(text, expected):
    def test(self):
        self.assertEqual({c['family'] for c in claims_from_text(text, 'README.md')}, set(expected))
    return test


for index, (text, expected) in enumerate([
    ('cached', ['cached']), ('caching', ['cached']), ('uses a cache', ['cached']),
    ('use a cache', ['cached']), ('with a cache', ['cached']), ('with caching', ['cached']),
    ('result cache', ['cached']), ('response cache', ['cached']),
    ('no caching', ['no-cache']), ('no cache', ['no-cache']), ('does not cache', ['no-cache']),
    ("doesn't cache", ['no-cache']), ('never caches', ['no-cache']), ('not cached', ['no-cache']),
    ('uncached', ['no-cache']), ('without caching', ['no-cache']), ('without a cache', ['no-cache']),
    ('no memoization', ['no-cache']), ('does not use a cache', ['no-cache']),
    ('clear the cache directory', []), ('cache size', []), ('cache', []),
    ('response local cache', []), ('uses the cache', []), ('cache-like', []),
    ('precached cachingly', []), ('not using caching', []), ("don't enable caching", []),
    ("isn't caching enabled", []), ('never use a cache', []),
    ('RESULTS ARE CACHED', ['cached']), ('Results are cached. No caching.', ['cached', 'no-cache']),
    ('```md\nresults are cached\n```', []), ('~~~\nno caching\n~~~', []),
    ('Results are `cached`.', ['cached']), ('Not useful. Results are cached.', ['cached']),
    ('Results are not\ncached.', ['no-cache']), ('not cached results', ['no-cache']),
]):
    setattr(Claims, 'test_phrase_%02d' % index, claim_case(text, expected))


class Evidence(unittest.TestCase):
    def test_dedupe(self):
        self.assertEqual(len(evidence_from_text('cache(x); cache(y)', 'main.py')), 1)

    def test_distinct_sites(self):
        self.assertEqual(len(evidence_from_text('import cachetools\ncache(x)\nstore_cache[x] = y', 'main.py')), 3)


def evidence_case(source, count):
    def test(self):
        self.assertEqual(len(evidence_from_text(source, 'main.py')), count)
    return test


for index, (source, count) in enumerate([
    ('import functools', 0), ('import cachetools as ct', 1), ('from diskcache import Cache', 1),
    ('import requests_cache', 1), ('from joblib import Memory as M', 1),
    ('from cachetools.keys import hashkey', 1), ('import unknown_cache', 0),
    ('@functools.lru_cache\ndef f(): pass', 1), ('@cache\ndef f(): pass', 1),
    ('@lru_cache(maxsize=8)\ndef f(): pass', 1), ('functools.cache(f)', 1),
    ('other.lru_cache(f)', 1), ('cache[key] = value', 1), ('self._CACHE[key] = value', 1),
    ('result_cache[key] += 1', 1), ('cache[key]', 0), ('cache.get(key)', 0),
    ('cache = {}', 0), ('cache.clear()', 0), ('f.cache_clear()', 0),
    ('cache.set(key, value)', 1), ('self._cache.put(key, value)', 1),
    ('CACHE.__setitem__(key, value)', 1), ('data.set(key, value)', 0),
    ('memoize(f)', 1), ('decorators.cached(f)', 1),
    ('sqlite3.connect("cache.db")', 1), ('sqlite3.connect("app.db")', 0),
    ('shelve.open("CACHE")', 1), ('dbm.open(filename="my_cache")', 1),
    ('sqlite3.connect(database="cache.db")', 1), ('sqlite3.connect(filename)', 0),
    ('sqlite3.connect("app.db", timeout="cache")', 0),
    ('headers = {"Cache-Control": "max-age=100", "ETag": "v1"}', 0),
    ('if False:\n import cachetools', 1),
]):
    setattr(Evidence, 'test_site_%02d' % index, evidence_case(source, count))


class Trees(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def write(self, path, text):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding='utf-8')
        return target

    def project(self, claim='results are cached', source='def work(): return 1'):
        self.write('README.md', claim)
        self.write('main.py', source)

    def test_cached_false(self):
        self.project()
        findings = audit(self.root)['findings']
        self.assertEqual([(f['rule'], f['severity']) for f in findings], [('documented-cached-claim-false', 'HIGH')])

    def test_no_cache_false_all_evidence(self):
        self.project('no caching', 'import cachetools\ncache[x] = y')
        finding, = audit(self.root)['findings']
        self.assertEqual(finding['rule'], 'documented-no-cache-claim-false')
        self.assertEqual(len(finding['evidence']), 2)

    def test_cached_verified(self):
        self.project(source='@cache\ndef work(): return 1')
        result = audit(self.root)
        self.assertEqual(result['findings'], [])
        self.assertEqual(result['verified_claims'][0]['family'], 'cached')

    def test_no_cache_verified(self):
        self.project('no caching')
        self.assertEqual(audit(self.root)['verified_claims'][0]['family'], 'no-cache')

    def test_no_claim(self):
        self.project('ordinary project', 'import cachetools')
        self.assertEqual(audit(self.root)['findings'], [])

    def test_testonly_both_rules(self):
        for claim, family in [('results are cached', 'cached'), ('no caching', 'no-cache')]:
            self.project(claim)
            self.write('tests/test_work.py', '@lru_cache\ndef work(): pass')
            finding, = audit(self.root)['findings']
            self.assertEqual(finding['rule'], 'documented-' + family + '-claim-false-test-only')
            self.assertEqual(finding['severity'], 'MEDIUM')
            self.assertEqual(len(finding['evidence']), 1)

    def test_production_priority(self):
        self.project('no caching', 'import cachetools')
        self.write('tests/test_work.py', 'import diskcache')
        finding, = audit(self.root)['findings']
        self.assertEqual(finding['severity'], 'HIGH')
        self.assertEqual([e['path'] for e in finding['evidence']], ['main.py'])

    def test_both_families_no_source(self):
        self.write('README.md', 'Results are cached. No caching.')
        findings = audit(self.root)['findings']
        self.assertEqual(len(findings), 2)
        self.assertEqual({f['family'] for f in findings}, {'cached', 'no-cache'})
        self.assertTrue(all(f['severity'] == 'HIGH' and f['evidence'] == [{'kind': 'no-source'}] for f in findings))

    def test_no_source_priority(self):
        self.write('README.md', 'no caching')
        self.write('tests/test_work.py', 'import cachetools')
        self.assertEqual(audit(self.root)['findings'][0]['evidence'], [{'kind': 'no-source'}])

    def test_family_collapse(self):
        self.project('Cached results. Caching enabled. Uses a cache.')
        self.assertEqual(len(audit(self.root)['findings']), 1)
        self.assertEqual(len(audit(self.root)['claims']), 3)

    def test_docs_scope(self):
        self.project('ordinary project')
        self.write('docs/deep/guide.md', 'results are cached')
        self.write('OTHER.md', 'no caching')
        self.assertEqual([c['path'] for c in audit(self.root)['claims']], ['docs/deep/guide.md'])

    def test_excluded_sources(self):
        self.project()
        for path in ['tests/worker.py', 'fixtures/sample.py', 'test_worker.py', 'worker_test.py', 'pkg/tests/sub.py']:
            self.write(path, 'import cachetools')
        for path in ['docs/main.py', 'examples/main.py', 'pkg/examples/main.py']:
            self.write(path, 'import diskcache')
        result = audit(self.root)
        self.assertEqual(result['production_evidence'], [])
        self.assertEqual(len(result['test_only_evidence']), 5)
        self.assertEqual(result['production_source_count'], 1)

    def test_ignore_evidence(self):
        self.project(source='import cachetools')
        self.write('plain.py', 'x = 1')
        result = audit(self.root, ['main.py'])
        self.assertEqual(result['production_evidence'], [])
        self.assertEqual(result['findings'][0]['rule'], 'documented-cached-claim-false')

    def test_ignore_docs_directory(self):
        self.project('ordinary project')
        self.write('docs/guide.md', 'cached')
        self.write('pkg/cache.py', 'import cachetools')
        result = audit(self.root, ['docs/*', 'pkg/'])
        self.assertEqual(result['claims'], [])
        self.assertEqual(result['production_evidence'], [])

    def test_symlinks(self):
        self.project()
        self.write('external/value.py', 'import cachetools')
        (self.root / 'alias.py').symlink_to(self.root / 'external/value.py')
        (self.root / 'linked').symlink_to(self.root / 'external', target_is_directory=True)
        self.assertEqual(audit(self.root, ['external'])['production_evidence'], [])

    def test_env_never_opened(self):
        self.project('no caching')
        self.write('.env', 'FIXTURE_ENV_MARKER_DO_NOT_READ')
        original = Path.open
        def guarded(path, *args, **kwargs):
            self.assertNotEqual(path.name, '.env')
            return original(path, *args, **kwargs)
        with patch.object(Path, 'open', guarded):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(main([str(self.root), '--json']), 0)
            self.assertNotIn('FIXTURE_ENV_MARKER_DO_NOT_READ', output.getvalue())
            json.loads(output.getvalue())

    def test_invalid_source(self):
        self.project(source='this is invalid !')
        self.assertEqual(audit(self.root)['warnings'], [{'path': 'main.py', 'message': 'SyntaxError'}])

    def test_invalid_root(self):
        with self.assertRaises(ValueError):
            audit(self.root / 'missing')

    def test_line_number(self):
        self.assertEqual(claims_from_text('\n\nResults are cached.', 'README.md')[0]['line'], 3)


class CLI(unittest.TestCase):
    fixtures = Path(__file__).parent / 'fixtures'

    def run_cli(self, *args):
        return subprocess.run([sys.executable, '-m', 'cache_claim_audit', *map(str, args)], capture_output=True, text=True)

    def test_fixtures(self):
        for fixture, code, rule in [
            ('good/cached', 0, None), ('good/nocache', 0, None),
            ('bad/cached', 2, 'documented-cached-claim-false'),
            ('bad/nocache', 2, 'documented-no-cache-claim-false'),
            ('testonly', 2, 'documented-cached-claim-false-test-only')]:
            with self.subTest(fixture=fixture):
                result = self.run_cli(self.fixtures / fixture, '--json')
                self.assertEqual(result.returncode, code, result.stderr)
                payload = json.loads(result.stdout)
                self.assertIn('verified_claims', payload)
                self.assertEqual([f['rule'] for f in payload['findings']], [rule] if rule else [])
                if rule:
                    self.assertEqual(payload['findings'][0]['severity'], 'MEDIUM' if fixture == 'testonly' else 'HIGH')
                else:
                    self.assertEqual(len(payload['verified_claims']), 1)

    def test_fail_on_high(self):
        result = self.run_cli(self.fixtures / 'testonly', '--fail-on', 'HIGH', '--json')
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout)['findings'][0]['severity'], 'MEDIUM')

    def test_high_fails(self):
        self.assertEqual(self.run_cli(self.fixtures / 'bad/cached', '--fail-on', 'HIGH').returncode, 2)

    def test_version(self):
        result = self.run_cli('--version')
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), '0.1.0')

    def test_usage(self):
        for args in [('--unknown',), ('--fail-on', 'LOW'), ('/nonexistent/cache-claim-audit',)]:
            self.assertEqual(self.run_cli(*args).returncode, 1)

    def test_help(self):
        result = self.run_cli('--help')
        self.assertEqual(result.returncode, 0)
        self.assertIn('cache-claim-audit', result.stdout)
        self.assertIn('--ignore', result.stdout)

    def test_verbose(self):
        result = self.run_cli(self.fixtures / 'bad/nocache', '--verbose')
        self.assertEqual(result.returncode, 2)
        self.assertIn('cache-primitive', result.stdout)
        self.assertIn('README.md:1', result.stdout)

    def test_repeat_ignore(self):
        result = self.run_cli(self.fixtures / 'bad/nocache', '--ignore', 'README.md', '--ignore', '*.py', '--json')
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout)['claims'], [])


if __name__ == '__main__':
    unittest.main()
