# Windows 使用说明
本说明介绍校园网自动连接程序的账号配置、后台运行和登录 Windows 后自启动操作。

## 快速开始

1. 将整个 ZIP 解压到固定目录，保留 EXE 旁的 `_internal` 文件夹。
2. 双击 `configure.cmd`，填写学号和密码，将 `CAMPUS_ADAPTER` 设为 `ecjtu`。
3. 将 `CAMPUS_ISP_SUFFIX` 设为移动 `@cmcc`、电信 `@telecom` 或联通 `@unicom`；学号不带后缀。
4. 保存配置后双击 `campus-auto-login.exe`，程序在后台运行。

## 使用

在 `logs/campus.log` 中查看联网检查状态，`INTERNET_OK` 表示联网检查通过。修改配置后需在任务管理器结束程序再打开；不要重复启动。

双击 `install-startup.cmd` 创建当前用户的登录计划任务，安装成功后保留程序目录。此入口会迁移本目录的旧启动快捷方式；系统策略拒绝任务创建时，保留旧快捷方式并显示错误。取消自启动使用 `remove-startup.cmd`，不会结束正在运行的进程。

要移动程序目录，先在旧目录取消自启动，移动后再安装。完整运行包无需安装 Python。

## 配置与排障

账号设置见[首页配置](README.md#配置)。已有 `.env` 不会被配置入口覆盖。无法登录时检查账号、密码、运营商后缀与 `CAMPUS_ADAPTER`；使用代理软件的 TUN 模式时可先关闭再检查。

程序为非官方工具，仅用于本人有权使用的校园网账号，不绕过认证、缴费或访问限制。请遵守学校及运营商规定，妥善保管本机 `.env`，不要上传密码或个人日志；校园网系统变化可能影响可用性。

问题反馈：[GitHub Issues](https://github.com/DUSK1NG/ecjtu-auto-login/issues)。邮箱：[jk1ng@qq.com](mailto:jk1ng@qq.com)。
