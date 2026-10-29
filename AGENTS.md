# Deployment boundaries

- This is a standalone Warpgate gateway project, with no history or dependencies from the previous SSH jump kit.
- Never run server installers on a client machine. VPS changes require Linux, root and --on-vps; Mac changes require Darwin, root, --on-server and an explicitly named server account.
- Reuse existing remote Mac/Windows accounts, including administrator accounts; never create users or reset passwords. Do not use macOS root/system accounts. Restrict the selected account's connections from the VPS while preserving existing SSH key paths and other sources. Keep VPS service identities separate.
- Windows changes require a 64-bit elevated Windows process, -OnServer and validated setup JSON. Support existing local accounts. Preserve other accounts; require an explicit -RestartSshd before affecting a running service. Native Windows shells do not imply persistent sessions.
- Never commit runtime configuration, private keys, encryption keys, passwords, tokens, receipts, database backups or real machine identifiers. local/ is ignored.
- Preserve existing services and unrelated SSH access. Fail on conflicting installations; handle migration explicitly.
- Unknown SSH host keys must be rejected. Never disable TLS verification or enable legacy algorithms to make a test pass.
- Tests use disposable directories and loopback listeners; do not modify system services or real accounts in tests.
- Distinguish prepared files, successful isolated tests and actual deployment/network/reboot acceptance.
