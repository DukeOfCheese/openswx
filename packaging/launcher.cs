// Small launcher installed as openswx.exe / openswx-cli.exe: the PyInstaller runtime (its exe and _internal)
// lives in %LOCALAPPDATA%\openswx\runtime, so the install folder holds only these launchers.
// Build (no SDK needed, .NET Framework 4 ships with Windows; packaging/build.py runs these):
//   csc /target:winexe /r:System.Windows.Forms.dll /win32icon:openswx.ico /out:openswx.exe launcher.cs
//   csc /target:exe /define:CLI /win32icon:openswx.ico /out:openswx-cli.exe launcher.cs   (console: mcp, sldinfo)
// The runtime folder: OPENSWX_RUNTIME, else HKCU\Software\openswx\RuntimeDir (the installer writes it),
// else %LOCALAPPDATA%\openswx\runtime.
using System;
using System.Diagnostics;
using System.IO;
using System.Text;
using System.Reflection;
using Microsoft.Win32;

// a version resource (name, product, publisher) like the rest of the install: an exe without one is more
// likely to be flagged by antivirus heuristics, and Windows shows these in Task Manager and file properties
[assembly: AssemblyProduct("openswx")]
[assembly: AssemblyCompany("openswx contributors")]
[assembly: AssemblyCopyright("Apache-2.0")]
#if CLI
[assembly: AssemblyTitle("openswx command line")]
#else
[assembly: AssemblyTitle("openswx")]
#endif

static class Launcher
{
#if CLI
    const string Target = "openswx-cli.exe";
#else
    const string Target = "openswx.exe";
#endif

    static string RuntimeDir()
    {
        string env = Environment.GetEnvironmentVariable("OPENSWX_RUNTIME");
        if (!string.IsNullOrEmpty(env)) return env;
        using (RegistryKey k = Registry.CurrentUser.OpenSubKey(@"Software\openswx"))
        {
            string v = k == null ? null : k.GetValue("RuntimeDir") as string;
            if (!string.IsNullOrEmpty(v)) return v;
        }
        return Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), @"openswx\runtime");
    }

    // Windows command-line quoting (CommandLineToArgvW rules) so arguments reach the app unchanged
    static string Quote(string a)
    {
        if (a.Length > 0 && a.IndexOfAny(new[] { ' ', '\t', '"' }) < 0) return a;
        var sb = new StringBuilder("\"");
        int slashes = 0;
        foreach (char c in a)
        {
            if (c == '\\') { slashes++; continue; }
            sb.Append('\\', c == '"' ? slashes * 2 + 1 : slashes);
            slashes = 0;
            sb.Append(c);
        }
        sb.Append('\\', slashes * 2).Append('"');
        return sb.ToString();
    }

    [STAThread]
    static int Main(string[] args)
    {
        string exe = Path.Combine(RuntimeDir(), Target);
        if (!File.Exists(exe))
        {
            string msg = "openswx runtime not found at " + exe + ". Reinstall openswx.";
#if CLI
            Console.Error.WriteLine(msg);
#else
            System.Windows.Forms.MessageBox.Show(msg, "openswx");
#endif
            return 9009;
        }
        var parts = new string[args.Length];
        for (int i = 0; i < args.Length; i++) parts[i] = Quote(args[i]);
        // no shell, no redirection: the child inherits this process's console and standard handles (MCP stdio)
        var psi = new ProcessStartInfo(exe, string.Join(" ", parts)) { UseShellExecute = false };
        psi.WorkingDirectory = Environment.CurrentDirectory;
        // so `openswx-cli register-file-types` points file types at the launchers, not the runtime
        psi.EnvironmentVariables["OPENSWX_LAUNCHER_DIR"] = AppDomain.CurrentDomain.BaseDirectory;
        using (Process p = Process.Start(psi))
        {
#if CLI
            p.WaitForExit();
            return p.ExitCode;
#else
            return 0;
#endif
        }
    }
}
