// =====================================================================
//  ATE Runner - standalone setup (no Inno Setup required)
//
//  Compiled with the .NET Framework C# compiler that ships with Windows:
//     csc.exe /target:winexe /codepage:65001 /platform:anycpu ^
//         /out:ATE_Setup-<ver>.exe /resource:payload.zip,payload.zip ^
//         /r:System.IO.Compression.FileSystem.dll /r:System.Windows.Forms.dll ^
//         /r:System.Drawing.dll setup.cs
//
//  What it does (all offline, no Python/Node needed on the target machine):
//     1. unpack the embedded payload.zip (app/ web/ runtime/ + launchers)
//     2. create Desktop + Start Menu shortcuts
//     3. register an uninstall entry (HKCU, no admin needed)
//     4. optionally open TCP 8000 in the firewall (only when elevated)
//     5. optionally launch ATE Runner
//
//  Silent usage (batch deployment):
//     ATE_Setup-1.0.exe /S /DIR=D:\ATERunner /NODESKTOP /NOFIREWALL /NOLAUNCH
// =====================================================================
using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.IO.Compression;
using System.Reflection;
using System.Windows.Forms;
using Microsoft.Win32;

static class AteSetup
{
    const string AppName = "ATE Runner";
    const string ExeName = "ATE_Launcher.exe";
    const string FallbackBat = "start-ate.bat";
    const string DefaultDir = @"C:\ATERunner";
    const string UninstallKey = @"Software\Microsoft\Windows\CurrentVersion\Uninstall\ATERunner";

    static bool silent;
    static string targetDir = DefaultDir;
    static bool wantFirewall = true;
    static bool wantDesktop = true;
    static bool wantLaunch = true;
    static Form form;
    static TextBox logBox;
    static ProgressBar bar;
    static Button closeBtn;

    [STAThread]
    static void Main(string[] args)
    {
        foreach (string raw in args)
        {
            string a = (raw ?? "").Trim().Trim('"');
            if (a.Equals("/S", StringComparison.OrdinalIgnoreCase) ||
                a.Equals("/SILENT", StringComparison.OrdinalIgnoreCase) ||
                a.Equals("/VERYSILENT", StringComparison.OrdinalIgnoreCase)) silent = true;
            else if (a.StartsWith("/DIR=", StringComparison.OrdinalIgnoreCase)) targetDir = a.Substring(5).Trim('"');
            else if (a.Equals("/NOFIREWALL", StringComparison.OrdinalIgnoreCase)) wantFirewall = false;
            else if (a.Equals("/NODESKTOP", StringComparison.OrdinalIgnoreCase)) wantDesktop = false;
            else if (a.Equals("/NOLAUNCH", StringComparison.OrdinalIgnoreCase)) wantLaunch = false;
        }
        if (targetDir.Length == 0) targetDir = DefaultDir;

        if (silent) { Environment.Exit(Run(null)); }

        Application.EnableVisualStyles();
        Application.SetCompatibleTextRenderingDefault(false);
        form = new Form();
        form.Text = AppName + " 安装程序";
        form.ClientSize = new Size(560, 360);
        form.StartPosition = FormStartPosition.CenterScreen;
        form.FormBorderStyle = FormBorderStyle.FixedDialog;
        form.MaximizeBox = false;

        Label title = new Label();
        title.Text = "安装 " + AppName;
        title.Font = new Font("Microsoft YaHei UI", 14F, FontStyle.Bold);
        title.SetBounds(18, 14, 520, 32);
        form.Controls.Add(title);

        Label sub = new Label();
        sub.Text = "安装目录：" + targetDir + "\r\n包内含前端、后端与嵌入式 Python 运行时，目标机无需安装 Python / Node。";
        sub.SetBounds(20, 48, 520, 44);
        form.Controls.Add(sub);

        logBox = new TextBox();
        logBox.Multiline = true;
        logBox.ReadOnly = true;
        logBox.ScrollBars = ScrollBars.Vertical;
        logBox.SetBounds(20, 96, 520, 196);
        logBox.BackColor = Color.FromArgb(252, 251, 255);
        form.Controls.Add(logBox);

        bar = new ProgressBar();
        bar.SetBounds(20, 302, 520, 16);
        bar.Maximum = 100;
        form.Controls.Add(bar);

        closeBtn = new Button();
        closeBtn.Text = "关闭";
        closeBtn.SetBounds(452, 326, 88, 26);
        closeBtn.Enabled = false;
        closeBtn.Click += delegate { form.Close(); };
        form.Controls.Add(closeBtn);

        form.Shown += delegate { Environment.ExitCode = Run(Log); };
        Application.Run(form);
    }

    static void Log(string msg)
    {
        if (logBox != null)
        {
            logBox.AppendText(msg + "\r\n");
            logBox.SelectionStart = logBox.TextLength;
            logBox.ScrollToCaret();
            Application.DoEvents();
        }
        Console.WriteLine(msg);
    }

    static void Pct(int v)
    {
        if (bar != null) { bar.Value = Math.Max(0, Math.Min(100, v)); Application.DoEvents(); }
    }

    // 返回 0 = 成功
    static int Run(Action<string> log)
    {
        try
        {
            if (log == null) log = delegate (string s) { };
            log("目标目录: " + targetDir);
            Directory.CreateDirectory(targetDir);

            log("校验写权限 …");
            string probe = Path.Combine(targetDir, ".ate_write_test");
            File.WriteAllText(probe, "ok");
            File.Delete(probe);

            log("解压程序文件（前端 web/ + 后端 app/ + 运行时 runtime/）…");
            ExtractPayload(targetDir, log);

            log("创建快捷方式 …");
            try
            {
                string exe = Path.Combine(targetDir, ExeName);
                string launchTarget = File.Exists(exe) ? exe : Path.Combine(targetDir, FallbackBat);
                MakeShortcut(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory),
                                          AppName + ".lnk"), launchTarget, targetDir);
                if (wantDesktop) { /* already created above */ }
                string startMenu = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Programs), AppName);
                Directory.CreateDirectory(startMenu);
                MakeShortcut(Path.Combine(startMenu, AppName + ".lnk"), launchTarget, targetDir);
                MakeShortcut(Path.Combine(startMenu, "卸载 " + AppName + ".lnk"),
                             Path.Combine(targetDir, "uninstall.bat"), targetDir);
            }
            catch (Exception e) { log("  [警告] 快捷方式创建失败: " + e.Message); }

            log("注册卸载信息 …");
            try
            {
                using (RegistryKey k = Registry.CurrentUser.CreateSubKey(UninstallKey))
                {
                    k.SetValue("DisplayName", AppName);
                    k.SetValue("DisplayVersion", VersionOf());
                    k.SetValue("Publisher", "ATE");
                    k.SetValue("InstallLocation", targetDir);
                    k.SetValue("UninstallString", "\"" + Path.Combine(targetDir, "uninstall.bat") + "\"");
                    k.SetValue("DisplayIcon", Path.Combine(targetDir, ExeName));
                    k.SetValue("NoModify", 1, RegistryValueKind.DWord);
                    k.SetValue("NoRepair", 1, RegistryValueKind.DWord);
                }
            }
            catch (Exception e) { log("  [警告] 写注册表失败: " + e.Message); }

            if (wantFirewall)
            {
                log("放行 TCP 8000（需管理员权限，失败不影响使用）…");
                try
                {
                    ProcessStartInfo psi = new ProcessStartInfo("netsh",
                        "advfirewall firewall add rule name=\"ATE Runner (8000)\" dir=in action=allow protocol=TCP localport=8000");
                    psi.CreateNoWindow = true; psi.UseShellExecute = false;
                    psi.RedirectStandardOutput = true; psi.RedirectStandardError = true;
                    Process p = Process.Start(psi);
                    p.WaitForExit(8000);
                    log(p.ExitCode == 0 ? "  已添加防火墙规则" : "  跳过（权限不足或规则已存在）");
                }
                catch (Exception e) { log("  [警告] 防火墙规则未添加: " + e.Message); }
            }

            Pct(100);
            log("");
            log("安装完成 → " + targetDir);
            log("双击桌面快捷方式「" + AppName + "」启动；界面会自动在浏览器中打开（默认 http://127.0.0.1:8000）。");
            log("测试数据（logs\\ workspace\\ app\\ate.db）都保存在安装目录内，卸载时可选择保留。");
            if (closeBtn != null) { closeBtn.Enabled = true; closeBtn.Text = "完成"; }

            if (wantLaunch) { try { LaunchInstalled(); } catch { } }
            return 0;
        }
        catch (Exception ex)
        {
            if (logBox != null)
            {
                MessageBox.Show("安装失败：" + ex.Message, AppName, MessageBoxButtons.OK, MessageBoxIcon.Error);
                if (closeBtn != null) closeBtn.Enabled = true;
            }
            Console.Error.WriteLine("setup failed: " + ex);
            return 1;
        }
    }

    static string VersionOf()
    {
        try
        {
            object[] attrs = Assembly.GetExecutingAssembly()
                .GetCustomAttributes(typeof(AssemblyInformationalVersionAttribute), false);
            if (attrs != null && attrs.Length > 0)
                return ((AssemblyInformationalVersionAttribute)attrs[0]).InformationalVersion;
            return Assembly.GetExecutingAssembly().GetName().Version.ToString();
        }
        catch { return "1.0.0.0"; }
    }

    static void LaunchInstalled()
    {
        string exe = Path.Combine(targetDir, ExeName);
        if (File.Exists(exe)) { Process.Start(new ProcessStartInfo(exe) { WorkingDirectory = targetDir }); return; }
        string bat = Path.Combine(targetDir, FallbackBat);
        if (File.Exists(bat)) Process.Start(new ProcessStartInfo(bat) { WorkingDirectory = targetDir });
    }

    static void MakeShortcut(string lnkPath, string targetPath, string workDir)
    {
        Type t = Type.GetTypeFromProgID("WScript.Shell");
        object shell = Activator.CreateInstance(t);
        object shortcut = t.InvokeMember("CreateShortcut", BindingFlags.InvokeMethod, null, shell, new object[] { lnkPath });
        Type st = shortcut.GetType();
        st.InvokeMember("TargetPath", BindingFlags.SetProperty, null, shortcut, new object[] { targetPath });
        st.InvokeMember("WorkingDirectory", BindingFlags.SetProperty, null, shortcut, new object[] { workDir });
        st.InvokeMember("Description", BindingFlags.SetProperty, null, shortcut, new object[] { AppName });
        st.InvokeMember("IconLocation", BindingFlags.SetProperty, null, shortcut, new object[] { targetPath + ",0" });
        st.InvokeMember("Save", BindingFlags.InvokeMethod, null, shortcut, null);
    }

    static void ExtractPayload(string destDir, Action<string> log)
    {
        Assembly asm = Assembly.GetExecutingAssembly();
        string resName = null;
        foreach (string n in asm.GetManifestResourceNames())
            if (n.EndsWith("payload.zip", StringComparison.OrdinalIgnoreCase)) { resName = n; break; }
        if (resName == null) throw new Exception("安装包内部缺少 payload.zip 资源");

        using (Stream s = asm.GetManifestResourceStream(resName))
        using (ZipArchive zip = new ZipArchive(s, ZipArchiveMode.Read))
        {
            int total = zip.Entries.Count, done = 0;
            string root = Path.GetFullPath(destDir).TrimEnd('\\') + "\\";
            foreach (ZipArchiveEntry e in zip.Entries)
            {
                string rel = e.FullName.Replace('/', '\\');
                string full = Path.GetFullPath(Path.Combine(root, rel));
                if (!full.StartsWith(root, StringComparison.OrdinalIgnoreCase))
                    throw new Exception("压缩包内路径非法: " + e.FullName);
                if (rel.EndsWith("\\"))
                {
                    Directory.CreateDirectory(full);
                }
                else
                {
                    Directory.CreateDirectory(Path.GetDirectoryName(full));
                    e.ExtractToFile(full, true);
                }
                done++;
                if (total > 0 && done % 25 == 0) Pct((int)(done * 90.0 / total));
            }
            log("  解压完成，共 " + total + " 项");
        }
    }
}
