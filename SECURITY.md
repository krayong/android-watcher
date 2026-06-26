# Security Policy

## Supported versions

The latest release and the `main` branch receive security fixes. Older releases
are not patched.

## Threat model and data handled

`android-watcher` is a self-hosted, single-user CLI. It does not run as a service
exposed to the network.

**Credentials at rest.** The Slack bot token is stored in a config file written
`0600`. The field supports `${ENV_VAR}` substitution so the secret need not be
written in plaintext — the literal `${...}` token is preserved on disk; the value
is resolved only at runtime from the environment.

**Outbound egress.** When AI triage is enabled, the tool shells out to the local
`claude` CLI and passes fetched page content to it. That content is sent to
Anthropic's API as part of the triage prompt. No other data leaves the machine.

## Reporting a vulnerability

Use GitHub's private security advisory feature: on the repository page go to
**Security → Advisories → Report a vulnerability**. Do not open a public issue
for security reports.

For sensitive reports where you prefer email, contact **androidwatcher@krayong.com**.
The GitHub advisory channel is preferred; email is an alternative.

Include a description of the vulnerability, steps to reproduce, and the version
or commit you tested against. You will receive an acknowledgement within a few
business days.
