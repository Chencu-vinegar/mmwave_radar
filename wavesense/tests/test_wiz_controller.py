"""WizLightController 单元测试（不连真实灯泡，不依赖网络）。

覆盖：IP 校验、未配置 / 库缺失时的降级返回、状态字典结构。
真实 UDP 开关与连通性在现场联调时验证。
"""
import unittest

from src.wiz_controller import WizLightController, normalize_ip


class TestNormalizeIp(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(normalize_ip("192.168.0.101"), "192.168.0.101")
        self.assertEqual(normalize_ip(" 10.0.0.1 "), "10.0.0.1")

    def test_invalid(self):
        self.assertIsNone(normalize_ip(""))
        self.assertIsNone(normalize_ip("192.168.0"))
        self.assertIsNone(normalize_ip("256.0.0.1"))
        self.assertIsNone(normalize_ip("abc.def.ghi.jkl"))
        self.assertIsNone(normalize_ip(None))


class TestControllerDegrade(unittest.TestCase):
    def test_not_configured_returns_false(self):
        ctl = WizLightController(ip="")
        self.assertFalse(ctl.is_configured())
        # 未配置时命令必须安全降级：返回失败且带中文提示，不抛异常
        ok, msg = ctl.turn_on()
        self.assertFalse(ok)
        self.assertTrue(msg)
        ok, msg = ctl.turn_off()
        self.assertFalse(ok)
        self.assertTrue(msg)

    def test_set_ip_validation(self):
        ctl = WizLightController()
        self.assertFalse(ctl.set_ip("999.1.1.1"))
        self.assertTrue(ctl.set_ip("192.168.0.101"))
        self.assertEqual(ctl.ip, "192.168.0.101")

    def test_status_dict_fields(self):
        ctl = WizLightController(ip="192.168.0.101")
        st = ctl.status_dict()
        self.assertTrue(st["configured"])
        self.assertEqual(st["ip"], "192.168.0.101")
        self.assertIn("library_ready", st)
        self.assertIn("last_state", st)
        self.assertIn("last_result", st)

    def test_signing_key_normalized(self):
        ctl = WizLightController(signing_key="   ")
        self.assertIsNone(ctl.signing_key)
        ctl.set_signing_key("abc123")
        self.assertEqual(ctl.signing_key, "abc123")


if __name__ == "__main__":
    unittest.main()
