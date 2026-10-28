# Deployment boundaries

- This is a standalone Warpgate gateway project, with no history or dependencies from the previous SSH jump kit.
- Never run server installers on a client machine. VPS changes require Linux, root and --on-vps; Mac changes require Darwin, root, --on-server and an explicitly named server account.
- Windows changes require a 64-bit elevated Windows process, -OnServer and validated setup JSON. Only dedicated non-admin local accounts are supported. Preserve other accounts; require an explicit -RestartSshd before affecting a running service. Native PowerShell does not imply persistent sessions.
- Never commit runtime configuration, private keys, encryption keys, passwords, tokens, receipts, database backups or real machine identifiers. local/ is ignored.
- Preserve existing services and unrelated SSH access. Fail on conflicting installations; handle migration explicitly.
- Unknown SSH host keys must be rejected. Never disable TLS verification or enable legacy algorithms to make a test pass.
- Tests use disposable directories and loopback listeners; do not modify system services or real accounts in tests.
- Distinguish prepared files, successful isolated tests and actual deployment/network/reboot acceptance.
