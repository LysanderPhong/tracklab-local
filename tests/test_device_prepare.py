import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import device


def fake_error(name):
    # Device identifiers in SDK exceptions must never leak into browser messages.
    return type(name, (Exception,), {})("private-device-serial")


class DevicePreparationTests(unittest.TestCase):
    def setUp(self):
        self.usb = SimpleNamespace(is_usb=True, serial="private-device-serial")
        self.service = SimpleNamespace(send_recv_plist=AsyncMock(return_value={"success": True}), close=AsyncMock())
        self.client = SimpleNamespace(
            all_values={"DeviceClass": "iPhone", "ProductType": "iPhone16,1", "ProductVersion": "18.2"},
            paired=True,
            pair=AsyncMock(), validate_pairing=AsyncMock(return_value=True),
            get_developer_mode_status=AsyncMock(return_value=False),
            start_lockdown_service=AsyncMock(return_value=self.service), close=AsyncMock(),
        )
        self.list = AsyncMock(return_value=[self.usb])
        self.create = AsyncMock(return_value=self.client)
        self.patches = [
            patch("pymobiledevice3.usbmux.list_devices", self.list),
            patch("pymobiledevice3.lockdown.create_using_usbmux", self.create),
        ]
        for mock in self.patches:
            mock.start()
            self.addCleanup(mock.stop)

    def inspect(self):
        return asyncio.run(device._inspect())

    def prepare(self):
        return device.prepare()

    def test_scan_remains_read_only_when_developer_mode_is_missing(self):
        result = self.inspect()
        self.assertEqual(result["stage"], "developer_mode_required")
        self.assertEqual(result["developer_mode"], "disabled")
        self.assertEqual(result["next_action"], "prepare")
        self.assertFalse(result["ready"])
        self.client.pair.assert_not_awaited()
        self.client.start_lockdown_service.assert_not_awaited()
        self.client.close.assert_awaited_once()
        self.assertFalse(self.create.call_args.kwargs["autopair"])

    def test_scan_does_not_prompt_new_phone_for_trust(self):
        self.client.paired = False
        result = self.inspect()
        self.assertEqual(result["stage"], "trust_required")
        self.assertEqual(result["next_action"], "prepare")
        self.client.pair.assert_not_awaited()
        self.client.get_developer_mode_status.assert_not_awaited()

    def test_prepare_reveals_toggle_only_and_closes_both_connections(self):
        result = self.prepare()
        self.assertTrue(result["revealed"])
        self.assertFalse(result["ready"])
        self.assertEqual(result["stage"], "developer_mode_required")
        self.client.pair.assert_not_awaited()
        self.client.start_lockdown_service.assert_awaited_once_with("com.apple.amfi.lockdown")
        self.service.send_recv_plist.assert_awaited_once_with({"action": 0})
        self.service.close.assert_awaited_once()
        self.client.close.assert_awaited_once()
        self.assertNotIn("_serial", result)
        self.assertNotIn(self.usb.serial, str(result))

    def test_untrusted_phone_pairs_then_validates_session_before_reveal(self):
        self.client.paired = False
        calls = []

        async def pair(**kwargs):
            calls.append("pair")
            self.assertEqual(kwargs, {"timeout": 30})

        async def validate():
            calls.append("validate")
            self.client.paired = True
            return True

        async def start(name):
            calls.append("reveal")
            self.assertTrue(self.client.paired)
            return self.service

        self.client.pair.side_effect = pair
        self.client.validate_pairing.side_effect = validate
        self.client.start_lockdown_service.side_effect = start
        self.assertTrue(self.prepare()["revealed"])
        self.assertEqual(calls, ["pair", "validate", "reveal"])

    def test_pending_denied_locked_or_restricted_trust_never_reveals(self):
        cases = {"PairingDialogResponsePendingError": "trust_pending", "UserDeniedPairingError": "trust_denied",
                 "PasswordRequiredError": "locked", "MCProtectedError": "prepare_failed"}
        for name, stage in cases.items():
            with self.subTest(name=name):
                self.client.paired = False
                self.client.pair.side_effect = fake_error(name)
                result = self.prepare()
                self.assertEqual(result["stage"], stage)
                self.assertFalse(result["revealed"])
                self.assertNotIn(self.usb.serial, str(result))
        self.client.validate_pairing.assert_not_awaited()
        self.client.start_lockdown_service.assert_not_awaited()
        self.assertEqual(self.client.close.await_count, len(cases))

    def test_pair_record_without_valid_session_does_not_reveal(self):
        self.client.paired = False
        self.client.validate_pairing.return_value = False
        result = self.prepare()
        self.assertEqual(result["stage"], "trust_required")
        self.assertFalse(result["revealed"])
        self.client.start_lockdown_service.assert_not_awaited()

    def test_already_enabled_performs_no_phone_mutation(self):
        self.client.get_developer_mode_status.return_value = True
        result = self.prepare()
        self.assertTrue(result["ready"])
        self.assertFalse(result["revealed"])
        self.assertEqual(result["developer_mode"], "enabled")
        self.assertEqual(result["next_action"], "")
        self.client.pair.assert_not_awaited()
        self.client.start_lockdown_service.assert_not_awaited()

    def test_rejected_reveal_is_never_claimed_successful(self):
        for response in [{"success": False}, {"Error": "Restricted"}, {}, {"success": 1}]:
            with self.subTest(response=response):
                self.service.send_recv_plist.return_value = response
                result = self.prepare()
                self.assertEqual(result["stage"], "prepare_failed")
                self.assertFalse(result["revealed"])
                self.assertFalse(result["ready"])
        self.assertEqual(self.service.close.await_count, 4)
        self.assertEqual(self.client.close.await_count, 4)
        for call in self.service.send_recv_plist.await_args_list:
            self.assertEqual(call.args, ({"action": 0},))

    def test_reveal_exception_closes_connections_and_keeps_not_ready(self):
        self.service.send_recv_plist.side_effect = fake_error("InvalidServiceError")
        result = self.prepare()
        self.assertEqual(result["stage"], "prepare_failed")
        self.assertFalse(result["revealed"])
        self.assertNotIn(self.usb.serial, str(result))
        self.service.close.assert_awaited_once()
        self.client.close.assert_awaited_once()

    def test_unknown_mode_status_can_reveal_but_does_not_claim_enabled(self):
        self.client.get_developer_mode_status.side_effect = fake_error("MissingValueError")
        scanned = self.inspect()
        self.assertEqual(scanned["stage"], "developer_mode_unknown")
        self.client.start_lockdown_service.assert_not_awaited()
        result = self.prepare()
        self.assertEqual(result["developer_mode"], "unknown")
        self.assertTrue(result["revealed"])
        self.assertFalse(result["ready"])

    def test_ios_before_16_does_not_require_or_reveal_toggle(self):
        self.client.all_values["ProductVersion"] = "15.8.3"
        result = self.prepare()
        self.assertTrue(result["ready"])
        self.assertEqual(result["developer_mode"], "not_required")
        self.client.get_developer_mode_status.assert_not_awaited()
        self.client.start_lockdown_service.assert_not_awaited()
        self.assertIn("实际执行结果", result["message"])

    def test_unknown_version_does_not_send_amfi_action(self):
        for version in [None, "", "unknown", "0.0"]:
            with self.subTest(version=version):
                self.client.all_values["ProductVersion"] = version
                result = self.prepare()
                self.assertEqual(result["stage"], "version_unknown")
                self.assertFalse(result["ready"])
        self.client.get_developer_mode_status.assert_not_awaited()
        self.client.start_lockdown_service.assert_not_awaited()

    def test_no_usb_multiple_usb_and_non_iphone_do_not_prepare(self):
        for devices, stage in [([], "disconnected"), ([self.usb, self.usb], "multiple_devices"),
                               ([SimpleNamespace(is_usb=False, serial="network")], "disconnected")]:
            with self.subTest(stage=stage):
                self.list.return_value = devices
                self.assertEqual(self.prepare()["stage"], stage)
        self.create.assert_not_awaited()
        self.list.return_value = [self.usb]
        self.client.all_values["DeviceClass"] = "iPad"
        self.assertEqual(self.prepare()["stage"], "unsupported_device")
        self.client.pair.assert_not_awaited()
        self.client.start_lockdown_service.assert_not_awaited()

    def test_locked_during_factory_has_actionable_error_without_identifier(self):
        self.create.side_effect = fake_error("PasswordRequiredError")
        result = self.prepare()
        self.assertEqual(result["stage"], "locked")
        self.assertNotIn(self.usb.serial, str(result))
        self.client.close.assert_not_awaited()  # Factory owns cleanup when creation fails.


if __name__ == "__main__":
    unittest.main()
