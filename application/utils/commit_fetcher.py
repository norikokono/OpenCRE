"""
GSoC Module A/B: Information Harvesting - Commit Fetcher Module

This module fetches commits from OWASP repositories within the last 24 hours,
extracting meaningful diffs and filtering out noise files. It serves as the
foundation for the Noise/Relevance Filter (Module B).

Features:
- GitHub API rate limit handling
- Configurable repository list via YAML
- Git diff extraction (clean text, not raw diff syntax)
- Noise file filtering with regex
- Comprehensive logging and error handling
"""

import logging
import os
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple
import requests
import yaml

logger = logging.getLogger(__name__)


class CommitFetcher:
    """Fetch and filter commits from OWASP repositories."""

    # Common junk files that should be excluded
    NOISE_PATTERNS = [
        r'.*\.lock$',           # package-lock.json, yarn.lock, etc.
        r'.*CNAME$',            # DNS config
        r'.*_config\.yml$',     # Jekyll config
        r'.*\.md5$',            # Hash files
        r'\.gitignore$',        # Git config
        r'\.github/workflows',  # CI/CD config
        r'__pycache__',         # Python cache
        r'node_modules',        # NPM packages
        r'\.DS_Store',          # macOS files
    ]

    GITHUB_API_BASE = "https://api.github.com"
    RATE_LIMIT_BUFFER = 10  # Keep 10 requests as safety buffer

    def __init__(self, github_token: Optional[str] = None):
        """
        Initialize the CommitFetcher.

        Args:
            github_token: GitHub API token for better rate limits
        """
        self.github_token = github_token or os.getenv("GITHUB_TOKEN")
        self.session = self._create_session()
        self.rate_limit_remaining = 60

    def _create_session(self) -> requests.Session:
        """Create configured requests session."""
        session = requests.Session()
        if self.github_token:
            session.headers.update(
                {"Authorization": f"token {self.github_token}"}
            )
        session.headers.update({"Accept": "application/vnd.github.v3+json"})
        return session

    def load_config(self, config_path: str = "repos.yaml") -> List[str]:
        """
        Load repository list from YAML config.

        Args:
            config_path: Path to repos.yaml file

        Returns:
            List of repository names in format "owner/repo"

        Raises:
            FileNotFoundError: If config file doesn't exist
            yaml.YAMLError: If config is invalid
        """
        if not os.path.exists(config_path):
            logger.error(f"Config file not found: {config_path}")
            raise FileNotFoundError(f"Config file not found: {config_path}")

        try:
            with open(config_path, "r") as f:
                config = yaml.safe_load(f)
            repos = config.get("repositories", [])
            logger.info(f"Loaded {len(repos)} repositories from config")
            return repos
        except yaml.YAMLError as e:
            logger.error(f"Invalid YAML config: {e}")
            raise

    def _check_rate_limit(self) -> bool:
        """
        Check GitHub API rate limit.

        Returns:
            True if rate limit allows requests, False otherwise
        """
        try:
            response = self.session.get(
                f"{self.GITHUB_API_BASE}/rate_limit"
            )
            if response.status_code == 200:
                remaining = response.json()["rate"]["remaining"]
                self.rate_limit_remaining = remaining
                if remaining <= self.RATE_LIMIT_BUFFER:
                    logger.warning(
                        f"Approaching rate limit: {remaining} requests remaining"
                    )
                    return False
                return True
            return False
        except Exception as e:
            logger.error(f"Error checking rate limit: {e}")
            return False

    def fetch_commits(
        self, repo: str, hours: int = 24
    ) -> Optional[List[Dict]]:
        """
        Fetch commits from a repository in the last N hours.

        Args:
            repo: Repository in format "owner/repo"
            hours: Number of hours to look back (default: 24)

        Returns:
            List of commit data or None if failed

        Raises:
            ValueError: If rate limit exceeded
        """
        if not self._check_rate_limit():
            raise ValueError("GitHub API rate limit exceeded")

        since = (datetime.utcnow() - timedelta(hours=hours)).isoformat()
        url = f"{self.GITHUB_API_BASE}/repos/{repo}/commits"
        params = {"since": since, "per_page": 100}

        try:
            logger.info(f"Fetching commits from {repo} (since {hours}h ago)")
            response = self.session.get(url, params=params)

            if response.status_code == 404:
                logger.warning(f"Repository not found: {repo}")
                return None

            if response.status_code != 200:
                logger.error(
                    f"GitHub API error {response.status_code}: {response.text}"
                )
                return None

            commits = response.json()
            logger.info(f"Found {len(commits)} commits in {repo}")
            return commits

        except requests.RequestException as e:
            logger.error(f"Request failed for {repo}: {e}")
            return None

    def _is_noise_file(self, filename: str) -> bool:
        """
        Check if a file should be filtered as noise.

        Args:
            filename: File path to check

        Returns:
            True if file is noise, False otherwise
        """
        import re

        for pattern in self.NOISE_PATTERNS:
            if re.search(pattern, filename):
                return True
        return False

    def extract_meaningful_changes(
        self, repo: str, commit_sha: str
    ) -> Optional[str]:
        """
        Extract meaningful text changes from a commit (not raw diff syntax).

        Args:
            repo: Repository in format "owner/repo"
            commit_sha: Commit SHA hash

        Returns:
            Cleaned commit message and file changes, or None if failed
        """
        try:
            url = f"{self.GITHUB_API_BASE}/repos/{repo}/commits/{commit_sha}"
            response = self.session.get(url)

            if response.status_code != 200:
                return None

            commit_data = response.json()
            message = commit_data.get("commit", {}).get("message", "")

            # Filter out noise files
            files = commit_data.get("files", [])
            meaningful_files = [
                f for f in files if not self._is_noise_file(f.get("filename", ""))
            ]

            if not meaningful_files:
                logger.debug(f"All files filtered as noise in {commit_sha}")
                return None

            # Extract file changes
            changes = []
            for file in meaningful_files:
                changes.append(
                    f"File: {file['filename']} "
                    f"(+{file['additions']}/-{file['deletions']})"
                )

            return f"{message}\n\nFiles Changed:\n" + "\n".join(changes)

        except Exception as e:
            logger.error(f"Error extracting changes from {commit_sha}: {e}")
            return None

    def process_repositories(
        self, config_path: str = "repos.yaml", hours: int = 24
    ) -> Dict[str, List[str]]:
        """
        Process all configured repositories and extract meaningful changes.

        Args:
            config_path: Path to repos.yaml config
            hours: Hours to look back (default: 24)

        Returns:
            Dictionary mapping repo names to list of meaningful changes
        """
        repos = self.load_config(config_path)
        results = {}

        for repo in repos[:5]:  # Limit to avoid rate limits
            commits = self.fetch_commits(repo, hours)
            if commits:
                meaningful = []
                for commit in commits:
                    change = self.extract_meaningful_changes(
                        repo, commit["sha"]
                    )
                    if change:
                        meaningful.append(change)
                results[repo] = meaningful
                logger.info(f"Found {len(meaningful)} meaningful commits in {repo}")

        return results
