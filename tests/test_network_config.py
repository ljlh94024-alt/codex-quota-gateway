import pathlib
import unittest

import yaml


ROOT = pathlib.Path(__file__).resolve().parents[1]


class NetworkConfigTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
        cls.override = yaml.safe_load((ROOT / "docker-compose.upstream-network.yml").read_text(encoding="utf-8"))
        cls.env = (ROOT / ".env.example").read_text(encoding="utf-8")
        cls.caddy = (ROOT / "caddy" / "Caddyfile").read_text(encoding="utf-8")

    def test_container_bind_and_host_publish(self):
        self.assertIn("BIND_HOST=0.0.0.0", self.env)
        self.assertEqual(self.compose["services"]["gateway"]["ports"], ["127.0.0.1:8080:8080"])
        self.assertEqual(self.compose["services"]["gateway"]["environment"]["BIND_HOST"], "0.0.0.0")

    def test_base_network_is_compose_managed(self):
        networks = self.compose["networks"]
        self.assertIn("gateway-net", networks)
        self.assertFalse(networks["gateway-net"].get("external", False))
        for service in ("gateway", "caddy", "public_test"):
            self.assertIn("gateway-net", self.compose["services"][service]["networks"])

    def test_host_gateway_mapping(self):
        self.assertIn("host.docker.internal:host-gateway", self.compose["services"]["gateway"]["extra_hosts"])

    def test_caddy_gets_only_domain_environment(self):
        caddy = self.compose["services"]["caddy"]
        self.assertNotIn("env_file", caddy)
        self.assertEqual(set(caddy["environment"]), {"DOMAIN"})
        self.assertIn("DOMAIN", caddy["environment"])

    def test_caddy_uses_domain_placeholder_and_gateway_dns(self):
        self.assertIn("{$DOMAIN}", self.caddy)
        self.assertIn("reverse_proxy gateway:8080", self.caddy)

    def test_upstream_override_isolated_to_gateway(self):
        self.assertEqual(set(self.override["services"]), {"gateway"})
        self.assertIn("upstream-net", self.override["services"]["gateway"]["networks"])
        self.assertTrue(self.override["networks"]["upstream-net"]["external"])
        self.assertIn("UPSTREAM_DOCKER_NETWORK", self.override["networks"]["upstream-net"]["name"])

    def test_docs_do_not_recommend_container_loopback_upstream(self):
        for name in ("README.md", "DEPLOYMENT.md"):
            text = (ROOT / name).read_text(encoding="utf-8")
            self.assertNotIn("UPSTREAM_BASE_URL=http://" + "127.0.0.1", text)

    def test_no_caddy_generator_remains(self):
        self.assertFalse((ROOT / "scripts" / "generate_caddy.py").exists())


if __name__ == "__main__":
    unittest.main()
