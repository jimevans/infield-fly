"""Tests for the deployment script, deploy/deploy.sh"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

CHECKOUT = Path(__file__).resolve().parent.parent
SCRIPT = CHECKOUT / "deploy" / "deploy.sh"
USER = subprocess.run(["whoami"], capture_output=True, text=True, check=True).stdout.strip()
SERVICE = "etc/systemd/system/infield-fly-web.service"
APACHE = "etc/apache2/userconf-enabled/infield-fly.conf"
PAM = "etc/pam.d/infield-fly"
FILTER = "etc/fail2ban/filter.d/infield-fly.conf"
JAIL = "etc/fail2ban/jail.d/infield-fly.conf"


def make_stub(path, exit_code="0"):
    """Creates a command that logs how it was called, and exits with a code"""

    path.write_text(f'#!/bin/sh\necho "$(basename "$0") $*" >> "$STUB_LOG"\nexit {exit_code}\n',
                    encoding="utf-8")
    path.chmod(0o755)
    return path


class Host:  # pylint: disable=too-many-instance-attributes

    """A host to deploy to, in a temporary directory standing in for its root directory"""

    def __init__(self, tmp_path):
        # The script is run from a copy of the deploy directory, in a clone without a
        # virtual environment or a deploy/deploy.env.
        self.checkout = tmp_path / "checkout"
        shutil.copytree(CHECKOUT / "deploy", self.checkout / "deploy",
                        ignore=shutil.ignore_patterns("deploy.env"))
        self.script = self.checkout / "deploy" / "deploy.sh"
        self.root = tmp_path / "root"
        for directory in ["etc/systemd/system", "etc/pam.d", "etc/apache2/userconf-enabled",
                          "etc/fail2ban/filter.d", "etc/fail2ban/jail.d"]:
            (self.root / directory).mkdir(parents=True)
        self.bin = tmp_path / "bin"
        self.bin.mkdir()
        self.command = make_stub(self.bin / "infield-fly")
        self.pip = make_stub(self.bin / "pip")
        self.home = tmp_path / "home"
        self.settings = self.home / ".config" / "infield-fly" / "settings.json"
        self.settings.parent.mkdir(parents=True)
        self.settings.write_text("{}", encoding="utf-8")
        self.log = tmp_path / "calls.log"
        self.env_file = tmp_path / "deploy.env"
        self.env = {
            "PATH": f"{self.bin}:/usr/bin:/bin",
            "HOME": str(self.home),
            "STUB_LOG": str(self.log),
            "DEPLOY_SUDO": "",
            "DEPLOY_TARGET_ROOT": str(self.root),
            "DEPLOY_SYSTEMCTL": str(make_stub(tmp_path / "systemctl")),
            "DEPLOY_APACHECTL": str(make_stub(tmp_path / "apache2ctl")),
        }

    def run(self, *args, answers=None, use_command=True, use_pip=True, **env):
        """Runs the script, returning the completed process"""

        options = ["--env", str(self.env_file)]
        if use_pip:
            options += ["--pip", str(self.pip)]
        if use_command:
            options += ["--command", str(self.command)]
        return subprocess.run([str(self.script), *options, *args], input=answers or "",
                              capture_output=True, text=True,
                              env={**self.env, **env}, check=False)

    def read(self, path):
        return (self.root / path).read_text(encoding="utf-8")

    @property
    def calls(self):
        return self.log.read_text(encoding="utf-8").splitlines() if self.log.exists() else []


@pytest.fixture
def host(tmp_path):
    return Host(tmp_path)


class TestFirstDeployment:

    def test_installs_all_files_with_host_values(self, host):
        result = host.run("--yes")

        assert result.returncode == 0, result.stderr
        service = host.read(SERVICE)
        assert f"User={USER}\n" in service
        assert f"WorkingDirectory={host.checkout}\n" in service
        assert (f"ExecStart={host.command} -c {host.settings} web --port 8642 "
                "--root-path /infield-fly\n") in service
        # Comments describing the values to fill in are left as they are.
        assert "#   YOUR_USER             the account running Infield Fly" in service
        assert "ProxyPass http://127.0.0.1:8642/\n" in host.read(APACHE)
        assert host.read(PAM) == (CHECKOUT / "deploy/pam.d/infield-fly").read_text(
            encoding="utf-8")
        assert "failregex" in host.read(FILTER)
        assert "[infield-fly]" in host.read(JAIL)

    def test_installs_and_reloads(self, host):
        result = host.run("--yes")

        assert host.calls == [
            f"pip -e {host.checkout}[web]",
            "systemctl daemon-reload",
            "apache2ctl configtest",
            "systemctl reload apache2",
            "systemctl reload fail2ban",
        ]
        assert "Start the web interface with: sudo systemctl enable --now infield-fly-web" in (
            result.stdout)

    def test_new_files_are_shown(self, host):
        result = host.run("--yes")

        assert f"New file: /{SERVICE}" in result.stdout
        assert f"+User={USER}" in result.stdout


class TestLaterDeployment:

    def test_unchanged_files_are_left_alone(self, host):
        host.run("--yes")
        host.log.unlink()

        result = host.run()

        assert result.returncode == 0, result.stderr
        for path in [SERVICE, PAM, APACHE, FILTER, JAIL]:
            assert f"Up to date: /{path}" in result.stdout
        assert host.calls == [f"pip -e {host.checkout}[web]"]
        assert "sudo systemctl restart infield-fly-web" in result.stdout

    def test_declined_change_keeps_installed_file(self, host):
        host.run("--yes")
        customized = host.read(APACHE) + "    # Hand edit\n"
        (host.root / APACHE).write_text(customized, encoding="utf-8")
        host.log.unlink()

        result = host.run(answers="n\n")

        assert f"Changes to /{APACHE}:" in result.stdout
        assert "-    # Hand edit" in result.stdout
        assert f"Skipped /{APACHE}" in result.stdout
        assert host.read(APACHE) == customized
        assert "systemctl reload apache2" not in host.calls

    def test_accepted_change_replaces_installed_file(self, host):
        host.run("--yes")
        (host.root / APACHE).write_text("old\n", encoding="utf-8")
        host.log.unlink()

        result = host.run(answers="y\n")

        assert f"Installed /{APACHE}" in result.stdout
        assert "ProxyPass" in host.read(APACHE)
        assert host.calls[-2:] == ["apache2ctl configtest", "systemctl reload apache2"]

    def test_no_answer_declines(self, host):
        result = host.run()

        assert result.returncode == 0, result.stderr
        assert not (host.root / SERVICE).exists()
        assert f"Skipped /{SERVICE}" in result.stdout

    def test_changed_unit_reloads_systemd(self, host):
        host.run("--yes")
        host.log.unlink()

        result = host.run("--yes", "--port", "9000")

        assert "ExecStart" in result.stdout
        assert "--port 9000" in host.read(SERVICE)
        assert "systemctl daemon-reload" in host.calls
        assert "sudo systemctl restart infield-fly-web" in result.stdout


class TestApacheCheck:

    def test_rejected_new_file_is_removed(self, host):
        make_stub(Path(host.env["DEPLOY_APACHECTL"]), exit_code="1")

        result = host.run("--yes")

        assert result.returncode == 1
        assert "Apache rejected the new configuration" in result.stderr
        assert not (host.root / APACHE).exists()
        assert "systemctl reload apache2" not in host.calls

    def test_rejected_change_restores_previous_file(self, host):
        (host.root / APACHE).write_text("working\n", encoding="utf-8")
        make_stub(Path(host.env["DEPLOY_APACHECTL"]), exit_code="1")

        result = host.run("--yes")

        assert result.returncode == 1
        assert host.read(APACHE) == "working\n"


class TestDryRun:

    def test_changes_nothing(self, host):
        result = host.run("--dry-run")

        assert result.returncode == 0, result.stderr
        assert f"Dry run: would run {host.pip} -e {host.checkout}[web]" in result.stdout
        assert f"Dry run: not installing /{SERVICE}" in result.stdout
        assert "Dry run complete; nothing was changed." in result.stdout
        assert host.calls == []
        assert not (host.root / SERVICE).exists()


class TestSettings:

    def test_env_file_and_options(self, host):
        host.env_file.write_text("WEB_PORT=9000\nSETTINGS_FILE=/srv/settings.json\n",
                                 encoding="utf-8")

        result = host.run("--yes", "--port", "9100")

        assert result.returncode == 0, result.stderr
        assert f"Reading settings from {host.env_file}" in result.stdout
        assert "-c /srv/settings.json web --port 9100" in host.read(SERVICE)
        assert "Warning: the settings file /srv/settings.json does not exist" in result.stderr

    def test_apache_file_can_be_turned_off(self, host):
        host.env_file.write_text('APACHE_CONF=""\n', encoding="utf-8")

        result = host.run("--yes")

        assert "Not installing the Apache configuration" in result.stdout
        assert not (host.root / APACHE).exists()

    def test_apache_file_elsewhere(self, host):
        (host.root / "etc/apache2/sites-available").mkdir()

        host.run("--yes", "--apache-conf", "/etc/apache2/sites-available/infield.conf")

        assert "ProxyPass" in host.read("etc/apache2/sites-available/infield.conf")
        assert not (host.root / APACHE).exists()

    def test_apache_file_in_missing_directory(self, host):
        result = host.run("--yes", "--apache-conf", "/etc/apache2/missing/infield.conf")

        assert result.returncode == 1
        assert "the directory of the Apache file" in result.stderr

    def test_apache_not_installed_without_shared_directory(self, host):
        (host.root / "etc/apache2/userconf-enabled").rmdir()

        result = host.run("--yes")

        assert result.returncode == 0, result.stderr
        assert "Not installing the Apache configuration" in result.stdout

    def test_settings_file_from_environment(self, host):
        host.run("--yes", INFIELD_FLY_CONFIG=str(host.settings.parent / "other.json"))

        assert f"-c {host.settings.parent / 'other.json'} web" in host.read(SERVICE)

    def test_virtual_environment_in_clone(self, host):
        venv_bin = host.checkout / ".venv" / "bin"
        venv_bin.mkdir(parents=True)
        make_stub(venv_bin / "pip")
        make_stub(venv_bin / "infield-fly")

        result = host.run("--yes", use_command=False, use_pip=False)

        assert result.returncode == 0, result.stderr
        assert host.calls[0] == f"pip install -e {host.checkout}[web]"
        assert f"ExecStart={venv_bin / 'infield-fly'} " in host.read(SERVICE)

    def test_user_install_without_virtual_environment(self, host):
        result = host.run("--dry-run", use_pip=False)

        assert f"Dry run: would run python3 -m pip install --user -e {host.checkout}[web]" in (
            result.stdout)

    def test_command_found_on_path(self, host):
        result = host.run("--yes", use_command=False)

        assert result.returncode == 0, result.stderr
        assert f"ExecStart={host.command} " in host.read(SERVICE)

    def test_command_defaults_to_user_bin(self, host):
        host.command.unlink()

        result = host.run("--yes", use_command=False)

        assert result.returncode == 1
        assert (f"the infield-fly command {host.home}/.local/bin/infield-fly does not exist"
                in result.stderr)

    def test_skip_install(self, host):
        result = host.run("--yes", "--skip-install")

        assert "Skipping installing Infield Fly" in result.stdout
        assert not any(x.startswith("pip ") for x in host.calls)

    def test_fail2ban_not_installed(self, host):
        for path in [host.root / "etc/fail2ban/filter.d", host.root / "etc/fail2ban/jail.d",
                     host.root / "etc/fail2ban"]:
            path.rmdir()

        result = host.run("--yes")

        assert result.returncode == 0, result.stderr
        assert "fail2ban is not installed" in result.stdout
        assert "systemctl reload fail2ban" not in host.calls


class TestInvalidUse:

    @pytest.mark.parametrize("args, message", [
        (["--port", "web"], "the web interface port 'web' is not a number"),
        (["--settings", "settings.json"], "the path 'settings.json' must be absolute"),
        (["--settings", "/my settings.json"], "contains a space"),
        (["--bogus"], "unknown option --bogus"),
        (["--port"], "option --port needs a value"),
    ])
    def test_invalid_options(self, host, args, message):
        result = host.run(*args)

        assert result.returncode == 1
        assert message in result.stderr
        assert host.calls == []

    def test_refuses_to_run_as_root(self, host):
        fake_id = host.bin / "id"
        fake_id.write_text("#!/bin/sh\necho 0\n", encoding="utf-8")
        fake_id.chmod(0o755)

        result = host.run("--yes")

        assert result.returncode == 1
        assert "not as root" in result.stderr
        assert host.calls == []

    def test_help(self, host):
        result = host.run("--help")

        assert result.returncode == 0
        assert result.stdout.startswith("Usage: deploy/deploy.sh [options]")


def test_example_env_file_documents_every_setting():
    example = (CHECKOUT / "deploy" / "deploy.env.example").read_text(encoding="utf-8")
    script = SCRIPT.read_text(encoding="utf-8")

    for setting in ["INFIELD_FLY_COMMAND", "PIP_COMMAND", "SETTINGS_FILE", "WEB_PORT",
                    "APACHE_CONF"]:
        assert f"#{setting}=" in example
        assert setting in script
    assert os.access(SCRIPT, os.X_OK)
