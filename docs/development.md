# 源码运行与打包

## 环境

源码需要 Python 3.11 或更高版本。下载或克隆仓库后，从仓库根目录创建隔离环境：

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

将 `.env.example` 复制为 `.env`，按[首页配置](../README.md#配置)填写。模块入口是 `src.main`；常驻运行使用 `python -m src.main`，从上述虚拟环境的解释器启动，按 `Ctrl+C` 退出。

查看实际支持的选项：

```powershell
.venv\Scripts\python.exe -m src.main --help
```

输出中的命令用法：

```text
usage: main.py [-h] [--once] [--check-only]
```

`--once` 只执行一次检测；`--check-only` 强制只检查网络，不提交认证凭据，两者可组合。真实校园网认证与 Windows 登录计划任务应在对应环境中另行验证。

## 测试

```powershell
.venv\Scripts\python.exe -m pytest -q
```

一次实测输出：

```text
124 passed in 1.42s
```

测试使用伪造响应和临时配置，不需要真实账号。网络与认证参数定义在配置和适配器源码中；`CHECK_INTERVAL` 控制已联网时的检查间隔，不等同于认证重试间隔。

## Windows 打包

```powershell
.venv\Scripts\python.exe -m pip install -r requirements-build.txt
.\build-windows.ps1
```

一次实测的最后输出（仓库绝对路径省略）：

```text
Built …\dist\campus-auto-login. Distribute the whole folder, including _internal.
```

脚本先运行测试，再打包到 `dist/campus-auto-login/`。分发整个目录，保留 `_internal`、配置模板和启动管理入口；不分发个人 `.env` 或日志。运行包使用说明见 [WINDOWS-README.md](../WINDOWS-README.md)。
