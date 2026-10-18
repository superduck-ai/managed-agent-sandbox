import importlib.util
from pathlib import Path
import signal
import subprocess
import unittest
from unittest.mock import Mock, call, patch

spec = importlib.util.spec_from_file_location('manager', Path(__file__).parent / 'slides/manager.py')
manager = importlib.util.module_from_spec(spec)
spec.loader.exec_module(manager)


class ManagerTests(unittest.TestCase):
    def test_startup_failure_cleans_started_service(self):
        server = Mock()
        server.poll.return_value = 1
        with patch.object(manager.sys, 'argv', ['manager', 'task-run']), patch.object(manager.signal, 'signal'):
            with patch.object(manager.subprocess, 'Popen', return_value=server), patch.object(manager, 'stop') as stop:
                with self.assertRaisesRegex(RuntimeError, 'exited during startup'):
                    manager.main()
                self.assertEqual(stop.call_args_list, [call(None), call(server)])

    def test_manager_spawn_failure_cleans_both_services(self):
        first, second = Mock(), Mock()
        first.poll.return_value = second.poll.return_value = None
        with patch.object(manager.sys, 'argv', ['manager', 'task-run']), patch.object(manager.signal, 'signal'):
            with patch.object(manager.subprocess, 'Popen', side_effect=[first, second, OSError('cannot spawn')]):
                with patch.object(manager.urllib.request, 'urlopen') as urlopen, patch.object(manager, 'stop') as stop:
                    urlopen.return_value.__enter__.return_value.status = 200
                    with self.assertRaisesRegex(OSError, 'cannot spawn'):
                        manager.main()
                    self.assertEqual(stop.call_args_list, [call(None), call(second), call(first)])

    def test_stop_escalates_and_waits(self):
        process = Mock(pid=123)
        process.poll.return_value = None
        process.wait.side_effect = [subprocess.TimeoutExpired('fixture', 10), 0]
        with patch.object(manager.os, 'killpg') as kill:
            manager.stop(process)
        self.assertEqual(kill.call_args_list, [call(123, signal.SIGTERM), call(123, signal.SIGKILL)])
        self.assertEqual(process.wait.call_count, 2)

    def test_non_task_command_passes_arguments_to_original_binary(self):
        with patch.object(manager.sys, 'argv', ['manager', 'provision-packages', '--help']):
            with patch.object(manager.os, 'execv', side_effect=SystemExit(0)) as execute:
                with self.assertRaises(SystemExit):
                    manager.main()
        original = '/opt/env-runner/environment-manager'
        execute.assert_called_once_with(original, [original, 'provision-packages', '--help'])

    def test_successful_manager_exit_cleans_both_services(self):
        first, second, worker = Mock(), Mock(), Mock(returncode=0)
        first.poll.return_value = second.poll.return_value = None
        worker.poll.return_value = 0
        with patch.object(manager.sys, 'argv', ['manager', 'task-run', '--help']), patch.object(manager.signal, 'signal'):
            with patch.object(manager.subprocess, 'Popen', side_effect=[first, second, worker]):
                with patch.object(manager.urllib.request, 'urlopen') as urlopen, patch.object(manager, 'stop') as stop:
                    urlopen.return_value.__enter__.return_value.status = 200
                    self.assertEqual(manager.main(), 0)
                    self.assertEqual(stop.call_args_list, [call(worker), call(second), call(first)])


if __name__ == '__main__':
    unittest.main()
