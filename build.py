#!/usr/bin/env python3
import os
import html
import re
import sys
import time
import shutil
import getpass
import argparse
import subprocess
import urllib.request
from pathlib import Path
from datetime import datetime
from urllib.parse import urlencode, urlparse, parse_qs, unquote, urljoin


# -----------------------------
# Defaults
# -----------------------------

DEFAULT_ODOO_VERSION = "19.0"

# Public-repo safe default.
# This is only a local Docker image name, not a registry path.
DEFAULT_IMAGE_REPO = "odoo-ee"

DEB_DIR = Path("deb")
DOCKER_BUILD_DEB = "odoo_enterprise.deb"

ODOO_DOWNLOAD_PAGE = "https://www.odoo.com/thanks/download"


# -----------------------------
# CLI
# -----------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build Odoo Enterprise Docker image from latest .deb or subscription key."
    )

    parser.add_argument(
        "--yes",
        action="store_true",
        help="Non-interactive mode. Automatically confirms important steps.",
    )

    parser.add_argument(
        "--odoo-version",
        default=None,
        help="Odoo version, example: 19.0",
    )

    parser.add_argument(
        "--image-name",
        default=None,
        help="Local Docker image name, example: odoo-ee:19.0",
    )

    package_group = parser.add_mutually_exclusive_group()

    package_group.add_argument(
        "--use-latest",
        action="store_true",
        help="Use latest detected .deb from deb/.",
    )

    package_group.add_argument(
        "--sub-key",
        default=None,
        help="Odoo subscription key. Not recommended because it may appear in shell history.",
    )

    package_group.add_argument(
        "--sub-key-file",
        default=None,
        help="Path to file containing Odoo subscription key.",
    )

    parser.add_argument(
        "--push",
        action="store_true",
        help="Push image to a container registry after build.",
    )

    parser.add_argument(
        "--no-push",
        action="store_true",
        help="Do not push image after build.",
    )

    parser.add_argument(
        "--registry-image",
        "--dockerhub-image",
        dest="registry_image",
        default=None,
        help="Full registry image name, example: docker.io/username/odoo-ee:19.0",
    )

    parser.add_argument(
        "--registry-login",
        "--docker-login",
        dest="registry_login",
        action="store_true",
        help="Run docker login before pushing.",
    )

    return parser.parse_args()


# -----------------------------
# Basic helpers
# -----------------------------

def confirm(question: str, default: bool = False, auto_yes: bool = False) -> bool:
    if auto_yes:
        print(f"{question} [auto-yes]")
        return True

    suffix = "[Y/n]" if default else "[y/N]"
    answer = input(f"{question} {suffix}: ").strip().lower()

    if not answer:
        return default

    return answer in {"y", "yes"}


def run(cmd: list[str]) -> None:
    print()
    print("+ " + " ".join(cmd))
    subprocess.run(cmd, check=True)


def need_cmd(name: str) -> None:
    if not shutil.which(name):
        raise SystemExit(f"Missing required command: {name}")


def ask_odoo_version(args: argparse.Namespace) -> str:
    if args.odoo_version:
        return args.odoo_version

    print()
    version = input(f"Enter Odoo version [{DEFAULT_ODOO_VERSION}]: ").strip()
    return version or DEFAULT_ODOO_VERSION


def get_odoo_major(version: str) -> str:
    return version.split(".")[0]


def get_platform_version(version: str) -> str:
    major = get_odoo_major(version)
    return f"deb_{major}e"


def get_default_image_name(odoo_version: str) -> str:
    return f"{DEFAULT_IMAGE_REPO}:{odoo_version}"


def ask_image_name(odoo_version: str, args: argparse.Namespace) -> str:
    if args.image_name:
        return args.image_name

    if args.yes:
        return get_default_image_name(odoo_version)

    default_image_name = get_default_image_name(odoo_version)

    print()
    image_name = input(f"Enter local Docker image name [{default_image_name}]: ").strip()
    return image_name or default_image_name


def ask_registry_image_name(local_image_name: str, args: argparse.Namespace) -> str:
    if args.registry_image:
        return args.registry_image

    if args.yes:
        raise SystemExit(
            "Push requested, but no registry image was provided. "
            "Use --registry-image, example: docker.io/username/odoo-ee:19.0"
        )

    print()
    print("Registry image names should look like:")
    print("docker.io/username/repository:tag")
    print("ghcr.io/owner/repository:tag")
    print("registry.gitlab.com/group/project/repository:tag")
    print()
    print(f"Local image name: {local_image_name}")

    registry_image = input("Enter full registry image name: ").strip()

    if not registry_image:
        raise SystemExit("Registry image name is required to push.")

    return registry_image


def format_bytes(size: float) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]

    for unit in units:
        if size < 1024:
            return f"{size:.1f} {unit}"

        size /= 1024

    return f"{size:.1f} PB"


def download_with_progress(response, destination: Path) -> None:
    total_header = response.headers.get("Content-Length")

    try:
        total_size = int(total_header) if total_header else 0
    except ValueError:
        total_size = 0

    downloaded = 0
    chunk_size = 1024 * 1024
    start_time = time.time()

    with destination.open("wb") as file:
        while True:
            chunk = response.read(chunk_size)

            if not chunk:
                break

            file.write(chunk)
            downloaded += len(chunk)

            elapsed = max(time.time() - start_time, 0.001)
            speed = downloaded / elapsed

            if total_size > 0:
                percent = downloaded / total_size * 100
                bar_width = 30
                filled = int(bar_width * downloaded / total_size)
                bar = "#" * filled + "-" * (bar_width - filled)

                message = (
                    f"\rDownloading: [{bar}] "
                    f"{percent:6.2f}% "
                    f"{format_bytes(downloaded)} / {format_bytes(total_size)} "
                    f"at {format_bytes(speed)}/s"
                )
            else:
                message = (
                    f"\rDownloading: "
                    f"{format_bytes(downloaded)} "
                    f"at {format_bytes(speed)}/s"
                )

            print(message, end="", flush=True)

    print()


# -----------------------------
# .deb helpers
# -----------------------------

def validate_deb(path: Path) -> None:
    if not path.exists():
        raise SystemExit(f"File not found: {path}")

    size = path.stat().st_size

    if size < 10_000_000:
        raise SystemExit(f"Invalid .deb: file is too small: {size} bytes")

    with path.open("rb") as file:
        magic = file.read(8)

    if not magic.startswith(b"!<arch>"):
        raise SystemExit(f"Invalid .deb: {path} does not look like a Debian package")

    if shutil.which("dpkg-deb"):
        result = subprocess.run(
            ["dpkg-deb", "--info", str(path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        if result.returncode != 0:
            raise SystemExit(f"Invalid .deb: dpkg-deb could not read {path}")


def extract_release_date(path: Path) -> int:
    match = re.search(r"(20\d{6})", path.name)

    if match:
        return int(match.group(1))

    return 0


def extract_release_version(path: Path) -> str:
    match = re.match(r"^odoo_[^+]+\+e\.(\d{8})_all\.deb$", path.name)

    if match:
        return match.group(1)

    return "unknown"


def deb_matches_version(path: Path, odoo_version: str) -> bool:
    pattern = rf"^odoo_{re.escape(odoo_version)}\+e\.\d{{8}}_all\.deb$"
    return re.match(pattern, path.name) is not None


def find_latest_deb(odoo_version: str) -> Path | None:
    DEB_DIR.mkdir(exist_ok=True)

    debs = [
        deb for deb in DEB_DIR.glob("*.deb")
        if deb_matches_version(deb, odoo_version)
    ]

    if not debs:
        return None

    debs.sort(
        key=lambda p: (
            extract_release_date(p),
            p.stat().st_mtime,
        ),
        reverse=True,
    )

    return debs[0]


def list_debs(odoo_version: str) -> None:
    DEB_DIR.mkdir(exist_ok=True)

    debs = [
        deb for deb in DEB_DIR.glob("*.deb")
        if deb_matches_version(deb, odoo_version)
    ]

    if not debs:
        print(f"No Odoo {odoo_version} .deb files found in deb/")
        return

    debs.sort(
        key=lambda p: (
            extract_release_date(p),
            p.stat().st_mtime,
        ),
        reverse=True,
    )

    print(f"Detected Odoo {odoo_version} .deb files:")

    for index, deb in enumerate(debs, start=1):
        release_date = extract_release_date(deb)
        release_text = str(release_date) if release_date else "no release date"
        modified = datetime.fromtimestamp(deb.stat().st_mtime).strftime(
            "%Y-%m-%d %H:%M"
        )

        print(
            f"{index}) {deb.name} "
            f"| release: {release_text} "
            f"| modified: {modified}"
        )


def check_dockerignore(docker_build_deb: str, args: argparse.Namespace) -> None:
    dockerignore = Path(".dockerignore")

    if not dockerignore.exists():
        return

    lines = dockerignore.read_text(errors="ignore").splitlines()
    blocked_patterns = {
        "*.deb",
        "**/*.deb",
        docker_build_deb,
        f"./{docker_build_deb}",
    }

    for line in lines:
        clean = line.strip()

        if not clean or clean.startswith("#") or clean.startswith("!"):
            continue

        if clean in blocked_patterns:
            print()
            print("WARNING: .dockerignore may block the temporary .deb file:")
            print(f"Matched pattern: {clean}")
            print()
            print("The Dockerfile needs the temporary root file:")
            print(f"./{docker_build_deb}")
            print()

            if not confirm("Continue anyway?", auto_yes=args.yes):
                raise SystemExit("Cancelled.")


def filename_from_response(response, fallback_url: str, odoo_version: str) -> str:
    content_disposition = response.headers.get("Content-Disposition", "")

    match = re.search(r"filename\*=UTF-8''([^;]+)", content_disposition)

    if match:
        return Path(unquote(match.group(1)).strip('"')).name

    match = re.search(r'filename="?([^";]+)"?', content_disposition)

    if match:
        return Path(match.group(1).strip('"')).name

    final_url = response.geturl() if hasattr(response, "geturl") else fallback_url
    url_path = urlparse(final_url).path
    name = Path(unquote(url_path)).name

    if name.endswith(".deb"):
        return name

    today = datetime.now().strftime("%Y%m%d")
    return f"odoo_{odoo_version}+e.{today}_all.deb"


# -----------------------------
# Odoo download flow
# -----------------------------

def resolve_odoo_cdn_url_from_subscription_key(
    subscription_code: str,
    platform_version: str,
    args: argparse.Namespace,
) -> str:
    query = urlencode(
        {
            "code": subscription_code,
            "platform_version": platform_version,
        }
    )

    url = f"{ODOO_DOWNLOAD_PAGE}?{query}"

    print()
    print("Odoo download page:")
    print(f"{ODOO_DOWNLOAD_PAGE}?code=***&platform_version={platform_version}")

    if not confirm("Request Odoo download page now?", default=True, auto_yes=args.yes):
        raise SystemExit("Cancelled.")

    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0",
        },
    )

    print()
    print("Requesting Odoo download page...")

    with urllib.request.urlopen(request, timeout=120) as response:
        page = response.read().decode("utf-8", errors="replace")

    hrefs = re.findall(r"""href=['"]([^'"]+)['"]""", page)

    for href in hrefs:
        href = html.unescape(href)
        href = urljoin(url, href)

        if "download.odoocdn.com" not in href:
            continue

        if "payload=" not in href:
            continue

        parsed = urlparse(href)
        payload = parse_qs(parsed.query).get("payload", [""])[0]

        if not payload:
            continue

        print()
        print("Found Odoo CDN download link.")
        print("Payload detected successfully.")
        return href

    raise SystemExit(
        "Could not find Odoo CDN download link in the HTML page. "
        "Check the subscription key or selected Odoo version."
    )


def download_deb(download_url: str, odoo_version: str, args: argparse.Namespace) -> Path:
    DEB_DIR.mkdir(exist_ok=True)

    print()
    print("Ready to download Enterprise .deb into:")
    print(DEB_DIR.resolve())

    if not confirm("Download now?", default=True, auto_yes=args.yes):
        raise SystemExit("Cancelled.")

    request = urllib.request.Request(
        download_url,
        headers={
            "User-Agent": "Mozilla/5.0",
        },
    )

    print()
    print("Downloading Odoo Enterprise .deb...")

    with urllib.request.urlopen(request, timeout=300) as response:
        filename = filename_from_response(response, download_url, odoo_version)

        if not filename.endswith(".deb"):
            today = datetime.now().strftime("%Y%m%d")
            filename = f"odoo_{odoo_version}+e.{today}_all.deb"

        final_path = DEB_DIR / filename
        tmp_path = DEB_DIR / f"{filename}.download"

        download_with_progress(response, tmp_path)

    print()
    print(f"Downloaded temporary file: {tmp_path}")

    validate_deb(tmp_path)

    if final_path.exists():
        print()
        print(f"File already exists: {final_path}")

        replace = args.yes or confirm("Replace existing file?", default=False)

        if replace:
            final_path.unlink()
        else:
            tmp_path.unlink()
            print(f"Keeping existing file: {final_path}")
            return final_path

    tmp_path.rename(final_path)

    print()
    print(f"Saved .deb as: {final_path}")
    return final_path


def read_subscription_key(args: argparse.Namespace) -> str:
    if args.sub_key:
        return args.sub_key.strip()

    if args.sub_key_file:
        key_path = Path(args.sub_key_file)

        if not key_path.exists():
            raise SystemExit(f"Subscription key file not found: {key_path}")

        return key_path.read_text(encoding="utf-8").strip()

    if args.yes:
        raise SystemExit(
            "No .deb found and no subscription key provided. "
            "Use --sub-key-file /path/to/key.txt or --sub-key."
        )

    print()
    return getpass.getpass("Enter Odoo subscription key: ").strip()


def download_with_subscription_key(odoo_version: str, args: argparse.Namespace) -> Path:
    sub_key = read_subscription_key(args)

    if not sub_key:
        raise SystemExit("Subscription key is empty.")

    platform_version = get_platform_version(odoo_version)

    cdn_url = resolve_odoo_cdn_url_from_subscription_key(
        sub_key,
        platform_version,
        args,
    )

    return download_deb(cdn_url, odoo_version, args)


# -----------------------------
# Package selection
# -----------------------------

def choose_package(odoo_version: str, args: argparse.Namespace) -> Path:
    print()
    list_debs(odoo_version)

    latest = find_latest_deb(odoo_version)

    if args.use_latest:
        if not latest:
            raise SystemExit(
                f"--use-latest was set, but no Odoo {odoo_version} .deb was found in deb/."
            )

        print()
        print(f"Using latest detected .deb: {latest}")
        validate_deb(latest)
        return latest

    if args.sub_key or args.sub_key_file:
        print()
        print("Subscription key option provided. Downloading new package.")
        return download_with_subscription_key(odoo_version, args)

    if latest:
        print()
        print(f"Latest detected .deb: {latest}")

        if confirm("Use latest detected .deb?", default=True, auto_yes=args.yes):
            validate_deb(latest)
            return latest

        print()
        print("You chose not to use the latest detected .deb.")
        print("Subscription key will be used to download a new package.")
        return download_with_subscription_key(odoo_version, args)

    print()
    print(f"No Odoo {odoo_version} .deb file detected in deb/.")
    print("Subscription key is required to download Odoo Enterprise.")

    return download_with_subscription_key(odoo_version, args)


# -----------------------------
# Temporary Dockerfile package
# -----------------------------

class TemporaryDockerDeb:
    def __init__(self, source: Path, args: argparse.Namespace):
        self.source = source
        self.target = Path(DOCKER_BUILD_DEB)
        self.backup = None
        self.args = args

    def __enter__(self):
        validate_deb(self.source)

        print()
        print("Dockerfile expects:")
        print(f"./{self.target}")
        print()
        print("Selected real package:")
        print(self.source)
        print()

        if not confirm(
            "Prepare temporary Docker build .deb copy?",
            default=True,
            auto_yes=self.args.yes,
        ):
            raise SystemExit("Cancelled.")

        if self.target.exists():
            timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
            self.backup = Path(f"{self.target}.backup-{timestamp}")

            print()
            print(f"Existing {self.target} found.")
            print(f"It will be backed up as: {self.backup}")

            if not confirm(
                "Backup existing file and continue?",
                default=True,
                auto_yes=self.args.yes,
            ):
                raise SystemExit("Cancelled.")

            self.target.rename(self.backup)

        shutil.copy2(self.source, self.target)

        print()
        print(f"Prepared Docker build package: {self.target}")
        return self.target

    def __exit__(self, exc_type, exc, tb):
        if self.target.exists():
            self.target.unlink()

        if self.backup and self.backup.exists():
            self.backup.rename(self.target)


# -----------------------------
# Docker build and push
# -----------------------------

def build_image(
    image_name: str,
    selected_deb: Path,
    odoo_version: str,
    args: argparse.Namespace,
) -> None:
    odoo_release = extract_release_version(selected_deb)
    build_args = [
        "--build-arg",
        f"ODOO_VERSION={odoo_version}",
        "--build-arg",
        f"ODOO_RELEASE={odoo_release}",
    ]

    print()
    print("Build summary:")
    print(f"Local image name: {image_name}")
    print(f"Odoo version: {odoo_version}")
    print(f"Odoo release: {odoo_release}")
    print(f"Selected .deb: {selected_deb}")
    print(f"Dockerfile temporary .deb: ./{DOCKER_BUILD_DEB}")
    print()
    print(
        "Build command: "
        f"docker build {' '.join(build_args)} -t {image_name} ."
    )
    print()

    if not confirm("Start Docker build now?", default=True, auto_yes=args.yes):
        raise SystemExit("Build cancelled.")

    with TemporaryDockerDeb(selected_deb, args):
        run(["docker", "build", *build_args, "-t", image_name, "."])


def should_push(args: argparse.Namespace) -> bool:
    if args.no_push:
        return False

    if args.push:
        return True

    if args.yes:
        return False

    return confirm("Push image to a container registry now?", default=False)


def push_to_registry(local_image_name: str, args: argparse.Namespace) -> None:
    print()

    if not should_push(args):
        print("Skipping registry push.")
        return

    registry_image_name = ask_registry_image_name(local_image_name, args)

    if registry_image_name != local_image_name:
        print()
        print(f"Local image: {local_image_name}")
        print(f"Registry image: {registry_image_name}")

        if not confirm("Tag local image for registry?", default=True, auto_yes=args.yes):
            raise SystemExit("Push cancelled.")

        run(["docker", "tag", local_image_name, registry_image_name])

    print()

    if args.registry_login or (
        not args.yes and confirm("Run docker login first?", default=False)
    ):
        run(["docker", "login"])

    print()
    print(f"Push command: docker push {registry_image_name}")

    if not confirm("Run Docker push now?", default=True, auto_yes=args.yes):
        raise SystemExit("Push cancelled.")

    run(["docker", "push", registry_image_name])

    print()
    print(f"Pushed: {registry_image_name}")


# -----------------------------
# Main
# -----------------------------

def main() -> None:
    args = parse_args()

    if args.push and args.no_push:
        raise SystemExit("Use either --push or --no-push, not both.")

    os.chdir(Path(__file__).resolve().parent)

    need_cmd("docker")

    DEB_DIR.mkdir(exist_ok=True)

    odoo_version = ask_odoo_version(args)
    platform_version = get_platform_version(odoo_version)

    print()
    print("Odoo Enterprise Docker builder")
    print("Dockerfile stores the selected version and release as image metadata.")
    print()
    print(f"Odoo version: {odoo_version}")
    print(f"Odoo platform_version: {platform_version}")
    print(f"Default local image: {get_default_image_name(odoo_version)}")
    print(f"Deb folder: {DEB_DIR}/")
    print(f"Dockerfile temporary package: ./{DOCKER_BUILD_DEB}")

    check_dockerignore(DOCKER_BUILD_DEB, args)

    selected_deb = choose_package(odoo_version, args)

    image_name = ask_image_name(odoo_version, args)

    build_image(image_name, selected_deb, odoo_version, args)

    print()
    print(f"Build finished: {image_name}")

    push_to_registry(image_name, args)

    print()
    print("Done.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print()
        print("Cancelled.")
        sys.exit(1)
