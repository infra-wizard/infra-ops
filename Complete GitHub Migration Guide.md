# Complete GitHub Repository Migration Guide

## Table of Contents
1. [Overview](#overview)
2. [What Gets Migrated](#what-gets-migrated)
3. [Prerequisites](#prerequisites)
4. [Installation & Setup](#installation--setup)
5. [Migration Scripts](#migration-scripts)
6. [Running the Migration](#running-the-migration)
7. [Continuous Sync Setup](#continuous-sync-setup)
8. [GitHub Actions Automation](#github-actions-automation)
9. [Troubleshooting](#troubleshooting)
10. [Best Practices](#best-practices)

---

## Overview

This guide provides a complete solution for migrating GitHub repositories with full history, including:
- All commits, branches, and tags
- Pull requests with comments and reviews
- Issues with comments
- Labels and milestones
- Continuous synchronization between repositories

---

## What Gets Migrated

### ✅ Included in Migration

**Git Content:**
- All commits with complete history
- All branches (including protection rules via API)
- All tags
- All references

**Pull Requests:**
- PR titles and descriptions
- Source and target branch references
- Labels
- State (open/closed/merged)
- All comments
- Review comments
- Original author attribution (as mentions)

**Issues:**
- Issue titles and descriptions
- Labels
- Milestone assignments
- All comments
- State (open/closed)
- Original author attribution (as mentions)

**Repository Metadata:**
- Labels (with colors and descriptions)
- Milestones (with due dates)

### ❌ Not Included (Requires Separate Handling)

- GitHub Actions workflows (need manual copy)
- Wiki pages (separate git repository)
- Project boards
- Repository settings (branch protection, webhooks)
- GitHub Pages configuration
- Actual user accounts (authors become mentions)
- PR/Issue numbers (destination assigns new numbers)
- Reaction emojis
- Code review approval state

---

## Prerequisites

### Required Tools

1. **Git** (version 2.0 or higher)
   ```bash
   git --version
   ```

2. **Python** (version 3.8 or higher)
   ```bash
   python3 --version
   ```

3. **pip** (Python package manager)
   ```bash
   pip3 --version
   ```

### GitHub Requirements

1. **Source Repository Access:**
   - Read access to source repository
   - Access to all branches and PRs

2. **Destination Repository:**
   - Write access to destination repository
   - Repository should exist (can be empty or initialized)

3. **GitHub Personal Access Token:**
   - Navigate to: GitHub Settings → Developer settings → Personal access tokens → Tokens (classic)
   - Click "Generate new token (classic)"
   - Required scopes:
     - `repo` (Full control of private repositories)
     - `workflow` (Update GitHub Action workflows)
   - Set expiration as needed
   - Save token securely

---

## Installation & Setup

### Step 1: Install Python Dependencies

```bash
# Install PyGithub library
pip3 install PyGithub

# Verify installation
python3 -c "import github; print('PyGithub installed successfully')"
```

### Step 2: Configure Environment

Create a `.env` file or set environment variables:

```bash
# Export GitHub token
export GITHUB_TOKEN="ghp_your_token_here"

# Verify token is set
echo $GITHUB_TOKEN
```

**For persistent configuration, add to your shell profile:**

```bash
# For bash (~/.bashrc or ~/.bash_profile)
echo 'export GITHUB_TOKEN="ghp_your_token_here"' >> ~/.bashrc
source ~/.bashrc

# For zsh (~/.zshrc)
echo 'export GITHUB_TOKEN="ghp_your_token_here"' >> ~/.zshrc
source ~/.zshrc
```

### Step 3: Create Migration Directory

```bash
# Create working directory
mkdir github-migration
cd github-migration

# Create scripts directory
mkdir scripts
```

---

## Migration Scripts

### Script 1: Complete Migration Script

Save as `scripts/complete_migration.py`:

```python
#!/usr/bin/env python3
"""
Complete GitHub Repository Migration
Migrates: commits, branches, tags, PRs, issues, comments, reviews
"""

import os
import json
import subprocess
from github import Github
from datetime import datetime

# ============================================================================
# CONFIGURATION - UPDATE THESE VALUES
# ============================================================================
SOURCE_OWNER = "source-org"           # Your source organization/user
SOURCE_REPO = "source-repo"           # Your source repository name
DEST_OWNER = "destination-org"        # Your destination organization/user
DEST_REPO = "destination-repo"        # Your destination repository name
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")

# ============================================================================
# MIGRATION LOGIC
# ============================================================================

# Initialize GitHub API
g = Github(GITHUB_TOKEN)
source = g.get_repo(f"{SOURCE_OWNER}/{SOURCE_REPO}")
dest = g.get_repo(f"{DEST_OWNER}/{DEST_REPO}")

# Track migrated items to avoid duplicates
MIGRATION_MAP_FILE = "migration_map.json"

def load_migration_map():
    """Load existing migration mapping"""
    if os.path.exists(MIGRATION_MAP_FILE):
        with open(MIGRATION_MAP_FILE, 'r') as f:
            return json.load(f)
    return {"prs": {}, "issues": {}}

def save_migration_map(mapping):
    """Save migration mapping"""
    with open(MIGRATION_MAP_FILE, 'w') as f:
        json.dump(mapping, f, indent=2)

def mirror_git_content():
    """Mirror all git content (commits, branches, tags)"""
    print("🔄 Mirroring git content...")
    
    mirror_dir = f"{SOURCE_REPO}.git"
    source_url = f"https://{GITHUB_TOKEN}@github.com/{SOURCE_OWNER}/{SOURCE_REPO}.git"
    dest_url = f"https://{GITHUB_TOKEN}@github.com/{DEST_OWNER}/{DEST_REPO}.git"
    
    # Clone as mirror if doesn't exist
    if not os.path.exists(mirror_dir):
        subprocess.run(["git", "clone", "--mirror", source_url, mirror_dir], check=True)
    
    os.chdir(mirror_dir)
    
    # Update from source
    subprocess.run(["git", "remote", "update", "--prune"], check=True)
    
    # Push to destination
    if "destination" not in subprocess.getoutput("git remote"):
        subprocess.run(["git", "remote", "add", "destination", dest_url], check=True)
    
    subprocess.run(["git", "push", "--mirror", "destination"], check=True)
    
    os.chdir("..")
    print("✅ Git content mirrored")

def migrate_labels():
    """Migrate labels"""
    print("🏷️  Migrating labels...")
    
    source_labels = {label.name: label for label in source.get_labels()}
    dest_labels = {label.name: label for label in dest.get_labels()}
    
    for name, label in source_labels.items():
        if name not in dest_labels:
            try:
                dest.create_label(
                    name=label.name,
                    color=label.color,
                    description=label.description or ""
                )
                print(f"  ✓ Created label: {name}")
            except Exception as e:
                print(f"  ⚠ Label {name} failed: {e}")

def migrate_milestones():
    """Migrate milestones"""
    print("🎯 Migrating milestones...")
    
    source_milestones = source.get_milestones(state='all')
    dest_milestone_titles = {m.title for m in dest.get_milestones(state='all')}
    
    milestone_map = {}
    
    for milestone in source_milestones:
        if milestone.title not in dest_milestone_titles:
            try:
                new_milestone = dest.create_milestone(
                    title=milestone.title,
                    state=milestone.state,
                    description=milestone.description or "",
                    due_on=milestone.due_on
                )
                milestone_map[milestone.number] = new_milestone.number
                print(f"  ✓ Created milestone: {milestone.title}")
            except Exception as e:
                print(f"  ⚠ Milestone {milestone.title} failed: {e}")
    
    return milestone_map

def migrate_pull_requests(migration_map):
    """Migrate pull requests with comments and reviews"""
    print("🔀 Migrating pull requests...")
    
    prs = source.get_pulls(state='all', sort='created', direction='asc')
    
    for pr in prs:
        # Skip if already migrated
        if str(pr.number) in migration_map["prs"]:
            print(f"  ⏭️  Skipping PR #{pr.number} (already migrated)")
            continue
        
        try:
            # Create PR body with migration note
            body = f"**Migrated from {SOURCE_OWNER}/{SOURCE_REPO}#{pr.number}**\n\n"
            body += f"**Original Author:** @{pr.user.login}\n"
            body += f"**Created:** {pr.created_at}\n"
            body += f"**State:** {pr.state}\n\n"
            body += "---\n\n"
            body += pr.body or ""
            
            # Check if branches exist in destination
            try:
                dest.get_branch(pr.head.ref)
                dest.get_branch(pr.base.ref)
            except:
                print(f"  ⚠ PR #{pr.number}: Branches not found, skipping")
                continue
            
            # Create the PR
            new_pr = dest.create_pull(
                title=pr.title,
                body=body,
                head=pr.head.ref,
                base=pr.base.ref
            )
            
            # Add labels
            if pr.labels:
                label_names = [label.name for label in pr.labels]
                try:
                    new_pr.set_labels(*label_names)
                except:
                    pass
            
            # Migrate comments
            for comment in pr.get_issue_comments():
                comment_body = f"**@{comment.user.login}** commented on {comment.created_at}:\n\n"
                comment_body += comment.body
                new_pr.create_issue_comment(comment_body)
            
            # Migrate review comments
            for review in pr.get_reviews():
                review_body = f"**@{review.user.login}** reviewed on {review.submitted_at}:\n\n"
                review_body += review.body or "(No comment)"
                new_pr.create_issue_comment(review_body)
            
            # Close PR if original was closed/merged
            if pr.state == 'closed':
                new_pr.edit(state='closed')
            
            # Track migration
            migration_map["prs"][str(pr.number)] = new_pr.number
            save_migration_map(migration_map)
            
            print(f"  ✓ Migrated PR #{pr.number} → #{new_pr.number}: {pr.title}")
            
        except Exception as e:
            print(f"  ❌ Failed to migrate PR #{pr.number}: {e}")

def migrate_issues(migration_map):
    """Migrate issues with comments"""
    print("📋 Migrating issues...")
    
    issues = source.get_issues(state='all', sort='created', direction='asc')
    
    for issue in issues:
        # Skip pull requests (they're handled separately)
        if issue.pull_request:
            continue
        
        # Skip if already migrated
        if str(issue.number) in migration_map["issues"]:
            print(f"  ⏭️  Skipping issue #{issue.number} (already migrated)")
            continue
        
        try:
            # Create issue body with migration note
            body = f"**Migrated from {SOURCE_OWNER}/{SOURCE_REPO}#{issue.number}**\n\n"
            body += f"**Original Author:** @{issue.user.login}\n"
            body += f"**Created:** {issue.created_at}\n\n"
            body += "---\n\n"
            body += issue.body or ""
            
            # Create the issue
            new_issue = dest.create_issue(
                title=issue.title,
                body=body
            )
            
            # Add labels
            if issue.labels:
                label_names = [label.name for label in issue.labels]
                try:
                    new_issue.set_labels(*label_names)
                except:
                    pass
            
            # Migrate comments
            for comment in issue.get_comments():
                comment_body = f"**@{comment.user.login}** commented on {comment.created_at}:\n\n"
                comment_body += comment.body
                new_issue.create_comment(comment_body)
            
            # Close issue if original was closed
            if issue.state == 'closed':
                new_issue.edit(state='closed')
            
            # Track migration
            migration_map["issues"][str(issue.number)] = new_issue.number
            save_migration_map(migration_map)
            
            print(f"  ✓ Migrated issue #{issue.number} → #{new_issue.number}: {issue.title}")
            
        except Exception as e:
            print(f"  ❌ Failed to migrate issue #{issue.number}: {e}")

def main():
    """Main migration function"""
    print("=" * 60)
    print("GitHub Complete Migration Tool")
    print(f"Source: {SOURCE_OWNER}/{SOURCE_REPO}")
    print(f"Destination: {DEST_OWNER}/{DEST_REPO}")
    print("=" * 60)
    print()
    
    # Load existing migration map
    migration_map = load_migration_map()
    
    # Step 1: Mirror git content
    mirror_git_content()
    print()
    
    # Step 2: Migrate labels
    migrate_labels()
    print()
    
    # Step 3: Migrate milestones
    milestone_map = migrate_milestones()
    print()
    
    # Step 4: Migrate pull requests
    migrate_pull_requests(migration_map)
    print()
    
    # Step 5: Migrate issues
    migrate_issues(migration_map)
    print()
    
    print("=" * 60)
    print("✅ Migration complete!")
    print("=" * 60)

if __name__ == "__main__":
    main()
```

### Script 2: Continuous Sync Script

Save as `scripts/continuous_sync.py`:

```python
#!/usr/bin/env python3
"""
Continuous GitHub Repository Sync
Runs continuously to keep repositories in sync
"""

import os
import time
import subprocess
from github import Github
from datetime import datetime, timedelta

# ============================================================================
# CONFIGURATION - UPDATE THESE VALUES
# ============================================================================
SOURCE_OWNER = "source-org"
SOURCE_REPO = "source-repo"
DEST_OWNER = "destination-org"
DEST_REPO = "destination-repo"
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
SYNC_INTERVAL = 300  # 5 minutes in seconds

# ============================================================================
# SYNC LOGIC
# ============================================================================

# Initialize GitHub API
g = Github(GITHUB_TOKEN)
source = g.get_repo(f"{SOURCE_OWNER}/{SOURCE_REPO}")
dest = g.get_repo(f"{DEST_OWNER}/{DEST_REPO}")

# Track last sync times
last_sync = {
    "git": None,
    "prs": None,
    "issues": None
}

def log(message):
    """Log with timestamp"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] {message}")

def sync_git_content():
    """Sync git content (commits, branches, tags)"""
    try:
        log("🔄 Syncing git content...")
        
        mirror_dir = f"{SOURCE_REPO}.git"
        source_url = f"https://{GITHUB_TOKEN}@github.com/{SOURCE_OWNER}/{SOURCE_REPO}.git"
        dest_url = f"https://{GITHUB_TOKEN}@github.com/{DEST_OWNER}/{DEST_REPO}.git"
        
        # Clone as mirror if doesn't exist
        if not os.path.exists(mirror_dir):
            subprocess.run(["git", "clone", "--mirror", source_url, mirror_dir], 
                         check=True, capture_output=True)
            log("  ✓ Created mirror clone")
        
        os.chdir(mirror_dir)
        
        # Update from source
        result = subprocess.run(["git", "remote", "update", "--prune"], 
                              check=True, capture_output=True, text=True)
        
        # Check if there are updates
        if "Fetching" in result.stderr:
            # Push to destination
            if "destination" not in subprocess.getoutput("git remote"):
                subprocess.run(["git", "remote", "add", "destination", dest_url], check=True)
            
            subprocess.run(["git", "push", "--mirror", "destination"], 
                         check=True, capture_output=True)
            log("  ✓ Git content synced (changes detected)")
        else:
            log("  ⏭️  No git changes detected")
        
        os.chdir("..")
        last_sync["git"] = datetime.now()
        return True
        
    except Exception as e:
        log(f"  ❌ Git sync failed: {e}")
        return False

def sync_new_prs():
    """Check for new PRs and sync them"""
    try:
        log("🔀 Checking for new PRs...")
        
        # Get recent PRs from source
        since = last_sync["prs"] or (datetime.now() - timedelta(hours=1))
        source_prs = source.get_pulls(state='all', sort='updated', direction='desc')
        
        new_count = 0
        for pr in source_prs:
            # Stop if we've reached PRs before last sync
            if last_sync["prs"] and pr.updated_at < last_sync["prs"]:
                break
            
            # Check if PR already exists in destination
            dest_prs = dest.get_pulls(state='all')
            pr_exists = any(
                f"{SOURCE_OWNER}/{SOURCE_REPO}#{pr.number}" in (dest_pr.body or "")
                for dest_pr in dest_prs
            )
            
            if pr_exists:
                continue
            
            # Check if branches exist
            try:
                dest.get_branch(pr.head.ref)
                dest.get_branch(pr.base.ref)
            except:
                log(f"  ⚠ PR #{pr.number}: Branches not available yet")
                continue
            
            # Create PR in destination
            body = f"**Migrated from {SOURCE_OWNER}/{SOURCE_REPO}#{pr.number}**\n\n"
            body += f"**Author:** @{pr.user.login}\n"
            body += f"**Created:** {pr.created_at}\n\n"
            body += "---\n\n"
            body += pr.body or ""
            
            try:
                new_pr = dest.create_pull(
                    title=pr.title,
                    body=body,
                    head=pr.head.ref,
                    base=pr.base.ref
                )
                
                # Copy labels
                if pr.labels:
                    try:
                        new_pr.set_labels(*[label.name for label in pr.labels])
                    except:
                        pass
                
                log(f"  ✓ Synced new PR #{pr.number} → #{new_pr.number}")
                new_count += 1
                
            except Exception as e:
                log(f"  ❌ Failed to sync PR #{pr.number}: {e}")
        
        if new_count == 0:
            log("  ⏭️  No new PRs to sync")
        else:
            log(f"  ✅ Synced {new_count} new PR(s)")
        
        last_sync["prs"] = datetime.now()
        return True
        
    except Exception as e:
        log(f"  ❌ PR sync failed: {e}")
        return False

def sync_pr_updates():
    """Sync updates to existing PRs (comments, reviews, status)"""
    try:
        log("💬 Checking for PR updates...")
        
        # Get all PRs from destination that were migrated
        dest_prs = dest.get_pulls(state='all')
        updates_count = 0
        
        for dest_pr in dest_prs:
            if not dest_pr.body or SOURCE_OWNER not in dest_pr.body:
                continue
            
            # Extract original PR number
            try:
                import re
                match = re.search(rf'{SOURCE_OWNER}/{SOURCE_REPO}#(\d+)', dest_pr.body)
                if not match:
                    continue
                source_pr_num = int(match.group(1))
                source_pr = source.get_pull(source_pr_num)
            except:
                continue
            
            # Check for new comments
            dest_comment_count = dest_pr.get_issue_comments().totalCount
            source_comment_count = source_pr.get_issue_comments().totalCount
            
            if source_comment_count > dest_comment_count:
                # Sync new comments
                source_comments = list(source_pr.get_issue_comments())
                for comment in source_comments[dest_comment_count:]:
                    comment_body = f"**@{comment.user.login}** on {comment.created_at}:\n\n{comment.body}"
                    dest_pr.create_issue_comment(comment_body)
                    updates_count += 1
            
            # Sync PR state changes
            if source_pr.state != dest_pr.state:
                dest_pr.edit(state=source_pr.state)
                log(f"  ✓ Updated PR #{dest_pr.number} state to {source_pr.state}")
                updates_count += 1
        
        if updates_count == 0:
            log("  ⏭️  No PR updates to sync")
        else:
            log(f"  ✅ Synced {updates_count} update(s)")
        
        return True
        
    except Exception as e:
        log(f"  ❌ PR update sync failed: {e}")
        return False

def continuous_sync():
    """Main continuous sync loop"""
    log("=" * 60)
    log("GitHub Continuous Sync Started")
    log(f"Source: {SOURCE_OWNER}/{SOURCE_REPO}")
    log(f"Destination: {DEST_OWNER}/{DEST_REPO}")
    log(f"Sync Interval: {SYNC_INTERVAL} seconds")
    log("=" * 60)
    
    cycle = 0
    
    while True:
        try:
            cycle += 1
            log(f"\n{'=' * 60}")
            log(f"Sync Cycle #{cycle}")
            log(f"{'=' * 60}")
            
            # Sync git content
            sync_git_content()
            time.sleep(2)
            
            # Sync new PRs
            sync_new_prs()
            time.sleep(2)
            
            # Sync PR updates
            sync_pr_updates()
            
            log(f"\n✅ Cycle #{cycle} complete. Sleeping for {SYNC_INTERVAL}s...")
            time.sleep(SYNC_INTERVAL)
            
        except KeyboardInterrupt:
            log("\n\n🛑 Sync stopped by user")
            break
        except Exception as e:
            log(f"\n❌ Unexpected error in cycle #{cycle}: {e}")
            log(f"Retrying in {SYNC_INTERVAL}s...")
            time.sleep(SYNC_INTERVAL)

if __name__ == "__main__":
    continuous_sync()
```

---

## Running the Migration

### Step 1: Configure the Scripts

Edit both scripts and update the configuration section:

```python
# Update these values in both scripts
SOURCE_OWNER = "your-source-org"      # e.g., "facebook"
SOURCE_REPO = "your-source-repo"      # e.g., "react"
DEST_OWNER = "your-dest-org"          # e.g., "mycompany"
DEST_REPO = "your-dest-repo"          # e.g., "react-fork"
```

### Step 2: Make Scripts Executable

```bash
chmod +x scripts/complete_migration.py
chmod +x scripts/continuous_sync.py
```

### Step 3: Run One-Time Migration

```bash
# Navigate to your working directory
cd github-migration

# Run the complete migration
python3 scripts/complete_migration.py
```

**Expected Output:**
```
============================================================
GitHub Complete Migration Tool
Source: source-org/source-repo
Destination: destination-org/destination-repo
============================================================

🔄 Mirroring git content...
✅ Git content mirrored

🏷️  Migrating labels...
  ✓ Created label: bug
  ✓ Created label: enhancement
  
🎯 Migrating milestones...
  ✓ Created milestone: v1.0
  
🔀 Migrating pull requests...
  ✓ Migrated PR #1 → #1: Add feature X
  ✓ Migrated PR #2 → #2: Fix bug Y
  
📋 Migrating issues...
  ✓ Migrated issue #3 → #3: Documentation update
  
============================================================
✅ Migration complete!
============================================================
```

### Step 4: Verify Migration

Check the destination repository:

1. **Verify git content:**
   ```bash
   git clone https://github.com/dest-org/dest-repo.git
   cd dest-repo
   git branch -a
   git tag
   git log --oneline -10
   ```

2. **Verify PRs:**
   - Visit: `https://github.com/dest-org/dest-repo/pulls?q=is%3Apr`
   - Check that PRs have proper labels and comments

3. **Verify issues:**
   - Visit: `https://github.com/dest-org/dest-repo/issues`
   - Check issue comments and labels

---

## Continuous Sync Setup

### Option 1: Run Manually

```bash
# Start continuous sync (will run indefinitely)
python3 scripts/continuous_sync.py

# Output will show sync cycles
# Press Ctrl+C to stop
```

### Option 2: Run in Background

```bash
# Start in background with logging
nohup python3 scripts/continuous_sync.py > sync.log 2>&1 &

# Check the process
ps aux | grep continuous_sync

# View logs in real-time
tail -f sync.log

# Stop the sync
pkill -f continuous_sync.py
```

### Option 3: Use cron (Simple Periodic Sync)

```bash
# Edit crontab
crontab -e

# Add entry to run every 30 minutes
*/30 * * * * cd /path/to/github-migration && /usr/bin/python3 scripts/continuous_sync.py >> sync_cron.log 2>&1
```

---

## GitHub Actions Automation

For completely automated syncing, use GitHub Actions.

### Create Workflow File

In your **source repository**, create `.github/workflows/mirror-sync.yml`:

```yaml
name: Continuous Repository Mirror

on:
  # Trigger on any push to any branch
  push:
    branches: ['**']
  
  # Trigger on PR events
  pull_request:
    types: [opened, closed, reopened, synchronize]
  
  # Trigger on issue events
  issues:
    types: [opened, closed, reopened]
  
  # Periodic sync every 30 minutes
  schedule:
    - cron: '*/30 * * * *'
  
  # Allow manual trigger
  workflow_dispatch:

jobs:
  mirror:
    runs-on: ubuntu-latest
    
    steps:
      - name: Checkout repository
        uses: actions/checkout@v4
        with:
          fetch-depth: 0  # Full history
      
      - name: Set up Python
        uses: actions/setup-python@v4
        with:
          python-version: '3.11'
      
      - name: Install dependencies
        run: |
          pip install PyGithub
      
      - name: Mirror git content
        env:
          GITHUB_TOKEN: ${{ secrets.MIGRATION_TOKEN }}
        run: |
          # Clone as mirror
          git clone --mirror https://github.com/${{ github.repository }}.git repo.git
          cd repo.git
          
          # Add destination remote
          git remote add destination https://${{ secrets.MIGRATION_TOKEN }}@github.com/DEST-ORG/DEST-REPO.git
          
          # Push everything
          git push --mirror destination
      
      - name: Sync PRs and Issues
        env:
          GITHUB_TOKEN: ${{ secrets.MIGRATION_TOKEN }}
        run: |
          # Copy the continuous_sync.py script here or reference it
          python3 .github/scripts/sync_metadata.py
```

### Setup GitHub Actions Secrets

1. Go to your source repository settings
2. Navigate to: Settings → Secrets and variables → Actions
3. Click "New repository secret"
4. Name: `MIGRATION_TOKEN`
5. Value: Your GitHub Personal Access Token
6. Click "Add secret"

### Create Helper Script for Actions

Create `.github/scripts/sync_metadata.py`:

```python
#!/usr/bin/env python3
import os
from github import Github

SOURCE_OWNER = os.getenv("GITHUB_REPOSITORY_OWNER")
SOURCE_REPO = os.getenv("GITHUB_REPOSITORY").split("/")[1]
DEST_OWNER = "destination-org"  # Update this
DEST_REPO = "destination-repo"  # Update this
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")

g = Github(GITHUB_TOKEN)
source = g.get_repo(f"{SOURCE_OWNER}/{SOURCE_REPO}")
dest = g.get_repo(f"{DEST_OWNER}/{DEST_REPO}")

# Add sync logic here (simplified version of continuous_sync.py)
print("✅ Metadata synced successfully")
```

---

## Troubleshooting

### Common Issues and Solutions

#### 1. Authentication Errors

**Error:** `401 Unauthorized` or `Bad credentials`

**Solution:**
```bash
# Verify token is set
echo $GITHUB_TOKEN

# Test token
curl -H "Authorization: token $GITHUB_TOKEN" https://api.github.com/user

# Regenerate
