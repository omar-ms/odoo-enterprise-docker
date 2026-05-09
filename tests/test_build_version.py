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
