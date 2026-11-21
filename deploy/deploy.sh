#!/usr/bin/env bash
# Installs Infield Fly from this clone, with its web interface dependencies, then fills in
# the deployment files in this directory with this host's values, and installs those that
# changed, showing the changes and asking before replacing each installed file. It does
# not pull changes or restart the web interface. Run it as the account that runs Infield
# Fly, not as root; it uses sudo to install files.

set -euo pipefail
# Bash 5.2 makes "&" in a ${var//pattern/replacement} replacement mean the match.
shopt -u patsub_replacement 2>/dev/null || true

usage() {
    cat <<'EOF'
Usage: deploy/deploy.sh [options]

Installs Infield Fly and its deployment files on this host. Settings are read from
deploy/deploy.env (see deploy/deploy.env.example), and options override them.

Options:
  -y, --yes             install changed files without asking
  -n, --dry-run         show what would change, without installing anything
      --skip-install    don't install Infield Fly with pip
      --env FILE        read settings from FILE instead of deploy/deploy.env
      --command PATH    path of the infield-fly command (INFIELD_FLY_COMMAND)
      --pip COMMAND     command used to install Infield Fly (PIP_COMMAND)
      --settings PATH   path of the settings file (SETTINGS_FILE)
      --port PORT       local port of the web interface (WEB_PORT)
      --browse-roots "PATH ..."
                        folders from which downloads can be chosen (BROWSE_ROOTS)
      --apache-conf PATH
                        Apache configuration file to install (APACHE_CONF)
  -h, --help            show this help
EOF
}

fail() {
    echo "deploy.sh: $*" >&2
    exit 1
}

script_directory=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
checkout=$(cd "$script_directory/.." && pwd)

# The commands run with administrator rights, and the directory to which installed paths
# are relative, can be changed for testing.
SUDO=${DEPLOY_SUDO-sudo}
SYSTEMCTL=${DEPLOY_SYSTEMCTL:-systemctl}
APACHECTL=${DEPLOY_APACHECTL:-apache2ctl}
TARGET_ROOT=${DEPLOY_TARGET_ROOT:-}

assume_yes=false
dry_run=false
skip_install=false
env_file="$script_directory/deploy.env"
option_command=""
option_pip=""
option_settings=""
option_port=""
option_browse_roots=""
option_apache_conf=""

option_value() {
    [ $# -ge 2 ] || fail "option $1 needs a value"
    printf '%s' "$2"
}

while [ $# -gt 0 ]; do
    case "$1" in
        -y|--yes) assume_yes=true ;;
        -n|--dry-run) dry_run=true ;;
        --skip-install) skip_install=true ;;
        --env) env_file=$(option_value "$@"); shift ;;
        --command) option_command=$(option_value "$@"); shift ;;
        --pip) option_pip=$(option_value "$@"); shift ;;
        --settings) option_settings=$(option_value "$@"); shift ;;
        --port) option_port=$(option_value "$@"); shift ;;
        --browse-roots) option_browse_roots=$(option_value "$@"); shift ;;
        --apache-conf) option_apache_conf=$(option_value "$@"); shift ;;
        -h|--help) usage; exit 0 ;;
        *) usage >&2; fail "unknown option $1" ;;
    esac
    shift
done

if [ "$(id -u)" -eq 0 ]; then
    fail "run this as the account that runs Infield Fly, not as root"
fi

if [ -f "$env_file" ]; then
    echo "Reading settings from $env_file"
    # shellcheck source=/dev/null
    . "$env_file"
fi
if [ -n "$option_command" ]; then INFIELD_FLY_COMMAND=$option_command; fi
if [ -n "$option_pip" ]; then PIP_COMMAND=$option_pip; fi
if [ -n "$option_settings" ]; then SETTINGS_FILE=$option_settings; fi
if [ -n "$option_port" ]; then WEB_PORT=$option_port; fi
if [ -n "$option_browse_roots" ]; then BROWSE_ROOTS=$option_browse_roots; fi
if [ -n "$option_apache_conf" ]; then APACHE_CONF=$option_apache_conf; fi

user=$(whoami)

# Work out this host's values for the deployment files, checking those that don't depend
# on installing Infield Fly before installing it.
SETTINGS_FILE=${SETTINGS_FILE:-${INFIELD_FLY_CONFIG:-$HOME/.config/infield-fly/settings.json}}
WEB_PORT=${WEB_PORT:-8642}
if [ -z "${APACHE_CONF+set}" ]; then
    if [ -d "$TARGET_ROOT/etc/apache2/userconf-enabled" ]; then
        APACHE_CONF=/etc/apache2/userconf-enabled/infield-fly.conf
    else
        APACHE_CONF=""
    fi
fi

check_path() {
    case "$1" in
        *[[:space:]]*) fail "the path '$1' contains a space, which the systemd unit can't use" ;;
        /*) ;;
        *) fail "the path '$1' must be absolute" ;;
    esac
}

case "$WEB_PORT" in
    ''|*[!0-9]*) fail "the web interface port '$WEB_PORT' is not a number" ;;
esac
check_path "$checkout"
check_path "$SETTINGS_FILE"
BROWSE_ROOT_OPTIONS=""
for root in ${BROWSE_ROOTS:-}; do
    check_path "$root"
    [ -d "$root" ] || echo "Warning: the browse root $root does not exist" >&2
    BROWSE_ROOT_OPTIONS="$BROWSE_ROOT_OPTIONS --browse-root $root"
done
BROWSE_ROOT_OPTIONS=${BROWSE_ROOT_OPTIONS# }
if [ -n "$APACHE_CONF" ] && [ ! -d "$TARGET_ROOT$(dirname "$APACHE_CONF")" ]; then
    fail "the directory of the Apache file $APACHE_CONF does not exist"
fi

# Install Infield Fly, with its web interface dependencies.
if [ -z "${PIP_COMMAND:-}" ]; then
    if [ -x "$checkout/.venv/bin/pip" ]; then
        PIP_COMMAND="$checkout/.venv/bin/pip install"
    else
        PIP_COMMAND="python3 -m pip install --user"
    fi
fi
if $skip_install; then
    echo "Skipping installing Infield Fly"
elif $dry_run; then
    echo "Dry run: would run $PIP_COMMAND -e $checkout[web]"
else
    echo "Installing Infield Fly with: $PIP_COMMAND -e $checkout[web]"
    # PIP_COMMAND is a command with arguments, so it is split into words.
    # shellcheck disable=SC2086
    $PIP_COMMAND -e "$checkout[web]"
fi

if [ -z "${INFIELD_FLY_COMMAND:-}" ]; then
    if [ -x "$checkout/.venv/bin/infield-fly" ]; then
        INFIELD_FLY_COMMAND="$checkout/.venv/bin/infield-fly"
    elif INFIELD_FLY_COMMAND=$(command -v infield-fly); then
        :
    else
        INFIELD_FLY_COMMAND="$HOME/.local/bin/infield-fly"
    fi
fi
check_path "$INFIELD_FLY_COMMAND"
[ -x "$INFIELD_FLY_COMMAND" ] || $dry_run \
    || fail "the infield-fly command $INFIELD_FLY_COMMAND does not exist; set INFIELD_FLY_COMMAND"
[ -f "$SETTINGS_FILE" ] || echo "Warning: the settings file $SETTINGS_FILE does not exist" >&2

echo "Deploying for user $user:"
echo "  infield-fly command: $INFIELD_FLY_COMMAND"
echo "  settings file:       $SETTINGS_FILE"
echo "  web interface port:  $WEB_PORT"
echo "  browse roots:        ${BROWSE_ROOTS:-(none)}"
echo "  Apache file:         ${APACHE_CONF:-(not installed)}"
echo

work_directory=$(mktemp -d)
trap 'rm -rf "$work_directory"' EXIT

# Fills in the values in capitals in a deployment file, except in comments, which
# describe them.
render() {
    local line
    while IFS= read -r line || [ -n "$line" ]; do
        case "$line" in
            '#'*) ;;
            *)
                line=${line//YOUR_USER/$user}
                line=${line//INFIELD_FLY_CHECKOUT/$checkout}
                line=${line//INFIELD_FLY_COMMAND/$INFIELD_FLY_COMMAND}
                line=${line//SETTINGS_FILE/$SETTINGS_FILE}
                line=${line//WEB_PORT/$WEB_PORT}
                line=${line//BROWSE_ROOT_OPTIONS/$BROWSE_ROOT_OPTIONS}
                # Leave no trailing space where an empty value was filled in.
                while [ "${line% }" != "$line" ]; do line=${line% }; done
                ;;
        esac
        printf '%s\n' "$line"
    done < "$1"
}

confirm() {
    local answer
    if $assume_yes; then
        return 0
    fi
    read -r -p "$1 [y/N] " answer || return 1
    case "$answer" in
        y|Y|yes|Yes|YES) return 0 ;;
        *) return 1 ;;
    esac
}

# Fills in a deployment file and installs it at a path, if it differs from the installed
# file. Sets "installed" to whether it was installed, and "previous" to a copy of the
# file it replaced, or to "" if there was none.
install_file() {
    local template="$script_directory/$1" target=$2
    local destination="$TARGET_ROOT$target" rendered
    rendered="$work_directory/$(basename "$target").new"
    previous=""
    installed=false
    render "$template" > "$rendered"

    if [ -e "$destination" ]; then
        previous="$work_directory/$(basename "$target").previous"
        if [ -r "$destination" ]; then
            cp "$destination" "$previous"
        else
            $SUDO cat "$destination" > "$previous"
        fi
        if cmp -s "$previous" "$rendered"; then
            echo "Up to date: $target"
            return
        fi
        echo "Changes to $target:"
        diff -u -L "$target (installed)" -L "$target (new)" "$previous" "$rendered" || true
    else
        echo "New file: $target"
        diff -u -L /dev/null -L "$target (new)" /dev/null "$rendered" || true
    fi

    if $dry_run; then
        echo "Dry run: not installing $target"
    elif confirm "Install $target?"; then
        $SUDO install -m 0644 "$rendered" "$destination"
        installed=true
        echo "Installed $target"
    else
        echo "Skipped $target"
    fi
    echo
}

service_target=/etc/systemd/system/infield-fly-web.service
install_file infield-fly-web.service "$service_target"
service_installed=$installed
service_was_new=false
if [ -z "$previous" ]; then service_was_new=true; fi
if $service_installed; then
    $SUDO $SYSTEMCTL daemon-reload
fi

install_file pam.d/infield-fly /etc/pam.d/infield-fly

if [ -n "$APACHE_CONF" ]; then
    install_file apache-infield-fly.conf "$APACHE_CONF"
    if $installed; then
        if $SUDO $APACHECTL configtest; then
            $SUDO $SYSTEMCTL reload apache2
            echo "Reloaded Apache"
        else
            # Put back the configuration that worked, so Apache isn't left unable to start.
            if [ -n "$previous" ]; then
                $SUDO install -m 0644 "$previous" "$TARGET_ROOT$APACHE_CONF"
            else
                $SUDO rm -f "$TARGET_ROOT$APACHE_CONF"
            fi
            fail "Apache rejected the new configuration, so the previous one was restored"
        fi
    fi
else
    echo "Not installing the Apache configuration; set APACHE_CONF to the file to install,"
    echo "which must be included by the site's HTTPS virtual host."
    echo
fi

if [ -d "$TARGET_ROOT/etc/fail2ban" ]; then
    install_file fail2ban/filter.d/infield-fly.conf /etc/fail2ban/filter.d/infield-fly.conf
    fail2ban_changed=$installed
    install_file fail2ban/jail.d/infield-fly.conf /etc/fail2ban/jail.d/infield-fly.conf
    if $installed || $fail2ban_changed; then
        $SUDO $SYSTEMCTL reload fail2ban
        echo "Reloaded fail2ban"
    fi
else
    echo "fail2ban is not installed, so failed logins won't be banned. Install it with"
    echo "sudo apt install fail2ban, then run this again."
    echo
fi

if $dry_run; then
    echo "Dry run complete; nothing was changed."
elif $service_installed && $service_was_new; then
    echo "Start the web interface with: sudo systemctl enable --now infield-fly-web"
else
    echo "Restart the web interface to use the new code with:"
    echo "  sudo systemctl restart infield-fly-web"
fi
