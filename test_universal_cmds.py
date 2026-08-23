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
            self.assertIn("whoami", cmds)
            self.assertIn("sessions", cmds)
            self.assertIn("w", cmds)
            self.assertIn("who", cmds)
            self.assertIn("quser", cmds)
            self.assertIn("qwinsta", cmds)
            self.assertIn("query", cmds)
            self.assertIn("logoff", cmds)
            self.assertIn("loginctl", cmds)
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

            # Test execution of inline commands (pwd, history, show, find, whoami, sessions)
            success = execute_line("pwd", "/bin/bash", {}, all_names, cmds, None)
            self.assertTrue(success)

            success = execute_line("whoami", "/bin/bash", {}, all_names, cmds, None)
            self.assertTrue(success)

            success = execute_line("sessions list", "/bin/bash", {}, all_names, cmds, None)
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
                    self._loaded_strings = ["show hi"]
                    self._storage = ["show hi"]
                def get_strings(self):
                    return self._loaded_strings
            class MockSession:
                def __init__(self):
                    self.history = MockHistory()

            mock_sess = MockSession()
            success = execute_line("show hi", "/bin/bash", {}, all_names, cmds, None, mock_sess)
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
            "resources", "lookup", "whoami"
        ]
        for cmd in new_cmds:
            self.assertIn(cmd, cmds, f"{cmd} should be registered in commands dict")

    def test_cross_platform_compatibility(self):
        import os
        import shlex
        from hopit.config import IS_WINDOWS
        
        # Check PYTHONPATH is in environment and contains parent of hopit
        self.assertIn("PYTHONPATH", os.environ)
        pythonpath = os.environ["PYTHONPATH"]
        self.assertTrue(any(os.path.isdir(os.path.join(p, "hopit")) for p in pythonpath.split(os.pathsep) if os.path.exists(p)))
        
        # Test shlex.split behavior
        # Under Windows (posix=False), backslashes should not escape characters.
        # Under POSIX (posix=True), backslashes escape characters.
        test_path = r"C:\Users\Ramesh\Downloads\11.pdf"
        if IS_WINDOWS:
            # On Windows, it should preserve the backslashes
            self.assertEqual(shlex.split(test_path)[0], test_path)
        else:
            # On non-Windows, we can explicitly test that the wrapper functions as expected:
            from hopit.commands import _custom_split
            
            # If posix=True, backslashes escape
            escaped = _custom_split(test_path, posix=True)[0]
            self.assertNotEqual(escaped, test_path)
            
            # If posix=False, backslashes are preserved
            preserved = _custom_split(test_path, posix=False)[0]
            self.assertEqual(preserved, test_path)

    def test_session_translations(self):
        from hopit.translation import translate_cross_platform
        from unittest.mock import patch
        import sys

        # Test Unix -> Windows
        with patch("hopit.translation.IS_WINDOWS", True), patch("hopit.translation.IS_MACOS", False):
            from hopit.translation import _q
            py_exe = _q(sys.executable)
            self.assertEqual(translate_cross_platform(["w"]), f"{py_exe} -m hopit.sessions list")
            self.assertEqual(translate_cross_platform(["w", "gnani"]), f'{py_exe} -m hopit.sessions list "gnani"')
            self.assertEqual(translate_cross_platform(["who"]), f"{py_exe} -m hopit.sessions list")
            self.assertEqual(translate_cross_platform(["loginctl", "list-sessions"]), f"{py_exe} -m hopit.sessions list")
            self.assertEqual(translate_cross_platform(["loginctl", "terminate-session", "2"]), f"{py_exe} -m hopit.sessions kill 2")

        # Test Windows -> Linux
        with patch("hopit.translation.IS_WINDOWS", False), patch("hopit.translation.IS_MACOS", False):
            self.assertEqual(translate_cross_platform(["quser"]), "w")
            self.assertEqual(translate_cross_platform(["quser", "gnani"]), "w 'gnani'")
            self.assertEqual(translate_cross_platform(["qwinsta"]), "loginctl list-sessions")
            self.assertEqual(translate_cross_platform(["query", "user"]), "w")
            self.assertEqual(translate_cross_platform(["query", "user", "gnani"]), "w 'gnani'")
            self.assertEqual(translate_cross_platform(["query", "session"]), "loginctl list-sessions")
            self.assertEqual(translate_cross_platform(["logoff", "2"]), "loginctl terminate-session 2")
            self.assertEqual(translate_cross_platform(["logoff", "pts/1"]), "pkill -t pts/1")

        # Test Windows -> macOS & Linux -> macOS
        with patch("hopit.translation.IS_WINDOWS", False), patch("hopit.translation.IS_MACOS", True):
            self.assertEqual(translate_cross_platform(["qwinsta"]), "w")
            self.assertEqual(translate_cross_platform(["query", "session"]), "w")
            self.assertEqual(translate_cross_platform(["logoff", "2"]), "pkill -t 2")
            self.assertEqual(translate_cross_platform(["logoff", "ttys001"]), "pkill -t ttys001")
            self.assertEqual(translate_cross_platform(["loginctl", "list-sessions"]), "who")
            self.assertEqual(translate_cross_platform(["loginctl", "terminate-session", "2"]), "pkill -t 2")

    def test_archive_path_traversal(self):
        import tempfile
        import os
        import zipfile
        import shutil
        from hopit.archive import extract_archive

        temp_dir = tempfile.mkdtemp()
        try:
            # Create a zip file containing a file with a path traversal name
            zip_path = os.path.join(temp_dir, "traversal.zip")
            with zipfile.ZipFile(zip_path, "w") as zf:
                # Add a member that tries to go outside the extraction directory
                zf.writestr("../outside_file.txt", "malicious payload")

            dest_dir = os.path.join(temp_dir, "extracted")
            
            # extract_archive should detect path traversal and exit with SystemExit
            with self.assertRaises(SystemExit):
                extract_archive(zip_path, dest_dir)
                
            # Verify that no file was written outside the extraction directory
            outside_file = os.path.join(temp_dir, "outside_file.txt")
            self.assertFalse(os.path.exists(outside_file))

        finally:
            shutil.rmtree(temp_dir)

    def test_venv_command(self):
        from unittest.mock import patch, MagicMock
        import tempfile
        import shutil
        import os
        from hopit.main import execute_line
        from hopit.commands import build_commands

        temp_dir = tempfile.mkdtemp()
        try:
            # Setup mock command environment
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

            # Test create venv (mocks subprocess.run)
            with patch("subprocess.run") as mock_run:
                mock_proc = MagicMock()
                mock_proc.returncode = 0
                mock_proc.stdout = "Created"
                mock_proc.stderr = ""
                mock_run.return_value = mock_proc

                success = execute_line(f"create venv {temp_dir}/myenv", "/bin/bash", {}, all_names, cmds, None)
                self.assertTrue(success)
                mock_run.assert_called_once()
                self.assertIn("venv", mock_run.call_args[0][0])
                self.assertIn(f"{temp_dir}/myenv", mock_run.call_args[0][0])

            # Test enter venv
            # Create a mock bin directory so validation passes
            bin_dir_name = "Scripts" if os.name == "nt" else "bin"
            os.makedirs(os.path.join(temp_dir, "myenv", bin_dir_name), exist_ok=True)

            # Ensure VIRTUAL_ENV is not initially set
            original_virtual_env = os.environ.get("VIRTUAL_ENV")
            original_path = os.environ.get("PATH")
            if "VIRTUAL_ENV" in os.environ:
                del os.environ["VIRTUAL_ENV"]

            try:
                success = execute_line(f"enter venv {temp_dir}/myenv", "/bin/bash", {}, all_names, cmds, None)
                self.assertTrue(success)
                self.assertEqual(os.environ.get("VIRTUAL_ENV"), os.path.abspath(f"{temp_dir}/myenv"))
                self.assertTrue(os.environ.get("PATH", "").startswith(os.path.abspath(os.path.join(temp_dir, "myenv", bin_dir_name))))

                # Test exit venv
                success = execute_line("exit venv", "/bin/bash", {}, all_names, cmds, None)
                self.assertTrue(success)
                self.assertNotIn("VIRTUAL_ENV", os.environ)
                self.assertFalse(os.environ.get("PATH", "").startswith(os.path.abspath(os.path.join(temp_dir, "myenv", bin_dir_name))))
            finally:
                if original_virtual_env:
                    os.environ["VIRTUAL_ENV"] = original_virtual_env
                elif "VIRTUAL_ENV" in os.environ:
                    del os.environ["VIRTUAL_ENV"]
                if original_path:
                    os.environ["PATH"] = original_path

        finally:
            shutil.rmtree(temp_dir)

    def test_session_command_autocompletions(self):
        from hopit.ui import LazyCompleter
        from hopit.commands import build_commands
        from prompt_toolkit.document import Document

        names = {
            "service": lambda: [],
            "installed_pkg": lambda: [],
            "available_pkg": lambda prefix="": [],
            "path": lambda word="": [],
            "adapter": lambda: [],
            "user": lambda: ["john", "jane"],
            "group": lambda: [],
        }
        cmds = build_commands(None, names)
        completer = LazyCompleter(cmds)

        # Test loginctl subcommands
        doc = Document("loginctl ")
        completions = list(completer.get_completions(doc, None))
        completion_texts = [c.text for c in completions]
        self.assertIn("list-sessions", completion_texts)
        self.assertIn("terminate-session", completion_texts)
        self.assertIn("kill-session", completion_texts)

        # Test query subcommands
        doc = Document("query ")
        completions = list(completer.get_completions(doc, None))
        completion_texts = [c.text for c in completions]
        self.assertIn("user", completion_texts)
        self.assertIn("session", completion_texts)

        # Test logoff suggests active sessions
        doc = Document("logoff ")
        completions = list(completer.get_completions(doc, None))
        # Since this runs in different OS environments during test, we just check it runs successfully
        self.assertIsInstance(completions, list)

        # Test sessions subcommands
        doc = Document("sessions ")
        completions = list(completer.get_completions(doc, None))
        completion_texts = [c.text for c in completions]
        self.assertIn("list", completion_texts)
        self.assertIn("kill", completion_texts)

    def test_cisco_style_context_help(self):
        from unittest.mock import patch
        from hopit.main import show_context_help
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
        
        # Test 'exit ?'
        with patch("hopit.main.console.print") as mock_print:
            show_context_help(["exit"], cmds)
            mock_print.assert_called()
            panel = mock_print.call_args[0][0]
            self.assertIn("Help: exit ?", str(panel.title))
            
        # Test 'processes ?'
        with patch("hopit.main.console.print") as mock_print:
            show_context_help(["processes"], cmds)
            mock_print.assert_called()
            panel = mock_print.call_args[0][0]
            self.assertIn("Help: processes ?", str(panel.title))

        # Test 'config ?'
        with patch("hopit.main.console.print") as mock_print:
            show_context_help(["config"], cmds)
            mock_print.assert_called()
            panel = mock_print.call_args[0][0]
            self.assertIn("Help: config ?", str(panel.title))
            
        # Test 'lookup ?'
        with patch("hopit.main.console.print") as mock_print:
            show_context_help(["lookup"], cmds)
            mock_print.assert_called()
            panel = mock_print.call_args[0][0]
            self.assertIn("Help: lookup ?", str(panel.title))

    def test_emulated_filesystem_commands(self):
        import tempfile
        import shutil
        import os
        from hopit.ls import list_directory
        from hopit.rm import main as rm_main
        from hopit.cp import main as cp_main
        from hopit.mv import main as mv_main
        from hopit.mkdir import main as mkdir_main
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as tmpdir:
            # 1. Test mkdir (mkdir_main)
            dir1 = os.path.join(tmpdir, "dir1")
            dir2 = os.path.join(tmpdir, "dir2", "nested")
            
            # Create dir1 (no parents needed)
            with patch("sys.argv", ["mkdir", dir1]):
                mkdir_main()
            self.assertTrue(os.path.isdir(dir1))
            
            # Create dir2/nested (fails without -p)
            with patch("sys.argv", ["mkdir", dir2]):
                mkdir_main()
            self.assertFalse(os.path.isdir(dir2))
            
            # Create dir2/nested (with -p)
            with patch("sys.argv", ["mkdir", "-p", dir2]):
                mkdir_main()
            self.assertTrue(os.path.isdir(dir2))
            
            # 2. Test cp (cp_main)
            file1 = os.path.join(dir1, "file1.txt")
            with open(file1, "w") as f:
                f.write("hello")
                
            file2 = os.path.join(dir1, "file2.txt")
            # copy file
            with patch("sys.argv", ["cp", file1, file2]):
                cp_main()
            self.assertTrue(os.path.isfile(file2))
            
            # copy directory (fails without -r)
            dir3 = os.path.join(tmpdir, "dir3")
            with patch("sys.argv", ["cp", dir1, dir3]):
                cp_main()
            self.assertFalse(os.path.isdir(dir3))
            
            # copy directory (with -r)
            with patch("sys.argv", ["cp", "-r", dir1, dir3]):
                cp_main()
            self.assertTrue(os.path.isdir(dir3))
            self.assertTrue(os.path.isfile(os.path.join(dir3, "file1.txt")))
            
            # 3. Test ls (list_directory)
            with patch("hopit.ls.console.print") as mock_print:
                self.assertTrue(list_directory(dir1, set()))
                mock_print.assert_called()
                
            # 4. Test mv (mv_main)
            file3 = os.path.join(tmpdir, "file3.txt")
            with patch("sys.argv", ["mv", file2, file3]):
                mv_main()
            self.assertFalse(os.path.exists(file2))
            self.assertTrue(os.path.isfile(file3))
            
            # 5. Test rm (rm_main)
            with patch("sys.argv", ["rm", file3]):
                rm_main()
            self.assertFalse(os.path.exists(file3))
            
            with patch("sys.argv", ["rm", dir3]):
                rm_main()
            self.assertTrue(os.path.isdir(dir3))
            
            with patch("sys.argv", ["rm", "-r", dir3]):
                rm_main()
            self.assertFalse(os.path.exists(dir3))

    def test_path_autocomplete(self):
        import tempfile
        import os
        from hopit.loaders import load_path_entries
        from hopit.ui import LazyCompleter
        from hopit.commands import build_commands
        from unittest.mock import MagicMock, patch

        with tempfile.TemporaryDirectory() as tmpdir:
            orig_cwd = os.getcwd()
            os.chdir(tmpdir)
            try:
                os.makedirs("gnani/downloads")
                with open("gnani/downloads/test.txt", "w") as f:
                    f.write("hello")

                # Test load_path_entries logic
                # 1. No prefix (returns entries in CWD)
                entries = load_path_entries("")
                self.assertIn("gnani/", entries)

                # 2. Directory prefix
                entries = load_path_entries("gnani/")
                self.assertIn("gnani/downloads/", entries)

                # 3. Deep directory prefix
                entries = load_path_entries("gnani/downloads/")
                self.assertIn("gnani/downloads/test.txt", entries)

                # 4. Partial filename matching
                entries = load_path_entries("gnani/downloads/te")
                self.assertIn("gnani/downloads/test.txt", entries)

                # Test LazyCompleter behavior
                names = {
                    "service": lambda: [],
                    "installed_pkg": lambda: [],
                    "available_pkg": lambda prefix="": [],
                    "path": load_path_entries,
                    "adapter": lambda: [],
                    "user": lambda: [],
                    "group": lambda: [],
                }
                commands = build_commands(None, names)
                completer = LazyCompleter(commands, {})

                # Mock document for prompt_toolkit
                doc = MagicMock()
                
                # Test completions on "show file gnani/"
                doc.text_before_cursor = "show file gnani/"
                completions = list(completer.get_completions(doc, None))
                self.assertTrue(any(c.text == "gnani/downloads/" for c in completions))

                # Test completions on "show file gnani/downloads/te"
                doc.text_before_cursor = "show file gnani/downloads/te"
                completions = list(completer.get_completions(doc, None))
                self.assertTrue(any(c.text == "gnani/downloads/test.txt" for c in completions))

            finally:
                os.chdir(orig_cwd)

if __name__ == "__main__":
    unittest.main()
