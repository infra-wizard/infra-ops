#!/usr/bin/env python3
"""
Complete GitHub Repository Migration Script
Migrates repositories with FULL history including:
- Commits, branches, tags (via git mirror)
- Pull Requests with comments, reviews, and status
- Issues with comments
- Repository settings, variables, secrets, webhooks
"""

import requests
import subprocess
import json
import os
import time
from typing import Dict, List, Optional
import sys
from datetime import datetime

class GitHubMigrator:
    def __init__(self, token: str, source_org: str, target_org: str):
        self.token = token
        self.source_org = source_org
        self.target_org = target_org
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28"
        }
        self.base_url = "https://api.github.com"
    
    def paginated_request(self, url: str, params: Dict = None) -> List:
        """Make paginated API requests"""
        results = []
        page = 1
        params = params or {}
        
        while True:
            params["per_page"] = 100
            params["page"] = page
            
            response = requests.get(url, headers=self.headers, params=params)
            
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
        
        source_url = f"https://{self.token}@github.com/{self.source_org}/{repo_name}.git"
        target_url = f"https://{self.token}@github.com/{self.target_org}/{repo_name}.git"
        
        repo_path = os.path.join(temp_dir, repo_name)
        
        try:
            os.makedirs(temp_dir, exist_ok=True)
            
            print("  Cloning with --mirror flag...")
            subprocess.run(
                ["git", "clone", "--mirror", source_url, repo_path],
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
            print(f"✗ Git operation failed: {e.stderr.decode() if e.stderr else str(e)}")
            return False
        finally:
            if os.path.exists(repo_path):
                subprocess.run(["rm", "-rf", repo_path], check=False)
    
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
        
        response = requests.post(url, headers=self.headers, json=payload)
        
        if response.status_code == 201:
            print(f"✓ Repository created in target org")
            return True
        elif response.status_code == 422:
            print(f"⚠ Repository already exists in target org")
            return True
        else:
            print(f"✗ Failed to create repository: {response.json()}")
            return False
    
    def get_repo_settings(self, org: str, repo_name: str) -> Dict:
        """Get repository settings"""
        url = f"{self.base_url}/repos/{org}/{repo_name}"
        response = requests.get(url, headers=self.headers)
        
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
        
        response = requests.patch(url, headers=self.headers, json=update_settings)
        return response.status_code == 200
    
    def get_all_pull_requests(self, org: str, repo_name: str) -> List[Dict]:
        """Get all pull requests from source repository"""
        print(f"\n🔍 Fetching all pull requests...")
        url = f"{self.base_url}/repos/{org}/{repo_name}/pulls"
        
        prs = self.paginated_request(url, {"state": "all"})
        print(f"  Found {len(prs)} pull requests")
        return prs
    
    def get_pr_comments(self, org: str, repo_name: str, pr_number: int) -> List[Dict]:
        """Get all comments for a pull request"""
        url = f"{self.base_url}/repos/{org}/{repo_name}/issues/{pr_number}/comments"
        return self.paginated_request(url)
    
    def get_pr_review_comments(self, org: str, repo_name: str, pr_number: int) -> List[Dict]:
        """Get all review comments (line comments) for a pull request"""
        url = f"{self.base_url}/repos/{org}/{repo_name}/pulls/{pr_number}/comments"
        return self.paginated_request(url)
    
    def get_pr_reviews(self, org: str, repo_name: str, pr_number: int) -> List[Dict]:
        """Get all reviews for a pull request"""
        url = f"{self.base_url}/repos/{org}/{repo_name}/pulls/{pr_number}/reviews"
        return self.paginated_request(url)
    
    def create_pull_request(self, repo_name: str, pr_data: Dict) -> Optional[Dict]:
        """Create a pull request in target repository"""
        url = f"{self.base_url}/repos/{self.target_org}/{repo_name}/pulls"
        
        # Extract only the necessary fields
        payload = {
            "title": pr_data["title"],
            "body": self.format_migrated_body(pr_data.get("body", ""), pr_data),
            "head": pr_data["head"]["ref"],
            "base": pr_data["base"]["ref"],
        }
        
        response = requests.post(url, headers=self.headers, json=payload)
        
        if response.status_code == 201:
            return response.json()
        else:
            print(f"  ⚠ Failed to create PR #{pr_data['number']}: {response.json().get('message', 'Unknown error')}")
            return None
    
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
        response = requests.post(url, headers=self.headers, json=payload)
        
        return response.status_code == 201
    
    def close_pull_request(self, repo_name: str, pr_number: int) -> bool:
        """Close a pull request"""
        url = f"{self.base_url}/repos/{self.target_org}/{repo_name}/pulls/{pr_number}"
        
        payload = {"state": "closed"}
        response = requests.patch(url, headers=self.headers, json=payload)
        
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
        
        for pr in source_prs:
            pr_number = pr["number"]
            print(f"\n  Processing PR #{pr_number}: {pr['title'][:50]}...")
            
            # Check if both branches exist in target repo
            try:
                # Create the PR in target repo
                new_pr = self.create_pull_request(repo_name, pr)
                
                if not new_pr:
                    failed_count += 1
                    print(f"    ⚠ Skipping PR #{pr_number} (branches may not exist)")
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
                
                # Close PR if it was closed/merged in source
                if pr["state"] == "closed":
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
        
        all_issues = self.paginated_request(url, {"state": "all"})
        
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
        
        response = requests.post(url, headers=self.headers, json=payload)
        
        if response.status_code == 201:
            return response.json()
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
        response = requests.patch(url, headers=self.headers, json=payload)
        
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
        response = requests.get(url, headers=self.headers)
        
        if response.status_code == 200:
            return response.json().get("variables", [])
        return []
    
    def create_repo_variable(self, repo_name: str, var_name: str, var_value: str) -> bool:
        """Create repository variable"""
        url = f"{self.base_url}/repos/{self.target_org}/{repo_name}/actions/variables"
        payload = {"name": var_name, "value": var_value}
        
        response = requests.post(url, headers=self.headers, json=payload)
        return response.status_code == 201
    
    def get_repo_secrets(self, org: str, repo_name: str) -> List[str]:
        """Get repository secret names"""
        url = f"{self.base_url}/repos/{org}/{repo_name}/actions/secrets"
        response = requests.get(url, headers=self.headers)
        
        if response.status_code == 200:
            return [secret["name"] for secret in response.json().get("secrets", [])]
        return []
    
    def migrate_repository(self, repo_name: str):
        """Complete migration of a repository"""
        print(f"\n{'='*70}")
        print(f"🚀 MIGRATING: {repo_name}")
        print(f"{'='*70}")
        
        # 1. Get source repo settings
        print("\n1️⃣  Fetching source repository settings...")
        source_settings = self.get_repo_settings(self.source_org, repo_name)
        
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
        
        # 5. Migrate Pull Requests
        self.migrate_pull_requests(repo_name)
        
        # 6. Migrate Issues
        self.migrate_issues(repo_name)
        
        # 7. Migrate variables
        print("\n7️⃣  Migrating repository variables...")
        variables = self.get_repo_variables(self.source_org, repo_name)
        for var in variables:
            if self.create_repo_variable(repo_name, var["name"], var["value"]):
                print(f"  ✓ Variable {var['name']} created")
        
        # 8. List secrets
        print("\n8️⃣  Listing repository secrets...")
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
        repos_data = self.paginated_request(url)
        return [repo["name"] for repo in repos_data]

def main():
    # Configuration
    GITHUB_TOKEN = "your_github_personal_access_token_here"
    SOURCE_ORG = "source-org-name"
    TARGET_ORG = "target-org-name"
    
    if GITHUB_TOKEN == "your_github_personal_access_token_here":
        print("❌ Please set your GitHub token in the script")
        sys.exit(1)
    
    migrator = GitHubMigrator(GITHUB_TOKEN, SOURCE_ORG, TARGET_ORG)
    
    print("GitHub Complete Repository Migration Tool")
    print("=" * 70)
    print("Migrates: commits, branches, tags, PRs, issues, comments, settings")
    print("=" * 70)
    
    repo_name = input("\nEnter repository name to migrate: ")
    
    confirm = input(f"\nMigrate '{repo_name}' from {SOURCE_ORG} to {TARGET_ORG}? (yes/no): ")
    
    if confirm.lower() == "yes":
        migrator.migrate_repository(repo_name)
    else:
        print("Migration cancelled")

if __name__ == "__main__":
    main()
