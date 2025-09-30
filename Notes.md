GitHub Self-Hosted Runners Configuration - Implementation Notes
Initial Setup: Static Runners
Configuration:

Implemented GitHub self-hosted runners using static machines (D3, D2 instance types)

Challenges Encountered:

Lack of access at GitHub Organization level
Unable to configure a single runner accessible to all repositories
Limited to repository-level runner configuration
Static resources remained allocated even when not in use


Solution: Migration to Ephemeral Runners
Technology Stack:

Platform: Azure Kubernetes Service (AKS)
Tool: Actions Runner Controller (ARC)

Implementation Approach:

Switched from static runners to ephemeral runners
Configured at repository level using ARC in AKS cluster


How It Works
Workflow Execution Process:

Trigger: Workflow is initiated in the repository
Dynamic Provisioning: Pod spins up automatically in AKS
Execution: Workflow runs inside the ephemeral pod
Cleanup: Pod is automatically deprecated/destroyed after workflow completion
Result: Clean workspace maintained for every run


Key Benefits
✓ Automatic Scaling - Resources provisioned on-demand
✓ Clean Environment - Fresh workspace for every workflow run
✓ Cost Optimization - No idle static resources
✓ Isolation - Each workflow runs in its own isolated pod
✓ No Manual Cleanup - Automatic pod termination post-execution
