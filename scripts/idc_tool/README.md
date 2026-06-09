# Minimal idc_tool

`idc_tool.py` is a minimal IAM Identity Center management CLI for hardened jumpbox environments.

It uses only:

- `boto3`
- `pyyaml`
- Python built-in `argparse`

The implementation is intentionally limited to the two packages above plus Python standard library modules.

## Install

```bash
cd scripts
python3 -m venv .venv-idc-tool
. .venv-idc-tool/bin/activate
pip install -r idc_tool/requirements.txt
```

## Config

Default config path:

```text
scripts/idc_tool/config.yaml
```

Example:

```yaml
region: eu-west-1
profile: admin-sso
identity_store_id: d-xxxxxxxxxx
sso_instance_arn: arn:aws:sso:::instance/ssoins-xxxxxxxxxxxxxxxx
```

Profile resolution order:

1. `--profile <profile-name>`
2. `profile` in `config.yaml`
3. fail with:

```text
ERROR: No AWS profile specified.
Provide --profile <name> or set profile in config.yaml.
```

All AWS clients are created from:

```python
session = boto3.Session(profile_name=profile, region_name=region)
```

The tool creates service clients only from that explicit session and never silently falls back to default AWS credentials.

## Examples

```bash
python idc_tool.py users list
python idc_tool.py users list --output json
python idc_tool.py users list --profile breakglass-admin

python idc_tool.py groups list
python idc_tool.py memberships add --user john.doe --group Developers
python idc_tool.py memberships list --group Developers

python idc_tool.py permission-sets list
python idc_tool.py accounts list
python idc_tool.py assignments list --output json
python idc_tool.py assignments create --account-id 123456789012 --permission-set ReadOnlyAccess --group Developers
python idc_tool.py assignments delete --account-id 123456789012 --permission-set ReadOnlyAccess --group Developers

python idc_tool.py reports memberships
python idc_tool.py reports memberships --output json
python idc_tool.py reports inventory --output-file reports/idc-inventory.json
```

For AWS SSO profiles, log in first:

```bash
aws sso login --profile admin-sso
```

If the SSO session expires, the tool prints the login command for the selected profile.
