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

if __name__ == "__main__":
    unittest.main()
