import unittest
from hopit.translation import (
    translate_chmod_to_windows,
    translate_chown_to_windows,
    translate_chgrp_to_windows,
    translate_useradd_to_windows,
    translate_userdel_to_windows,
    translate_passwd_to_windows,
    translate_usermod_to_windows,
    translate_usermod_to_mac,
    translate_groupadd_to_windows,
    translate_groupdel_to_windows,
    translate_win_net_to_unix,
)

class TestUserPermsTranslation(unittest.TestCase):
    def test_chmod_to_windows(self):
        # Numeric perms
        cmd1 = translate_chmod_to_windows(["755", "C:\\test"])
        self.assertIn("icacls", cmd1)
        self.assertIn("/grant:r *S-1-5-32-544:", cmd1)  # Admins/Owner
        self.assertIn("/grant:r *S-1-5-32-545:", cmd1)  # Users
        self.assertIn("/grant:r *S-1-1-0:", cmd1)       # Everyone
        
        # Recursive chmod
        cmd2 = translate_chmod_to_windows(["-R", "644", "C:\\test"])
        self.assertIn(" /t", cmd2)
        
        # Symbolic perms
        cmd3 = translate_chmod_to_windows(["g+w", "C:\\test"])
        self.assertIn("/grant:r *S-1-5-32-545:", cmd3)
        self.assertIn("(W)", cmd3)

    def test_chown_chgrp_to_windows(self):
        cmd_chown = translate_chown_to_windows(["admin", "C:\\test"])
        self.assertEqual(cmd_chown, "icacls 'C:\\test' /setowner 'admin'")
        
        cmd_chown_r = translate_chown_to_windows(["-R", "admin:group", "C:\\test"])
        self.assertEqual(cmd_chown_r, "icacls 'C:\\test' /setowner 'admin' /t")
        
        cmd_chgrp = translate_chgrp_to_windows(["group", "C:\\test"])
        self.assertEqual(cmd_chgrp, "icacls 'C:\\test' /setowner 'group'")

    def test_user_group_mgmt_to_windows(self):
        self.assertEqual(translate_useradd_to_windows(["john"]), "net user 'john' /add")
        self.assertEqual(translate_useradd_to_windows(["john", "pw123"]), "net user 'john' 'pw123' /add")
        self.assertEqual(translate_userdel_to_windows(["john"]), "net user 'john' /delete")
        self.assertEqual(translate_passwd_to_windows(["john", "newpw"]), "net user 'john' 'newpw'")
        self.assertEqual(translate_passwd_to_windows(["john"]), "net user 'john' *")
        
        # usermod -L/U
        self.assertEqual(translate_usermod_to_windows(["-L", "john"]), "net user 'john' /active:no")
        self.assertEqual(translate_usermod_to_windows(["-U", "john"]), "net user 'john' /active:yes")
        # usermod add group
        self.assertEqual(translate_usermod_to_windows(["-aG", "admin", "john"]), "net localgroup 'admin' 'john' /add")
        
        # groupadd/del
        self.assertEqual(translate_groupadd_to_windows(["mygroup"]), "net localgroup 'mygroup' /add")
        self.assertEqual(translate_groupdel_to_windows(["mygroup"]), "net localgroup 'mygroup' /delete")

    def test_usermod_to_mac(self):
        self.assertEqual(translate_usermod_to_mac(["-L", "john"]), "dscl . -create /Users/'john' UserShell /usr/bin/false")
        self.assertEqual(translate_usermod_to_mac(["-U", "john"]), "dscl . -create /Users/'john' UserShell /bin/bash")
        self.assertEqual(translate_usermod_to_mac(["-aG", "admin", "john"]), "dseditgroup -o edit -a 'john' -t user 'admin'")

    def test_win_net_to_unix(self):
        self.assertTrue(callable(translate_win_net_to_unix))
        
        # Net user translation
        cmd_list = translate_win_net_to_unix(["user"])
        self.assertTrue("dscl" in cmd_list or "passwd" in cmd_list)
        
        # Let's check with specific flag
        cmd_del = translate_win_net_to_unix(["user", "john", "/delete"])
        self.assertTrue("userdel" in cmd_del or "sysadminctl" in cmd_del)
        
        cmd_add = translate_win_net_to_unix(["user", "john", "pw123", "/add"])
        self.assertTrue("useradd" in cmd_add or "sysadminctl" in cmd_add)
        
        # Net localgroup
        cmd_gadd = translate_win_net_to_unix(["localgroup", "mygroup", "/add"])
        self.assertTrue("groupadd" in cmd_gadd or "dseditgroup" in cmd_gadd)
        
        cmd_gdel = translate_win_net_to_unix(["localgroup", "mygroup", "/delete"])
        self.assertTrue("groupdel" in cmd_gdel or "dseditgroup" in cmd_gdel)
        
        cmd_gmember = translate_win_net_to_unix(["localgroup", "mygroup", "john", "/add"])
        self.assertTrue("usermod" in cmd_gmember or "dseditgroup" in cmd_gmember)


if __name__ == "__main__":
    unittest.main()
