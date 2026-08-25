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

            ProcessStartInfo startInfo = new ProcessStartInfo
            {
                FileName = "cmd.exe",
                // /c runs the command and then we explicitly call 'pause' so the window stays open forever
                Arguments = $"/c \" \"{pythonPath}\" \"{scriptPath}\" & echo. & echo Program exited. & pause \"",
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
