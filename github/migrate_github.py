#!/usr/bin/env python3
"""
Complete GitHub Repository Migration Script with Separate Source/Target Tokens
Migrates repositories with FULL history including:
- Commits, branches, tags (via git mirror)
- Pull Requests (open, closed, merged) with comments, reviews, labels, assignees
- Issues with comments, labels, assignees
- Repository settings (merge options, default branch)
- Branch protection rules (required reviews, status checks, restrictions)
- Labels (colors and descriptions)
- Webhooks (configuration, note: secrets require manual setup)
- Repository variables (Actions)
- Repository secrets (listed, require manual migration)
"""

import requests
import subprocess
import json
import os
import time
from typing import Dict, List, Optional
import sys
from datetime import datetime
import argparse

class GitHubMigrator:
    def __init__(self, source_token: str, target_token: str, source_org: str, target_org: str):
        self.source_token = source_token
        self.target_token = target_token
        self.source_org = source_org
        self.target_org = target_org
        
        # Headers for source org
        self.source_headers = {
            "Authorization": f"Bearer {source_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28"
        }
        
        # Headers for target org
        self.target_headers = {
            "Authorization": f"Bearer {target_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28"
        }
        
        self.base_url = "https://api.github.com"
    
    def paginated_request(self, url: str, headers: Dict, params: Dict = None) -> List:
        """Make paginated API requests with specified headers"""
        results = []
        page = 1
        params = params or {}
        
        while True:
            params["per_page"] = 100
            params["page"] = page
            
            response = requests.get(url, headers=headers, params=params)
            
            if response.status_code != 200:
                break
            
            data = response.json()
            if not data:
                break
            
            if isinstance(data, list):
                results.extend(data)
            else:
                results.append(data)
            
            page += 1
            
            # Check if there are more pages
            if "Link" not in response.headers:
                break
            if 'rel="next"' not in response.headers.get("Link", ""):
                break
        
        return results
    
    def clone_and_push_mirror(self, repo_name: str, temp_dir: str = "./temp_migration") -> bool:
        """Clone repository with full history and push to target"""
        print(f"\n📦 Cloning repository with full history...")
        
        source_url = f"https://{self.source_token}@github.com/{self.source_org}/{repo_name}.git"
        target_url = f"https://{self.target_token}@github.com/{self.target_org}/{repo_name}.git"
        
        repo_path = os.path.join(temp_dir, repo_name)
        
        try:
            os.makedirs(temp_dir, exist_ok=True)
            
            print("  Cloning with --mirror flag...")
            subprocess.run(
                ["git", "clone", "--mirror", source_url, repo_path],
                check=True,
                capture_output=True
            )
            
            # Configure git for large repositories
            print("  Configuring git for large push...")
            subprocess.run(
                ["git", "-C", repo_path, "config", "http.postBuffer", "524288000"],
                check=True,
                capture_output=True
            )
            subprocess.run(
                ["git", "-C", repo_path, "config", "http.version", "HTTP/1.1"],
                check=True,
                capture_output=True
            )
            
            print("  Pushing to target repository...")
            subprocess.run(
                ["git", "-C", repo_path, "push", "--mirror", target_url],
                check=True,
                capture_output=True
            )
            
            print("✓ Repository mirrored successfully")
            return True
            
        except subprocess.CalledProcessError as e:
            error_msg = e.stderr.decode() if e.stderr else str(e)
            print(f"✗ Git operation failed: {error_msg}")
            
            # Try alternative push method if mirror fails
            if "postBuffer" in error_msg or "RPC failed" in error_msg:
                print("  ⚠ Large repository detected, trying chunked push...")
                return self.push_mirror_chunked(repo_path, target_url)
            
            return False
        finally:
            if os.path.exists(repo_path):
                subprocess.run(["rm", "-rf", repo_path], check=False)
    
    def push_mirror_chunked(self, repo_path: str, target_url: str) -> bool:
        """Push repository in chunks (for large repos)"""
        try:
            print("  Pushing branches individually...")
            
            # Get all branches
            result = subprocess.run(
                ["git", "-C", repo_path, "for-each-ref", "--format=%(refname:short)", "refs/heads/"],
                capture_output=True,
                text=True,
                check=True
            )
            branches = result.stdout.strip().split('\n') if result.stdout.strip() else []
            
            # Push each branch
            for branch in branches:
                print(f"    Pushing branch: {branch}")
                subprocess.run(
                    ["git", "-C", repo_path, "push", target_url, f"refs/heads/{branch}"],
                    check=True,
                    capture_output=True
                )
            
            # Push all tags
            print("  Pushing tags...")
            subprocess.run(
                ["git", "-C", repo_path, "push", "--tags", target_url],
                check=True,
                capture_output=True
            )
            
            print("✓ Repository pushed successfully (chunked)")
            return True
            
        except subprocess.CalledProcessError as e:
            print(f"✗ Chunked push failed: {e.stderr.decode() if e.stderr else str(e)}")
            return False
    
    def create_target_repo(self, repo_name: str, settings: Dict) -> bool:
        """Create repository in target org"""
        url = f"{self.base_url}/orgs/{self.target_org}/repos"
        
        payload = {
            "name": repo_name,
            "description": settings.get("description", ""),
            "private": settings.get("private", False),
            "has_issues": settings.get("has_issues", True),
            "has_projects": settings.get("has_projects", True),
            "has_wiki": settings.get("has_wiki", True),
        }
        
        response = requests.post(url, headers=self.target_headers, json=payload)
        
        if response.status_code == 201:
            print(f"✓ Repository created in target org")
            return True
        elif response.status_code == 422:
            print(f"⚠ Repository already exists in target org")
            return True
        else:
            print(f"✗ Failed to create repository: {response.json()}")
            return False
    
    def get_repo_settings(self, org: str, repo_name: str, use_source: bool = True) -> Dict:
        """Get repository settings"""
        url = f"{self.base_url}/repos/{org}/{repo_name}"
        headers = self.source_headers if use_source else self.target_headers
        response = requests.get(url, headers=headers)
        
        if response.status_code == 200:
            return response.json()
        return {}
    
    def update_repo_settings(self, repo_name: str, settings: Dict) -> bool:
        """Update repository settings"""
        url = f"{self.base_url}/repos/{self.target_org}/{repo_name}"
        
        update_settings = {
            "description": settings.get("description"),
            "homepage": settings.get("homepage"),
            "private": settings.get("private"),
            "has_issues": settings.get("has_issues"),
            "has_projects": settings.get("has_projects"),
            "has_wiki": settings.get("has_wiki"),
            "allow_squash_merge": settings.get("allow_squash_merge"),
            "allow_merge_commit": settings.get("allow_merge_commit"),
            "allow_rebase_merge": settings.get("allow_rebase_merge"),
            "allow_auto_merge": settings.get("allow_auto_merge"),
            "delete_branch_on_merge": settings.get("delete_branch_on_merge"),
        }
        
        response = requests.patch(url, headers=self.target_headers, json=update_settings)
        return response.status_code == 200
    
    def update_default_branch(self, repo_name: str, default_branch: str) -> bool:
        """Update default branch of repository"""
        url = f"{self.base_url}/repos/{self.target_org}/{repo_name}"
        
        payload = {"default_branch": default_branch}
        response = requests.patch(url, headers=self.target_headers, json=payload)
        
        if response.status_code == 200:
            print(f"  ✓ Default branch set to: {default_branch}")
            return True
        else:
            print(f"  ⚠ Failed to set default branch: {response.json().get('message', 'Unknown error')}")
            return False
    
    def get_all_branches(self, org: str, repo_name: str) -> List[str]:
        """Get all branches from repository"""
        url = f"{self.base_url}/repos/{org}/{repo_name}/branches"
        branches_data = self.paginated_request(url, self.source_headers)
        return [branch["name"] for branch in branches_data]
    
    def get_branch_protection(self, org: str, repo_name: str, branch: str) -> Optional[Dict]:
        """Get branch protection rules for a branch"""
        url = f"{self.base_url}/repos/{org}/{repo_name}/branches/{branch}/protection"
        response = requests.get(url, headers=self.source_headers)
        
        if response.status_code == 200:
            return response.json()
        elif response.status_code == 404:
            return None  # No protection rules
        else:
            print(f"  ⚠ Error fetching protection for {branch}: {response.json().get('message', 'Unknown error')}")
            return None
    
    def set_branch_protection(self, repo_name: str, branch: str, protection_data: Dict) -> bool:
        """Set branch protection rules"""
        url = f"{self.base_url}/repos/{self.target_org}/{repo_name}/branches/{branch}/protection"
        
        # Build protection payload from source data
        payload = {}
        
        # Required status checks
        if "required_status_checks" in protection_data:
            required_status_checks = protection_data["required_status_checks"]
            payload["required_status_checks"] = {
                "strict": required_status_checks.get("strict", False),
                "contexts": required_status_checks.get("contexts", [])
            }
        
        # Enforce admins
        if "enforce_admins" in protection_data:
            payload["enforce_admins"] = protection_data["enforce_admins"]["enabled"]
        
        # Required pull request reviews
        if "required_pull_request_reviews" in protection_data:
            required_reviews = protection_data["required_pull_request_reviews"]
            payload["required_pull_request_reviews"] = {
                "dismiss_stale_reviews": required_reviews.get("dismiss_stale_reviews", False),
                "require_code_owner_reviews": required_reviews.get("require_code_owner_reviews", False),
                "required_approving_review_count": required_reviews.get("required_approving_review_count", 1),
                "require_last_push_approval": required_reviews.get("require_last_push_approval", False),
                "dismissal_restrictions": {} if not required_reviews.get("dismissal_restrictions") else {
                    "users": [u["login"] for u in required_reviews["dismissal_restrictions"].get("users", [])],
                    "teams": [t["slug"] for t in required_reviews["dismissal_restrictions"].get("teams", [])]
                },
                "bypass_pull_request_allowances": {} if not required_reviews.get("bypass_pull_request_allowances") else {
                    "users": [u["login"] for u in required_reviews["bypass_pull_request_allowances"].get("users", [])],
                    "teams": [t["slug"] for t in required_reviews["bypass_pull_request_allowances"].get("teams", [])]
                }
            }
        
        # Restrictions
        if "restrictions" in protection_data:
            restrictions = protection_data["restrictions"]
            payload["restrictions"] = {
                "users": [u["login"] for u in restrictions.get("users", [])],
                "teams": [t["slug"] for t in restrictions.get("teams", [])],
                "apps": [a["slug"] for a in restrictions.get("apps", [])]
            }
        
        # Allow force pushes
        if "allow_force_pushes" in protection_data:
            payload["allow_force_pushes"] = protection_data["allow_force_pushes"]["enabled"]
        
        # Allow deletions
        if "allow_deletions" in protection_data:
            payload["allow_deletions"] = protection_data["allow_deletions"]["enabled"]
        
        # Required linear history
        if "required_linear_history" in protection_data:
            payload["required_linear_history"] = protection_data["required_linear_history"]["enabled"]
        
        # Require signed commits
        if "require_signed_commits" in protection_data:
            payload["require_signed_commits"] = protection_data["require_signed_commits"]["enabled"]
        
        # Require conversation resolution
        if "required_conversation_resolution" in protection_data:
            payload["required_conversation_resolution"] = protection_data["required_conversation_resolution"]["enabled"]
        
        # Lock branch
        if "lock_branch" in protection_data:
            payload["lock_branch"] = protection_data["lock_branch"]["enabled"]
        
        # Require deployments to succeed
        if "required_deployments" in protection_data:
            payload["required_deployments"] = {
                "enforcement_level": protection_data["required_deployments"].get("enforcement_level", "none"),
                "environments": protection_data["required_deployments"].get("environments", [])
            }
        
        response = requests.put(url, headers=self.target_headers, json=payload)
        
        if response.status_code == 200:
            print(f"    ✓ Protection rules applied to {branch}")
            return True
        else:
            error_msg = response.json().get('message', 'Unknown error')
            print(f"    ⚠ Failed to set protection for {branch}: {error_msg}")
            return False
    
    def migrate_branch_protection(self, repo_name: str, default_branch: str) -> bool:
        """Migrate branch protection rules for ALL branches"""
        print(f"\n🛡️  Migrating branch protection rules...")
        
        # Get all branches from source repository
        print(f"  Fetching all branches...")
        all_branches = self.get_all_branches(self.source_org, repo_name)
        print(f"  Found {len(all_branches)} branches")
        
        protected_branches = []
        migrated_count = 0
        failed_count = 0
        
        # Check each branch for protection rules
        for branch in all_branches:
            protection = self.get_branch_protection(self.source_org, repo_name, branch)
            if protection:
                protected_branches.append((branch, protection))
                print(f"  ✓ Found protection rules for branch: {branch}")
        
        if not protected_branches:
            print(f"  No branch protection rules found")
            return True
        
        print(f"\n  Migrating protection rules for {len(protected_branches)} branch(es)...")
        
        # Migrate protection rules for each protected branch
        for branch, protection in protected_branches:
            if self.set_branch_protection(repo_name, branch, protection):
                migrated_count += 1
            else:
                failed_count += 1
            time.sleep(0.5)  # Rate limiting
        
        print(f"\n  ✅ Branch Protection Migration Summary:")
        print(f"    Migrated: {migrated_count}")
        print(f"    Failed: {failed_count}")
        
        return failed_count == 0
    
    def get_all_pull_requests(self, org: str, repo_name: str) -> List[Dict]:
        """Get all pull requests from source repository"""
        print(f"\n🔍 Fetching all pull requests...")
        url = f"{self.base_url}/repos/{org}/{repo_name}/pulls"
        
        prs = self.paginated_request(url, self.source_headers, {"state": "all"})
        print(f"  Found {len(prs)} pull requests")
        return prs
    
    def get_pr_comments(self, org: str, repo_name: str, pr_number: int) -> List[Dict]:
        """Get all comments for a pull request"""
        url = f"{self.base_url}/repos/{org}/{repo_name}/issues/{pr_number}/comments"
        return self.paginated_request(url, self.source_headers)
    
    def get_pr_review_comments(self, org: str, repo_name: str, pr_number: int) -> List[Dict]:
        """Get all review comments (line comments) for a pull request"""
        url = f"{self.base_url}/repos/{org}/{repo_name}/pulls/{pr_number}/comments"
        return self.paginated_request(url, self.source_headers)
    
    def get_pr_reviews(self, org: str, repo_name: str, pr_number: int) -> List[Dict]:
        """Get all reviews for a pull request"""
        url = f"{self.base_url}/repos/{org}/{repo_name}/pulls/{pr_number}/reviews"
        return self.paginated_request(url, self.source_headers)
    
    def branch_exists(self, repo_name: str, branch: str) -> bool:
        """Check if a branch exists in target repository"""
        url = f"{self.base_url}/repos/{self.target_org}/{repo_name}/branches/{branch}"
        response = requests.get(url, headers=self.target_headers)
        return response.status_code == 200
    
    def create_pull_request(self, repo_name: str, pr_data: Dict) -> Optional[Dict]:
        """Create a pull request in target repository"""
        url = f"{self.base_url}/repos/{self.target_org}/{repo_name}/pulls"
        
        head_branch = pr_data["head"]["ref"]
        base_branch = pr_data["base"]["ref"]
        
        # Check if branches exist before attempting to create PR
        if not self.branch_exists(repo_name, head_branch):
            print(f"    ⚠ Head branch '{head_branch}' does not exist in target repo")
            return None
        
        if not self.branch_exists(repo_name, base_branch):
            print(f"    ⚠ Base branch '{base_branch}' does not exist in target repo")
            return None
        
        # Extract only the necessary fields
        payload = {
            "title": pr_data["title"],
            "body": self.format_migrated_body(pr_data.get("body", ""), pr_data),
            "head": head_branch,
            "base": base_branch,
        }
        
        # Add labels if present
        if "labels" in pr_data and pr_data["labels"]:
            payload["labels"] = [label["name"] for label in pr_data["labels"]]
        
        response = requests.post(url, headers=self.target_headers, json=payload)
        
        if response.status_code == 201:
            new_pr = response.json()
            
            # Add assignees if present
            if "assignees" in pr_data and pr_data["assignees"]:
                assignees = [assignee["login"] for assignee in pr_data["assignees"]]
                self.add_issue_assignees(repo_name, new_pr["number"], assignees)
            
            # Add milestone if present
            if "milestone" in pr_data and pr_data["milestone"]:
                milestone_number = pr_data["milestone"]["number"]
                # Note: Milestones need to be migrated first, this is a placeholder
                # We'll handle milestone migration separately
            
            return new_pr
        else:
            error_data = response.json()
            error_msg = error_data.get('message', 'Unknown error')
            errors = error_data.get('errors', [])
            if errors:
                error_details = '; '.join([f"{e.get('field', '')}: {e.get('message', '')}" for e in errors])
                error_msg = f"{error_msg} ({error_details})"
            print(f"    ⚠ Failed to create PR #{pr_data['number']}: {error_msg}")
            return None
    
    def add_issue_assignees(self, repo_name: str, issue_number: int, assignees: List[str]) -> bool:
        """Add assignees to an issue or PR"""
        url = f"{self.base_url}/repos/{self.target_org}/{repo_name}/issues/{issue_number}/assignees"
        payload = {"assignees": assignees}
        response = requests.post(url, headers=self.target_headers, json=payload)
        return response.status_code == 201
    
    def merge_pull_request(self, repo_name: str, pr_number: int, merge_method: str = "merge") -> bool:
        """Merge a pull request"""
        url = f"{self.base_url}/repos/{self.target_org}/{repo_name}/pulls/{pr_number}/merge"
        payload = {"merge_method": merge_method}  # merge, squash, or rebase
        response = requests.put(url, headers=self.target_headers, json=payload)
        return response.status_code == 200
    
    def format_migrated_body(self, body: str, original_data: Dict) -> str:
        """Format body with migration metadata"""
        metadata = f"""
---
**🔄 Migrated from:** {self.source_org}/{original_data.get('head', {}).get('repo', {}).get('name', 'unknown')}
**📅 Original PR:** #{original_data['number']} by @{original_data['user']['login']}
**📝 Created:** {original_data['created_at']}
**✅ Status:** {original_data['state']} {'(merged)' if original_data.get('merged_at') else ''}
---

"""
        return metadata + (body or "")
    
    def create_issue_comment(self, repo_name: str, issue_number: int, comment: Dict) -> bool:
        """Create a comment on an issue/PR"""
        url = f"{self.base_url}/repos/{self.target_org}/{repo_name}/issues/{issue_number}/comments"
        
        body = f"**@{comment['user']['login']}** commented on {comment['created_at']}:\n\n{comment.get('body', '')}"
        
        payload = {"body": body}
        response = requests.post(url, headers=self.target_headers, json=payload)
        
        return response.status_code == 201
    
    def close_pull_request(self, repo_name: str, pr_number: int) -> bool:
        """Close a pull request"""
        url = f"{self.base_url}/repos/{self.target_org}/{repo_name}/pulls/{pr_number}"
        
        payload = {"state": "closed"}
        response = requests.patch(url, headers=self.target_headers, json=payload)
        
        return response.status_code == 200
    
    def migrate_pull_requests(self, repo_name: str):
        """Migrate all pull requests with their comments and reviews"""
        print(f"\n📋 Migrating Pull Requests...")
        
        source_prs = self.get_all_pull_requests(self.source_org, repo_name)
        
        if not source_prs:
            print("  No pull requests to migrate")
            return
        
        migrated_count = 0
        failed_count = 0
        failed_prs = []
        
        for pr in source_prs:
            pr_number = pr["number"]
            print(f"\n  Processing PR #{pr_number}: {pr['title'][:50]}...")
            
            # Check if both branches exist in target repo
            try:
                # Create the PR in target repo
                new_pr = self.create_pull_request(repo_name, pr)
                
                if not new_pr:
                    failed_count += 1
                    head_branch = pr.get("head", {}).get("ref", "unknown")
                    base_branch = pr.get("base", {}).get("ref", "unknown")
                    failed_prs.append({
                        "number": pr_number,
                        "title": pr.get("title", "")[:50],
                        "head": head_branch,
                        "base": base_branch
                    })
                    continue
                
                new_pr_number = new_pr["number"]
                print(f"    ✓ Created as PR #{new_pr_number}")
                
                # Migrate general comments
                comments = self.get_pr_comments(self.source_org, repo_name, pr_number)
                print(f"    Migrating {len(comments)} comments...")
                
                for comment in comments:
                    self.create_issue_comment(repo_name, new_pr_number, comment)
                    time.sleep(0.5)  # Rate limiting
                
                # Migrate review comments (line-by-line comments)
                review_comments = self.get_pr_review_comments(self.source_org, repo_name, pr_number)
                if review_comments:
                    print(f"    Found {len(review_comments)} review comments")
                    summary = self.create_review_summary(review_comments)
                    self.create_issue_comment(repo_name, new_pr_number, {
                        "user": {"login": "migration-bot"},
                        "created_at": datetime.now().isoformat(),
                        "body": summary
                    })
                
                # Migrate reviews
                reviews = self.get_pr_reviews(self.source_org, repo_name, pr_number)
                if reviews:
                    print(f"    Found {len(reviews)} reviews")
                    review_summary = self.create_reviews_summary(reviews)
                    self.create_issue_comment(repo_name, new_pr_number, {
                        "user": {"login": "migration-bot"},
                        "created_at": datetime.now().isoformat(),
                        "body": review_summary
                    })
                
                # Handle PR state (closed or merged)
                if pr["state"] == "closed":
                    if pr.get("merged_at"):  # PR was merged
                        # Try to merge the PR (if branches still exist)
                        merge_method = "merge"  # Default, could be determined from PR data
                        if self.merge_pull_request(repo_name, new_pr_number, merge_method):
                            print(f"    ✓ Merged PR #{new_pr_number}")
                        else:
                            # If merge fails (e.g., branches deleted), just close it
                            self.close_pull_request(repo_name, new_pr_number)
                            print(f"    ✓ Closed PR #{new_pr_number} (merge attempted but failed)")
                    else:  # PR was just closed
                        self.close_pull_request(repo_name, new_pr_number)
                        print(f"    ✓ Closed PR #{new_pr_number}")
                
                migrated_count += 1
                time.sleep(1)  # Rate limiting
                
            except Exception as e:
                print(f"    ✗ Error migrating PR #{pr_number}: {str(e)}")
                failed_count += 1
        
        print(f"\n✅ Pull Request Migration Complete:")
        print(f"  Migrated: {migrated_count}")
        print(f"  Failed: {failed_count}")
        
        if failed_prs:
            print(f"\n  ⚠ Failed PRs (branches may not exist or were deleted):")
            for failed_pr in failed_prs[:10]:  # Show first 10
                print(f"    - PR #{failed_pr['number']}: {failed_pr['title']} (head: {failed_pr['head']}, base: {failed_pr['base']})")
            if len(failed_prs) > 10:
                print(f"    ... and {len(failed_prs) - 10} more")
    
    def create_review_summary(self, review_comments: List[Dict]) -> str:
        """Create a summary of review comments"""
        summary = "## 📝 Review Comments (from original PR)\n\n"
        
        for comment in review_comments[:20]:  # Limit to first 20
            user = comment.get("user", {}).get("login", "unknown")
            path = comment.get("path", "unknown file")
            line = comment.get("line", "?")
            body = comment.get("body", "")
            
            summary += f"**@{user}** on `{path}:{line}`:\n> {body}\n\n"
        
        if len(review_comments) > 20:
            summary += f"\n_... and {len(review_comments) - 20} more review comments_\n"
        
        return summary
    
    def create_reviews_summary(self, reviews: List[Dict]) -> str:
        """Create a summary of reviews"""
        summary = "## ✅ Reviews (from original PR)\n\n"
        
        for review in reviews:
            user = review.get("user", {}).get("login", "unknown")
            state = review.get("state", "COMMENTED")
            body = review.get("body", "")
            
            emoji = {"APPROVED": "✅", "CHANGES_REQUESTED": "🔄", "COMMENTED": "💬"}.get(state, "📝")
            
            summary += f"{emoji} **@{user}** - {state}\n"
            if body:
                summary += f"> {body}\n"
            summary += "\n"
        
        return summary
    
    def get_all_issues(self, org: str, repo_name: str) -> List[Dict]:
        """Get all issues (excluding PRs)"""
        url = f"{self.base_url}/repos/{org}/{repo_name}/issues"
        
        all_issues = self.paginated_request(url, self.source_headers, {"state": "all"})
        
        # Filter out pull requests
        issues = [issue for issue in all_issues if "pull_request" not in issue]
        return issues
    
    def create_issue(self, repo_name: str, issue_data: Dict) -> Optional[Dict]:
        """Create an issue in target repository"""
        url = f"{self.base_url}/repos/{self.target_org}/{repo_name}/issues"
        
        payload = {
            "title": issue_data["title"],
            "body": self.format_migrated_issue_body(issue_data),
            "labels": [label["name"] for label in issue_data.get("labels", [])],
        }
        
        response = requests.post(url, headers=self.target_headers, json=payload)
        
        if response.status_code == 201:
            new_issue = response.json()
            
            # Add assignees if present
            if "assignees" in issue_data and issue_data["assignees"]:
                assignees = [assignee["login"] for assignee in issue_data["assignees"]]
                self.add_issue_assignees(repo_name, new_issue["number"], assignees)
            
            # Add milestone if present (milestones need to be migrated first)
            if "milestone" in issue_data and issue_data["milestone"]:
                # Note: Milestone migration handled separately
                pass
            
            return new_issue
        return None
    
    def format_migrated_issue_body(self, issue_data: Dict) -> str:
        """Format issue body with migration metadata"""
        metadata = f"""
---
**🔄 Migrated from:** {self.source_org}
**📅 Original Issue:** #{issue_data['number']} by @{issue_data['user']['login']}
**📝 Created:** {issue_data['created_at']}
**✅ Status:** {issue_data['state']}
---

"""
        return metadata + (issue_data.get("body", "") or "")
    
    def close_issue(self, repo_name: str, issue_number: int) -> bool:
        """Close an issue"""
        url = f"{self.base_url}/repos/{self.target_org}/{repo_name}/issues/{issue_number}"
        
        payload = {"state": "closed"}
        response = requests.patch(url, headers=self.target_headers, json=payload)
        
        return response.status_code == 200
    
    def migrate_issues(self, repo_name: str):
        """Migrate all issues with their comments"""
        print(f"\n🐛 Migrating Issues...")
        
        source_issues = self.get_all_issues(self.source_org, repo_name)
        
        if not source_issues:
            print("  No issues to migrate")
            return
        
        print(f"  Found {len(source_issues)} issues")
        
        for issue in source_issues:
            issue_number = issue["number"]
            print(f"  Processing Issue #{issue_number}: {issue['title'][:50]}...")
            
            new_issue = self.create_issue(repo_name, issue)
            
            if not new_issue:
                print(f"    ✗ Failed to create issue")
                continue
            
            new_issue_number = new_issue["number"]
            print(f"    ✓ Created as Issue #{new_issue_number}")
            
            # Migrate comments
            comments = self.get_pr_comments(self.source_org, repo_name, issue_number)
            if comments:
                print(f"    Migrating {len(comments)} comments...")
                for comment in comments:
                    self.create_issue_comment(repo_name, new_issue_number, comment)
                    time.sleep(0.5)
            
            # Close if it was closed in source
            if issue["state"] == "closed":
                self.close_issue(repo_name, new_issue_number)
            
            time.sleep(1)
        
        print(f"✅ Issues migrated successfully")
    
    def get_repo_variables(self, org: str, repo_name: str) -> List[Dict]:
        """Get repository variables"""
        url = f"{self.base_url}/repos/{org}/{repo_name}/actions/variables"
        response = requests.get(url, headers=self.source_headers)
        
        if response.status_code == 200:
            return response.json().get("variables", [])
        return []
    
    def create_repo_variable(self, repo_name: str, var_name: str, var_value: str) -> bool:
        """Create repository variable"""
        url = f"{self.base_url}/repos/{self.target_org}/{repo_name}/actions/variables"
        payload = {"name": var_name, "value": var_value}
        
        response = requests.post(url, headers=self.target_headers, json=payload)
        return response.status_code == 201
    
    def get_repo_secrets(self, org: str, repo_name: str) -> List[str]:
        """Get repository secret names"""
        url = f"{self.base_url}/repos/{org}/{repo_name}/actions/secrets"
        response = requests.get(url, headers=self.source_headers)
        
        if response.status_code == 200:
            return [secret["name"] for secret in response.json().get("secrets", [])]
        return []
    
    def get_repo_webhooks(self, org: str, repo_name: str) -> List[Dict]:
        """Get repository webhooks"""
        url = f"{self.base_url}/repos/{org}/{repo_name}/hooks"
        return self.paginated_request(url, self.source_headers)
    
    def create_repo_webhook(self, repo_name: str, webhook_data: Dict) -> bool:
        """Create a webhook in target repository"""
        url = f"{self.base_url}/repos/{self.target_org}/{repo_name}/hooks"
        
        # Build webhook payload (exclude sensitive fields like secret)
        payload = {
            "name": "web",
            "active": webhook_data.get("active", True),
            "events": webhook_data.get("events", ["push"]),
            "config": {
                "url": webhook_data["config"]["url"],
                "content_type": webhook_data["config"].get("content_type", "json"),
                "insecure_ssl": webhook_data["config"].get("insecure_ssl", "0"),
            }
        }
        
        # Note: webhook secret cannot be retrieved from source, must be set manually
        if "secret" in webhook_data["config"]:
            print(f"    ⚠ Webhook secret cannot be migrated automatically - requires manual setup")
        
        response = requests.post(url, headers=self.target_headers, json=payload)
        
        if response.status_code == 201:
            print(f"    ✓ Webhook created: {webhook_data['config']['url']}")
            return True
        else:
            error_msg = response.json().get('message', 'Unknown error')
            print(f"    ⚠ Failed to create webhook: {error_msg}")
            return False
    
    def migrate_webhooks(self, repo_name: str):
        """Migrate repository webhooks"""
        print(f"\n🔗 Migrating webhooks...")
        
        webhooks = self.get_repo_webhooks(self.source_org, repo_name)
        
        if not webhooks:
            print("  No webhooks to migrate")
            return
        
        print(f"  Found {len(webhooks)} webhooks")
        
        for webhook in webhooks:
            if self.create_repo_webhook(repo_name, webhook):
                time.sleep(0.5)  # Rate limiting
    
    def get_all_labels(self, org: str, repo_name: str) -> List[Dict]:
        """Get all labels from repository"""
        url = f"{self.base_url}/repos/{org}/{repo_name}/labels"
        return self.paginated_request(url, self.source_headers)
    
    def create_label(self, repo_name: str, label_data: Dict) -> bool:
        """Create a label in target repository"""
        url = f"{self.base_url}/repos/{self.target_org}/{repo_name}/labels"
        
        payload = {
            "name": label_data["name"],
            "color": label_data["color"].lstrip("#"),  # Remove # if present
            "description": label_data.get("description", "")
        }
        
        response = requests.post(url, headers=self.target_headers, json=payload)
        
        # 422 means label already exists, which is fine
        return response.status_code in [201, 422]
    
    def migrate_labels(self, repo_name: str):
        """Migrate repository labels"""
        print(f"\n🏷️  Migrating labels...")
        
        labels = self.get_all_labels(self.source_org, repo_name)
        
        if not labels:
            print("  No labels to migrate")
            return
        
        print(f"  Found {len(labels)} labels")
        
        migrated = 0
        for label in labels:
            if self.create_label(repo_name, label):
                migrated += 1
        
        print(f"  ✓ Migrated {migrated} labels")
    
    def migrate_repository(self, repo_name: str):
        """Complete migration of a repository"""
        print(f"\n{'='*70}")
        print(f"🚀 MIGRATING: {repo_name}")
        print(f"{'='*70}")
        
        # 1. Get source repo settings
        print("\n1️⃣  Fetching source repository settings...")
        source_settings = self.get_repo_settings(self.source_org, repo_name, use_source=True)
        
        # 2. Create target repository
        print("\n2️⃣  Creating target repository...")
        self.create_target_repo(repo_name, source_settings)
        time.sleep(2)
        
        # 3. Mirror repository
        print("\n3️⃣  Mirroring repository (commits, branches, tags)...")
        self.clone_and_push_mirror(repo_name)
        time.sleep(3)
        
        # 4. Update settings
        print("\n4️⃣  Updating repository settings...")
        self.update_repo_settings(repo_name, source_settings)
        
        # 4.5. Update default branch
        default_branch = source_settings.get("default_branch", "main")
        print(f"\n4️⃣.5️⃣  Setting default branch to {default_branch}...")
        self.update_default_branch(repo_name, default_branch)
        
        # 4.6. Migrate labels (needed before PRs/issues)
        self.migrate_labels(repo_name)
        
        # 5. Migrate branch protection rules
        self.migrate_branch_protection(repo_name, default_branch)
        
        # 6. Migrate Pull Requests
        self.migrate_pull_requests(repo_name)
        
        # 7. Migrate Issues
        self.migrate_issues(repo_name)
        
        # 8. Migrate webhooks
        self.migrate_webhooks(repo_name)
        
        # 9. Migrate variables
        print("\n9️⃣  Migrating repository variables...")
        variables = self.get_repo_variables(self.source_org, repo_name)
        for var in variables:
            if self.create_repo_variable(repo_name, var["name"], var["value"]):
                print(f"  ✓ Variable {var['name']} created")
        
        # 10. List secrets
        print("\n🔟 Listing repository secrets...")
        secrets = self.get_repo_secrets(self.source_org, repo_name)
        if secrets:
            print(f"  ⚠ Found {len(secrets)} secrets (require manual migration):")
            for secret in secrets:
                print(f"    - {secret}")
        
        print(f"\n{'='*70}")
        print(f"✅ MIGRATION COMPLETED: {repo_name}")
        print(f"{'='*70}")
    
    def get_repo_list(self) -> List[str]:
        """Get all repositories from source org"""
        url = f"{self.base_url}/orgs/{self.source_org}/repos"
        repos_data = self.paginated_request(url, self.source_headers)
        return [repo["name"] for repo in repos_data]

def main():
    parser = argparse.ArgumentParser(
        description='Migrate GitHub repositories between organizations with full history',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
Examples:
  # Using environment variables (recommended):
  export SOURCE_GITHUB_TOKEN="ghp_source_token"
  export TARGET_GITHUB_TOKEN="ghp_target_token"
  python migrate_github.py --source-org my-source --target-org my-target --repo my-repo

  # Using command-line arguments:
  python migrate_github.py \\
    --source-token "ghp_source_token" \\
    --target-token "ghp_target_token" \\
    --source-org my-source \\
    --target-org my-target \\
    --repo my-repo

  # Interactive mode:
  python migrate_github.py
        '''
    )
    
    parser.add_argument('--source-token', 
                       help='GitHub token for source organization (or use SOURCE_GITHUB_TOKEN env var)')
    parser.add_argument('--target-token', 
                       help='GitHub token for target organization (or use TARGET_GITHUB_TOKEN env var)')
    parser.add_argument('--source-org', 
                       help='Source organization name (or use SOURCE_ORG env var)')
    parser.add_argument('--target-org', 
                       help='Target organization name (or use TARGET_ORG env var)')
    parser.add_argument('--repo', 
                       help='Repository name to migrate')
    parser.add_argument('--list-repos', action='store_true',
                       help='List all repositories in source organization')
    
    args = parser.parse_args()
    
    # Get tokens from arguments or environment variables
    source_token = args.source_token or os.getenv("SOURCE_GITHUB_TOKEN")
    target_token = args.target_token or os.getenv("TARGET_GITHUB_TOKEN")
    source_org = args.source_org or os.getenv("SOURCE_ORG")
    target_org = args.target_org or os.getenv("TARGET_ORG")
    
    # Interactive mode if missing parameters
    if not all([source_token, target_token, source_org, target_org]):
        print("GitHub Complete Repository Migration Tool")
        print("=" * 70)
        print("Migrates: commits, branches, tags, PRs, issues, comments, settings")
        print("=" * 70)
        print()
        
        if not source_token:
            source_token = input("Enter SOURCE GitHub token: ").strip()
        if not target_token:
            target_token = input("Enter TARGET GitHub token: ").strip()
        if not source_org:
            source_org = input("Enter SOURCE organization name: ").strip()
        if not target_org:
            target_org = input("Enter TARGET organization name: ").strip()
    
    # Validate all required parameters
    if not all([source_token, target_token, source_org, target_org]):
        print("\n❌ Error: Missing required parameters")
        print("\nRequired:")
        print("  - Source GitHub token (--source-token or SOURCE_GITHUB_TOKEN env var)")
        print("  - Target GitHub token (--target-token or TARGET_GITHUB_TOKEN env var)")
        print("  - Source organization (--source-org or SOURCE_ORG env var)")
        print("  - Target organization (--target-org or TARGET_ORG env var)")
        print("\nRun with --help for more information")
        sys.exit(1)
    
    # Create migrator instance
    migrator = GitHubMigrator(source_token, target_token, source_org, target_org)
    
    # List repos mode
    if args.list_repos:
        print(f"\n📚 Repositories in {source_org}:")
        print("=" * 70)
        try:
            repos = migrator.get_repo_list()
            for i, repo in enumerate(repos, 1):
                print(f"{i}. {repo}")
            print(f"\nTotal: {len(repos)} repositories")
        except Exception as e:
            print(f"❌ Error listing repositories: {str(e)}")
        sys.exit(0)
    
    # Get repository name
    repo_name = args.repo
    if not repo_name:
        repo_name = input("\nEnter repository name to migrate: ").strip()
    
    if not repo_name:
        print("❌ Repository name is required")
        sys.exit(1)
    
    # Confirm migration
    print(f"\n{'='*70}")
    print(f"Migration Plan:")
    print(f"  FROM: {source_org}/{repo_name}")
    print(f"  TO:   {target_org}/{repo_name}")
    print(f"{'='*70}")
    
    confirm = input("\nProceed with migration? (yes/no): ").strip().lower()
    
    if confirm == "yes":
        try:
            migrator.migrate_repository(repo_name)
        except Exception as e:
            print(f"\n❌ Migration failed: {str(e)}")
            sys.exit(1)
    else:
        print("Migration cancelled")
        sys.exit(0)

if __name__ == "__main__":
    main()
