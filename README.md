# Odoo Enterprise Docker Build

Builds a custom Odoo Enterprise Docker image from an Odoo Enterprise `.deb`
package. The build tool can reuse a local package from `deb/` or download a new
package with an Odoo subscription key, then build locally and optionally push to
a container registry.

## Project Files

- `build.py` - main interactive and non-interactive build tool
- `build.sh` - Bash wrapper for `build.py`
- `Dockerfile` - Ubuntu Noble based Odoo Enterprise runtime image
- `entrypoint.sh` - starts Odoo and waits for PostgreSQL when needed
- `wait-for-psql.py` - PostgreSQL readiness helper used by the entrypoint
- `odoo.conf` - default Odoo configuration copied into the image
- `deb/` - local cache for downloaded Odoo Enterprise `.deb` packages
- `.github/workflows/build-odoo-ee.yml` - manual and scheduled GitHub Actions build

## Requirements

- Docker
- Python 3
- A valid Odoo Enterprise subscription key, unless a matching `.deb` already
  exists in `deb/`
- Registry credentials only when pushing images

## Local Build

Run the interactive builder:

```bash
python3 build.py
```

On Windows PowerShell:

```powershell
python build.py
```

The default local image name is `odoo-ee:<version>`, for example
`odoo-ee:19.0`.

## Common Local Commands

Use the latest cached Odoo `19.0` package from `deb/` and build without prompts:

```bash
python3 build.py --yes --odoo-version 19.0 --use-latest --image-name odoo-ee:19.0 --no-push
```

Download a fresh package using a key file and build locally:

```bash
python3 build.py --odoo-version 19.0 --sub-key-file ./odoo-sub-key.txt --image-name odoo-ee:19.0 --no-push
```

Build and push to a registry:

```bash
python3 build.py \
  --odoo-version 19.0 \
  --use-latest \
  --image-name odoo-ee:19.0 \
  --push \
  --registry-image docker.io/username/odoo-ee:19.0
```

Run through the Bash wrapper:

```bash
./build.sh --odoo-version 19.0 --use-latest --image-name odoo-ee:19.0 --no-push
```

## Subscription Key Handling

Prefer entering the subscription key interactively or passing a local file with
`--sub-key-file`. Avoid `--sub-key` for normal use because command-line
arguments may be stored in shell history.

Downloaded packages are saved under `deb/`. Future local builds can reuse the
latest matching package with `--use-latest`.

## Build Behavior

The Dockerfile expects a root-level package named `odoo_19_enterprise.deb`.
`build.py` keeps the Dockerfile unchanged by temporarily copying the selected
package into that expected filename, then running:

```bash
docker build -t <local-image-name> .
```

After the build finishes, the temporary package is removed. If a file with the
same name already existed, it is backed up and restored.

`.gitignore` excludes `.deb` packages, temporary downloads, backup files, and
the local subscription key file. `.dockerignore` excludes the `deb/` cache,
GitHub metadata, Python cache files, temporary downloads, backups, and
`odoo-sub-key.txt` from the Docker build context. The temporary root-level
`odoo_19_enterprise.deb` remains available to the Dockerfile during the build.

## GitHub Actions

Workflow file:
[`.github/workflows/build-odoo-ee.yml`](.github/workflows/build-odoo-ee.yml)

The workflow can be run manually from the GitHub Actions tab or by its daily
schedule. Configure repository secrets and variables in:

```text
GitHub repository -> Settings -> Secrets and variables -> Actions
```

### Required Repository Secrets

| Secret                  | Required When     | Purpose                                                     |
| ----------------------- | ----------------- | ----------------------------------------------------------- |
| `ODOO_SUBSCRIPTION_KEY` | Always            | Downloads the Odoo Enterprise `.deb` package.                |
| `REGISTRY_USERNAME`     | Only when pushing | Username for`docker/login-action`.                          |
| `REGISTRY_TOKEN`        | Only when pushing | Registry password or access token for `docker/login-action`. |

For Docker Hub, `REGISTRY_USERNAME` is usually your Docker Hub username and
`REGISTRY_TOKEN` should be a Docker Hub access token rather than your account
password.

### Repository Variables

| Variable           | Required When             | Default                      | Purpose                                             |
| ------------------ | ------------------------- | ---------------------------- | --------------------------------------------------- |
| `SCHEDULE_ENABLED` | Only for scheduled builds | Disabled unless set to `true` | Allows the daily scheduled workflow to run.         |
| `BUILD_BRANCH`     | Optional                  | Current workflow branch      | Branch checked out by scheduled/defaulted runs.     |
| `ODOO_VERSION`     | Optional                  | `19.0`                       | Odoo version to download and build.                 |
| `PUSH_IMAGE`       | Optional                  | `false`                      | Set to `true` to push after the image is built.      |
| `IMAGE_REGISTRY`   | Optional                  | `docker.io`                  | Registry host, for example`docker.io` or `ghcr.io`. |
| `IMAGE_REPOSITORY` | Required when pushing     | Empty                        | Registry repository, for example`username/odoo-ee`. |
| `IMAGE_TAG`        | Optional                  | Odoo version                 | Registry image tag.                                 |

Manual workflow inputs use the same names in lowercase:

- `build_branch`
- `odoo_version`
- `push_image`
- `image_registry`
- `image_repository`
- `image_tag`

Manual inputs take precedence when provided. Empty manual inputs fall back to
repository variables where the workflow allows it, then to the workflow default.

### Scheduled Builds

The scheduled workflow runs daily at `21:30` UTC, which is `00:30` in
Asia/Riyadh. Scheduled builds only run when:

```text
SCHEDULE_ENABLED=true
```

For a scheduled build that also pushes an image, set at least:

```text
SCHEDULE_ENABLED=true
PUSH_IMAGE=true
IMAGE_REGISTRY=docker.io
IMAGE_REPOSITORY=username/odoo-ee
```

And configure these secrets:

```text
ODOO_SUBSCRIPTION_KEY
REGISTRY_USERNAME
REGISTRY_TOKEN
```

The workflow builds a local image named `odoo-ee:<tag>`, then tags and pushes
`<registry>/<repository>:<tag>` only when pushing is enabled. It also frees disk
space on the GitHub runner before building and removes sensitive or temporary
files after the build.

## Image Runtime

The image exposes the standard Odoo ports:

- `8069` - HTTP
- `8071` - HTTPS/XML-RPC secure port
- `8072` - longpolling/websocket port

It defines these volumes:

- `/var/lib/odoo`
- `/mnt/extra-addons`

Database connection settings can be passed through environment variables used
by `entrypoint.sh`:

```bash
docker run --rm \
  -e HOST=db \
  -e PORT=5432 \
  -e USER=odoo \
  -e PASSWORD=odoo \
  -p 8069:8069 \
  odoo-ee:19.0
```

The entrypoint waits for PostgreSQL before starting Odoo, except when running
`odoo scaffold` or a custom command.

## CLI Reference

```text
python3 build.py [--yes] [--odoo-version VERSION] [--image-name IMAGE]
                 [--use-latest | --sub-key KEY | --sub-key-file FILE]
                 [--push] [--no-push] [--registry-image IMAGE]
                 [--registry-login]
```

`--dockerhub-image` and `--docker-login` are accepted aliases for
`--registry-image` and `--registry-login`.

Use `python3 build.py --help` for the latest option descriptions.

