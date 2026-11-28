# Agent Guidelines for pixellens (`iconkat`)

## PyPI Publishing

The PyPI API token is stored in the macOS Keychain under service `UV_PUBLISH_TOKEN` for account `$USER`. When publishing a new release, run:

```bash
rm -rf dist && uv build
UV_PUBLISH_TOKEN="$(security find-generic-password -a "$USER" -s "UV_PUBLISH_TOKEN" -w)" uv publish
```

Never print, log, or store the token in files or git history.
