# Security

Laolao runs entirely on your machine. After the one-time model download at
setup, it opens no outbound network connections: audio, transcripts and video
never leave the device. The only listening sockets are on `localhost` — the
caption WebSocket (`:8765`) and, under the Electron app, the frame sink
(`:8766`) — and neither accepts connections from other hosts.

If you find something that contradicts that, or any other vulnerability, please
report it privately through
[GitHub Security Advisories](https://github.com/BensonRen/Laolao/security/advisories/new)
rather than a public issue. You will get a reply within a week.
