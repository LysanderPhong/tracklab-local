"""Pinned pymobiledevice3 adapter; exposes no identifiers to the browser."""
from __future__ import annotations

import asyncio
import hashlib
import os
import platform
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
SOCKET = "127.0.0.1:27015" if platform.system() == "Windows" else "/var/run/usbmuxd"


class DeviceError(Exception):
    pass


def fingerprint(serial: str) -> str:
    return hashlib.sha256(serial.encode()).hexdigest()


def _result(base: dict, stage: str, badge: str, title: str, message: str,
            *, next_action: str = "", **extra) -> dict:
    return base | {"stage": stage, "badge": badge, "title": title,
                   "message": message, "next_action": next_action} | extra


def _preparation_error(base: dict, error: Exception) -> dict:
    # Error text can contain device identifiers. Return only our own messages.
    name = type(error).__name__
    if name == "PairingDialogResponsePendingError":
        return _result(base, "trust_pending", "等待信任", "请在手机上确认信任",
                       "连接请求已发送。请解锁手机，点“信任”并输入手机密码，然后再次一键连接。", next_action="prepare")
    if name == "UserDeniedPairingError":
        return _result(base, "trust_denied", "信任被拒绝", "手机拒绝了信任请求",
                       "请在手机上允许信任这台电脑，再次一键连接；若不再弹窗，可先拔插数据线。", next_action="prepare")
    if name in {"PasswordRequiredError", "PasscodeRequiredError", "DeviceHasPasscodeSetError"}:
        return _result(base, "locked", "请解锁", "需要在手机上完成确认",
                       "请解锁手机并确认信任，再次一键连接。程序不会修改或要求移除手机密码。", next_action="prepare")
    if name in {"MCProtectedError", "GetProhibitedError", "SetProhibitedError"}:
        return _result(base, "prepare_failed", "请求被限制", "手机拒绝了连接准备请求",
                       "系统限制了此请求。若手机由学校或单位管理，请向管理员确认是否允许；程序不能代替管理员解除限制。", next_action="prepare")
    return _result(base, "prepare_failed", "准备未完成", "未能显示开发者模式入口",
                   "手机未确认此请求成功。请保持解锁后重试；仍没有入口时，可用 Xcode 的设备管理界面配对，再检查手机设置。", next_action="prepare")


async def _check(*, prepare: bool) -> dict:
    from pymobiledevice3.usbmux import list_devices
    from pymobiledevice3.lockdown import create_using_usbmux
    devices = [d for d in await list_devices(usbmux_address=SOCKET) if d.is_usb]
    base = {"connected": False, "ready": False, "model": "", "version": "",
            "developer_mode": "unknown", "revealed": False}
    if not devices:
        return _result(base, "disconnected", "未连接", "还没有检测到 iPhone",
                       "请用支持数据传输的线连接手机，解锁后再次一键连接。")
    if len(devices) != 1:
        return _result(base, "multiple_devices", "多台设备", "检测到多台 Apple 设备",
                       "请仅保留需要测试的 iPhone 的 USB 连接，再次一键连接。")
    serial = devices[0].serial
    base.update(connected=True, _serial=serial)
    try:
        client = await create_using_usbmux(serial=serial, autopair=False, connection_type="USB", pairing_records_cache_folder=ROOT / "runtime" / "pair-cache", usbmux_address=SOCKET)
    except Exception as error:
        return _preparation_error(base, error)
    try:
        values = client.all_values
        base.update(connected=True, model=str(values.get("ProductType", "iPhone")), version=str(values.get("ProductVersion", "未知")), _serial=serial)
        if values.get("DeviceClass") != "iPhone":
            return _result(base, "unsupported_device", "设备不符", "当前连接的不是 iPhone",
                           "当前仅验证 iPhone，请连接目标手机。")
        if not client.paired:
            if not prepare:
                return _result(base, "trust_required", "等待信任", "手机已连接，需要信任电脑",
                               "点“一键连接”，再在手机上确认“信任”并输入手机密码。", next_action="prepare")
            try:
                await client.pair(timeout=30)
                # pair() writes a record; validate_pairing() establishes the SSL session.
                if not await client.validate_pairing():
                    return _result(base, "trust_required", "等待信任", "信任尚未完成",
                                   "请确认手机已信任这台电脑，再次一键连接。", next_action="prepare")
                values = client.all_values
                base.update(model=str(values.get("ProductType", "iPhone")), version=str(values.get("ProductVersion", "未知")))
            except Exception as error:
                return _preparation_error(base, error)

        version = base["version"]
        if not re.fullmatch(r"\d+(?:\.\d+)*", version) or int(version.split(".")[0]) < 1:
            return _result(base, "version_unknown", "版本待确认", "无法确认手机的 iOS 版本",
                           "请解锁手机后重新检查。暂不发送开发者模式请求，也不能确认定位服务兼容性。", next_action="prepare")
        if int(version.split(".")[0]) < 16:
            return _result(base, "ready", "已连接", "iPhone 连接就绪",
                           "此 iOS 版本没有开发者模式开关要求。定位服务是否兼容仍需以实际执行结果为准。", ready=True, developer_mode="not_required")

        try:
            enabled = await client.get_developer_mode_status()
            base["developer_mode"] = "enabled" if enabled else "disabled"
        except Exception as error:
            enabled = False
            if type(error).__name__ in {"PasswordRequiredError", "PasscodeRequiredError", "MCProtectedError", "GetProhibitedError", "SetProhibitedError"}:
                return _preparation_error(base, error)
            if not prepare:
                return _result(base, "developer_mode_unknown", "状态待确认", "无法读取开发者模式",
                               "请解锁手机后点“一键连接”；如果设置里没有入口，程序会尝试显示它。", next_action="prepare")
        if enabled:
            return _result(base, "ready", "已连接", "iPhone 连接就绪",
                           "USB、信任状态和开发者模式均已就绪。回放期间请保留数据线连接。", ready=True)
        if prepare:
            try:
                # Only reveal the Settings toggle. Actions 1/2 reboot or confirm
                # Developer Mode and must remain under the phone owner's control.
                service = await client.start_lockdown_service("com.apple.amfi.lockdown")
                try:
                    response = await service.send_recv_plist({"action": 0})
                finally:
                    await service.close()
                if response.get("success") is not True:
                    return _result(base, "prepare_failed", "请求未成功", "手机未确认开发者模式入口已显示",
                                   "请保持手机解锁后重试；若仍无入口，可用 Xcode 配对。受管理的手机也可能被系统策略限制，需由管理员确认。", next_action="prepare")
            except Exception as error:
                return _preparation_error(base, error)
            return _result(base, "developer_mode_required", "需手机确认", "已请求显示开发者模式入口",
                           "在手机“设置 → 隐私与安全性 → 开发者模式”开启，按手机提示重启并确认，再点“一键连接”。若设置已打开，请退出后重新进入。", next_action="prepare", revealed=True)
        return _result(base, "developer_mode_required", "需开发者模式", "电脑已获信任，开发者模式未开启",
                       "点“一键连接”显示入口，再到手机“设置 → 隐私与安全性 → 开发者模式”开启，按手机提示重启并确认。", next_action="prepare")
    finally:
        await client.close()


async def _inspect() -> dict:
    """Read device readiness without prompting trust or changing phone settings."""
    return await _check(prepare=False)


async def _prepare() -> dict:
    """Request trust and reveal the Settings toggle; never enable or reboot."""
    return await _check(prepare=True)


def _inspection() -> dict:
    try:
        return asyncio.run(asyncio.wait_for(_inspect(), timeout=15))
    except TimeoutError:
        raise DeviceError("设备检查超时。请解锁手机并检查数据线，再试一次。") from None
    except Exception as error:
        if isinstance(error, DeviceError):
            raise
        name = type(error).__name__
        if any(word in name.lower() for word in ("pair", "locked", "password", "permission")):
            raise DeviceError("手机可能尚未解锁或信任这台电脑。请先在电脑和手机上完成信任，再次检查。") from None
        raise DeviceError(f"设备检查失败（{name}）。请检查 USB 连接，并保持手机解锁。") from None


def scan() -> dict:
    return {k: v for k, v in _inspection().items() if not k.startswith("_")}


def prepare() -> dict:
    try:
        result = asyncio.run(asyncio.wait_for(_prepare(), timeout=45))
    except TimeoutError:
        raise DeviceError("连接准备超时。请在手机上完成信任或解锁后，再次一键连接。") from None
    except Exception as error:
        raise DeviceError(f"连接准备失败（{type(error).__name__}）。请检查 USB 连接并保持手机解锁。") from None
    return {k: v for k, v in result.items() if not k.startswith("_")}


def require_ready() -> dict:
    result = _inspection()
    if not result["ready"]:
        raise DeviceError(result["message"])
    return result


def _environment() -> dict:
    environment = os.environ.copy()
    for key in list(environment):
        if key.startswith("PYMOBILEDEVICE3_") or key == "USBMUXD_SOCKET_ADDRESS":
            environment.pop(key)
    environment.update(PYTHONUNBUFFERED="1", NO_COLOR="1")
    return environment


def _command(action: str, serial: str, *arguments: str) -> list[str]:
    return [sys.executable, str(ROOT / "sdk_cli.py"), "developer", "dvt", "simulate-location", action, *connection_arguments(serial), *arguments]


def connection_arguments(serial: str) -> list[str]:
    # Native remoted is macOS-only. The pinned SDK provides an in-process
    # userspace transport for Windows; actual Windows hardware remains untested.
    transport = "--native" if platform.system() == "Darwin" else "--userspace"
    return [transport, "--usbmux", SOCKET, "--udid", serial]


def _failure(output: str) -> str:
    lowered = output.lower()
    if any(x in lowered for x in ("developermodedisabled", "developer mode is disabled")):
        return "手机开发者模式未开启，请开启并重启后重试。"
    if any(x in lowered for x in ("disk image", "developerdiskimage", "notmounted", "personalizedimage")):
        return "设备开发者镜像尚未就绪。需要先检查当前 iOS 的镜像支持，尚不能确认定位接口可用。"
    if any(x in lowered for x in ("notpaired", "pairing", "passwordprotected", "device is locked")):
        return "手机尚未解锁或完成信任，请在手机与电脑上确认后重试。"
    if any(x in lowered for x in ("tunnel", "remotepairing", "remoted", "invalidservice")):
        return "未能建立定位服务连接。USB 连接成功不代表该 iOS 版本的定位服务可用，需要继续核查兼容性。"
    return "本次定位指令未完成。请检查设备连接并尝试清除模拟。"


def _finish_process(process: subprocess.Popen) -> None:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def clear_location(expected_fingerprint: str | None = None) -> dict:
    ready = require_ready()
    if expected_fingerprint and fingerprint(ready["_serial"]) != expected_fingerprint:
        raise DeviceError("连接的不是上次测试的那台手机。请连接原手机，再清除模拟位置。")
    try:
        result = subprocess.run(_command("clear", ready["_serial"]), stdin=subprocess.DEVNULL, capture_output=True, text=True, env=_environment(), cwd=ROOT, timeout=45)
    except subprocess.TimeoutExpired:
        raise DeviceError("清除指令超时，真实定位是否恢复尚未确认。请保持原手机连接后重试；必要时重启手机并检查地图。") from None
    if result.returncode:
        raise DeviceError(_failure(result.stdout + result.stderr))
    return {"message": "清除指令已发送。请打开手机地图确认恢复到实际位置；这一步尚不能由电脑自动验证。"}
