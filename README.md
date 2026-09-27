# TrackLab Local · 本地定位实验台

下载压缩包，在自己的电脑运行，通过本地网页控制连接到这台电脑的 iPhone。包含固定定位和动态路线回放；不需要 Render、云端账号、配对码或公共网站。

> 测试版。Mac + iPhone 曾有真机验证；Windows 尚待真机验证。只对你有权控制的设备进行开发测试。恢复指令完成后，请在手机地图中核实真实位置。

## 下载与启动

1. 在本仓库 **Releases** 下载 `TrackLab-Local-0.5.0-beta.zip`，完整解压到可写目录。不要在压缩包内直接启动。
2. 首次使用先安装 [Python 3.12](https://www.python.org/downloads/)（Mac 也可使用 [uv](https://docs.astral.sh/uv/getting-started/installation/)）。Windows 还需要 Apple 设备驱动。
3. 用数据线连接并解锁 iPhone，按手机提示信任电脑、开启开发者模式。
4. Mac 双击 `一键连接-Mac.command`；Windows 双击 `一键连接-Windows.cmd`。首次会联网安装锁定的依赖，可能需要几分钟。
5. 浏览器自动打开 `http://127.0.0.1:8769/fixed`。检测通过后，选择地点或绘制路线，再点击开始。

以后重复第 3–5 步即可。无需每次安装、输入网址或配对码。也可以下载 GitHub 的源码 ZIP，按相同步骤启动。

**这份发布包包含源码与启动脚本，不包含 Python，不是免安装的签名应用。** macOS 如果拦截下载的脚本，请核实来源后按系统指引操作；不要关闭系统安全保护。开发者也可在目录内执行 `uv sync --locked` 和 `uv run python desktop_launcher.py`。

## 工作方式与容量

每个人的浏览器 → 自己电脑的本地服务 → 自己的 USB 手机。服务仅监听 127.0.0.1，不开放到局域网，不需要你的服务器维持运行。GitHub 只用于分发源码和下载包。

每个本地服务当前只允许一个活动定位任务，不能把多部手机同时独立控制理解为已支持。不同电脑可以各自运行互不依赖的服务。

地图图块、地名搜索与附近跑道查询仍联网访问 OpenStreetMap、Photon、Nominatim、Overpass；这不是完全离线软件。搜索词、查询坐标会发给相应地图服务。规模增长时需自行配置适合用量的服务，不能把公共地图接口视为无限容量。

## 使用与退出

- 提前点击“恢复真实定位”后，会自动重新检查设备；通过后可选择新地点再次开始。
- 手机稍后才插入或状态不准时，点击“刷新连接状态”。按钮不可用时，查看旁边的具体原因。
- 关闭网页或启动窗口不会结束定位。本地后台服务继续运行，重新双击启动入口即可回到页面。
- 结束测试先点击恢复，并在手机确认。不要靠强制结束进程或拔线来恢复。
- 再次打开会复用已有服务。升级前先恢复定位并退出旧后台进程；不要在定位运行中覆盖环境。

依赖安装失败时，在项目目录运行 `uv sync --locked`，或用项目 `.venv` 内的 Python 执行 `-m pip install --require-hashes -r requirements-desktop.txt` 后再启动。启动日志位于 `runtime/desktop-launcher.log`；分享日志前自行去除设备信息。

## 构建与验证

使用 Python 3.12：

```sh
uv sync --locked
uv run python -m unittest discover -s tests -q
node tests/test_connection_state.cjs
node tests/test_restart_fixed.cjs
python3 build_release.py
```

压缩包及校验文件生成在 `dist/`。发布包排除 `.venv`、runtime、设备配对记录、缓存、旧卫星图片与 Android APK。固定版本依赖见 `uv.lock` 和带哈希的 `requirements-desktop.txt`。

## 许可

本项目原创代码使用 GPL-3.0-only，完整条款见 LICENSE。第三方组件保留原许可证与署名，详见 THIRD_PARTY.md。软件不提供担保。
