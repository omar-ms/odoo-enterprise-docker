import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import build


class OdooVersionSelectionTests(unittest.TestCase):
    def test_temp_docker_deb_name_is_fixed(self):
        self.assertEqual(build.DOCKER_BUILD_DEB, "odoo_enterprise.deb")

    def test_deb_matches_version_accepts_only_matching_official_names(self):
        self.assertTrue(
            build.deb_matches_version(
                Path("deb/odoo_18.0+e.20260509_all.deb"),
                "18.0",
            )
        )
        self.assertTrue(
            build.deb_matches_version(
                Path("deb/odoo_19.0+e.20260509_all.deb"),
                "19.0",
            )
        )
        self.assertFalse(
            build.deb_matches_version(
                Path("deb/odoo_18_enterprise.deb"),
                "18.0",
            )
        )
        self.assertFalse(
            build.deb_matches_version(
                Path("deb/odoo_19.0.1+e.20260509_all.deb"),
                "19.0",
            )
        )
        self.assertFalse(
            build.deb_matches_version(
                Path("deb/odoo_19.0+e.20260509_amd64.deb"),
                "19.0",
            )
        )
        self.assertFalse(
            build.deb_matches_version(
                Path("deb/odoo_19_enterprise.deb"),
                "18.0",
            )
        )

    def test_build_image_passes_only_target_version_to_docker(self):
        args = SimpleNamespace(yes=True)

        with (
            patch("build.TemporaryDockerDeb") as temporary_deb,
            patch("build.run") as run,
        ):
            temporary_deb.return_value.__enter__.return_value = Path(
                "odoo_enterprise.deb"
            )

            build.build_image(
                "odoo-ee:18.0",
                Path("deb/odoo_18.0+e.20260509_all.deb"),
                "18.0",
                args,
            )

        run.assert_called_once_with(
            [
                "docker",
                "build",
                "--build-arg",
                "ODOO_VERSION=18.0",
                "-t",
                "odoo-ee:18.0",
                ".",
            ]
        )


class DockerfileVersionContractTests(unittest.TestCase):
    def test_dockerfile_keeps_version_metadata_with_fixed_deb_copy(self):
        text = Path("Dockerfile").read_text()

        self.assertIn("ARG ODOO_VERSION=19.0", text)
        self.assertIn("ENV ODOO_VERSION=${ODOO_VERSION}", text)
        self.assertIn('LABEL org.opencontainers.image.version="${ODOO_VERSION}"', text)
        self.assertIn("COPY ./odoo_enterprise.deb /tmp/odoo_enterprise.deb", text)
        self.assertNotIn("ODOO_DEB_NAME", text)


class GitHubActionsContractTests(unittest.TestCase):
    def test_workflow_odoo_version_is_manual_choice_input(self):
        workflow = Path(".github/workflows/build-odoo-ee.yml").read_text()
        readme = Path("README.md").read_text()

        self.assertIn('odoo_version:\n        description: "Odoo version"', workflow)
        self.assertIn('        required: true', workflow)
        self.assertIn('        type: choice', workflow)
        self.assertIn('          - "19.0"', workflow)
        self.assertIn('          - "18.0"', workflow)
        self.assertIn('          - "17.0"', workflow)
        self.assertIn("Manual runs require `odoo_version`, `image_registry`, and `image_repository`.", readme)
        self.assertIn("`odoo_version` is a dropdown with `19.0`, `18.0`, and `17.0`.", readme)

    def test_workflow_image_registry_is_manual_choice_input(self):
        workflow = Path(".github/workflows/build-odoo-ee.yml").read_text()
        readme = Path("README.md").read_text()

        self.assertIn('image_registry:\n        description: "Registry host"', workflow)
        self.assertIn('        required: true', workflow)
        self.assertIn('          - "docker.io"', workflow)
        self.assertIn('          - "ghcr.io"', workflow)
        self.assertIn('          - "registry.gitlab.com"', workflow)
        self.assertIn('          - "quay.io"', workflow)
        self.assertIn('          - "registry.digitalocean.com"', workflow)
        self.assertNotIn('          - "aws-ecr"', workflow)
        self.assertNotIn('          - "google-artifact-registry"', workflow)
        self.assertNotIn('          - "azure-container-registry"', workflow)
        self.assertNotIn('          - "other"', workflow)
        self.assertIn("`image_registry` is a dropdown with `docker.io`, `ghcr.io`,", readme)

    def test_workflow_validates_credentials_before_build(self):
        workflow = Path(".github/workflows/build-odoo-ee.yml").read_text()
        readme = Path("README.md").read_text()

        self.assertIn(
            "IMAGE_REGISTRY: ${{ inputs.image_registry || vars.IMAGE_REGISTRY || '' }}",
            workflow,
        )
        self.assertIn(
            "ODOO_VERSION: ${{ inputs.odoo_version || vars.ODOO_VERSION || '' }}",
            workflow,
        )
        self.assertIn("- name: Validate required config", workflow)
        self.assertIn('echo "ODOO_VERSION is required."', workflow)
        self.assertIn('echo "IMAGE_REGISTRY is required."', workflow)
        self.assertIn('echo "IMAGE_REPOSITORY is required."', workflow)
        self.assertNotIn("Resolve registry host", workflow)
        self.assertNotIn('case "$IMAGE_REGISTRY" in', workflow)
        self.assertIn("- name: Validate registry credentials", workflow)
        self.assertIn("REGISTRY_USERNAME: ${{ secrets.REGISTRY_USERNAME }}", workflow)
        self.assertIn("REGISTRY_TOKEN: ${{ secrets.REGISTRY_TOKEN }}", workflow)
        self.assertIn('docker login "$IMAGE_REGISTRY"', workflow)
        self.assertIn("--password-stdin", workflow)
        self.assertIn("`ODOO_VERSION`,\n`IMAGE_REGISTRY`, and `IMAGE_REPOSITORY` must always be set", readme)

        self.assertLess(
            workflow.index("- name: Validate required config"),
            workflow.index("- name: Show selected config"),
        )
        self.assertLess(
            workflow.index("- name: Validate registry credentials"),
            workflow.index("- name: Build Odoo Enterprise image"),
        )
        self.assertLess(
            workflow.index("- name: Login to container registry"),
            workflow.index("- name: Build Odoo Enterprise image"),
        )

    def test_workflow_always_pushes_and_has_no_push_toggle(self):
        workflow = Path(".github/workflows/build-odoo-ee.yml").read_text()
        readme = Path("README.md").read_text()

        self.assertNotIn("push_image", workflow)
        self.assertNotIn("PUSH_IMAGE", workflow)
        self.assertNotIn("push_image", readme)
        self.assertNotIn("PUSH_IMAGE", readme)
        self.assertNotIn("if: ${{ env.PUSH_IMAGE == 'true' }}", workflow)

        self.assertIn("- name: Validate registry credentials", workflow)
        self.assertIn("- name: Login to container registry", workflow)
        self.assertIn("- name: Tag image for registry", workflow)
        self.assertIn("- name: Push image to registry", workflow)
        self.assertIn('docker push "$REGISTRY_IMAGE"', workflow)
        self.assertIn("GitHub Actions always pushes", readme)

    def test_workflow_manual_image_repository_input_is_required(self):
        workflow = Path(".github/workflows/build-odoo-ee.yml").read_text()
        readme = Path("README.md").read_text()

        self.assertIn(
            'image_repository:\n        description: "Registry repository, example: username/odoo-ee"\n        required: true',
            workflow,
        )
        self.assertNotIn(
            'image_repository:\n        description: "Registry repository, example: username/odoo-ee"\n        required: false',
            workflow,
        )
        self.assertIn("Manual runs require `odoo_version`, `image_registry`, and `image_repository`.", readme)
        self.assertIn("`image_tag` is optional and falls back to the selected Odoo version when left", readme)

    def test_workflow_requires_scheduled_version_and_registry_variables(self):
        workflow = Path(".github/workflows/build-odoo-ee.yml").read_text()
        readme = Path("README.md").read_text()

        self.assertIn(
            "ODOO_VERSION: ${{ inputs.odoo_version || vars.ODOO_VERSION || '' }}",
            workflow,
        )
        self.assertIn(
            "IMAGE_REGISTRY: ${{ inputs.image_registry || vars.IMAGE_REGISTRY || '' }}",
            workflow,
        )
        self.assertIn("| `ODOO_VERSION`     | Always", readme)
        self.assertIn("| `IMAGE_REGISTRY`   | Always", readme)
        self.assertIn("ODOO_VERSION=19.0", readme)

    def test_workflow_does_not_define_custom_branch_selection(self):
        workflow = Path(".github/workflows/build-odoo-ee.yml").read_text()
        readme = Path("README.md").read_text()

        self.assertNotIn("build_branch", workflow)
        self.assertNotIn("BUILD_BRANCH", workflow)
        self.assertNotIn("Checkout selected branch", workflow)
        self.assertNotIn("ref: ${{ env.BUILD_BRANCH }}", workflow)

        self.assertNotIn("BUILD_BRANCH", readme)
        self.assertNotIn("build_branch", readme)


class OdooConfigCompatibilityTests(unittest.TestCase):
    def test_docs_warn_that_odoo_conf_targets_19(self):
        conf = Path("odoo.conf").read_text()
        readme = Path("README.md").read_text()
        official_repo = "https://github.com/odoo/docker/tree/master"

        self.assertIn("Odoo 19.0", conf)
        self.assertIn(official_repo, conf)
        self.assertIn("Odoo 19.0", readme)
        self.assertIn("17.0", readme)
        self.assertIn("18.0", readme)
        self.assertIn(official_repo, readme)


if __name__ == "__main__":
    unittest.main()
