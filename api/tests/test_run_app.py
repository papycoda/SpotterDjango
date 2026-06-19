from io import StringIO
from unittest.mock import MagicMock, call, patch

from django.core.management import CommandError, call_command
from django.test import SimpleTestCase, override_settings


@override_settings(GEOCODING_AUTO_QUEUE_BATCH_SIZE=250)
class RunAppCommandTests(SimpleTestCase):
    @patch("api.management.commands.run_app.time.sleep", side_effect=KeyboardInterrupt)
    @patch("api.management.commands.run_app.subprocess.Popen")
    def test_launches_web_and_worker_as_separate_processes(self, popen, sleep):
        web = MagicMock()
        worker = MagicMock()
        web.poll.return_value = None
        worker.poll.return_value = None
        popen.side_effect = [web, worker]

        call_command("run_app", stdout=StringIO())

        web_command = popen.call_args_list[0].args[0]
        worker_command = popen.call_args_list[1].args[0]
        self.assertEqual(web_command[-3:], ["runserver", "127.0.0.1:8000", "--noreload"])
        self.assertEqual(
            worker_command[-4:],
            ["run_geocoding_worker", "--watch", "--auto-queue", "250"],
        )

    @patch("api.management.commands.run_app.time.sleep")
    @patch("api.management.commands.run_app.subprocess.Popen")
    def test_child_failure_terminates_its_running_sibling(self, popen, sleep):
        web = MagicMock()
        worker = MagicMock()
        web.poll.return_value = 1
        worker.poll.side_effect = [None, None]
        popen.side_effect = [web, worker]

        with self.assertRaisesMessage(CommandError, "web process exited with status 1"):
            call_command("run_app", stdout=StringIO())

        worker.terminate.assert_called_once_with()
        worker.wait.assert_called_once()

    @patch("api.management.commands.run_app.time.sleep", side_effect=KeyboardInterrupt)
    @patch("api.management.commands.run_app.subprocess.Popen")
    def test_interrupt_terminates_both_children(self, popen, sleep):
        web = MagicMock()
        worker = MagicMock()
        web.poll.return_value = None
        worker.poll.return_value = None
        popen.side_effect = [web, worker]

        call_command("run_app", stdout=StringIO())

        self.assertEqual(web.method_calls, [call.poll(), call.poll(), call.terminate(), call.wait(timeout=5)])
        self.assertEqual(
            worker.method_calls,
            [call.poll(), call.poll(), call.terminate(), call.wait(timeout=5)],
        )
