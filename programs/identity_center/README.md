# Identity Center CDK Deployment

This CDK module manages IAM Identity Center groups, group memberships, and group account assignments.

Users and AWS accounts are never created by this stack. Users must already exist in the configured Identity Store, and target AWS accounts must already exist.

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
