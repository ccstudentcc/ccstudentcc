# Security Policy

## Supported Scope

This repository contains profile README automation and workflow orchestration scripts.
Security fixes are accepted for the current main branch.

## Reporting a Vulnerability

Please do not open public issues for sensitive vulnerabilities.

Report privately by GitHub Security Advisories:

- Repository Security tab -> Report a vulnerability

If advisory flow is unavailable, open a minimal issue without exploit details and request a private contact channel.

## What to Include

Please include:

- A clear description of the vulnerability
- Affected files and workflow path
- Reproduction steps and prerequisites
- Potential impact
- Suggested mitigation (if known)

## Response Targets

- Initial triage response: within 3 business days
- Status update: within 7 business days
- Fix timeline: depends on severity and complexity

## Severity Guidance

Higher priority cases include:

- Token or secret exposure risk
- Privilege escalation in workflows
- Untrusted command execution paths
- Insecure artifact/state handling

## Security Best Practices for Contributors

- Never commit secrets, tokens, or private keys.
- Use environment variables for credentials.
- Keep workflow permissions minimal and explicit.
- Validate reusable workflow boundaries and secret mappings.
- Avoid unsafe shell interpolation with user-controlled input.

## Disclosure Policy

Please allow maintainers time to investigate and patch before public disclosure.
Coordinated disclosure is appreciated.