"""Exercise the real prompt loop without a physical terminal."""

import asyncio
import os
import unittest
from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import patch

from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import to_plain_text
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput
from prompt_toolkit.application import create_app_session, get_app

from joy.cli import main
from joy.diff import build_diff


class CliTest(unittest.TestCase):
    def test_paste_ending_in_newline_stays_visible(self):
        original_prompt = PromptSession.prompt
        failures = []
        inspected = []
        with create_pipe_input() as pipe:
            def prompt(session, *args, **kwargs):
                async def paste_and_inspect():
                    pipe.send_text('\x1b[200~first line\r\nlast line\r\n\x1b[201~')
                    await asyncio.sleep(0.05)
                    try:
                        self.assertEqual(session.default_buffer.text, 'first line\nlast line\n')
                        info = session.layout.current_window.render_info
                        visible = to_plain_text(info.ui_content.get_line(info.vertical_scroll))
                        self.assertIn('last line', visible)
                        inspected.append(True)
                    except AssertionError as error:
                        failures.append(error)
                    finally:
                        session.app.exit(result='/exit')

                kwargs['pre_run'] = lambda: session.app.create_background_task(paste_and_inspect())
                return original_prompt(session, *args, **kwargs)

            with create_app_session(input=pipe, output=DummyOutput()), \
                    redirect_stdout(StringIO()), patch.object(PromptSession, 'prompt', prompt), \
                    patch.dict(os.environ, {'OPENAI_API_KEY': 'test-key'}):
                main()
        if failures:
            raise failures[0]
        self.assertTrue(inspected)

    def test_prompt_and_exit(self):
        output = StringIO()
        original_prompt = PromptSession.prompt
        original_sleep = asyncio.sleep
        inputs = iter([
            "hello\x1b\rworld\r",
            "\x03",
            "failed request\r",
            "",
            "[bold]literal[/bold]\r",
            "draft for later\r",
            "\r",
            "",
            "/exit\r",
        ])
        defaults = []

        requests = []

        async def fake_reply(messages):
            requests.append(messages)
            await original_sleep(0.02)
            if messages[-1]["content"] == "failed request":
                yield "Partial reply before failure."
                raise RuntimeError("OpenAI unavailable.")
            yield "An actual "
            await original_sleep(0.02)
            windows = get_app().layout.visible_windows
            self.assertTrue(any(
                hasattr(window.content, "text")
                and callable(window.content.text)
                and to_plain_text(window.content.text()) == "An actual "
                for window in windows
            ))
            self.assertIn(get_app().layout.current_window, windows)
            yield "assistant reply."

        with create_pipe_input() as pipe:
            def prompt(session, *args, **kwargs):
                defaults.append(kwargs.get("default", ""))
                start = kwargs.get("pre_run")

                def feed():
                    if start:
                        start()
                    pipe.send_text(next(inputs))

                kwargs["pre_run"] = feed
                return original_prompt(session, *args, **kwargs)

            with create_app_session(input=pipe, output=DummyOutput()):
                with redirect_stdout(output), patch(
                    "joy.cli.stream_reply", side_effect=fake_reply
                ), patch.object(PromptSession, "prompt", prompt), patch.dict(
                    os.environ, {"OPENAI_API_KEY": "test-key"}
                ):
                    main()
        self.assertIn("Joy", output.getvalue())
        transcript = output.getvalue()
        self.assertRegex(transcript, r"› hello\s+world")
        self.assertIn("[bold]literal[/bold]", transcript)
        self.assertEqual(transcript.count("› "), 4)
        self.assertNotIn("Joy\n", transcript)
        self.assertIn("OpenAI unavailable.", transcript)
        self.assertIn("Partial reply before failure.", transcript)
        self.assertIn("Response cancelled.", transcript)
        self.assertEqual(transcript.count("An actual assistant reply."), 2)
        self.assertEqual(defaults[6], "draft for later")
        self.assertEqual([m["role"] for m in requests[-1]], [
            "system", "user", "assistant", "user",
        ])
        self.assertEqual(requests[-1][1]["content"], "[bold]literal[/bold]")
        self.assertEqual(requests[-1][-1]["content"], "draft for later")

    def run_session(self, inputs, fake_reply):
        output = StringIO()
        original_prompt = PromptSession.prompt
        inputs = iter(inputs)
        with create_pipe_input() as pipe:
            def prompt(session, *args, **kwargs):
                start = kwargs.get("pre_run")

                def feed():
                    if start:
                        start()
                    pipe.send_text(next(inputs))

                kwargs["pre_run"] = feed
                return original_prompt(session, *args, **kwargs)

            with create_app_session(input=pipe, output=DummyOutput()), redirect_stdout(output), \
                    patch("joy.cli.stream_reply", side_effect=fake_reply), \
                    patch.object(PromptSession, "prompt", prompt), \
                    patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}):
                main()
        return output.getvalue()

    def test_file_diffs_print_between_the_text_around_them(self):
        diff = build_diff("src/app.py", "x = 1\ny = 2\n", "x = 1\ny = 3\n")

        async def fake_reply(messages):
            yield "Updating the file."
            yield diff
            yield "\n\nDone."

        transcript = self.run_session(["change y\r", "", "/exit\r"], fake_reply)
        positions = [transcript.index(text) for text in (
            "Updating the file.", "Edited src/app.py  +1 -1", "- y = 2", "+ y = 3", "Done.")]
        self.assertEqual(positions, sorted(positions))
        self.assertRegex(transcript, r"2\s+- y = 2")
        self.assertRegex(transcript, r"2 \+ y = 3")
        self.assertEqual(transcript.count("Edited src/app.py"), 1)

    def test_file_diffs_survive_a_failed_reply(self):
        diff = build_diff("new.py", None, "print('hi')\n")

        async def fake_reply(messages):
            yield diff
            raise RuntimeError("Stream broke.")

        transcript = self.run_session(["make it\r", "", "/exit\r"], fake_reply)
        self.assertIn("Created new.py  +1", transcript)
        self.assertIn("Stream broke.", transcript)
        self.assertLess(transcript.index("Created new.py"), transcript.index("Stream broke."))

    def test_missing_key(self):
        output = StringIO()
        with patch.dict(os.environ, {"OPENAI_API_KEY": ""}), redirect_stdout(output):
            main()
        self.assertIn("Set OPENAI_API_KEY", output.getvalue())
