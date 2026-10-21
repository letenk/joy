"""File diffs: line numbers, truncation, and safe terminal rendering."""

import unittest
from io import StringIO

from rich.console import Console

from joy.diff import CONTEXT, MAX_ROWS, build_diff, render_diff


def lines(count, start=1):
    return ''.join(f'line {number}\n' for number in range(start, start + count))


def kinds(diff):
    return [(row.kind, row.old, row.new, row.text) for row in diff.rows]


def render(diff, width=60):
    output = StringIO()
    Console(file=output, width=width, color_system=None).print(render_diff(diff))
    return output.getvalue()


class DiffTest(unittest.TestCase):
    def test_edit_shows_context_and_both_line_numbers(self):
        before = lines(10)
        diff = build_diff('a.py', before, before.replace('line 5\n', 'five\n'))
        self.assertEqual((diff.created, diff.added, diff.removed, diff.hidden), (False, 1, 1, 0))
        self.assertEqual(kinds(diff), [
            ('ctx', 2, 2, 'line 2'), ('ctx', 3, 3, 'line 3'), ('ctx', 4, 4, 'line 4'),
            ('del', 5, None, 'line 5'), ('add', None, 5, 'five'),
            ('ctx', 6, 6, 'line 6'), ('ctx', 7, 7, 'line 7'), ('ctx', 8, 8, 'line 8'),
        ])

    def test_numbers_diverge_after_inserted_lines(self):
        before = lines(8)
        diff = build_diff('a.py', before, before.replace('line 2\n', 'line 2\nnew a\nnew b\n'))
        self.assertEqual([row for row in kinds(diff) if row[0] == 'add'],
                         [('add', None, 3, 'new a'), ('add', None, 4, 'new b')])
        # Old line 3 is now line 5; both numbers stay visible so neither is ambiguous.
        self.assertIn(('ctx', 3, 5, 'line 3'), kinds(diff))

    def test_new_file_marks_every_line_added(self):
        diff = build_diff('src/new.py', None, 'one\ntwo\n')
        self.assertEqual((diff.created, diff.added, diff.removed), (True, 2, 0))
        self.assertEqual(kinds(diff), [('add', None, 1, 'one'), ('add', None, 2, 'two')])
        self.assertTrue(build_diff('empty.txt', None, '').created)

    def test_no_change_has_no_diff(self):
        self.assertIsNone(build_diff('a.py', 'same\n', 'same\n'))

    def test_distant_changes_are_separated_by_a_gap(self):
        before = lines(40)
        after = before.replace('line 2\n', 'two\n').replace('line 39\n', 'thirty-nine\n')
        diff = build_diff('a.py', before, after)
        self.assertEqual(sum(row.kind == 'gap' for row in diff.rows), 1)
        self.assertEqual((diff.added, diff.removed), (2, 2))
        self.assertIn(('add', None, 39, 'thirty-nine'), kinds(diff))
        # Lines between the hunks are not shown.
        self.assertNotIn('line 20', [row.text for row in diff.rows])

    def test_change_near_file_edges_clips_context(self):
        diff = build_diff('a.py', lines(5), lines(5).replace('line 1\n', 'one\n'))
        self.assertEqual(kinds(diff)[0], ('del', 1, None, 'line 1'))
        diff = build_diff('a.py', lines(5), lines(5).replace('line 5\n', 'five\n'))
        self.assertEqual(kinds(diff)[-1], ('add', None, 5, 'five'))
        self.assertEqual(sum(row.kind == 'ctx' for row in diff.rows), CONTEXT)

    def test_long_diffs_are_truncated_but_counted(self):
        diff = build_diff('big.txt', None, lines(MAX_ROWS + 25))
        self.assertEqual(len(diff.rows), MAX_ROWS)
        self.assertEqual((diff.added, diff.hidden), (MAX_ROWS + 25, 25))
        self.assertIn('25 more lines', render(diff))

    def test_render_shows_path_counts_numbers_and_code(self):
        before = lines(10)
        text = render(build_diff('src/a.py', before, before.replace('line 5\n', 'five\n')))
        self.assertIn('Edited src/a.py  +1 -1', text)
        # Rows are padded to the full width so the background colour spans the line.
        self.assertRegex(text, r'(?m)^5\s+- line 5 *$')
        self.assertRegex(text, r'(?m)^\s+5 \+ five *$')
        self.assertRegex(text, r'(?m)^4 4\s+line 4 *$')
        text = render(build_diff('b.txt', None, 'one\n'))
        self.assertIn('Created b.txt  +1', text)
        self.assertRegex(text, r'(?m)^1 \+ one *$')

    def test_render_treats_code_as_text_not_markup(self):
        text = render(build_diff('a.md', None, '[bold]literal[/bold] [link=x]y[/link]\n'))
        self.assertIn('[bold]literal[/bold] [link=x]y[/link]', text)

    def test_render_neutralizes_terminal_escapes(self):
        text = render(build_diff('a\x1b]0;title\x07.txt', None, 'red \x1b[31mred\x00\t|\n'))
        self.assertNotIn('\x1b', text)
        self.assertNotIn('\x00', text)
        self.assertIn('red ␛[31mred␀', text)

    def test_render_wraps_long_lines_instead_of_cutting_them(self):
        text = render(build_diff('a.txt', None, 'q' * 150 + 'END\n'), width=40)
        self.assertIn('END', text)
        self.assertEqual(text.count('q'), 150)


if __name__ == '__main__':
    unittest.main()
