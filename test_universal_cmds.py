import unittest
import shlex
import sys
from hopit.commands import (
    system_status_cmd,
    system_start_cmd,
    system_stop_cmd,
    system_restart_cmd,
    system_enable_cmd,
    system_disable_cmd,
    firewall_cmd,
    disk_cmd,
    archive_cmd,
    download_cmd,
    search_cmd,
    killport_cmd,
)

class TestUniversalCommands(unittest.TestCase):
    def test_service_direct_cmds(self):
        self.assertTrue(len(system_status_cmd("nginx")) > 0)
        self.assertTrue(len(system_start_cmd("nginx")) > 0)
        self.assertTrue(len(system_stop_cmd("nginx")) > 0)
        self.assertTrue(len(system_restart_cmd("nginx")) > 0)
        self.assertTrue(len(system_enable_cmd("nginx")) > 0)
        self.assertTrue(len(system_disable_cmd("nginx")) > 0)

    def test_firewall_cmd(self):
        res_status = firewall_cmd("status")
        self.assertTrue(len(res_status) > 0)

        res_allow = firewall_cmd("allow 8080")
        self.assertTrue(len(res_allow) > 0)

        res_block = firewall_cmd("block 8080")
        self.assertTrue(len(res_block) > 0)

    def test_disk_cmd(self):
        res_list = disk_cmd("list")
        self.assertTrue(len(res_list) > 0)

        res_usage = disk_cmd("usage .")
        self.assertTrue(len(res_usage) > 0)

    def test_module_commands(self):
        self.assertEqual(archive_cmd("create out.zip ."), [sys.executable, "-m", "hopit.archive", "create", "out.zip", "."])
        self.assertEqual(download_cmd("https://example.com/file.txt"), [sys.executable, "-m", "hopit.download", "https://example.com/file.txt"])
        self.assertEqual(search_cmd("test ."), [sys.executable, "-m", "hopit.search", "test", "."])
        self.assertEqual(killport_cmd("8080"), [sys.executable, "-m", "hopit.killport", "8080"])

    def test_create_cmd(self):
        import tempfile
        import os
        import shutil
        from hopit.main import execute_line
        from hopit.commands import build_commands

        temp_dir = tempfile.mkdtemp()
        try:
            names = {
                "service": lambda: [],
                "installed_pkg": lambda: [],
                "available_pkg": lambda prefix="": [],
                "path": lambda word="": [],
                "adapter": lambda: [],
                "user": lambda: [],
                "group": lambda: [],
            }
            cmds = build_commands(None, names)
            all_names = list(cmds.keys())

            # Test creating a folder
            folder_to_create = os.path.join(temp_dir, "test_folder_sub", "deep_dir")
            line = f"create folder {shlex.quote(folder_to_create)}"
            success = execute_line(line, "/bin/bash", {}, all_names, cmds, None)
            self.assertTrue(success)
            self.assertTrue(os.path.isdir(folder_to_create))

            # Test creating a file
            file_to_create = os.path.join(temp_dir, "test_file_sub", "file.txt")
            line = f"create file {shlex.quote(file_to_create)}"
            success = execute_line(line, "/bin/bash", {}, all_names, cmds, None)
            self.assertTrue(success)
            self.assertTrue(os.path.isfile(file_to_create))

            # Test creating a file that already exists
            success = execute_line(line, "/bin/bash", {}, all_names, cmds, None)
            self.assertTrue(success)

            # Test autocompletions header
            from hopit.ui import LazyCompleter
            from prompt_toolkit.document import Document
            from prompt_toolkit.formatted_text import to_plain_text
            completer = LazyCompleter(cmds)

            # When typing "create ", it should NOT show the header
            doc = Document("create ")
            completions = list(completer.get_completions(doc, None))
            self.assertTrue(len(completions) > 0)
            self.assertNotIn("create here", to_plain_text(completions[0].display))
            
            # When typing "create folder ", it SHOULD show the header
            doc2 = Document("create folder ")
            completions2 = list(completer.get_completions(doc2, None))
            self.assertTrue(len(completions2) > 0)
            self.assertEqual(completions2[0].text, "")
            self.assertEqual(to_plain_text(completions2[0].display_meta), "info")
            self.assertIn("create here", to_plain_text(completions2[0].display))

        finally:
            shutil.rmtree(temp_dir)

    def test_new_cross_platform_commands(self):
        import tempfile
        import os
        import shutil
        from hopit.main import execute_line
        from hopit.commands import build_commands

        temp_dir = tempfile.mkdtemp()
        try:
            names = {
                "service": lambda: [],
                "installed_pkg": lambda: [],
                "available_pkg": lambda prefix="": [],
                "path": lambda word="": [],
                "adapter": lambda: [],
                "user": lambda: [],
                "group": lambda: [],
            }
            cmds = build_commands(None, names)
            all_names = list(cmds.keys())

            # Test registrations
            self.assertIn("pwd", cmds)
            self.assertIn("whereami", cmds)
            self.assertNotIn("showpath", cmds)
            self.assertIn("history", cmds)
            self.assertNotIn("showhistory", cmds)
            self.assertIn("env", cmds)
            self.assertNotIn("showenv", cmds)
            self.assertNotIn("variables", cmds)
            self.assertIn("show", cmds)
            self.assertIn("which", cmds)
            self.assertIn("where", cmds)
            self.assertIn("findcommand", cmds)
            self.assertNotIn("locatecommand", cmds)
            self.assertIn("touch", cmds)
            self.assertNotIn("newfile", cmds)
            self.assertNotIn("createfile", cmds)
            self.assertIn("cat", cmds)
            self.assertNotIn("viewfile", cmds)
            self.assertNotIn("showfile", cmds)
            self.assertIn("head", cmds)
            self.assertIn("viewstart", cmds)
            self.assertNotIn("showstart", cmds)
            self.assertIn("tail", cmds)
            self.assertIn("viewend", cmds)
            self.assertNotIn("showend", cmds)
            self.assertIn("less", cmds)
            self.assertIn("scrollfile", cmds)
            self.assertNotIn("pagefile", cmds)
            self.assertIn("tree", cmds)
            self.assertNotIn("showtree", cmds)
            self.assertNotIn("folderstructure", cmds)
            self.assertIn("find", cmds)
            self.assertIn("findfile", cmds)
            self.assertNotIn("locate", cmds)
            self.assertIn("grep", cmds)
            self.assertIn("findtext", cmds)
            self.assertNotIn("searchtext", cmds)

            # Test execution of inline commands (pwd, history, show, find)
            success = execute_line("pwd", "/bin/bash", {}, all_names, cmds, None)
            self.assertTrue(success)

            success = execute_line("history", "/bin/bash", {}, all_names, cmds, None)
            self.assertTrue(success)

            success = execute_line("show history", "/bin/bash", {}, all_names, cmds, None)
            self.assertTrue(success)

            success = execute_line("show h", "/bin/bash", {}, all_names, cmds, None)
            self.assertTrue(success)

            success = execute_line("find file pattern_here", "/bin/bash", {}, all_names, cmds, None)
            self.assertTrue(success)

            success = execute_line("find f pattern_here", "/bin/bash", {}, all_names, cmds, None)
            self.assertTrue(success)

            success = execute_line("create fi test_file.txt", "/bin/bash", {}, all_names, cmds, None)
            self.assertTrue(success)

            # Test history expansion
            class MockHistory:
                def __init__(self):
                    self._loaded_strings = ["show h"]
                    self._storage = ["show h"]
                def get_strings(self):
                    return self._loaded_strings
            class MockSession:
                def __init__(self):
                    self.history = MockHistory()

            mock_sess = MockSession()
            success = execute_line("show h", "/bin/bash", {}, all_names, cmds, None, mock_sess)
            self.assertTrue(success)
            self.assertEqual(mock_sess.history._loaded_strings[0], "show history")
            self.assertEqual(mock_sess.history._storage[-1], "show history")

            # cleanup created test file
            if os.path.exists("test_file.txt"):
                os.remove("test_file.txt")

        finally:
            shutil.rmtree(temp_dir)

    def test_new_network_and_process_registrations(self):
        from hopit.commands import build_commands
        names = {
            "service": lambda: [],
            "installed_pkg": lambda: [],
            "available_pkg": lambda prefix="": [],
            "path": lambda word="": [],
            "adapter": lambda: [],
            "user": lambda: [],
            "group": lambda: [],
        }
        cmds = build_commands(None, names)
        new_cmds = [
            "ping", "traceroute", "dns", "nslookup", "route", "arp", "netstat",
            "connections", "hostname", "gateway", "mac", "curl", "wget",
            "ssh", "scp", "sftp", "ps", "process", "kill", "pkill", "top",
            "resources"
        ]
        for cmd in new_cmds:
            self.assertIn(cmd, cmds, f"{cmd} should be registered in commands dict")

if __name__ == "__main__":
    unittest.main()
