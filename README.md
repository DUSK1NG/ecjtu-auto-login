# 华东交通大学校园网自动连接
Windows 校园网自动认证工具，用于连接华东交通大学有线网或 Wi-Fi 后，以用户自己的移动、电信或联通账号登录。

## 快速开始

从[发布页](https://github.com/DUSK1NG/ecjtu-auto-login/releases/latest)下载 `campus-auto-login-windows.zip`，完整解压到固定目录。双击 `configure.cmd` 填写账号配置，保存后运行 `campus-auto-login.exe`；程序在后台运行，不显示窗口。

## 使用

查看 `logs/campus.log`，`INTERNET_OK` 表示联网检查通过。请勿重复启动；修改配置后，在任务管理器结束程序再重新打开。

需要登录 Windows 后自动启动时，双击 `install-startup.cmd`；取消自启动使用 `remove-startup.cmd`，不会结束当前进程。移动目录前先取消自启动，移动后重新安装。完整步骤与排障见 [Windows 使用说明](WINDOWS-README.md)。

## 配置

在配置入口打开的 `.env` 中填写：

```dotenv
CAMPUS_ADAPTER=ecjtu
CAMPUS_USERNAME=你的学号
CAMPUS_PASSWORD=你的密码
CAMPUS_ISP_SUFFIX=@cmcc
CHECK_INTERVAL=10
```

移动后缀为 `@cmcc`，电信为 `@telecom`，联通为 `@unicom`；账号建议只填学号。账号密码保存在本机，反馈时不要上传 `.env` 或包含个人信息的日志。

## 开发

源码运行、测试和 Windows 打包见[开发说明](docs/development.md)。

本项目为非官方工具，与学校及运营商无隶属关系。仅用于有权使用的账号，不绕过认证、缴费或访问限制；使用须遵守学校及运营商规定。校园网变化可能影响可用性。
