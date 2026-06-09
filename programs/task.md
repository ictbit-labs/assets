# Task: Create Identity Center CDK Stack Framework

## Goal

Create a new Identity Center CDK project structure that integrates with the existing custom deployment framework (`deploy_cdk.py`).

This task only creates the framework, structure, configuration loading, and stack wiring.

Do not implement full group creation, memberships, or assignments logic yet. That will be implemented in follow-up tasks.

The purpose of this task is to establish a reusable Identity Center deployment module that follows the existing CDK deployment model used across the platform.

---

# Existing Deployment Model

The project already contains a custom deployment utility:

```text
scripts/deploy_cdk.py
```

The deployment utility is responsible for:

* Reading `manifest.json`
* Loading `config.yaml`
* Generating CDK context flags (`-c`)
* Running CDK synth
* Running CDK deploy
* Monitoring CloudFormation progress
* Detecting stack failures
* Running post-deployment scripts
* Supporting both create and update workflows
* Displaying stack diffs
* Managing deployment lifecycle

The new Identity Center module must integrate with this existing deployment flow.

Do not duplicate deployment logic.

Do not create a new deployment framework.

---

# Directory Structure

Create:

```text
identity_center/
├── app.py
├── cdk.json
├── config/
│   └── config.yaml
├── stacks/
│   └── groups.py
├── logs/
└── cdk.out/
```

---

# app.py

Create a standard CDK application entry point.

Responsibilities:

* Load configuration
* Read CDK context values
* Instantiate the Identity Center stack
* Pass configuration into stack constructors
* Support deployment through existing `deploy_cdk.py`

Example responsibility:

```text
Load config.yaml
↓
Validate required fields
↓
Create CDK App
↓
Instantiate Groups Stack
↓
Synthesize
```

---

# stacks/groups.py

Create the initial stack implementation.

Requirements:

* Use AWS CDK v2
* Create a stack class
* Accept configuration object
* Validate configuration
* Expose logging
* Prepare framework for future resources

Do not create resources yet.

Placeholder support should exist for future:

```text
Identity Store Groups
Identity Store Group Memberships
Permission Set Assignments
```

The stack should deploy successfully even with no resources defined.

---

# config/config.yaml

Create a baseline configuration file.

Example:

```yaml
region: eu-west-1

identity_store_id: ""

sso_instance_arn: ""

groups: []
```

No hardcoded values.

The file serves as the future source of truth for Identity Center deployments.

---

# cdk.json

Create a standard CDK configuration file.

Must support context injection from:

```text
deploy_cdk.py
```

No environment-specific values should be hardcoded.

---

# logs

Create a logs directory.

Purpose:

```text
deployment logs
validation logs
future identity center operation logs
```

No implementation required beyond directory creation.

---

# cdk.out

Create and ensure proper exclusion handling.

This directory should remain CDK-generated output and must not contain committed synthesized artifacts.

Update ignore files as appropriate.

---

# Validation

Add configuration validation.

Required validations:

```text
config file exists
region exists
identity_store_id field exists
sso_instance_arn field exists
```

Values may be empty for now.

The goal is to fail early if configuration structure is invalid.

---

# Logging

Add structured logging support.

Requirements:

```text
stack initialization
configuration loading
validation results
resource preparation
```

Use Python logging.

---

# Success Criteria

The following command should work successfully through the existing deployment framework:

```bash
python scripts/deploy_cdk.py identity_center/
```

Expected result:

```text
Configuration loaded
Stack instantiated
CDK synth successful
CloudFormation deployment successful
```

No resources need to be created during this phase.

The purpose of this task is to establish the Identity Center CDK foundation that future tasks will extend with:

* Identity Store Groups
* Group Memberships
* Permission Set Assignments
* Account Assignments
* Additional Identity Center automation

```
```
