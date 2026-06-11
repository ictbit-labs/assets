# Identity Center CDK Deployment

This CDK app manages AWS IAM Identity Center groups, group memberships, and group account assignments from `config/config.yaml`.

The app is intentionally scoped to Identity Center access wiring:

- It can create missing Identity Store groups.
- It can create missing group memberships for existing users.
- It can create missing account assignments for existing AWS accounts and permission sets.
- It never creates Identity Store users, AWS accounts, or permission sets.
- It references existing matching groups, memberships, and assignments instead of recreating them.
- It keeps CloudFormation-created memberships and assignments in the template until they are removed from `config/config.yaml`.

## Files

- `app.py` loads and validates configuration, validates the selected AWS account with STS, resolves Identity Center resources, and synthesizes the stack.
- `stacks/groups.py` defines the CloudFormation resources and outputs.
- `config/config.yaml` is the default deployment configuration.
- `state.json` is generated during synth and records whether each resolved group, membership, and assignment is `created` or `existing`.
- `cdk.json` runs the app with `python3 app.py` and writes synthesized output to `cdk.out`.

## Prerequisites

- Python 3 with the dependencies in `requirements.txt`.
- AWS CDK v2.
- An explicit AWS profile configured locally. The app refuses to deploy with the `default` profile.
- The deploying identity must be able to call STS, Identity Store, and SSO Admin APIs.
- The Identity Center instance, Identity Store users, target AWS accounts, and permission sets must already exist.

Install the Python dependencies from this directory:

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
```

## Configuration

The default config path is `config/config.yaml`.

Required top-level fields:

```yaml
aws_profile: admin-sso
expected_account_id: "123456789012"
region: eu-west-1
identity_store_id: d-xxxxxxxxxx
sso_instance_arn: arn:aws:sso:::instance/ssoins-xxxxxxxxxxxxxxxx
groups: []
```

`expected_account_id` is a safety check. During synth or deploy, the app calls STS using `aws_profile` and aborts if the active account does not match this value.

Each group entry supports only `name`, `description`, `members`, and `assignments`:

```yaml
groups:
  - name: Developers
    description: Developer access group
    members:
      - username: john.doe
      - email: jane@example.com
      - user_id: abc123
    assignments:
      - account_id: "111122223333"
        permission_set_name: ReadOnlyAccess
      - account_id: "444455556666"
        permission_set_arn: arn:aws:sso:::permissionSet/ssoins-xxx/ps-xxx
```

Member rules:

- Each member must use exactly one of `username`, `email`, or `user_id`.
- Users are looked up in the configured Identity Store.
- Email lookup scans Identity Store users and requires exactly one matching email.

Assignment rules:

- `account_id` must be a 12-digit AWS account ID.
- Each assignment must use exactly one of `permission_set_name` or `permission_set_arn`.
- Permission set names are resolved to ARNs through SSO Admin and must match exactly one permission set.

Duplicate group names, duplicate members within a group, duplicate assignments within a group, and unsupported fields fail validation before synthesis.

## Examples

Use these as separate patterns. Do not combine them until you are ready to manage all of the listed groups, memberships, and assignments in one deployment.

### Example 1: Create One Group

If the group display name does not already exist in the configured Identity Store, the stack creates it.

```yaml
groups:
  - name: Developers
    description: Developer access group
```

### Example 2: Create Multiple Groups

Each group is resolved independently. Missing groups are created; existing groups with the same display name are referenced.

```yaml
groups:
  - name: Developers
    description: Developer access group

  - name: SecurityAdmins
    description: Security administrators group

  - name: FinanceReadOnly
    description: Finance read-only access group
```

### Example 3: Create A Group And Add Users

Users must already exist in Identity Store. The app can resolve users by `username`, `email`, or `user_id`.

```yaml
groups:
  - name: Developers
    description: Developer access group
    members:
      - username: john.doe
      - email: jane@example.com
      - user_id: 12345678-90ab-cdef-1234-567890abcdef
```

### Example 4: Add Users To An Existing Group

There is no separate import block. Declare the existing group by its exact Identity Center display name. If exactly one group named `Developers` already exists, the stack references it and creates only missing memberships.

```yaml
groups:
  - name: Developers
    description: Existing developer access group
    members:
      - username: john.doe
      - email: jane@example.com
```

### Example 5: Create A Group And Assign A Permission Set

The target AWS account and permission set must already exist. Use `permission_set_name` when the name is unique.

```yaml
groups:
  - name: FinanceReadOnly
    description: Finance read-only access group
    assignments:
      - account_id: "111122223333"
        permission_set_name: ReadOnlyAccess
```

### Example 6: Assign A Permission Set To An Existing Group

Declare the existing group by exact display name and add the desired account assignment. If the assignment already exists, the stack references it; if it is missing, the stack creates it.

```yaml
groups:
  - name: SecurityAdmins
    description: Existing security administrators group
    assignments:
      - account_id: "111122223333"
        permission_set_name: SecurityAudit
```

### Example 7: Use A Permission Set ARN

Use `permission_set_arn` when you want to avoid name lookup or when names could be ambiguous.

```yaml
groups:
  - name: PlatformAdmins
    description: Platform administrator access group
    assignments:
      - account_id: "111122223333"
        permission_set_arn: arn:aws:sso:::permissionSet/ssoins-xxxxxxxxxxxxxxxx/ps-xxxxxxxxxxxxxxxx
```

### Example 8: Add Users And Assign Access

This creates or references the group, creates any missing memberships, and creates any missing assignments.

```yaml
groups:
  - name: Developers
    description: Developer access group
    members:
      - username: john.doe
      - email: jane@example.com
    assignments:
      - account_id: "111122223333"
        permission_set_name: ReadOnlyAccess
      - account_id: "444455556666"
        permission_set_name: PowerUserAccess
```

### Supported Options

- Groups are resolved only by exact display `name`; `group_id` and explicit import modes are not supported.
- Members can be declared by exactly one of `username`, `email`, or `user_id`.
- Assignments can resolve permission sets by exactly one of `permission_set_name` or `permission_set_arn`.
- Empty groups are allowed.
- Empty `members` or `assignments` lists are allowed.
- Existing groups, memberships, and assignments are referenced when they already match the desired config.

## Context Overrides

Values in `config/config.yaml` can be overridden with CDK context:

```bash
cdk synth \
  -c profile=admin-sso \
  -c expected_account_id=123456789012 \
  -c region=eu-west-1 \
  -c identity_store_id=d-xxxxxxxxxx \
  -c sso_instance_arn=arn:aws:sso:::instance/ssoins-xxxxxxxxxxxxxxxx
```

Supported context keys:

- `profile` or `aws_profile`
- `expected_account_id`
- `region`
- `identity_store_id`
- `sso_instance_arn`
- `groups`
- `config_path`
- `state_path`
- `stack_name`

If `config_path` is relative, it is resolved from this directory.

## Synth And Deploy

From this directory:

```bash
cdk synth
cdk diff
cdk deploy
```

To deploy with a custom stack name:

```bash
cdk deploy -c stack_name=IdentityCenterGroupsStack
```

The app uses `BootstraplessSynthesizer`, so it does not require CDK bootstrap assets for this stack.

## Resource Resolution

Before the stack is created, the app checks the current Identity Center state:

- If a configured group display name does not exist, CloudFormation creates it.
- If exactly one configured group display name exists, the stack references that group ID.
- If multiple groups match the same display name, deployment is aborted.
- If a configured membership or assignment already exists and `state.json` does not record it as CloudFormation-created, the stack references it.
- If `state.json` records a configured membership or assignment as `created`, the stack keeps the CloudFormation resource in the template even when AWS now reports that it exists.

CloudFormation outputs include group IDs, membership IDs, account assignment details, and whether each item was `created` or `existing`.

Created groups are given a `RETAIN` removal policy.

## Lifecycle Management

`config/config.yaml` is the desired state for resources owned by this stack:

- Removing a CloudFormation-created membership from config removes it from the template, so CloudFormation deletes the membership from AWS.
- Removing a CloudFormation-created assignment from config removes it from the template, so CloudFormation deletes the assignment from AWS.
- Removing a CloudFormation-created group from config removes it from the stack, but the physical Identity Center group remains because groups use `RemovalPolicy.RETAIN`.
- Removing an externally managed group, membership, or assignment from config has no delete effect because it was never emitted as a CloudFormation resource.

Ownership is persisted in `state.json`. Entries with `source: "created"` are treated as CloudFormation-owned on later synths. Entries with `source: "existing"` remain reference-only. If the state file is malformed, has an unsupported source, or says a resource was external but AWS no longer has it, the app aborts before deployment.

## Drift Detection

CloudFormation is the desired state for memberships and assignments created by this stack. To check drift:

```bash
aws cloudformation detect-stack-drift --stack-name <stack-name>
aws cloudformation describe-stack-drift-detection-status --stack-drift-detection-id <id>
aws cloudformation describe-stack-resource-drifts --stack-name <stack-name>
```

Expected behavior:

- If a CloudFormation-managed group membership is manually removed, drift should be detected.
- If a CloudFormation-managed account assignment is manually removed, drift should be detected.
- Existing groups, memberships, and assignments that are referenced but not created by this stack may not be fully drift-managed by CloudFormation.

## Failure Modes

The app aborts before synth/deploy when it cannot prove the deployment is safe. Common causes:

- The configured AWS profile does not exist or has no credentials.
- The active AWS account does not match `expected_account_id`.
- A configured Identity Store user or permission set cannot be found.
- A username, email, group display name, membership, or permission set lookup returns multiple matches.
- A group, member, or assignment entry contains unsupported fields.
