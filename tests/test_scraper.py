import contextlib
import importlib.util
import io
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('scraper', Path(__file__).parents[1] / 'scraper/scraper.py')
s = importlib.util.module_from_spec(spec)
spec.loader.exec_module(s)


def event(source, name='old'):
    return dict(id=source + '-' + name, name=name, source=source,
                url='https://example.com', start='2026-10-10', end='2026-12-01',
                reg_deadline='2026-08-01')


def fail():
    raise RuntimeError('simulated network failure')


@contextlib.contextmanager
def workspace_temp():
    root = Path(__file__).resolve().parent
    with tempfile.TemporaryDirectory(dir=root) as folder:
        assert Path(folder).resolve().parent == root
        yield folder


class ScraperTests(unittest.TestCase):
    def test_retention(self):
        with patch.object(s, 'TODAY', date(2026, 10, 6)):
            self.assertTrue(s.is_recent(event('a')))
            self.assertTrue(s.is_recent(dict(start='2026-11-01', reg_deadline='2026-08-01')))
            self.assertFalse(s.is_recent(dict(end='2026-08-01', reg_deadline='2026-12-01')))
            self.assertFalse(s.is_recent(dict(start='2026-08-01')))
            self.assertTrue(s.is_recent(dict(start='2026-08-01', reg_deadline='2026-08-15')))

    def run_sources(self, sources):
        with workspace_temp() as folder:
            output = Path(folder) / 'hackathons.json'
            initial = json.dumps(dict(updated_at='old', events=[event('a'), event('b')],
                                      source_status={'b': {'last_success_at': '2026-09-29 08:00'}}))
            output.write_text(initial, encoding='utf-8')
            with patch.object(s, 'DATA_DIR', folder), patch.object(s, 'OUTPUT_FILE', str(output)), \
                 patch.object(s, 'MANUAL_FILE', str(Path(folder) / 'manual.json')), \
                 patch.object(s, 'RANKED_FILE', str(Path(folder) / 'ranked.json')), \
                 patch.object(s, 'SOURCES', sources), patch.object(s.sys, 'argv', ['scraper']), \
                 patch.object(s, 'TODAY', date(2026, 10, 6)), contextlib.redirect_stdout(io.StringIO()):
                code = 0
                try:
                    s.main()
                except SystemExit as exc:
                    code = exc.code
            return initial, output.read_text(encoding='utf-8'), code

    def test_all_failure_preserves_bytes(self):
        before, after, code = self.run_sources([('a', fail), ('b', fail)])
        self.assertEqual(before, after)
        self.assertEqual(code, 1)

    def test_partial_failure_keeps_source(self):
        _, after, code = self.run_sources([('a', fail), ('b', lambda: [event('b', 'new')])])
        data = json.loads(after)
        self.assertEqual({e['id'] for e in data['events']}, {'a-old', 'b-new'})
        self.assertEqual(len(data['errors']), 1)
        self.assertEqual(data['source_cache']['a'][0]['id'], 'a-old')
        self.assertEqual(data['source_status']['a']['status'], 'cached')
        self.assertIsNone(data['source_status']['a']['last_success_at'])
        self.assertEqual(data['source_status']['b']['status'], 'ok')
        self.assertRegex(data['source_status']['b']['last_success_at'], r'^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$')
        self.assertEqual(code, 0)

    def test_empty_source_preserves_cache(self):
        _, after, _ = self.run_sources([('a', lambda: []), ('b', lambda: [event('b', 'new')])])
        self.assertIn('a-old', {e['id'] for e in json.loads(after)['events']})

    def test_invalid_source_preserves_cache(self):
        _, after, _ = self.run_sources([('a', lambda: [{}]), ('b', lambda: [event('b', 'new')])])
        self.assertIn('a-old', {e['id'] for e in json.loads(after)['events']})

    def test_fallback_preserves_last_success_time(self):
        _, after, _ = self.run_sources([('a', lambda: [event('a', 'new')]), ('b', fail)])
        source = json.loads(after)['source_status']['b']
        self.assertEqual(source['last_success_at'], '2026-09-29 08:00')
        self.assertEqual(source['status'], 'cached')

    def test_failed_replace_preserves_file(self):
        with workspace_temp() as folder:
            output = Path(folder) / 'data.json'
            output.write_text('original', encoding='utf-8')
            with patch.object(s, 'DATA_DIR', folder), patch.object(s, 'OUTPUT_FILE', str(output)), \
                 patch.object(s.os, 'replace', side_effect=OSError('simulated disk failure')):
                with self.assertRaises(OSError):
                    s.atomic_write({'events': [event('a')]})
            self.assertEqual(output.read_text(), 'original')
            self.assertEqual(len(list(Path(folder).iterdir())), 1)


if __name__ == '__main__':
    unittest.main()
