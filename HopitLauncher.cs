using System;
using System.Diagnostics;
using System.IO;
using System.Reflection;

namespace HopitLauncher
{
    class Program
    {
        static void Main(string[] args)
        {
            // Get the directory where this launcher is installed
            string baseDir = AppDomain.CurrentDomain.BaseDirectory;
            
            // Path to the bundled standalone Python environment
            string pythonPath = Path.Combine(baseDir, "python", "python.exe");
            string scriptPath = Path.Combine(baseDir, "src", "hopit-cli.py");

            string userHome = Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);

            ProcessStartInfo startInfo = new ProcessStartInfo
            {
                FileName = "cmd.exe",
                // Change to User Home directory, enable UTF-8 for icons (chcp 65001), then run python
                Arguments = "/c \"cd /d \"" + userHome + "\" && chcp 65001 >nul && \"" + pythonPath + "\" \"" + scriptPath + "\" & echo. & echo Program exited. & pause \"",
                UseShellExecute = true,
                Verb = "runas"
            };

            try
            {
                Process.Start(startInfo);
            }
            catch (System.ComponentModel.Win32Exception)
            {
                // The user clicked "No" on the UAC Admin prompt. 
                // We just exit silently.
            }
        }
    }
}
