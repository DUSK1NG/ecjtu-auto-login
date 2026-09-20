# ecjtu-auto-login · 华东交通大学校园网自动连接程序

这是面向华东交通大学校园网的非官方自动连接程序。连接校园有线网或 Wi-Fi 后自动完成登录认证，支持移动、电信、联通，可设置开机后后台运行。

## 下载

在 [下载页面](https://github.com/DUSK1NG/ecjtu-auto-login/releases/latest) 选择 `campus-auto-login-windows.zip`。

将整个压缩包解压到固定目录，无需安装 Python。

## 配置账号

双击 `configure.cmd`，在打开的配置文件中填写：

```dotenv
CAMPUS_ADAPTER=ecjtu
CAMPUS_USERNAME=你的学号
CAMPUS_PASSWORD=你的密码
CAMPUS_ISP_SUFFIX=@cmcc
CHECK_INTERVAL=10
```

按自己的运营商修改后缀：

| 运营商 | CAMPUS_ISP_SUFFIX |
|---|---|
| 中国移动 | `@cmcc` |
| 中国电信 | `@telecom` |
| 中国联通 | `@unicom` |

账号建议只填学号，不带运营商后缀。保存并关闭配置文件。已有 `.env` 不会被配置入口覆盖。

## 启动程序

双击 `campus-auto-login.exe`，程序会在后台运行，不弹出窗口。

连接校园有线网或 Wi-Fi 后，程序会检查联网状态，需要时自动登录。查看 `logs/campus.log`，出现 `INTERNET_OK` 表示已经联网。

请勿重复启动。修改配置后，在任务管理器结束 `campus-auto-login.exe`，再重新打开。

## 设置开机自启动

双击 `install-startup.cmd`。安装成功后，下次登录 Windows 通过当前用户的计划任务立即启动（不设置启动延迟、不等待网络条件，电池供电也启动）。安装时会自动迁移本目录的旧启动快捷方式；如果系统策略拒绝创建任务，会保留原快捷方式并报告错误。

安装后请保留程序目录。需要移动目录时，先取消自启动，移动后再重新安装。

## 取消自启动或退出

- 取消自启动：双击 `remove-startup.cmd`。
- 退出当前程序：在任务管理器结束 `campus-auto-login.exe`。

取消自启动不会结束当前已经运行的程序。

## 无法登录时

确认账号、密码及运营商后缀填写正确，且 `CAMPUS_ADAPTER=ecjtu`。如果开启了代理软件的 TUN 模式，先关闭后重试。

## 从源码运行

已下载 Windows 版的用户无需执行本节。源码运行需要 Python 3.11 或更高版本。

```powershell
git clone https://github.com/DUSK1NG/ecjtu-auto-login.git
cd ecjtu-auto-login
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
notepad .env
.\.venv\Scripts\python.exe -m src.main
```

按上面的账号配置说明填写 `.env`，运行后按 `Ctrl+C` 退出。

## 连接速度与 Windows 打包

启动后立即检查网络；网卡未就绪或尚未识别学校环境时，每次检测结束后等待 1 秒重查。已联网时默认每 10 秒检查，`CHECK_INTERVAL` 仅控制已联网的检查间隔。

识别到学校门户重定向并核对当前路由 IP 后，立即进入认证，省去后续探针的超时等待。未知门户仍执行完整检测；认证后的成功状态仍须通过独立联网探针验证。探针连接/读取超时均为 1.5 秒；认证服务器保留原超时设置。认证失败后等待 2、3、5、10 秒重试，后续上限为 10 秒（请求耗时另计）。

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
.\build-windows.ps1
```

打包输出在 `dist/campus-auto-login/`。采用目录模式，省去原单文件程序每次启动时的临时解压；必须保留 exe 旁的 `_internal` 文件夹。实际开机联网时间仍取决于 Windows 登录、网卡就绪和学校服务器响应。

## 免责声明

本项目为个人开发的非官方工具，与华东交通大学及各运营商无隶属或授权关系，仅用于简化本人有权使用的校园网账号登录操作，不提供绕过认证、缴费或网络访问限制的功能。请遵守学校及运营商的网络使用规定。

程序按现状提供，不保证在所有设备或校园网系统更新后持续可用。请自行判断是否使用，并妥善保管保存在本机 `.env` 中的账号密码；反馈问题时不要上传密码或包含个人信息的日志。

## 联系方式

邮箱：[jk1ng@qq.com](mailto:jk1ng@qq.com)

问题反馈：[GitHub Issues](https://github.com/DUSK1NG/ecjtu-auto-login/issues)
