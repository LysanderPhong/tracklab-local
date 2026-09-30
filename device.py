"""Pinned pymobiledevice3 adapter; exposes no identifiers to the browser."""
from __future__ import annotations

import asyncio
import hashlib
import os
import platform
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
SOCKET = "127.0.0.1:27015" if platform.system() == "Windows" else "/var/run/usbmuxd"


class DeviceError(Exception):
    pass


def fingerprint(serial: str) -> str:
    return hashlib.sha256(serial.encode()).hexdigest()


async def _inspect() -> dict:
    from pymobiledevice3.usbmux import list_devices
    from pymobiledevice3.lockdown import create_using_usbmux
    devices = [d for d in await list_devices(usbmux_address=SOCKET) if d.is_usb]
    base = {"connected": False, "ready": False, "model": "", "version": ""}
    if not devices:
        return base | {"badge": "未连接", "title": "还没有检测到 iPhone", "message": "请用支持数据传输的线连接手机，解锁后再次检查。"}
    if len(devices) != 1:
        return base | {"badge": "多台设备", "title": "检测到多台 Apple 设备", "message": "请仅保留需要测试的 iPhone 的 USB 连接，再次检查。"}
    serial = devices[0].serial
    client = await create_using_usbmux(serial=serial, autopair=False, connection_type="USB", pairing_records_cache_folder=ROOT / "runtime" / "pair-cache", usbmux_address=SOCKET)
    try:
        values = client.all_values
        base.update(connected=True, model=str(values.get("ProductType", "iPhone")), version=str(values.get("ProductVersion", "未知")), _serial=serial)
        if values.get("DeviceClass") != "iPhone":
            return base | {"badge": "设备不符", "title": "当前连接的不是 iPhone", "message": "第一版仅验证 iPhone，请连接目标手机。"}
        if not client.paired:
            return base | {"badge": "等待信任", "title": "手机已连接，需要信任电脑", "message": "请在电脑的苹果设备管理界面选择 iPhone 并点击信任，再在手机上确认；完成后重新检查。"}
        try:
            enabled = await client.get_developer_mode_status()
        except Exception:
            return base | {"badge": "状态待确认", "title": "无法读取开发者模式", "message": "请保持手机解锁，检查“设置 → 隐私与安全性 → 开发者模式”，然后重新检查。"}
        if not enabled:
            return base | {"badge": "需开发者模式", "title": "电脑已获信任，开发者模式未开启", "message": "在手机“设置 → 隐私与安全性 → 开发者模式”开启，按手机提示重启并确认，再次检查。若没有该选项，请告诉我。"}
        return base | {"ready": True, "badge": "已连接", "title": "iPhone 连接就绪", "message": "USB、信任状态和开发者模式均已就绪。回放期间请保留数据线连接。"}
    finally:
        await client.close()


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
