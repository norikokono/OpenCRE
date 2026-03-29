"""
Unit tests for CommitFetcher module (GSoC Module A/B).

Tests cover:
- YAML config loading
- Noise file filtering
- Rate limit handling
- Error handling and logging
"""

import os
import tempfile
import unittest
from unittest.mock import patch, MagicMock
import yaml

from application.utils.commit_fetcher import CommitFetcher


class TestCommitFetcher(unittest.TestCase):
    """Test suite for CommitFetcher class."""

    def setUp(self):
        """Set up test fixtures."""
        self.fetcher = CommitFetcher()

    def test_noise_patterns(self):
        """Test that noise patterns correctly identify junk files."""
        noise_files = [
            "package-lock.json",
            "yarn.lock",
            "CNAME",
            "_config.yml",
            ".github/workflows/test.yml",
            "__pycache__/cache.pyc",
            "node_modules/package/index.js",
            ".DS_Store",
        ]

        for filename in noise_files:
            self.assertTrue(
                self.fetcher._is_noise_file(filename),
                f"Failed to identify {filename} as noise"
            )

    def test_real_files_not_filtered(self):
        """Test that real files are not incorrectly filtered."""
        real_files = [
            "README.md",
            "src/main.py",
            "docs/implementation.md",
            "application/web/web_main.py",
        ]

        for filename in real_files:
            self.assertFalse(
                self.fetcher._is_noise_file(filename),
                f"Incorrectly filtered {filename} as noise"
            )

    def test_load_config_valid(self):
        """Test loading valid YAML config."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False
        ) as f:
            config = {
                "repositories": [
                    "OWASP/ASVS",
                    "OWASP/wstg",
                    "OWASP/CheatSheetSeries",
                ]
            }
            yaml.dump(config, f)
            f.flush()

            try:
                repos = self.fetcher.load_config(f.name)
                self.assertEqual(len(repos), 3)
                self.assertIn("OWASP/ASVS", repos)
            finally:
                os.unlink(f.name)

    def test_load_config_missing_file(self):
        """Test that missing config file raises error."""
        with self.assertRaises(FileNotFoundError):
            self.fetcher.load_config("nonexistent.yaml")

    def test_load_config_invalid_yaml(self):
        """Test that invalid YAML raises error."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False
        ) as f:
            f.write("invalid: yaml: content: [")
            f.flush()

            try:
                with self.assertRaises(yaml.YAMLError):
                    self.fetcher.load_config(f.name)
            finally:
                os.unlink(f.name)

    @patch("application.utils.commit_fetcher.requests.Session.get")
    def test_rate_limit_check(self, mock_get):
        """Test GitHub rate limit checking."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"rate": {"remaining": 100}}
        mock_get.return_value = mock_response

        result = self.fetcher._check_rate_limit()
        self.assertTrue(result)
        self.assertEqual(self.fetcher.rate_limit_remaining, 100)

    @patch("application.utils.commit_fetcher.requests.Session.get")
    def test_rate_limit_exceeded(self, mock_get):
        """Test handling of exceeded rate limit."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"rate": {"remaining": 5}}
        mock_get.return_value = mock_response

        result = self.fetcher._check_rate_limit()
        self.assertFalse(result)

    @patch("application.utils.commit_fetcher.requests.Session.get")
    def test_fetch_commits_success(self, mock_get):
        """Test successful commit fetching."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [
            {
                "sha": "abc123",
                "commit": {"message": "Fix: update docs"},
            },
            {
                "sha": "def456",
                "commit": {"message": "Feature: add new API"},
            },
        ]
        mock_get.return_value = mock_response

        self.fetcher.rate_limit_remaining = 50
        commits = self.fetcher.fetch_commits("OWASP/ASVS")
        
        self.assertIsNotNone(commits)
        self.assertEqual(len(commits), 2)

    @patch("application.utils.commit_fetcher.requests.Session.get")
    def test_fetch_commits_not_found(self, mock_get):
        """Test handling of missing repository."""
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_get.return_value = mock_response

        self.fetcher.rate_limit_remaining = 50
        commits = self.fetcher.fetch_commits("OWASP/NonExistent")
        
        self.assertIsNone(commits)

    @patch("application.utils.commit_fetcher.CommitFetcher._check_rate_limit")
    def test_fetch_commits_rate_limit(self, mock_limit):
        """Test rate limit exception during fetch."""
        mock_limit.return_value = False

        with self.assertRaises(ValueError):
            self.fetcher.fetch_commits("OWASP/ASVS")

    @patch("application.utils.commit_fetcher.requests.Session.get")
    def test_extract_meaningful_changes(self, mock_get):
        """Test extraction of meaningful changes from commit."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "commit": {"message": "Fix: critical security issue"},
            "files": [
                {
                    "filename": "src/security.py",
                    "additions": 10,
                    "deletions": 5,
                },
                {
                    "filename": "package-lock.json",
                    "additions": 100,
                    "deletions": 50,
                },  # Should be filtered
            ],
        }
        mock_get.return_value = mock_response

        changes = self.fetcher.extract_meaningful_changes("OWASP/ASVS", "abc123")
        
        self.assertIsNotNone(changes)
        self.assertIn("Fix: critical security issue", changes)
        self.assertIn("src/security.py", changes)
        self.assertNotIn("package-lock.json", changes)


if __name__ == "__main__":
    unittest.main()
