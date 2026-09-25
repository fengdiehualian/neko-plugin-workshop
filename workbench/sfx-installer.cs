using System;
using System.IO;
using System.IO.Compression;
using System.Reflection;
using System.Diagnostics;
using System.Windows.Forms;

class SfxInstaller
{
    [STAThread]
    static int Main(string[] args)
    {
        bool silent = args.Length > 0 && args[0] == "--silent";
        string dest = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
            "Programs", "NEKOWorkshop");

        if (!silent)
        {
            var r = MessageBox.Show(
                "N.E.K.O. 插件工坊\n\n将安装到:\n" + dest + "\n\n(已安装过的旧版本会被覆盖)",
                "N.E.K.O. 插件工坊 安装程序", MessageBoxButtons.OKCancel, MessageBoxIcon.Information);
            if (r != DialogResult.OK) return 1;
        }

        try
        {
            // 升级安装:保留用户数据(runtime 里的会话/API 配置/工作区插件),只覆盖程序文件
            string runtimeDir = Path.Combine(dest, "runtime");
            string workspaceDir = Path.Combine(dest, "workspace");
            string backup = Path.Combine(Path.GetTempPath(), "NEKOWorkshop_user_" + Guid.NewGuid().ToString("N").Substring(0, 8));
            bool hasUserData = Directory.Exists(dest);
            if (hasUserData)
            {
                try
                {
                    Directory.CreateDirectory(backup);
                    if (Directory.Exists(runtimeDir)) Directory.Move(runtimeDir, Path.Combine(backup, "runtime"));
                    if (Directory.Exists(workspaceDir)) Directory.Move(workspaceDir, Path.Combine(backup, "workspace"));
                }
                catch { hasUserData = false; }
            }
            if (Directory.Exists(dest)) { try { Directory.Delete(dest, true); } catch { } }
            Directory.CreateDirectory(dest);

            using (var zip = new ZipArchive(Assembly.GetExecutingAssembly()
                .GetManifestResourceStream("payload.zip"), ZipArchiveMode.Read))
            {
                foreach (var e in zip.Entries)
                {
                    string rel = e.FullName.Replace('/', Path.DirectorySeparatorChar).TrimEnd('\\', '/');
                    if (rel.Length == 0) continue;
                    string target = Path.Combine(dest, rel);
                    if (e.FullName.EndsWith("/") || e.FullName.EndsWith("\\"))
                    {
                        Directory.CreateDirectory(target);
                        continue;
                    }
                    Directory.CreateDirectory(Path.GetDirectoryName(target));
                    e.ExtractToFile(target, true);
                }
            }

            // 回填用户数据:runtime 里出厂自带的空结构让位给备份(有备份才回填,避免覆盖新程序自带的目录)
            if (hasUserData && Directory.Exists(backup))
            {
                try
                {
                    string newRuntime = Path.Combine(backup, "runtime");
                    string newWorkspace = Path.Combine(backup, "workspace");
                    if (Directory.Exists(newRuntime))
                    {
                        if (Directory.Exists(runtimeDir)) Directory.Delete(runtimeDir, true);
                        Directory.Move(newRuntime, runtimeDir);
                    }
                    if (Directory.Exists(newWorkspace))
                    {
                        if (Directory.Exists(workspaceDir)) Directory.Delete(workspaceDir, true);
                        Directory.Move(newWorkspace, workspaceDir);
                    }
                    Directory.Delete(backup, true);
                }
                catch { }
            }

            // desktop shortcut
            string desktop = Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory);
            string lnk = Path.Combine(desktop, "N.E.K.O.插件工坊.lnk");
            string ps = "$ws = New-Object -ComObject WScript.Shell; $l = $ws.CreateShortcut('" +
                lnk.Replace("'", "''") + "'); $l.TargetPath = '" +
                Path.Combine(dest, "start.cmd").Replace("'", "''") + "'; $l.WorkingDirectory = '" +
                dest.Replace("'", "''") + "'; $l.IconLocation = '" +
                Path.Combine(dest, "_bin\\bun.exe").Replace("'", "''") + ",0'; $l.Save()";
            var psi = new ProcessStartInfo("powershell.exe", "-NoProfile -ExecutionPolicy Bypass -Command \"" + ps + "\"")
            { CreateNoWindow = true, UseShellExecute = false };
            Process.Start(psi).WaitForExit(10000);

            if (!silent)
            {
                MessageBox.Show("安装完成!工坊即将启动。\n\n(检测到旧版本,你的对话记录、API 配置和已生成的插件都已保留)", "N.E.K.O. 插件工坊",
                    MessageBoxButtons.OK, MessageBoxIcon.Information);
            }
            Process.Start(new ProcessStartInfo(Path.Combine(dest, "start.cmd"))
            { UseShellExecute = true });

            return 0;
        }
        catch (Exception ex)
        {
            if (!silent)
                MessageBox.Show("安装失败:\n" + ex.Message, "N.E.K.O. 插件工坊", MessageBoxButtons.OK, MessageBoxIcon.Error);
            File.WriteAllText(Path.Combine(Path.GetTempPath(), "nekosfx.log"), ex.ToString());
            return 2;
        }
    }
}