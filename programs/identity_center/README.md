# Identity Center CDK Deployment

This CDK app manages AWS IAM Identity Center groups, group memberships, and group account assignments from `config/config.yaml`.

The app is intentionally scoped to Identity Center access wiring:

- It can create missing Identity Store groups.
- It can create missing group memberships for existing users.
- It can create missing account assignments for existing AWS accounts and permission sets.
- It never creates Identity Store users, AWS accounts, or permission sets.
- It references existing matching groups, memberships, and assignments instead of recreating them.

## Files

- `app.py` loads and validates configuration, validates the selected AWS account with STS, resolves Identity Center resources, and synthesizes the stack.
- `stacks/groups.py` defines the CloudFormation resources and outputs.
- `config/config.yaml` is the default deployment configuration.
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
- If a configured membership already exists for the resolved group and user, the stack references it.
- If a configured account assignment already exists for the resolved group, account, and permission set, the stack references it.

CloudFormation outputs include group IDs, membership IDs, account assignment details, and whether each item was `created` or `existing`.

Created groups are given a `RETAIN` removal policy.

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
