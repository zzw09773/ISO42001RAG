#!/usr/bin/env python3
"""Deployment contracts that keep the offline release repeatable and safe."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
PROJECT_IMAGES = {
    "rag-api": "iso42001rag-rag-api:latest",
    "embed-proxy": "iso42001rag-embed-proxy:latest",
    "jupyter": "iso42001rag-jupyter:latest",
    "monitoring": "iso42001rag-monitoring:latest",
    "code-server": "iso42001rag-code-server:latest",
    "admin": "iso42001rag-admin:latest",
}


def compose_config(*files: str) -> dict:
    command = ["docker", "compose", "--env-file", ".env.example"]
    for file_name in files:
        command.extend(("-f", file_name))
    command.extend(("config", "--format", "json"))
    result = subprocess.run(
        command,
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


class ComposeContractTests(unittest.TestCase):
    def test_project_and_project_image_names_are_stable(self) -> None:
        config = compose_config("docker-compose.yaml")
        self.assertEqual(config["name"], "iso42001rag")
        for service, image in PROJECT_IMAGES.items():
            self.assertEqual(config["services"][service]["image"], image)

    def test_base_compose_keeps_direct_intranet_ports(self) -> None:
        config = compose_config("docker-compose.yaml")
        for service in ("rag-api", "admin"):
            ports = config["services"][service]["ports"]
            self.assertTrue(ports)
            self.assertTrue(all("host_ip" not in port for port in ports))

    def test_rag_api_reloads_only_admin_managed_runtime_env(self) -> None:
        config = compose_config("docker-compose.yaml")
        self.assertEqual(
            config["services"]["rag-api"]["environment"]["RAG_ENV_FILE"],
            "/runtime_config/rag-runtime.env",
        )
        env_mounts = [
            volume
            for volume in config["services"]["rag-api"]["volumes"]
            if volume["target"] == "/runtime_config"
        ]
        self.assertEqual(len(env_mounts), 1)
        self.assertTrue(env_mounts[0]["read_only"])
        self.assertEqual(Path(env_mounts[0]["source"]), ROOT / "admin_console" / "data")
        self.assertEqual(
            config["services"]["admin"]["environment"]["RAG_RUNTIME_ENV_FILE"],
            "/app/data/rag-runtime.env",
        )
        self.assertEqual(
            config["services"]["rag-api"]["environment"]["RAG_EFFECTIVE_ENV_FILE"],
            "/app/data/processed/rag-effective.env",
        )

    def test_hardening_binds_admin_port_to_loopback(self) -> None:
        config = compose_config("docker-compose.yaml", "docker-compose.hardening.yml")
        self.assertEqual(
            config["services"]["admin"]["ports"],
            [
                {
                    "mode": "ingress",
                    "host_ip": "127.0.0.1",
                    "target": 8300,
                    "published": "8300",
                    "protocol": "tcp",
                }
            ],
        )


class ReleaseScriptContractTests(unittest.TestCase):
    def test_deploy_waits_without_forcing_a_rebuild(self) -> None:
        script = (ROOT / "deploy.sh").read_text(encoding="utf-8")
        self.assertNotIn("docker compose up -d --build", script)
        self.assertIn("docker compose up -d --wait", script)
        self.assertIn("--no-build", script)
        self.assertIn("--pull never", script)
        self.assertIn("docker image inspect", script)
        self.assertIn("MISSING_IMAGES", script)

    def test_deploy_fails_closed_without_admin_login_configuration(self) -> None:
        script = (ROOT / "deploy.sh").read_text(encoding="utf-8")
        self.assertIn("ADMIN_CARD_SERIALS_VALUE", script)
        self.assertIn("ENABLE_PASSWORD_FALLBACK", script)
        self.assertIn("ADMIN_USERNAME", script)
        self.assertIn("ADMIN_PASSWORD", script)
        self.assertIn("PLACEHOLDER_SECRET_KEYS", script)
        self.assertIn("ALLOW_PLACEHOLDER_SECRETS", script)
        for key in (
            "POSTGRES_PASSWORD", "WEBUI_SECRET_KEY", "KEYCLOAK_ADMIN_PASSWORD",
            "OAUTH_CLIENT_SECRET", "CODESERVER_PASSWORD", "CODESERVER_SUDO_PASSWORD",
        ):
            self.assertIn(key, script)
        self.assertNotIn("postgresql://postgres:postgres@", script)

    def test_deploy_rejects_public_placeholder_secrets_before_docker(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            shutil.copy2(ROOT / "deploy.sh", temp_path / "deploy.sh")
            (temp_path / ".env").write_text(
                "ADMIN_CARD_SERIALS=1234567\n"
                "POSTGRES_PASSWORD=<填入強隨機密碼>\n"
                "WEBUI_SECRET_KEY=your-secret-key-here\n"
                "KEYCLOAK_ADMIN_PASSWORD=change-this-keycloak-admin-password\n"
                "OAUTH_CLIENT_SECRET=openwebui-dev-client-secret-change-me\n"
                "CODESERVER_PASSWORD=<填入強隨機密碼>\n"
                "CODESERVER_SUDO_PASSWORD=<填入強隨機密碼>\n",
                encoding="utf-8",
            )
            result = subprocess.run(
                ["bash", "deploy.sh"],
                cwd=temp_path,
                capture_output=True,
                text=True,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("公開範本值", result.stdout)
            self.assertIn("CODESERVER_PASSWORD", result.stdout)

    def test_deploy_rejects_quoted_placeholder_with_spaced_assignment(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            shutil.copy2(ROOT / "deploy.sh", temp_path / "deploy.sh")
            (temp_path / ".env").write_text(
                " ADMIN_CARD_SERIALS = '1234567'  \n"
                " POSTGRES_PASSWORD = \"postgres\"  \n"
                "WEBUI_SECRET_KEY=strong-webui-secret\n"
                "KEYCLOAK_ADMIN_PASSWORD=strong-keycloak-secret\n"
                "OAUTH_CLIENT_SECRET=strong-oauth-secret\n"
                "CODESERVER_PASSWORD=strong-code-secret\n"
                "CODESERVER_SUDO_PASSWORD=strong-sudo-secret\n",
                encoding="utf-8",
            )

            result = subprocess.run(
                ["bash", "deploy.sh"],
                cwd=temp_path,
                capture_output=True,
                text=True,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("公開範本值", result.stdout)
            self.assertIn("POSTGRES_PASSWORD", result.stdout)

    def test_deploy_normalizes_quoted_runtime_values(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            shutil.copy2(ROOT / "deploy.sh", temp_path / "deploy.sh")
            (temp_path / ".env").write_text(
                "ADMIN_CARD_SERIALS=1234567\n"
                "POSTGRES_PASSWORD=strong-postgres-secret\n"
                "WEBUI_SECRET_KEY=strong-webui-secret\n"
                "KEYCLOAK_ADMIN_PASSWORD=strong-keycloak-secret\n"
                "OAUTH_CLIENT_SECRET=strong-oauth-secret\n"
                "CODESERVER_PASSWORD=strong-code-secret\n"
                "CODESERVER_SUDO_PASSWORD=strong-sudo-secret\n"
                " CHAT_MODEL_NAME = \"o3\"  \n"
                "REASONING_EFFORT = 'low'\n",
                encoding="utf-8",
            )
            ssl_dir = temp_path / "nginx" / "ssl"
            ssl_dir.mkdir(parents=True)
            (ssl_dir / "cert.crt").touch()
            (ssl_dir / "cert.key").touch()
            bin_dir = temp_path / "bin"
            bin_dir.mkdir()
            docker_stub = bin_dir / "docker"
            docker_stub.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
            docker_stub.chmod(0o755)
            env = os.environ.copy()
            env["PATH"] = f"{bin_dir}:{env['PATH']}"

            result = subprocess.run(
                ["bash", "deploy.sh"],
                cwd=temp_path,
                capture_output=True,
                text=True,
                env=env,
            )

            self.assertNotEqual(result.returncode, 0)
            runtime_env = (
                temp_path / "admin_console" / "data" / "rag-runtime.env"
            ).read_text(encoding="utf-8")
            self.assertIn("CHAT_MODEL_NAME=o3\n", runtime_env)
            self.assertIn("REASONING_EFFORT=low\n", runtime_env)
            self.assertNotIn('"o3"', runtime_env)
            self.assertNotIn("'low'", runtime_env)

    def test_image_scripts_include_admin(self) -> None:
        save_script = (ROOT / "save_images.sh").read_text(encoding="utf-8")
        package_script = (ROOT / "make_update_package.sh").read_text(encoding="utf-8")

        self.assertIn(
            "docker compose build rag-api embed-proxy jupyter monitoring code-server admin",
            save_script,
        )
        self.assertIn('ADMIN_IMAGE="iso42001rag-admin:latest"', save_script)
        self.assertIn("images/admin.tar", save_script)

        self.assertIn(
            "docker compose build rag-api embed-proxy jupyter monitoring code-server admin",
            package_script,
        )
        self.assertIn("iso42001rag-admin:latest", package_script)
        self.assertIn("admin_console", package_script)
        for html_name in ("README.html", "AUDIT_EVIDENCE_INDEX.html", "PROJECT_STRUCTURE.html"):
            self.assertIn(html_name, package_script)
        self.assertRegex(package_script, r"\btests\b")
        self.assertIn("'admin_console/data/*'", package_script)


class CertificateContractTests(unittest.TestCase):
    def test_generated_certificates_are_not_tracked(self) -> None:
        if subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=ROOT,
            capture_output=True,
            text=True,
        ).returncode != 0:
            for key_path in (ROOT / "nginx" / "ssl").glob("*.key"):
                self.assertEqual(stat.S_IMODE(key_path.stat().st_mode), 0o600)
            return
        result = subprocess.run(
            ["git", "ls-files", "nginx/ssl/cert.crt", "nginx/ssl/cert.csr", "nginx/ssl/cert.key"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        tracked_and_present = [
            relative_path
            for relative_path in result.stdout.splitlines()
            if (ROOT / relative_path).exists()
        ]
        self.assertEqual(tracked_and_present, [])

    def test_generator_honors_ssl_dir_and_protects_private_key(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            script = temp_path / "generate_certs.sh"
            shutil.copy2(ROOT / "nginx/generate_certs.sh", script)
            ssl_dir = temp_path / "custom-ssl"
            env = os.environ.copy()
            env["SSL_DIR"] = str(ssl_dir)
            env["CERT_DNS"] = "deployment-contract.example"

            subprocess.run(
                ["bash", str(script)],
                check=True,
                capture_output=True,
                text=True,
                env=env,
            )

            for name in ("cert.crt", "cert.csr", "cert.key"):
                self.assertTrue((ssl_dir / name).is_file(), name)
            key_mode = stat.S_IMODE((ssl_dir / "cert.key").stat().st_mode)
            self.assertEqual(key_mode, 0o600)


if __name__ == "__main__":
    unittest.main(verbosity=2)
