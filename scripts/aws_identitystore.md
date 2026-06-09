# Minimal IAM Identity Center Tool

This directory now includes a minimal IAM Identity Center management tool:

```text
idc_tool.py
```

The tool is intended for hardened jumpbox environments where each operator uses an explicit local AWS profile.

## Dependencies

Use only:

```text
boto3
pyyaml
```

The CLI uses Python built-in `argparse`.

The minimal tool is intentionally limited to the two packages above plus Python standard library modules.

Install:

```bash
cd scripts
python3 -m venv .venv-idc-tool
. .venv-idc-tool/bin/activate
pip install -r idc_tool/requirements.txt
```

## Profile Handling

The tool must never silently use default AWS credentials.

Profile resolution order:

1. CLI flag: `--profile <profile-name>`
2. `profile` in `idc_tool/config.yaml`
3. fail with a clear error

Missing profile error:

```text
ERROR: No AWS profile specified.
Provide --profile <name> or set profile in config.yaml.
```

Example config:

```yaml
region: eu-west-1
profile: admin-sso
identity_store_id: d-xxxxxxxxxx
sso_instance_arn: arn:aws:sso:::instance/ssoins-xxxxxxxxxxxxxxxx
```

The CLI creates all AWS clients from an explicit session:

```python
session = boto3.Session(
    profile_name=profile,
    region_name=region,
)

identitystore = session.client("identitystore")
sso_admin = session.client("sso-admin")
organizations = session.client("organizations")
```

The tool creates service clients only from that explicit session.

For AWS SSO profiles:

```bash
aws sso login --profile admin-sso
```

If the SSO session expires, the tool prints:

```text
AWS SSO session expired. Run:
aws sso login --profile <profile>
```

## Commands

Users:

```bash
python idc_tool.py users list
python idc_tool.py users list --output json
python idc_tool.py users list --profile breakglass-admin
python idc_tool.py users search john
python idc_tool.py users create --username john.doe --email john@example.com --first-name John --last-name Doe
```

Groups:

```bash
python idc_tool.py groups list
python idc_tool.py groups list --output json
python idc_tool.py groups create --name Developers
python idc_tool.py groups search dev
```

Memberships:

```bash
python idc_tool.py memberships add --user john.doe --group Developers
python idc_tool.py memberships remove --user john.doe --group Developers
python idc_tool.py memberships list --group Developers
python idc_tool.py memberships list --user john.doe
```

Permission sets and accounts:

```bash
python idc_tool.py permission-sets list
python idc_tool.py accounts list
```

Assignments:

```bash
python idc_tool.py assignments list
python idc_tool.py assignments list --output json
python idc_tool.py assignments create --account-id 123456789012 --permission-set ReadOnlyAccess --group Developers
python idc_tool.py assignments create --account-id 123456789012 --permission-set ReadOnlyAccess --user john.doe
python idc_tool.py assignments delete --account-id 123456789012 --permission-set ReadOnlyAccess --group Developers
```

Reports:

```bash
python idc_tool.py reports memberships
python idc_tool.py reports memberships --output json
python idc_tool.py reports inventory --output-file reports/idc-inventory.json
```

## Output

Supported output modes:

- `text`
- `json`

Use JSON for automation:

```bash
python idc_tool.py users list --output json
python idc_tool.py groups list --output json
python idc_tool.py assignments list --output json
```
