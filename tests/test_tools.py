"""Local tools and Pi-style matching, without network access."""

import json
import tempfile
import unittest
from pathlib import Path
from threading import Event

from joy.tools import _replace, create_tools


class ToolsTest(unittest.TestCase):
    def test_pi_matching(self):
        cases = [
            ('a\r\nb\r\n', 'a\nb', 'c\nd', 'c\r\nd\r\n'),
            ('\ufeffa\r\n', 'a', 'b', '\ufeffb\r\n'),
            ('keep “this”  \nx = “old”  \nend  \n', 'x = "old"', 'x = "new"',
             'keep “this”  \nx = "new"\nend  \n'),
            ('a  \nb  \nc  \n', 'a\nb\n', 'z\n', 'z\nc  \n'),
            ('before\nﬁ = 1  ', 'fi = 1', 'fi = 2', 'before\nfi = 2'),
            ('x = “old”  \n', '“old”', '“new”', 'x = “new”  \n'),
        ]
        for original, old, new, expected in cases:
            with self.subTest(original=original, old=old):
                self.assertEqual(_replace(original, old, new), expected)
        for original, old, new in [('x x', 'x', 'y'), ('“x” "x"', '"x"', 'y'),
                                   ('a', 'missing', 'b'), ('a', '', 'b'),
                                   ('a', 'a', 'a'), ('a', ' ', 'b')]:
            with self.assertRaises(ValueError):
                _replace(original, old, new)

    def test_tools_end_to_end(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cancelled = Event()
            tools = create_tools(root, cancelled)

            def run(name, **arguments):
                return tools.execute({'function': {'name': name, 'arguments': json.dumps(arguments)}})[0]

            run('write', path='src/demo.py', content='print(“hello”)\n')
            self.assertIn('1: print', run('read', path='src/demo.py'))
            result = run('edit', path='src/demo.py', old_text='print("hello")', new_text='print("bye")')
            self.assertIn('+print("bye")', result)
            self.assertEqual((root / 'src/demo.py').read_text(), 'print("bye")\n')
            self.assertIn('bye', run('bash', command='python3 src/demo.py'))
            self.assertIn('exit code 7', run('bash', command='exit 7'))
            self.assertIn('Timed out', run('bash', command='sleep 5', timeout=1))
            for name, args in [('read', {'path': '../outside'}),
                               ('write', {'path': '../outside', 'content': 'no'}),
                               ('edit', {'path': 'src/demo.py', 'old_text': 'absent', 'new_text': 'no'})]:
                with self.assertRaises(ValueError):
                    run(name, **args)
            self.assertEqual((root / 'src/demo.py').read_text(), 'print("bye")\n')
            (root / 'escape').symlink_to(root.parent, target_is_directory=True)
            with self.assertRaises(ValueError):
                run('write', path='escape/outside', content='no')
            cancelled.set()
            with self.assertRaises(InterruptedError):
                run('write', path='cancelled', content='no')
            self.assertFalse((root / 'cancelled').exists())

    def test_file_changes_are_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            changes = []
            tools = create_tools(root, Event(), changes.append)

            def run(name, **arguments):
                return tools.execute({'function': {'name': name, 'arguments': json.dumps(arguments)}})[0]

            run('write', path='src/demo.py', content='a = 1\nb = 2\n')
            run('write', path='./src/../src/demo.py', content='a = 1\nb = 3\n')
            run('write', path='src/demo.py', content='a = 1\nb = 3\n')  # unchanged: no report
            run('edit', path='src/demo.py', old_text='a = 1', new_text='a = 10')
            created, overwritten, edited = changes
            self.assertEqual([change.path for change in changes], ['src/demo.py'] * 3)
            self.assertEqual((created.created, created.added, created.removed), (True, 2, 0))
            self.assertEqual((overwritten.created, overwritten.added, overwritten.removed), (False, 1, 1))
            self.assertEqual([(row.kind, row.old, row.new, row.text) for row in edited.rows
                              if row.kind != 'ctx'], [('del', 1, None, 'a = 1'), ('add', None, 1, 'a = 10')])

    def test_binary_overwrite_is_saved_without_a_diff(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'binary.bin').write_bytes(b'\xff\xfe\x00')
            changes = []
            tools = create_tools(root, Event(), changes.append)
            result = tools.execute({'function': {'name': 'write', 'arguments': json.dumps(
                {'path': 'binary.bin', 'content': 'text now\n'})}})[0]
            self.assertEqual(result, 'Wrote binary.bin.')
            self.assertEqual((root / 'binary.bin').read_text(), 'text now\n')
            self.assertEqual(changes, [])

    def test_failing_preview_does_not_fail_the_tool(self):
        def broken(change):
            raise RuntimeError('preview failed')

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tools = create_tools(root, Event(), broken)

            def run(name, **arguments):
                return tools.execute({'function': {'name': name, 'arguments': json.dumps(arguments)}})[0]

            self.assertEqual(run('write', path='x.txt', content='a\n'), 'Wrote x.txt.')
            self.assertIn('Edited x.txt', run('edit', path='x.txt', old_text='a', new_text='b'))
            self.assertEqual((root / 'x.txt').read_text(), 'b\n')
